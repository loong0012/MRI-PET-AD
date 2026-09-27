"""
重命名数据集脚本
功能：
1. 将 mri_crop/ 和 pet_crop/ 中的文件重命名并移动到 MRI/ 和 PET/
2. 文件名格式：XXX_S_XXXX_Group.nii -> sub-ADNIXXXSXXXX.nii.gz
3. 生成 ADNI.csv（Subject, Group, Age）

注意：Age 字段暂时留空，需要手动补充
"""

import os
import re
import shutil
import pandas as pd
from pathlib import Path


def extract_info(filename):
    """
    从文件名提取 Subject ID 和 Group
    例：002_S_4213_CN.nii -> (002_S_4213, sub-ADNI002S4213, CN)
    """
    # 去掉扩展名
    stem = Path(filename).stem  # 002_S_4213_CN
    # 用正则解析：XXX_S_XXXX_Group
    # 支持 3位数字_S_4位数字_标签 等格式
    match = re.match(r'^(\d+)_S_(\d+)_(AD|CN|pMCI|sMCI|MCI)$', stem)
    if not match:
        return None, None, None
    prefix_id = match.group(1)  # 002
    middle_id = match.group(2)  # 4213
    group = match.group(3)      # CN
    # 原始Subject（用于标签对应）
    original_subject = f"{prefix_id}_S_{middle_id}"
    # 新的Subject名
    new_subject = f"sub-ADNI{prefix_id}S{middle_id}"
    return original_subject, new_subject, group


def process_dataset(base_dir):
    """
    处理数据集
    """
    base_dir = Path(base_dir)
    mri_src = base_dir / "mri_crop"
    pet_src = base_dir / "pet_crop"
    mri_dst = base_dir / "MRI"
    pet_dst = base_dir / "PET"

    # 检查源目录
    if not mri_src.exists():
        print(f"错误：{mri_src} 不存在！")
        return
    if not pet_src.exists():
        print(f"错误：{pet_src} 不存在！")
        return

    # 创建目标目录
    mri_dst.mkdir(exist_ok=True)
    pet_dst.mkdir(exist_ok=True)
    print(f"已创建目录：{mri_dst}")
    print(f"已创建目录：{pet_dst}")

    # 获取源文件列表
    mri_files = sorted([f for f in mri_src.iterdir() if f.is_file() and f.suffix in ('.nii', '.nii.gz')])
    pet_files = sorted([f for f in pet_src.iterdir() if f.is_file() and f.suffix in ('.nii', '.nii.gz')])

    print(f"\nMRI 源文件数：{len(mri_files)}")
    print(f"PET 源文件数：{len(pet_files)}")

    # 解析文件名并创建映射
    records = []  # (original_subject, new_subject, group)
    skipped = []
    mri_map = {}  # new_subject -> source_path
    pet_map = {}

    for f in mri_files:
        orig_subj, new_subj, group = extract_info(f.name)
        if new_subj is None:
            skipped.append(f"MRI: {f.name}")
            continue
        mri_map[new_subj] = (f, group, orig_subj)

    for f in pet_files:
        orig_subj, new_subj, group = extract_info(f.name)
        if new_subj is None:
            skipped.append(f"PET: {f.name}")
            continue
        pet_map[new_subj] = (f, group, orig_subj)

    print(f"\n成功解析 MRI 文件：{len(mri_map)}")
    print(f"成功解析 PET 文件：{len(pet_map)}")
    if skipped:
        print(f"跳过不匹配格式的文件数：{len(skipped)}")
        for s in skipped[:5]:
            print(f"  - {s}")
        if len(skipped) > 5:
            print(f"  ... 还有 {len(skipped)-5} 个")

    # 找 MRI 和 PET 的交集
    common_subjects = sorted(set(mri_map.keys()) & set(pet_map.keys()))
    print(f"\nMRI 和 PET 配对成功的样本数：{len(common_subjects)}")

    # 检查 Group 是否一致
    group_mismatch = []
    for subj in common_subjects:
        if mri_map[subj][1] != pet_map[subj][1]:
            group_mismatch.append((subj, mri_map[subj][1], pet_map[subj][1]))
    if group_mismatch:
        print(f"\n警告：有 {len(group_mismatch)} 个样本 MRI 和 PET 的标签不一致！")
        for item in group_mismatch[:5]:
            print(f"  - {item[0]}: MRI={item[1]}, PET={item[2]}")

    # 执行重命名和复制（先复制，确认无误后再考虑删除源文件）
    success_count = 0
    csv_rows = []

    print(f"\n开始处理 {len(common_subjects)} 个样本...")
    for i, subj in enumerate(common_subjects):
        mri_src_path, group, orig_subj = mri_map[subj]
        pet_src_path, _, _ = pet_map[subj]

        # 目标文件名
        new_name = f"{subj}.nii.gz"
        mri_dst_path = mri_dst / new_name
        pet_dst_path = pet_dst / new_name

        # 复制文件（如果已存在则跳过）
        if not mri_dst_path.exists():
            shutil.copy2(mri_src_path, mri_dst_path)
        if not pet_dst_path.exists():
            shutil.copy2(pet_src_path, pet_dst_path)

        # 记录CSV行
        csv_rows.append({
            "Subject": subj,
            "Group": group,
            "Age": ""  # 年龄留空，需要手动补充
        })

        success_count += 1
        if (i + 1) % 100 == 0:
            print(f"  已处理 {i+1}/{len(common_subjects)}...")

    print(f"\n处理完成！成功复制 {success_count} 个样本")
    print(f"  MRI 输出目录：{mri_dst}")
    print(f"  PET 输出目录：{pet_dst}")

    # 验证
    mri_out_count = len([f for f in mri_dst.iterdir() if f.is_file()])
    pet_out_count = len([f for f in pet_dst.iterdir() if f.is_file()])
    print(f"\n输出目录文件数：MRI={mri_out_count}, PET={pet_out_count}")

    # 生成 ADNI.csv
    df = pd.DataFrame(csv_rows)
    csv_path = base_dir / "ADNI.csv"
    df.to_csv(csv_path, index=False, encoding='utf-8-sig')
    print(f"\n已生成 ADNI.csv：{csv_path}")
    print(f"  共 {len(df)} 条记录")

    # 标签统计
    group_counts = df['Group'].value_counts()
    print("\n标签分布：")
    for g, c in group_counts.items():
        print(f"  {g}: {c}")

    print("\n" + "="*60)
    print("注意：Age 字段目前为空，请手动补充到 ADNI.csv 中！")
    print("      源文件 mri_crop/ 和 pet_crop/ 保留未删除，请确认无误后再手动处理。")
    print("="*60)

    return csv_path


if __name__ == "__main__":
    base = r"d:\Desktop\TransMF_AD-master\datasets\MRI PET图像"
    process_dataset(base)
