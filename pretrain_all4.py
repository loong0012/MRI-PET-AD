"""
方案B 预训练脚本：用全部 1041 样本做 4 分类（CN/AD/sMCI/pMCI）预训练。
训练完成后产出 checkpoints/<name>/best_pretrain.pt，供 kfold_train_adversarial.py 微调加载。

用法：
    .venv\\Scripts\\python.exe pretrain_all4.py --name ALL4_PRETRAIN --dataroot "./datasets/MRI PET图像" ...
"""
import os
os.environ['KMP_DUPLICATE_LIB_OK'] = 'TRUE'

import random
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

_SEED = 20240823
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
from models.mymodel import model_ad
from options.option import Option
from sklearn.model_selection import train_test_split
from torch.utils.data import DataLoader, WeightedRandomSampler
from monai.data import Dataset
import ignite
from ignite.metrics import Accuracy, Loss, Average
from ignite.engine import Engine, Events
try:
    from ignite.contrib.handlers import ProgressBar
except ModuleNotFoundError as _e:
    import warnings
    warnings.warn(f"ProgressBar 不可用（{_e}）。继续训练，但不会显示 tqdm 进度条。")
    class _NoopProgressBar:
        def __init__(self, *a, **kw): pass
        def attach(self, *a, **kw): return None
        def __call__(self, *a, **kw): return None
    ProgressBar = _NoopProgressBar
from ignite.handlers import LRScheduler
from utils.utils import getOptimizer, mkdirs, EarlyStopping, EMA, Logger


def _str2bool(s) -> bool:
    if isinstance(s, bool):
        return s
    return str(s).strip().lower() in ('1', 'true', 'yes', 'y', 't')


def get_multiclass_weights(data, num_classes=4):
    """计算 num_classes 类的逆频率权重，返回 FloatTensor，平均权重=1"""
    counts = [0] * num_classes
    for item in data:
        lab = int(item['label'])
        if 0 <= lab < num_classes:
            counts[lab] += 1
    total = sum(counts)
    weights = []
    for c in counts:
        c = max(c, 1)
        weights.append((total / float(num_classes)) / c)
    for i, c in enumerate(counts):
        print(f'[Pretrain] class {i}: {c} samples -> weight {weights[i]:.3f}')
    return torch.FloatTensor(weights)


if __name__ == '__main__':
    device = torch.device('cuda:0' if torch.cuda.is_available() else 'cpu')
    opt = Option().parse()
    save_dir = os.path.join('./checkpoints', opt.name)
    mkdirs(save_dir)
    logger = Logger(save_dir)

    # ===== 强制 ALL4 任务 + 4 分类（预训练专用）=====
    NUM_CLASSES = 4
    opt.task = 'ALL4'
    opt.num_classes = NUM_CLASSES
    logger.print_message(f'[Pretrain] task=ALL4, num_classes={NUM_CLASSES}')

    # load ALL4 dataset（全部 1041 样本）
    ADNI_data = ADNI(dataroot=opt.dataroot, label_filename='ADNI.csv', task='ALL4').data_dict
    train_transforms, val_transforms = ADNI_transform(opt.aug)
    logger.print_message(f'[Pretrain] ALL4 total samples: {len(ADNI_data)}')

    use_ema = _str2bool(getattr(opt, 'use_ema', 'True'))
    use_non_blocking = _str2bool(getattr(opt, 'non_blocking', 'True')) and _str2bool(getattr(opt, 'pin_memory', 'True'))
    pin_mem = _str2bool(getattr(opt, 'pin_memory', 'True'))
    persist_wk = _str2bool(getattr(opt, 'persistent_workers', 'False'))
    num_workers = int(getattr(opt, 'num_workers', 0))
    grad_clip = float(getattr(opt, 'grad_clip_norm', 0.0))
    use_weighted_sampler = _str2bool(getattr(opt, 'use_weighted_sampler', 'True'))

    seed = 20240823
    logger.print_message(f'[Pretrain] random seed = {seed}')

    # ===== 80/20 分层切分（不分 fold）=====
    all_labels = [int(d['label']) for d in ADNI_data]
    indices = list(range(len(ADNI_data)))
    train_idx, val_idx = train_test_split(
        indices, test_size=0.2, stratify=all_labels, random_state=seed)
    train_data = [ADNI_data[i] for i in train_idx]
    val_data = [ADNI_data[i] for i in val_idx]
    logger.print_message(f'[Pretrain] Train: {len(train_data)}  Val: {len(val_data)}')

    train_dataset = Dataset(data=train_data, transform=train_transforms)
    val_dataset = Dataset(data=val_data, transform=val_transforms)

    # 4 类逆频率权重
    weights = get_multiclass_weights(train_data, num_classes=NUM_CLASSES)

    # DataLoader
    loader_kwargs = dict(
        batch_size=opt.batch_size,
        num_workers=num_workers,
        pin_memory=pin_mem,
        persistent_workers=(persist_wk and num_workers > 0),
    )
    if use_weighted_sampler:
        sample_w = [float(weights[int(item['label'])]) for item in train_data]
        train_sampler = WeightedRandomSampler(
            weights=sample_w, num_samples=len(train_data), replacement=True)
        train_loader = DataLoader(train_dataset, sampler=train_sampler,
                                  drop_last=True, **loader_kwargs)
        logger.print_message('[Pretrain] WeightedRandomSampler enabled')
    else:
        train_loader = DataLoader(train_dataset, shuffle=True, drop_last=True, **loader_kwargs)
    val_loader = DataLoader(val_dataset, shuffle=False, drop_last=False, **loader_kwargs)

    # ===== model (4 分类) =====
    net_model = model_ad(dim=opt.dim, depth=opt.trans_enc_depth, heads=4,
                         dim_head=opt.dim // 4, mlp_dim=opt.dim * 4, dropout=opt.dropout,
                         num_classes=NUM_CLASSES).to(device)
    optimizer, lr_scheduler = getOptimizer(net_model.parameters(), opt)

    # 普通的 CrossEntropyLoss（4 分类，带类别权重）
    criterion = torch.nn.CrossEntropyLoss(weight=weights.float().to(device))
    adv_criterion = torch.nn.CrossEntropyLoss()

    # 对抗分支开关
    use_adversarial = _str2bool(getattr(opt, 'use_adversarial', 'True'))
    ad_loss_weight = float(getattr(opt, 'ad_loss_weight', 0.1))
    if not use_adversarial or ad_loss_weight <= 0.0:
        use_adversarial = False
        ad_loss_weight = 0.0
    logger.print_message(f'[Pretrain] Adversarial enabled={use_adversarial}, weight={ad_loss_weight}')

    # EMA
    ema = None
    if use_ema:
        ema_decay = float(getattr(opt, 'ema_decay', 0.9998))
        ema = EMA(net_model, decay=ema_decay, device=device)
        logger.print_message(f'[Pretrain] EMA enabled with decay={ema_decay}')

    # EarlyStopping（按 accuracy，max 模式）
    esp_patience = int(getattr(opt, 'early_stop_patience', 15))
    early_stopper = None
    if esp_patience > 0:
        early_stopper = EarlyStopping(patience=esp_patience, mode='max', verbose=True)
        logger.print_message(f'[Pretrain] EarlyStop patience={esp_patience}, score=acc, mode=max')

    best_state_dict = None
    best_acc = None

    # ===== train step =====
    def train_step(engine, batch):
        output_dic = {}
        net_model.train()
        MRI = batch['MRI'].to(device, non_blocking=use_non_blocking)
        PET = batch['PET'].to(device, non_blocking=use_non_blocking)
        label = batch['label'].to(device, non_blocking=use_non_blocking)
        output_dic['label'] = label

        optimizer.zero_grad(set_to_none=True)
        output_logits, D_MRI_logits, D_PET_logits = net_model(MRI, PET)
        output_dic['logits'] = output_logits
        output_dic['D_MRI_logits'] = D_MRI_logits
        output_dic['D_PET_logits'] = D_PET_logits

        ce_loss = criterion(output_logits, label)
        mri_gt = torch.ones([D_MRI_logits.shape[0]], dtype=torch.int64).to(MRI.device)
        pet_gt = torch.zeros([D_PET_logits.shape[0]], dtype=torch.int64).to(PET.device)
        output_dic['D_MRI_label'] = mri_gt
        output_dic['D_PET_label'] = pet_gt

        if use_adversarial:
            ad_loss_raw = (adv_criterion(D_MRI_logits, mri_gt) + adv_criterion(D_PET_logits, pet_gt)) / 2
            ad_loss = ad_loss_raw * ad_loss_weight
        else:
            ad_loss = torch.zeros((), device=MRI.device)

        output_dic['ce_loss'] = float(ce_loss.detach())
        output_dic['ad_loss'] = float(ad_loss.detach())

        all_loss = ad_loss + ce_loss
        all_loss.backward()

        if grad_clip and grad_clip > 0:
            torch.nn.utils.clip_grad_norm_(net_model.parameters(), max_norm=grad_clip)

        optimizer.step()
        if ema is not None:
            ema.update()
        return output_dic

    trainer = Engine(train_step)
    ProgressBar().attach(trainer)
    trainer.add_event_handler(Events.EPOCH_STARTED, LRScheduler(lr_scheduler))

    # ===== val step =====
    def val_step(engine, batch):
        output_dic = {}
        net_model.eval()
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

    # metrics (4-class accuracy + loss)
    train_metrics = {"accuracy": Accuracy(output_transform=lambda x: [x['logits'], x['label']]),
                     "MRI_accuracy": Accuracy(output_transform=lambda x: [x['D_MRI_logits'], x['D_MRI_label']]),
                     "PET_accuracy": Accuracy(output_transform=lambda x: [x['D_PET_logits'], x['D_PET_label']]),
                     "ce_loss": Average(output_transform=lambda x: x['ce_loss']),
                     "ad_loss": Average(output_transform=lambda x: x['ad_loss'])}
    val_metrics = {"accuracy": Accuracy(output_transform=lambda x: [x['logits'], x['label']]),
                   "loss": Loss(criterion, output_transform=lambda x: [x['logits'], x['label']])}
    for name, metric in train_metrics.items():
        metric.attach(trainer, name)
    for name, metric in val_metrics.items():
        metric.attach(evaluator, name)

    @trainer.on(Events.EPOCH_COMPLETED)
    def log_training_results(trainer):
        metrics = trainer.state.metrics
        logger.print_message('-------------------------------------------------')
        curr_lr = optimizer.param_groups[0]['lr']
        logger.print_message(f'Current learning rate: {curr_lr:.6e}')
        logger.print_message(f"Training Results - Epoch[{trainer.state.epoch}] ")
        logger.print_message(f"ce_loss: {metrics['ce_loss']:.4f} "
                             f"ad_loss: {metrics['ad_loss']:.4f} "
                             f"accuracy: {metrics['accuracy']:.4f} "
                             f"MRIaccuracy: {metrics['MRI_accuracy']:.4f} "
                             f"PETaccuracy: {metrics['PET_accuracy']:.4f} ")

    @trainer.on(Events.EPOCH_COMPLETED)
    def log_validation_results(trainer):
        global best_state_dict, best_acc
        evaluator.run(val_loader)
        metrics = evaluator.state.metrics
        primary = float(metrics['accuracy'])
        logger.print_message(f"Validation Results - Epoch[{trainer.state.epoch}] "
                             f"loss: {metrics['loss']:.4f} accuracy(4cls): {metrics['accuracy']:.4f} "
                             f"[primary=acc: {primary:.4f}]")

        # 保存最佳模型
        better = (best_acc is None) or (primary > best_acc)
        if better:
            best_acc = float(primary)
            if ema is not None:
                backup = ema.apply_shadow()
                try:
                    best_state_dict = deepcopy(net_model.state_dict())
                finally:
                    ema.restore(backup)
            else:
                best_state_dict = deepcopy(net_model.state_dict())
            try:
                best_pt = os.path.join(save_dir, 'best_pretrain.pt')
                torch.save({'net_model': best_state_dict,
                            'epoch': trainer.state.epoch,
                            'best_acc': best_acc,
                            'num_classes': NUM_CLASSES,
                            'task': 'ALL4'}, best_pt)
                logger.print_message(f'  [Best] save best_pretrain.pt @ epoch {trainer.state.epoch}, '
                                     f'acc={best_acc:.4f}')
            except Exception as e:
                logger.print_message(f'  [Best] save ckpt failed: {e}')

        if early_stopper is not None:
            early_stopper.step(primary)
            if early_stopper.should_stop:
                logger.print_message(f'[EarlyStop] Triggered @ epoch {trainer.state.epoch}. '
                                     f'best acc={early_stopper.best:.4f}')
                trainer.terminate()

    total_epochs = int(opt.stage1_epochs) + int(opt.stage2_epochs)
    logger.print_message(f'[Pretrain] Start training, total epochs={total_epochs}')
    trainer.run(train_loader, total_epochs)

    # 最终保存
    if best_state_dict is not None:
        final_pt = os.path.join(save_dir, 'best_pretrain.pt')
        torch.save({'net_model': best_state_dict,
                    'epoch': -1,
                    'best_acc': best_acc,
                    'num_classes': NUM_CLASSES,
                    'task': 'ALL4'}, final_pt)
        logger.print_message(f'[Pretrain] Done. Best val acc={best_acc:.4f}. Saved to {final_pt}')
    else:
        logger.print_message('[Pretrain] Done. No best model saved (best_acc is None).')
