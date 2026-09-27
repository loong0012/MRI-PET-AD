import os
os.environ['KMP_DUPLICATE_LIB_OK'] = 'TRUE'

import glob
import random
import csv
from copy import deepcopy

import numpy as np
import torch

# ===== Windows MONAI Seed 溢出修复 (必须在任何 MONAI transform 初始化前执行) =====
try:
    import monai.transforms.transform as _m_tfm
    _SAFE_MAX = 2 ** 31
    _m_tfm.MAX_SEED = _SAFE_MAX
    import sys as _sys
    for _n, _m in list(_sys.modules.items()):
        if _n.startswith("monai") and hasattr(_m, "MAX_SEED"):
            try:
                setattr(_m, "MAX_SEED", _SAFE_MAX)
            except Exception:
                pass
except Exception:
    pass

# 多种子集成：通过环境变量 TRANS_MODEL_SEED 覆盖模型初始化种子。
# 注意 split seed（ADCN=42）独立于此，不受影响，保证各种子训练的 test 集完全对齐。
_SEED = int(os.environ.get('TRANS_MODEL_SEED', '20240823'))
random.seed(_SEED)
np.random.seed(_SEED)
torch.manual_seed(_SEED)
torch.cuda.manual_seed_all(_SEED)
try:
    from monai.utils import set_determinism
    set_determinism(seed=_SEED)
except Exception:
    pass
# ===================================================================================

from datasets.ADNI import ADNI, ADNI_transform
from models.mymodel import model_ad, model_CNN_ad
from options.option import Option
from sklearn.model_selection import train_test_split
from torch.utils.data import DataLoader
from torch.utils.data import WeightedRandomSampler
from monai.data import Dataset
import ignite
from ignite.metrics import Accuracy, Loss, Average, ConfusionMatrix
from ignite.engine import Engine, Events
try:
    from ignite.contrib.handlers import ProgressBar
except ModuleNotFoundError as _e:
    import warnings
    warnings.warn(f"ProgressBar 不可用（{_e}）。继续训练，但不会显示 tqdm 进度条。请 pip install tqdm 以启用进度条。")
    class _NoopProgressBar:
        def __init__(self, *a, **kw): pass
        def attach(self, *a, **kw): return None
        def __call__(self, *a, **kw): return None
    ProgressBar = _NoopProgressBar
from ignite.contrib.metrics import ROC_AUC
from ignite.handlers import Checkpoint, global_step_from_engine, DiskSaver, LRScheduler
from utils.utils import (
    getOptimizer, cal_confusion_metrics, mkdirs, get_dataset_weights,
    build_loss, EarlyStopping, EMA, stratified_split_train_val,
    stratified_kfold_indices, stratified_kfold_by_cohort,
    balanced_accuracy_from_confusion,
)
from torch.nn.functional import softmax
from utils.utils import Logger
import os


def _str2bool(s: str) -> bool:
    if isinstance(s, bool):
        return s
    return str(s).strip().lower() in ('1', 'true', 'yes', 'y', 't')


if __name__ == '__main__':
    device = torch.device('cuda:{}'.format(0))
    # initialize options and create output directory
    opt = Option().parse()
    save_dir = os.path.join('./checkpoints', opt.name)
    mkdirs(save_dir)

    # load ADNI dataset
    ADNI_data = ADNI(dataroot=opt.dataroot, label_filename='ADNI.csv', task=opt.task).data_dict
    # 方案 L：Gamma 非线性增强（扫描仪对比度差异，IN3d 无法吸收）
    use_gamma_aug = _str2bool(getattr(opt, 'use_gamma_aug', 'False'))
    gamma_range_str = str(getattr(opt, 'gamma_range', '0.8,1.2'))
    gamma_range = tuple(float(x) for x in gamma_range_str.split(','))
    gamma_prob = float(getattr(opt, 'gamma_prob', 0.3))
    train_transforms, val_transforms = ADNI_transform(
        opt.aug, use_gamma_aug=use_gamma_aug,
        gamma_range=gamma_range, gamma_prob=gamma_prob)
    if use_gamma_aug:
        print(f'[Gamma] enabled: range={gamma_range}, prob={gamma_prob}')
    logger_main = Logger(save_dir)

    use_ema = _str2bool(getattr(opt, 'use_ema', 'True'))
    use_non_blocking = _str2bool(getattr(opt, 'non_blocking', 'True')) and _str2bool(getattr(opt, 'pin_memory', 'True'))
    pin_mem = _str2bool(getattr(opt, 'pin_memory', 'True'))
    persist_wk = _str2bool(getattr(opt, 'persistent_workers', 'False'))
    num_workers = int(getattr(opt, 'num_workers', 0))
    num_fold = int(getattr(opt, 'num_fold', 5))
    grad_clip = float(getattr(opt, 'grad_clip_norm', 0.0))
    save_score = str(getattr(opt, 'save_score', 'auc'))
    save_csv = _str2bool(getattr(opt, 'save_csv_summary', 'True'))
    use_weighted_sampler = _str2bool(getattr(opt, 'use_weighted_sampler', 'True'))
    print(f'[Sampler] use_weighted_sampler={use_weighted_sampler}')

    # prepare kfold splits —— 用分层 KFold，避免类别不平衡导致的 split 偏差
    seed = 1
    if opt.task == 'ADCN':
        seed = 42
    elif opt.task == 'pMCIsMCI':
        seed = 996
    if opt.randint == 'True':
        seed = random.randint(1, 1000)
    print(f'The random seed is {seed}')

    # 标签数组（给 StratifiedKFold 用）
    all_labels = [int(d['label']) for d in ADNI_data]
    # 方案 K：队列+标签双重分层 CV（降低 fold 间方差）
    cohort_split = _str2bool(getattr(opt, 'cohort_split', 'False'))
    if cohort_split:
        kfold_splits = list(stratified_kfold_by_cohort(ADNI_data, n_splits=num_fold, seed=seed))
        print(f'[CohortSplit] enabled: 4-stratum (cohort×label) stratified KFold')
    else:
        kfold_splits = list(stratified_kfold_indices(all_labels, n_splits=num_fold, seed=seed))


    # get dataloaders according to splits
    def setup_dataflow(train_idx, test_idx):
        # 分层切分 train/val（防止 类别不平衡下 某一类全进val/全进train）
        labels_train_at_idx = [int(ADNI_data[int(i)]['label']) for i in train_idx]
        t_subidx, v_subidx = stratified_split_train_val(
            train_idx, labels_train_at_idx, val_ratio=0.2, seed=seed
        )
        train_idx2 = np.asarray([train_idx[i] for i in t_subidx], dtype=np.int64)
        val_idx2 = np.asarray([train_idx[i] for i in v_subidx], dtype=np.int64)

        train_data = [ADNI_data[int(i)] for i in train_idx2]
        val_data = [ADNI_data[int(i)] for i in val_idx2]
        test_data = [ADNI_data[int(i)] for i in test_idx]

        if opt.task == 'pMCIsMCI' and opt.extra_sample == 'True':
            ADNI_ADCN_data = ADNI(dataroot=opt.dataroot, label_filename='ADNI.csv', task='ADCN').data_dict
            train_data += ADNI_ADCN_data

        # create datasets
        train_dataset = Dataset(data=train_data, transform=train_transforms)
        val_dataset = Dataset(data=val_data, transform=val_transforms)
        test_dataset = Dataset(data=test_data, transform=val_transforms)
        print(f'Train Datasets: {len(train_dataset)}  Val: {len(val_dataset)}  Test: {len(test_dataset)}')

        # 先计算类别权重（逆频率），给 loss 和 WeightedRandomSampler 共用
        weights = get_dataset_weights(train_dataset, None)

        # ===== 方案2：merge_train_val=True 时把 val 并入 train（数据量 +20%，固定 epoch 训练）=====
        merge_tv = _str2bool(getattr(opt, 'merge_train_val', 'False'))
        if merge_tv and len(val_data) > 0:
            train_data = train_data + val_data
            val_data = []
            # 合并后需重新计算类别权重（类别比例不变，权重其实一致，这里保险起见重算）
            train_dataset = Dataset(data=train_data, transform=train_transforms)
            weights = get_dataset_weights(train_dataset, None)
            print(f'[MergeTV] train+val merged: {len(train_data)} samples, val set empty')

        # DataLoader 参数：Windows 下 num_workers 过大容易卡住，默认 2；persistent_workers 需要 num_workers>0
        loader_kwargs = dict(
            batch_size=opt.batch_size,
            num_workers=num_workers,
            pin_memory=pin_mem,
            persistent_workers=(persist_wk and num_workers > 0),
        )
        # 训练集：可选 WeightedRandomSampler 过采样少数类，让每个 batch 类别大致均衡
        if use_weighted_sampler:
            sample_w = [float(weights[int(item['label'])]) for item in train_data]
            train_sampler = WeightedRandomSampler(
                weights=sample_w, num_samples=len(train_data), replacement=True)
            train_loader = DataLoader(train_dataset, sampler=train_sampler,
                                      drop_last=True, **loader_kwargs)
            print(f'[Sampler] WeightedRandomSampler enabled (num_samples={len(train_data)})')
        else:
            train_loader = DataLoader(train_dataset, shuffle=True, drop_last=True, **loader_kwargs)
        # val 集为空（merge_train_val 模式）时返回 None，训练侧需判空跳过验证
        val_loader = DataLoader(val_dataset, shuffle=False, drop_last=False, **loader_kwargs) \
            if len(val_data) > 0 else None
        test_loader = DataLoader(test_dataset, shuffle=False, drop_last=False, **loader_kwargs)

        return train_loader, val_loader, test_loader, weights


    # initialize model, optimizer, loss
    def init_model(model):
        net_model = None
        num_classes = int(getattr(opt, 'num_classes', 2))
        if model == 'Transformer':
            net_model = model_ad(dim=opt.dim, depth=opt.trans_enc_depth, heads=4,
                                 dim_head=opt.dim // 4, mlp_dim=opt.dim * 4, dropout=opt.dropout,
                                 num_classes=num_classes).to(device)
        elif model == 'CNN':
            net_model = model_CNN_ad(dim=opt.dim, num_classes=num_classes).to(device)

        # ===== 方案B：加载预训练权重（只加载特征提取器，不加载 fc_cls 和 D）=====
        pretrained_path = str(getattr(opt, 'pretrained_path', ''))
        if pretrained_path:
            if os.path.isfile(pretrained_path):
                pretrained_dict = torch.load(pretrained_path, map_location=device)
                # 兼容直接保存 state_dict 或 {'net_model': state_dict} 两种格式
                if isinstance(pretrained_dict, dict) and 'net_model' in pretrained_dict \
                        and not all(isinstance(v, torch.Tensor) for v in pretrained_dict.values()):
                    pretrained_dict = pretrained_dict['net_model']
                model_dict = net_model.state_dict()
                strict = _str2bool(getattr(opt, 'pretrained_strict', 'False'))
                if strict:
                    # 严格加载（要求 keys 完全匹配）
                    net_model.load_state_dict(pretrained_dict, strict=True)
                    print(f'[Pretrain] strict-loaded {len(pretrained_dict)} layers from {pretrained_path}')
                else:
                    # 只加载特征提取器：跳过 fc_cls 和 D（分类头/域判别器与目标任务不同）
                    filtered = {k: v for k, v in pretrained_dict.items()
                                if k in model_dict and not k.startswith('fc_cls') and not k.startswith('D')
                                and model_dict[k].shape == v.shape}
                    model_dict.update(filtered)
                    net_model.load_state_dict(model_dict)
                    print(f'[Pretrain] loaded {len(filtered)}/{len(pretrained_dict)} layers '
                          f'(skipped fc_cls/D) from {pretrained_path}')
            else:
                print(f'[Pretrain] WARNING: pretrained_path not found: {pretrained_path}, train from scratch')
        return net_model


    def train_model(train_dataloader, val_dataloader, test_dataloader, fold, weights):
        # create fold checkpoint directory
        save_path_fold = os.path.join(save_dir, str(fold))
        mkdirs(save_path_fold)
        logger = Logger(save_path_fold)
        # initialize model, optimizer and loss
        net_model = init_model(opt.model)
        optimizer, lr_schedualer = getOptimizer(net_model.parameters(), opt)
        # 使用统一的损失构建器（LSCE / Focal + 类别平衡权重 + label smooth）
        criterion = build_loss(opt, class_weights=weights)
        # 对抗分支仍然用 vanilla CE（域判别器不需要 label smooth 或 focal）
        adv_criterion = torch.nn.CrossEntropyLoss()

        # ===== 对抗分支开关 + 权重 =====
        use_adversarial = _str2bool(getattr(opt, 'use_adversarial', 'True'))
        ad_loss_weight = float(getattr(opt, 'ad_loss_weight', 0.1))
        if not use_adversarial or ad_loss_weight <= 0.0:
            use_adversarial = False
            ad_loss_weight = 0.0
        logger.print_message(f'[Adversarial] enabled={use_adversarial}, weight={ad_loss_weight}')

        # ===== 方案 H：Mixup 数据增强（缓解 427 样本过拟合，抑制捷径学习）=====
        use_mixup = _str2bool(getattr(opt, 'use_mixup', 'False'))
        mixup_alpha = float(getattr(opt, 'mixup_alpha', 0.4))
        if use_mixup:
            logger.print_message(f'[Mixup] enabled: alpha={mixup_alpha}')
        else:
            mixup_alpha = 0.0

        # EMA
        ema = None
        if use_ema:
            ema_decay = float(getattr(opt, 'ema_decay', 0.9998))
            ema = EMA(net_model, decay=ema_decay, device=device)
            logger.print_message(f'[EMA] enabled with decay={ema_decay}')

        # EarlyStopping：按 save_score 决定 mode
        # fixed_epochs>0（方案2 固定 epoch 重训）时禁用早停与最佳模型选择
        fixed_epochs = int(getattr(opt, 'fixed_epochs', 0))
        fixed_mode = fixed_epochs > 0
        snapshot_epochs = set()
        for _e in str(getattr(opt, 'snapshot_epochs', '')).split(','):
            _e = _e.strip()
            if _e.isdigit():
                snapshot_epochs.add(int(_e))
        if fixed_mode:
            logger.print_message(f'[FixedMode] train {fixed_epochs} epochs without early stop / best select'
                                 + (f', snapshots @ {sorted(snapshot_epochs)}' if snapshot_epochs else ''))
        esp_patience = int(getattr(opt, 'early_stop_patience', 15))
        early_stopper = None
        if esp_patience > 0 and not fixed_mode:
            es_mode = 'min' if save_score == 'loss' else 'max'
            early_stopper = EarlyStopping(patience=esp_patience, mode=es_mode, verbose=True)
            logger.print_message(f'[EarlyStop] patience={esp_patience}, score={save_score}, mode={es_mode}')

        res_fold = []
        best_state_dict = None     # 保存最佳权重，用于最终 test
        best_primary = None        # 保存最佳主指标值

        # define train step
        def train_step(engine, batch):
            output_dic = {}
            net_model.train()
            MRI = batch['MRI'].to(device, non_blocking=use_non_blocking)
            PET = batch['PET'].to(device, non_blocking=use_non_blocking)
            label = batch['label'].to(device, non_blocking=use_non_blocking)
            output_dic['label'] = label

            optimizer.zero_grad(set_to_none=True)

            # ===== 方案 H：Mixup（在 forward 前混合输入，loss 用双标签加权）=====
            mixup_lam = 1.0
            if use_mixup and MRI.shape[0] > 1:
                mixup_lam = float(np.random.beta(mixup_alpha, mixup_alpha))
                if mixup_lam < 0.999:  # lambda≈1 时不混合
                    perm = torch.randperm(MRI.shape[0], device=MRI.device)
                    MRI = mixup_lam * MRI + (1.0 - mixup_lam) * MRI[perm]
                    PET = mixup_lam * PET + (1.0 - mixup_lam) * PET[perm]
                    label_mix = label[perm]

            output_logits, D_MRI_logits, D_PET_logits = net_model(MRI, PET)
            output_dic['logits'] = output_logits
            output_dic['D_MRI_logits'] = D_MRI_logits
            output_dic['D_PET_logits'] = D_PET_logits

            if mixup_lam < 0.999:
                ce_loss = mixup_lam * criterion(output_logits, label) + \
                          (1.0 - mixup_lam) * criterion(output_logits, label_mix)
            else:
                ce_loss = criterion(output_logits, label)
            mri_gt = torch.ones([D_MRI_logits.shape[0]], dtype=torch.int64).to(MRI.device)
            pet_gt = torch.zeros([D_PET_logits.shape[0]], dtype=torch.int64).to(PET.device)

            output_dic['D_MRI_label'] = mri_gt
            output_dic['D_PET_label'] = pet_gt

            # 对抗分支损失：当 use_adversarial=False 时直接置 0（等价于跳过对抗分支，
            # 主任务梯度只来自 ce_loss，让模型先学有效决策边界）
            if use_adversarial:
                ad_loss_raw = (adv_criterion(D_MRI_logits, mri_gt) + adv_criterion(D_PET_logits, pet_gt)) / 2
                ad_loss = ad_loss_raw * ad_loss_weight
            else:
                ad_loss = torch.zeros((), device=MRI.device)

            # 用 GPU Tensor 累计，防止频繁 .item() CPU 同步（但这里 .item() 只给 metrics 用在 ignite）
            output_dic['ce_loss'] = float(ce_loss.detach())
            output_dic['ad_loss'] = float(ad_loss.detach())

            all_loss = ad_loss + ce_loss
            all_loss.backward()

            # 梯度裁剪（防止 3D 卷积偶尔的梯度爆炸）
            if grad_clip and grad_clip > 0:
                torch.nn.utils.clip_grad_norm_(net_model.parameters(), max_norm=grad_clip)

            optimizer.step()

            # EMA 更新
            if ema is not None:
                ema.update()

            return output_dic

        trainer_label = Engine(train_step)
        ProgressBar().attach(trainer_label)
        lr_schedualer_handler = LRScheduler(lr_schedualer)
        trainer_label.add_event_handler(Events.EPOCH_STARTED, lr_schedualer_handler)

        # define validation step
        def val_step(engine, batch):
            output_dic = {}
            net_model.eval()
            # 验证时使用 EMA shadow 权重（通常更稳 +0.5~1.5 AUC/ACC）
            backup = None
            if ema is not None:
                backup = ema.apply_shadow()
            try:
                with torch.no_grad():
                    MRI = batch['MRI'].to(device, non_blocking=use_non_blocking)
                    PET = batch['PET'].to(device, non_blocking=use_non_blocking)
                    label = batch['label'].to(device, non_blocking=use_non_blocking)
                    output_dic['label'] = label
                    output_logits, _, _ = net_model(MRI, PET)
                    output_dic['logits'] = output_logits
                    all_loss = criterion(output_logits, label)
                    output_dic['loss'] = float(all_loss.detach())
            finally:
                if ema is not None and backup is not None:
                    ema.restore(backup)
            return output_dic

        evaluator = Engine(val_step)
        ProgressBar().attach(evaluator)

        class one_hot_transform:
            def __init__(self, target):
                self.target = target

            def __call__(self, output):
                y_pred, y = output[self.target], output['label']
                y_pred = torch.argmax(y_pred, dim=1).long()
                y_pred = ignite.utils.to_onehot(y_pred, 2)
                y = y.long()
                return y_pred, y

        # metrics
        train_metrics = {"accuracy": Accuracy(output_transform=lambda x: [x['logits'], x['label']]),
                         "MRI_accuracy": Accuracy(output_transform=lambda x: [x['D_MRI_logits'], x['D_MRI_label']]),
                         "PET_accuracy": Accuracy(output_transform=lambda x: [x['D_PET_logits'], x['D_PET_label']]),
                         "ce_loss": Average(output_transform=lambda x: x['ce_loss']),
                         "ad_loss": Average(output_transform=lambda x: x['ad_loss'])}
        val_metrics = {"accuracy": Accuracy(output_transform=lambda x: [x['logits'], x['label']]),
                       "confusion": ConfusionMatrix(num_classes=2,
                                                    output_transform=one_hot_transform(target='logits')),
                       "auc": ROC_AUC(output_transform=lambda x: [softmax(x['logits'], dim=1)[:, -1], x['label']]),
                       "loss": Loss(criterion, output_transform=lambda x: [x['logits'], x['label']])}

        for name, metric in train_metrics.items():
            metric.attach(trainer_label, name)
        for name, metric in val_metrics.items():
            metric.attach(evaluator, name)

        def _primary_score(metrics):
            """根据 save_score 提取主指标（用于早停+最佳模型保存）"""
            if save_score == 'acc':
                return float(metrics['accuracy'])
            if save_score == 'loss':
                return float(metrics['loss'])
            # 默认 auc
            return float(metrics['auc'])

        # logging for training every epoch
        @trainer_label.on(Events.EPOCH_COMPLETED)
        def log_training_results(trainer_label):
            metrics = trainer_label.state.metrics
            logger.print_message('-------------------------------------------------')
            curr_lr = optimizer.param_groups[0]['lr']
            logger.print_message((f'Current learning rate: {curr_lr:.6e}'))
            logger.print_message(f"Training Results - Epoch[{trainer_label.state.epoch}] ")
            logger.print_message(f"ce_loss: {metrics['ce_loss']:.4f} "
                                 f"ad_loss: {metrics['ad_loss']:.4f} "
                                 f"accuracy: {metrics['accuracy']:.4f} "
                                 f"MRIaccuracy: {metrics['MRI_accuracy']:.4f} "
                                 f"PETaccuracy: {metrics['PET_accuracy']:.4f} ")

        # logging for validation every epoch + 早停 + 最佳快照保存
        @trainer_label.on(Events.EPOCH_COMPLETED)
        def log_validation_results(trainer_label):
            nonlocal best_state_dict, best_primary

            # ===== 方案2：merge_train_val 模式下无验证集，仅按需保存快照 =====
            if val_dataloader is None:
                cur_epoch = trainer_label.state.epoch
                if fixed_mode and cur_epoch in snapshot_epochs:
                    snap_sd = None
                    if ema is not None:
                        backup = ema.apply_shadow()
                        try:
                            snap_sd = deepcopy(net_model.state_dict())
                        finally:
                            ema.restore(backup)
                    else:
                        snap_sd = deepcopy(net_model.state_dict())
                    snap_path = os.path.join(save_path_fold, f'snapshot_epoch{cur_epoch}.pt')
                    torch.save({'net_model': snap_sd, 'epoch': cur_epoch, 'source': 'snapshot'}, snap_path)
                    logger.print_message(f'  [Snapshot] saved EMA snapshot -> {snap_path}')
                return

            evaluator.run(val_dataloader)
            metrics = evaluator.state.metrics
            primary = _primary_score(metrics)
            sen, spe, f1 = cal_confusion_metrics(metrics['confusion'])
            bacc = balanced_accuracy_from_confusion(metrics['confusion'])
            logger.print_message(f"Validation Results - Epoch[{trainer_label.state.epoch}] ")
            logger.print_message(f"loss: {metrics['loss']:.4f} accuracy: {metrics['accuracy']:.4f} "
                                 f"balanced_acc: {bacc:.4f} "
                                 f"sensitivity: {sen:.4f} specificity: {spe:.4f} "
                                 f"f1 score: {f1:.4f} AUC: {metrics['auc']:.4f} "
                                 f"[primary={save_score}: {primary:.4f}]")

            # 最佳模型记录 & 保存到本地 pt（不仅靠 Ignite Checkpoint，还额外备份 best_state_dict 用于最终 test）
            greater_better = (save_score != 'loss')
            better = (best_primary is None)
            if best_primary is not None:
                better = (primary > best_primary) if greater_better else (primary < best_primary)
            if better:
                best_primary = float(primary)
                # 优先保存 EMA shadow 权重（如果启用）
                if ema is not None:
                    backup = ema.apply_shadow()
                    try:
                        best_state_dict = deepcopy(net_model.state_dict())
                    finally:
                        ema.restore(backup)
                else:
                    best_state_dict = deepcopy(net_model.state_dict())
                # 写一份 best_ckpt.pt 到 fold 目录（覆盖写，只保留单份最佳）
                try:
                    best_pt = os.path.join(save_path_fold, 'best_ckpt.pt')
                    torch.save({'net_model': best_state_dict,
                                'epoch': trainer_label.state.epoch,
                                f'best_{save_score}': best_primary}, best_pt)
                    logger.print_message(f'  [Best] save best ckpt @ epoch {trainer_label.state.epoch}, '
                                         f'{save_score}={best_primary:.4f}')
                except Exception as e:
                    logger.print_message(f'  [Best] save ckpt failed: {e}')

            # 快照保存（普通模式也可用，用于快照集成）
            if trainer_label.state.epoch in snapshot_epochs:
                snap_sd = None
                if ema is not None:
                    backup = ema.apply_shadow()
                    try:
                        snap_sd = deepcopy(net_model.state_dict())
                    finally:
                        ema.restore(backup)
                else:
                    snap_sd = deepcopy(net_model.state_dict())
                snap_path = os.path.join(save_path_fold, f'snapshot_epoch{trainer_label.state.epoch}.pt')
                torch.save({'net_model': snap_sd, 'epoch': trainer_label.state.epoch, 'source': 'snapshot'},
                           snap_path)
                logger.print_message(f'  [Snapshot] saved EMA snapshot -> {snap_path}')

            # 早停判定（fixed 模式下 early_stopper 恒为 None）
            if early_stopper is not None:
                improved = early_stopper.step(primary)
                if early_stopper.should_stop:
                    logger.print_message(f'[EarlyStop] Triggered @ epoch {trainer_label.state.epoch}. '
                                         f'best {save_score}={early_stopper.best:.4f}')
                    trainer_label.terminate()

        # (可选) 原 Ignite Checkpoint：按 save_score 保存 n_saved=1，兼容原代码路径
        greater_or_equal = (save_score != 'loss')
        _score_name = save_score
        checkpoint_saver = Checkpoint({'net_model': net_model},
                                      save_handler=DiskSaver(save_path_fold, require_empty=False),
                                      n_saved=1, filename_prefix='best_label', score_name=_score_name,
                                      global_step_transform=global_step_from_engine(trainer_label),
                                      greater_or_equal=greater_or_equal)

        # 注意：我们已经用 best_ckpt.pt 记录最佳权重，这里的 evaluator.COMPLETED checkpoint 仅作为兼容备份
        # 为了让它记录"最佳"，我们需要给 evaluator 注入一个 score；Ignite Checkpoint score_function 优先
        # 这里简单起见不再 attach 它，避免与 EMA/best_ckpt.pt 权重不一致。

        @trainer_label.on(Events.COMPLETED)
        def run_on_test(trainer_label):
            nonlocal best_state_dict
            # fixed 模式（方案2）：无 best_state_dict，用最终 EMA shadow 权重测试并保存
            if best_state_dict is None:
                if ema is not None:
                    backup = ema.apply_shadow()
                    try:
                        best_state_dict = deepcopy(net_model.state_dict())
                    finally:
                        ema.restore(backup)
                else:
                    best_state_dict = deepcopy(net_model.state_dict())
                try:
                    torch.save({'net_model': best_state_dict,
                                'epoch': trainer_label.state.epoch,
                                'source': 'fixed_mode_final'},
                               os.path.join(save_path_fold, 'best_ckpt.pt'))
                    logger.print_message(f'[FixedMode] saved final EMA weights -> best_ckpt.pt '
                                         f'@ epoch {trainer_label.state.epoch}')
                except Exception as e:
                    logger.print_message(f'[FixedMode] save final ckpt failed: {e}')

            # 优先使用我们记录的 best_state_dict（支持 EMA）
            if best_state_dict is not None:
                net_model.load_state_dict(best_state_dict)
                if best_primary is not None:
                    logger.print_message(f'Load best state_dict from memory (best {save_score}={best_primary:.4f})')
                else:
                    logger.print_message(f'Load state_dict from memory (fixed_mode, epoch={trainer_label.state.epoch})')
            else:
                # 兜底：使用 Ignite 保存的最佳
                try:
                    best_model_path = glob.glob(os.path.join(save_path_fold, 'best_label_net_model_*.pt'))
                    if best_model_path:
                        checkpoint_all = torch.load(best_model_path[0], map_location=device)
                        Checkpoint.load_objects(to_load={'net_model': net_model}, checkpoint=checkpoint_all)
                        logger.print_message(f'Load Ignite best model {best_model_path[0]}')
                except Exception as e:
                    logger.print_message(f'Ignite best model load failed: {e}. Use current weights.')

            evaluator.run(test_dataloader)
            metrics = evaluator.state.metrics
            sen, spe, f1 = cal_confusion_metrics(metrics['confusion'])
            bacc = balanced_accuracy_from_confusion(metrics['confusion'])
            logger.print_message('**************************************************************')
            logger.print_message(f"Test Results")
            logger.print_message(f"loss: {metrics['loss']:.4f} accuracy: {metrics['accuracy']:.4f} "
                                 f"balanced_acc: {bacc:.4f} "
                                 f"sensitivity: {sen:.4f} specificity: {spe:.4f} "
                                 f"f1 score: {f1:.4f} AUC: {metrics['auc']:.4f} ")
            logger_main.print_message_nocli(f"loss: {metrics['loss']:.4f} accuracy: {metrics['accuracy']:.4f} "
                                            f"balanced_acc: {bacc:.4f} "
                                            f"sensitivity: {sen:.4f} specificity: {spe:.4f} "
                                            f"f1 score: {f1:.4f} AUC: {metrics['auc']:.4f} ")
            res_fold[:] = [float(metrics['loss']), float(metrics['accuracy']),
                           float(bacc), float(sen), float(spe), float(f1), float(metrics['auc'])]

        # 方案2 fixed 模式：训练固定 epoch；否则按 stage1+stage2
        total_epochs = fixed_epochs if fixed_mode else (opt.stage1_epochs + opt.stage2_epochs)
        trainer_label.run(train_dataloader, total_epochs)

        return res_fold


    results = []
    start_fold = int(getattr(opt, 'start_fold', 0))
    for fold_idx, (train_idx, test_idx) in enumerate(kfold_splits):
        # 断点续训：跳过已完成训练（权重已保存）的 fold
        if fold_idx < start_fold:
            logger_main.print_message(f'************Fold {fold_idx}************')
            logger_main.print_message(f'[Resume] skipped (start_fold={start_fold}), '
                                      f'use saved weights in {os.path.join(save_dir, str(fold_idx))}')
            continue
        logger_main.print_message(f'************Fold {fold_idx}************')
        train_dataloader, val_dataloader, test_dataloader, weights = setup_dataflow(train_idx, test_idx)
        res = train_model(train_dataloader, val_dataloader, test_dataloader, fold_idx, weights)
        results.append(res)

    # calculate mean and std for each metrics
    results = np.asarray(results, dtype=np.float64)
    res_mean = np.mean(results, axis=0)
    res_std = np.std(results, axis=0)

    headers = ['loss', 'acc', 'balanced_acc', 'sen', 'spe', 'f1', 'auc']
    logger_main.print_message(f'************Final Results ({num_fold}-fold mean ± std)************')
    final_line = ''
    for h, m, s in zip(headers, res_mean, res_std):
        line = f'{h}: {m:.4f} +- {s:.4f}\n'
        logger_main.print_message(line.rstrip('\n'))
        final_line += line
    print(f'The random seed is {seed}')

    # 生成汇总 CSV
    if save_csv:
        csv_path = os.path.join(save_dir, 'results_summary.csv')
        try:
            with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
                w = csv.writer(f)
                w.writerow(['fold'] + headers)
                for i, row in enumerate(results):
                    w.writerow([f'fold_{i}'] + [f'{v:.6f}' for v in row])
                w.writerow([])
                w.writerow(['stat'] + headers)
                w.writerow(['mean'] + [f'{v:.6f}' for v in res_mean])
                w.writerow(['std'] + [f'{v:.6f}' for v in res_std])
                w.writerow([])
                w.writerow(['seed', seed])
                w.writerow(['model', opt.model])
                w.writerow(['task', opt.task])
                w.writerow(['batch_size', opt.batch_size])
                w.writerow(['lr', opt.lr])
                w.writerow(['optimizer', getattr(opt, 'optimizer', 'AdamW')])
                w.writerow(['loss_type', getattr(opt, 'loss_type', 'lsce')])
                w.writerow(['save_score', save_score])
                w.writerow(['use_ema', use_ema])
                w.writerow(['early_stop_patience', getattr(opt, 'early_stop_patience', 0)])
            print(f'[CSV] saved results summary -> {csv_path}')
        except Exception as e:
            print(f'[CSV] save failed: {e}')
