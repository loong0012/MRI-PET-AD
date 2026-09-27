import pandas as pd
import os
from monai.transforms import (
    EnsureChannelFirstd,
    Compose,
    LoadImaged,
    SaveImaged,
    ScaleIntensityd,
    NormalizeIntensityd,
    SpatialCropd,
    SpatialPadd,
    RandFlipd,
    EnsureTyped, RandRotated, RandZoomd,
    MapTransform,
)

# ==============================================================
# Windows + NumPy2.x + MONAI 1.x OverflowError 修复
#
# 根因：
#   MONAI Compose 在 __init__ 时会调用:
#     s = np.random.randint(MAX_SEED, dtype="uint32")  其中 MAX_SEED = 2^32
#     _seed = s % MAX_SEED
#   np.uint32 对 int(2^32) 取模时，Python 会把 uint32 转成 C long，
#   Windows 下 C long 是 32-bit signed (max=2^31-1)，当 s>2^31-1 就溢出。
#
# 修复：在任何 transform 加载前，把 monai 的 MAX_SEED 降到 2^31，
#       让 randint 生成的结果天然 < 2^31，避免后续任何转换溢出。
# ==============================================================
import numpy as np
try:
    import monai.transforms.transform as _monai_tfm
    _C_SAFE_MAX_SEED = 2 ** 31  # 保证生成的 uint32 一定 < 2^31
    _monai_tfm.MAX_SEED = _C_SAFE_MAX_SEED
    # 同步 patch 所有导入了 MAX_SEED 的其他 monai 内部模块
    import sys as _sys
    for _name, _mod in list(_sys.modules.items()):
        if _name.startswith("monai") and hasattr(_mod, "MAX_SEED"):
            try:
                setattr(_mod, "MAX_SEED", _C_SAFE_MAX_SEED)
            except Exception:
                pass
    # 设置 numpy/torch 的全局 seed，保证首次 Compose 初始化前 RNG 状态安全
    np.random.seed(20240823)
except Exception:
    pass


class ADNI:
    def __init__(self, dataroot, label_filename, task):
        self.csv = pd.read_csv(os.path.join(dataroot, label_filename))
        # ===== NaN 安全修复 =====
        # - 年龄字段缺失时用全体中位数填充，避免后续 torch.as_tensor(NaN) 产生异常
        if 'Age' in self.csv.columns:
            try:
                age_series = pd.to_numeric(self.csv['Age'], errors='coerce')
                median_age = float(age_series.median())
                if not np.isfinite(median_age):
                    median_age = 75.0
                self.csv['Age'] = age_series.fillna(median_age).astype(float)
            except Exception:
                self.csv['Age'] = 75.0
        else:
            self.csv['Age'] = 75.0
        # Subject/Group 缺失值直接去除
        self.csv = self.csv.dropna(subset=['Subject', 'Group']).reset_index(drop=True)

        self.labels = None
        self.label_dict = None
        self.data_dict = None
        mri_dir = os.path.join(dataroot, 'MRI')
        pet_dir = os.path.join(dataroot, 'PET')

        # get data and labels according to specific task
        if task == 'ADCN':
            self.labels = self.csv[(self.csv['Group'] == 'AD') | (self.csv['Group'] == 'CN')]
            self.label_dict = {'CN': 0, 'AD': 1}
        if task == 'pMCIsMCI':
            self.labels = self.csv[(self.csv['Group'] == 'pMCI') | (self.csv['Group'] == 'sMCI')]
            self.label_dict = {'sMCI': 0, 'pMCI': 1}
        if task == 'MCICN':
            self.labels = self.csv[
                (self.csv['Group'] == 'pMCI') | (self.csv['Group'] == 'sMCI') | (self.csv['Group'] == 'MCI') | (
                            self.csv['Group'] == 'CN')]
            self.label_dict = {'CN': 0, 'sMCI': 1, 'pMCI': 1, 'MCI': 1}
        if task == 'ALL4':
            self.labels = self.csv[(self.csv['Group'] == 'AD') | (self.csv['Group'] == 'CN') |
                                   (self.csv['Group'] == 'sMCI') | (self.csv['Group'] == 'pMCI')]
            self.label_dict = {'CN': 0, 'AD': 1, 'sMCI': 2, 'pMCI': 3}

        # 自动检测 MRI/PET 目录中的实际扩展名 (.nii.gz 或 .nii)
        # 避免硬编码扩展名与实际文件不匹配（如 gzip 头缺失报错）
        def _detect_ext(img_dir, subject_names):
            """扫描目录中第一个存在的 subject 文件，确定使用的扩展名"""
            for ext_candidate in ('.nii.gz', '.nii'):
                for sub in subject_names:
                    if os.path.isfile(os.path.join(img_dir, sub + ext_candidate)):
                        return ext_candidate
            # 回退：扫描目录拿到任意文件的后缀
            try:
                for fname in os.listdir(img_dir):
                    if fname.endswith('.nii.gz'):
                        return '.nii.gz'
                    if fname.endswith('.nii'):
                        return '.nii'
            except Exception:
                pass
            return '.nii.gz'  # 最终回退

        subject_all = self.csv['Subject'].tolist()
        mri_ext = _detect_ext(mri_dir, subject_all)
        pet_ext = _detect_ext(pet_dir, subject_all)

        # ===== 文件存在性过滤：防止脏数据（CSV里有Subject但文件缺失）直接在DataLoader中报错 =====
        valid_rows_mask = []
        missing_count = 0
        for subj, subj_label in zip(self.csv['Subject'].tolist(), self.csv['Group'].tolist()):
            if (subj_label not in self.label_dict) if self.label_dict is not None else False:
                valid_rows_mask.append(False)
                missing_count += 1
                continue
            mri_path = os.path.join(mri_dir, subj + mri_ext)
            pet_path = os.path.join(pet_dir, subj + pet_ext)
            if os.path.isfile(mri_path) and os.path.isfile(pet_path):
                valid_rows_mask.append(True)
            else:
                valid_rows_mask.append(False)
                missing_count += 1
        if missing_count > 0 and self.labels is not None:
            # 只在过滤 label subset 之前统计一次，但不修改 csv 本身（否则会重复索引）
            print(f"[ADNI] 警告：从 CSV 中过滤掉 {missing_count} 条在 MRI/PET 中缺失对应文件的 Subject")

        # 过滤：label 过滤 + 文件存在 都满足
        # 简单做法：构造原始索引后，按 label 过滤后再检查存在性
        # 创建 data_dict 时按 subject 逐个验证文件是否存在，不存在则跳过
        # create data dic
        subject_list = self.labels['Subject'].tolist()
        label_list = self.labels['Group'].tolist()
        age_list = self.labels['Age'].tolist()
        raw_data_dict = []
        for subject_name, subject_label, subject_age in zip(subject_list, label_list, age_list):
            if subject_label not in self.label_dict:
                continue
            mri_path = os.path.join(mri_dir, subject_name + mri_ext)
            pet_path = os.path.join(pet_dir, subject_name + pet_ext)
            if not (os.path.isfile(mri_path) and os.path.isfile(pet_path)):
                continue
            raw_data_dict.append({
                'MRI': mri_path,
                'PET': pet_path,
                'label': self.label_dict[subject_label],
                'age': float(subject_age) if np.isfinite(float(subject_age)) else 75.0,
                'Subject': subject_name,
            })
        # 防止 CSV 过滤后 self.labels 长度与 data_dict 不一致，这里覆写 self.labels 为有效长度
        # 注意：self.labels 只在 __len__ 里使用，这里让 __len__ 与 data_dict 一致
        self.data_dict = raw_data_dict

    def __len__(self):
        # 以实际文件存在性过滤后的 data_dict 为准
        return len(self.data_dict)

    def get_weights(self):
        label_list = []
        for item in self.data_dict:
            label_list.append(item['label'])
        return float(label_list.count(0)), float(label_list.count(1))


class RandGammaD(MapTransform):
    """随机 Gamma 校正：x -> x^gamma，作用于 [0,1] 区间的强度值。
    非线性变换，改变直方图形状，InstanceNorm3d 无法完全吸收（只归一化均值/方差）。
    必须放在 ScaleIntensityd 之后、NormalizeIntensityd 之前。"""
    def __init__(self, keys, gamma_range=(0.8, 1.2), prob=0.3):
        super().__init__(keys)
        self.gamma_range = gamma_range
        self.prob = prob

    def __call__(self, data):
        import numpy as np
        if np.random.rand() > self.prob:
            return data
        gamma = float(np.random.uniform(*self.gamma_range))
        d = dict(data)
        for k in self.keys:
            img = d[k]
            # ScaleIntensityd 后值在 [0,1]，安全做幂
            if hasattr(img, 'pow'):
                d[k] = img.clamp(min=0).pow(gamma)
            else:
                arr = np.asarray(img, dtype=np.float32)
                arr = np.clip(arr, 0, None) ** gamma
                d[k] = arr
        return d


def ADNI_transform(aug='True', use_gamma_aug=False, gamma_range=(0.8, 1.2), gamma_prob=0.3):
    # 数据预处理关键点：
    # 1) ScaleIntensityd: 先把图像强度线性缩放到 [0,1]，消除跨被试绝对强度差异
    # 2) [可选] RandGammaD: 非线性 gamma 校正（方案 L），在 Scale 后、Normalize 前
    #    改变直方图形状，模拟不同扫描仪对比度差异，IN3d 无法完全吸收
    # 3) NormalizeIntensityd: 再做 z-score 标准化 (x-mean)/std，
    #    让 MRI 与 PET 共同处于零均值单位方差，更利于 Transformer/CNN 收敛
    # 4) 顺序：先 ScaleIntensityd 再 NormalizeIntensityd，保证 NormalizeIntensityd 输入稳定
    if aug == 'True':
        transform_list = [
                    LoadImaged(keys=['MRI', 'PET']),
                    EnsureChannelFirstd(keys=['MRI', 'PET']),
                    ScaleIntensityd(keys=['MRI', 'PET']),
        ]
        if use_gamma_aug:
            transform_list.append(
                RandGammaD(keys=['MRI', 'PET'], gamma_range=gamma_range, prob=gamma_prob)
            )
        transform_list.extend([
                    NormalizeIntensityd(keys=['MRI', 'PET'], nonzero=True, channel_wise=False),
                    # Augment
                    RandFlipd(keys=['MRI', 'PET'], prob=0.3, spatial_axis=0),
                    RandRotated(keys=['MRI', 'PET'], prob=0.3, range_x=0.05),
                    RandZoomd(keys=['MRI', 'PET'], prob=0.3, min_zoom=0.95, max_zoom=1),
                    EnsureTyped(keys=['MRI', 'PET'])
        ])
        train_transform = Compose(transform_list)
    else:
        train_transform = Compose([
                    LoadImaged(keys=['MRI', 'PET']),
                    EnsureChannelFirstd(keys=['MRI', 'PET']),
                    ScaleIntensityd(keys=['MRI', 'PET']),
                    NormalizeIntensityd(keys=['MRI', 'PET'], nonzero=True, channel_wise=False),
                    EnsureTyped(keys=['MRI', 'PET'])
                ])
    test_transform = Compose([
                LoadImaged(keys=['MRI', 'PET']),
                EnsureChannelFirstd(keys=['MRI', 'PET']),
                ScaleIntensityd(keys=['MRI', 'PET']),
                NormalizeIntensityd(keys=['MRI', 'PET'], nonzero=True, channel_wise=False),
                EnsureTyped(keys=['MRI', 'PET'])
            ])
    return train_transform, test_transform


def ADNI_transform_Mnet(aug='True'):
    if aug == 'True':
        train_transform = Compose([
                    LoadImaged(keys=['MRI', 'PET']),
                    EnsureChannelFirstd(keys=['MRI', 'PET']),
                    ScaleIntensityd(keys=['MRI', 'PET']),
                    SpatialPadd(keys=['MRI', 'PET'], spatial_size=(91,109,91)),
                    # Augment
                    RandFlipd(keys=['MRI', 'PET'], prob=0.3, spatial_axis=0),
                    RandRotated(keys=['MRI', 'PET'], prob=0.3, range_x=0.05),
                    RandZoomd(keys=['MRI', 'PET'], prob=0.3, min_zoom=0.95, max_zoom=1),
                    EnsureTyped(keys=['MRI', 'PET'])
                ])
    else:
        train_transform = Compose([
                    LoadImaged(keys=['MRI', 'PET']),
                    EnsureChannelFirstd(keys=['MRI', 'PET']),
                    ScaleIntensityd(keys=['MRI', 'PET']),
                    SpatialPadd(keys=['MRI', 'PET'], spatial_size=(91, 109, 91)),
                    EnsureTyped(keys=['MRI', 'PET'])
                ])
    test_transform = Compose([
                LoadImaged(keys=['MRI', 'PET']),
                EnsureChannelFirstd(keys=['MRI', 'PET']),
                ScaleIntensityd(keys=['MRI', 'PET']),
                SpatialPadd(keys=['MRI', 'PET'], spatial_size=(91, 109, 91)),
                EnsureTyped(keys=['MRI', 'PET'])
            ])
    return train_transform, test_transform

def ADNI_transform_ADVIT(aug='True'):
    train_transform = Compose([
                    LoadImaged(keys=['MRI', 'PET']),
                    EnsureChannelFirstd(keys=['MRI', 'PET']),
                    ScaleIntensityd(keys=['MRI', 'PET']),
                    SpatialPadd(keys=['MRI', 'PET'], spatial_size=(128, 128, 79)),
                    EnsureTyped(keys=['MRI', 'PET'])
                ])
    test_transform = Compose([
                LoadImaged(keys=['MRI', 'PET']),
                EnsureChannelFirstd(keys=['MRI', 'PET']),
                ScaleIntensityd(keys=['MRI', 'PET']),
                SpatialPadd(keys=['MRI', 'PET'], spatial_size=(128, 128, 79)),
                EnsureTyped(keys=['MRI', 'PET'])
            ])
    return train_transform, test_transform

