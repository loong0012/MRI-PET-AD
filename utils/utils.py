import os
import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy as np
from torch.utils.data import SubsetRandomSampler
import itertools

# =============================================================================
#  工具：早停 (EarlyStopping) & 指数滑动平均 (EMA)
# =============================================================================
class EarlyStopping:
    """基于验证 metric 的早停。mode='max' 适合 ACC/AUC；mode='min' 适合 LOSS"""
    def __init__(self, patience: int = 15, min_delta: float = 0.0, mode: str = "max", verbose: bool = True):
        self.patience = patience
        self.min_delta = min_delta
        self.mode = mode
        self.verbose = verbose
        self.counter = 0
        self.best = None
        self.should_stop = False
        self.is_better = (lambda a, b: a - min_delta > b) if mode == "max" else (lambda a, b: a + min_delta < b)

    def step(self, metric) -> bool:
        if self.best is None or self.is_better(metric, self.best):
            self.best = float(metric)
            self.counter = 0
            return True
        self.counter += 1
        if self.verbose and self.counter > 0:
            print(f"[EarlyStop] no improvement for {self.counter}/{self.patience} epochs (best={self.best:.4f})")
        if self.counter >= self.patience:
            self.should_stop = True
        return False


class EMA:
    """Model exponential moving average。训练中 shadow weight 平滑，测试时加载 shadow 往往更稳。"""
    def __init__(self, model: nn.Module, decay: float = 0.9998, device=None):
        self.decay = decay
        self.model = model
        self.device = device
        self.shadow = {name: p.detach().clone().to(device=device) for name, p in model.named_parameters()}

    @torch.no_grad()
    def update(self):
        for name, p in self.model.named_parameters():
            if not p.requires_grad:
                continue
            self.shadow[name].mul_(self.decay).add_(p.detach().to(self.shadow[name].device), alpha=1 - self.decay)

    @torch.no_grad()
    def apply_shadow(self):
        """把 shadow 写入模型（用于评估）。返回原 state_dict 以便 restore。"""
        backup = {name: p.detach().clone() for name, p in self.model.named_parameters()}
        for name, p in self.model.named_parameters():
            if name in self.shadow:
                p.data.copy_(self.shadow[name].data.to(p.device))
        return backup

    @torch.no_grad()
    def restore(self, backup):
        for name, p in self.model.named_parameters():
            if name in backup:
                p.data.copy_(backup[name].data.to(p.device))


# =============================================================================
#  损失函数：Label Smoothing CE + Focal Loss（类别不平衡时很关键）
# =============================================================================
class LabelSmoothCrossEntropy(nn.Module):
    def __init__(self, smoothing: float = 0.05, weight=None):
        super().__init__()
        self.smoothing = smoothing
        self.weight = weight

    def forward(self, logits: torch.Tensor, target: torch.Tensor) -> torch.Tensor:
        n_classes = logits.shape[-1]
        log_probs = F.log_softmax(logits, dim=-1)
        with torch.no_grad():
            true_dist = torch.zeros_like(log_probs)
            true_dist.fill_(self.smoothing / (n_classes - 1))
            true_dist.scatter_(1, target.unsqueeze(1), 1.0 - self.smoothing)
        if self.weight is not None:
            w = self.weight.to(target.device)
            sample_w = w[target]  # (B,)
            loss = (-true_dist * log_probs).sum(dim=-1) * sample_w
            return loss.mean()
        return torch.mean(torch.sum(-true_dist * log_probs, dim=-1))


class FocalLoss(nn.Module):
    def __init__(self, alpha=None, gamma: float = 2.0, smoothing: float = 0.0, reduction: str = "mean"):
        super().__init__()
        self.alpha = alpha
        self.gamma = gamma
        self.smoothing = smoothing
        self.reduction = reduction

    def forward(self, logits: torch.Tensor, target: torch.Tensor) -> torch.Tensor:
        n_classes = logits.shape[-1]
        probs = F.softmax(logits, dim=-1)
        # Label smoothing on true distribution
        with torch.no_grad():
            tgt = torch.zeros_like(probs)
            tgt.fill_(self.smoothing / (n_classes - 1))
            tgt.scatter_(1, target.unsqueeze(1), 1.0 - self.smoothing)
        pt = (probs * tgt).sum(dim=-1)  # 对应正确类的置信度
        log_pt = pt.clamp(min=1e-9).log()
        FL = -((1 - pt) ** self.gamma) * log_pt
        if self.alpha is not None:
            w = self.alpha.to(target.device)
            w = w[target]
            FL = FL * w
        if self.reduction == "mean":
            return FL.mean()
        if self.reduction == "sum":
            return FL.sum()
        return FL


def build_loss(opt, class_weights=None):
    """根据 opt 统一构建分类损失。
    class_weights: 1D tensor of shape (num_classes,)，通过 get_dataset_weights 计算得到后归一化传入"""
    if opt.loss_type == 'focal':
        alpha = None
        if class_weights is not None:
            # Focal 的 alpha 用作类级别平衡权重
            alpha = class_weights.float()
            alpha = alpha / alpha.sum() * 2
        return FocalLoss(alpha=alpha, gamma=float(getattr(opt, 'focal_gamma', 2.0)),
                         smoothing=float(getattr(opt, 'label_smooth', 0.05)))
    # 默认使用 Label Smoothing CrossEntropy，效果通常比 vanilla CE 稳定
    weight = None
    if class_weights is not None and getattr(opt, 'use_class_weight', True):
        weight = class_weights.float().to(torch.device('cuda:0' if torch.cuda.is_available() else 'cpu'))
    return LabelSmoothCrossEntropy(smoothing=float(getattr(opt, 'label_smooth', 0.05)), weight=weight)


# =============================================================================
#  Optimizer + LR Scheduler（AdamW + CosineAnnealingWarmRestarts / CosineAnnealingLR）
# =============================================================================
def getOptimizer(net_para, opt):
    lr = float(opt.lr)
    wd = float(getattr(opt, 'weight_decay', 1e-4))  # 默认 AdamW 需要稍微加一点 weight decay
    betas = (float(getattr(opt, 'adam_beta1', 0.9)), float(getattr(opt, 'adam_beta2', 0.999)))

    if opt.optimizer == 'SGD':
        optimizer = torch.optim.SGD(net_para, lr=lr, momentum=0.9, weight_decay=wd, nesterov=True)
        lr_schedualer = torch.optim.lr_scheduler.CosineAnnealingLR(
            optimizer, T_max=max(1, int(getattr(opt, 'stage1_epochs', 20)) + int(getattr(opt, 'stage2_epochs', 20))))
        return optimizer, lr_schedualer

    # 默认 AdamW（比 Adam 更稳，尤其是医学图像）
    optim_cls = torch.optim.AdamW if (opt.optimizer == 'AdamW' or True) else torch.optim.Adam
    if opt.optimizer == 'Adam':
        optim_cls = torch.optim.Adam
    optimizer = optim_cls(net_para, lr=lr, weight_decay=wd, betas=betas)

    total_epochs = max(1, int(getattr(opt, 'stage1_epochs', 20)) + int(getattr(opt, 'stage2_epochs', 20)))
    sch_name = str(getattr(opt, 'lr_policy', 'cosine'))
    if sch_name == 'warmcos':
        # Warmup + CosineAnnealingWarmRestarts
        first_restart = max(4, total_epochs // 3)
        lr_schedualer = torch.optim.lr_scheduler.CosineAnnealingWarmRestarts(
            optimizer, T_0=first_restart, T_mult=2, eta_min=lr * 1e-3)
    elif sch_name == 'multistep':
        lr_schedualer = torch.optim.lr_scheduler.MultiStepLR(optimizer, milestones=[25, 36], gamma=0.1)
    else:  # cosine（推荐默认）
        lr_schedualer = torch.optim.lr_scheduler.CosineAnnealingLR(
            optimizer, T_max=total_epochs, eta_min=lr * 1e-3)
    return optimizer, lr_schedualer


# =============================================================================
#  指标：NaN safe 的混淆矩阵（解决除零）
# =============================================================================
def cal_confusion_metrics(c_matrix):
    """输入 shape (2, 2) 的 confusion matrix（argmax 后计算）：
       rows=真实 (0 先于 1)  cols=预测
       一般 Ignite ConfusionMatrix(num_classes=2, output_transform) 返回的矩阵:
       matrix[y_true][y_pred] += 1
       即 matrix[1,1]=TP, matrix[1,0]=FN, matrix[0,1]=FP, matrix[0,0]=TN"""
    if hasattr(c_matrix, 'cpu'):
        c_matrix = c_matrix.detach().cpu().numpy()
    c_matrix = np.asarray(c_matrix, dtype=np.float64)
    if c_matrix.shape != (2, 2):
        return float('nan'), float('nan'), float('nan')

    TP, FN = float(c_matrix[1, 1]), float(c_matrix[1, 0])
    FP, TN = float(c_matrix[0, 1]), float(c_matrix[0, 0])

    def _safe_div(num, den):
        if den <= 0 or not np.isfinite(den):
            return 0.0
        r = num / den
        return r if np.isfinite(r) else 0.0

    precision = _safe_div(TP, TP + FP)
    recall = _safe_div(TP, TP + FN)      # sensitivity
    specificity = _safe_div(TN, FP + TN)
    f_den = precision + recall
    f1 = _safe_div(2 * precision * recall, f_den) if f_den > 0 else 0.0
    sen = recall
    spe = specificity
    # balanced accuracy 也可返回供参考
    return sen, spe, f1


def balanced_accuracy_from_confusion(c_matrix):
    if hasattr(c_matrix, 'cpu'):
        c_matrix = c_matrix.detach().cpu().numpy()
    c_matrix = np.asarray(c_matrix, dtype=np.float64)
    if c_matrix.shape != (2, 2):
        return float('nan')
    TP, FN = float(c_matrix[1, 1]), float(c_matrix[1, 0])
    FP, TN = float(c_matrix[0, 1]), float(c_matrix[0, 0])
    sen = TP / (TP + FN) if (TP + FN) > 0 else 0.0
    spe = TN / (FP + TN) if (FP + TN) > 0 else 0.0
    bacc = 0.5 * (sen + spe)
    return float(bacc) if np.isfinite(bacc) else 0.0


# =============================================================================
#  Data split helpers（分层划分 / 分层 KFold：解决类别不平衡下 split 偏差）
# =============================================================================
def stratified_split_train_val(train_idx, labels_train, val_ratio=0.2, seed=42):
    """train_idx: array of indices；labels_train: list/array of labels (对齐 train_idx)
    返回 train_subidx, val_subidx（相对于 train_idx 的位置索引，调用方再转原始索引）"""
    from sklearn.model_selection import train_test_split
    t_idx, v_idx = train_test_split(
        list(range(len(train_idx))),
        test_size=val_ratio,
        stratify=labels_train,
        random_state=seed,
    )
    return t_idx, v_idx


def stratified_kfold_indices(y, n_splits=5, seed=42):
    """返回 (train_indices, test_indices) 的 generator，类似 KFold.split，但按标签分层"""
    from sklearn.model_selection import StratifiedKFold
    skf = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=seed)
    indices = np.arange(len(y))
    for tr, te in skf.split(indices, y):
        yield tr, te


def _cohort_code(subject_id):
    """从 Subject ID 提取队列码：S 码 <2000 = ADNI1(1.5T)，>=2000 = ADNI2+(3T)"""
    import re
    m = re.match(r'sub-ADNI\d{3}S(\d+)', str(subject_id))
    if m:
        code = int(m.group(1))
        return 0 if code < 2000 else 1  # 0=ADNI1, 1=ADNI2+
    return 1  # 未知归入多数类


def stratified_kfold_by_cohort(data_dict, n_splits=5, seed=42):
    """队列+标签双重分层 KFold：按 (cohort, label) 组合分层，保证每 fold 队列×标签比例一致。

    data_dict: ADNI.data_dict 列表，每个元素需含 'Subject' 和 'label'。
    返回 (train_indices, test_indices) generator。
    """
    from sklearn.model_selection import StratifiedKFold
    combined = []
    for d in data_dict:
        c = _cohort_code(d['Subject'])
        l = int(d['label'])
        combined.append(c * 2 + l)  # 4 组：ADNI1_CN=0, ADNI1_AD=1, ADNI2_CN=2, ADNI2_AD=3
    combined = np.array(combined)
    skf = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=seed)
    indices = np.arange(len(data_dict))
    for tr, te in skf.split(indices, combined):
        yield tr, te


# =============================================================================
#  Dataset 权重（修正原函数 train_idx 未使用、dataset.data 访问名错误问题）
# =============================================================================
def get_dataset_weights(dataset, train_idx):
    """计算类别平衡权重：返回 1/num_per_class 归一化的 FloatTensor([w0, w1])"""
    # dataset 可能是 monai.data.Dataset（有 .data）或 list
    if isinstance(dataset, list):
        data = dataset
    elif hasattr(dataset, 'data'):
        data = dataset.data
    else:
        data = list(dataset)  # 兜底

    count_0 = 0
    count_1 = 0
    # 如果 train_idx 给定了（通常是 numpy array），按 train_idx 指定 subset 统计
    if train_idx is not None and len(train_idx) > 0 and len(train_idx) <= len(data):
        enum = [data[int(i)] for i in train_idx]
    else:
        enum = list(data)
    for item in enum:
        lab = int(item['label'])
        if lab == 0:
            count_0 += 1
        elif lab == 1:
            count_1 += 1
    total = count_0 + count_1
    if count_0 == 0:
        count_0 = 1
    if count_1 == 0:
        count_1 = 1
    # 经典反频率归一化（使得两个类平均权重 = 1）
    w0 = (total / 2.0) / count_0
    w1 = (total / 2.0) / count_1
    weights = torch.FloatTensor([w0, w1])
    print(f'negative class (0) has {count_0} samples -> weight {w0:.3f}')
    print(f'positive class (1) has {count_1} samples -> weight {w1:.3f}')
    return weights


# =============================================================================
#  Dir / Logger
# =============================================================================
def mkdir(path):
    if not os.path.exists(path):
        os.makedirs(path)


def mkdirs(paths):
    if isinstance(paths, list) and not isinstance(paths, str):
        for path in paths:
            mkdir(path)
    else:
        mkdir(paths)


def dataset_random_split(dataset, collate_fn, val_radio=.2, batch_size=1):
    dataset_size = len(dataset)
    indices = list(range(dataset_size))
    split = int(np.floor(val_radio * dataset_size))
    np.random.shuffle(indices)
    train_indices, val_indices = indices[split:], indices[:split]
    train_sampler = SubsetRandomSampler(train_indices)
    valid_sampler = SubsetRandomSampler(val_indices)
    train_loader = torch.utils.data.DataLoader(dataset, batch_size=batch_size, sampler=train_sampler,
                                               collate_fn=collate_fn)
    validation_loader = torch.utils.data.DataLoader(dataset, batch_size=batch_size, sampler=valid_sampler,
                                                    collate_fn=collate_fn)
    return train_loader, validation_loader, len(train_indices), len(val_indices)


class Logger():
    def __init__(self, log_dir):
        self.log_name = os.path.join(log_dir, 'log.txt')
        with open(self.log_name, "a") as log_file:
            log_file.write(f'================ {self.log_name} ================\n')

    def print_message(self, msg):
        print(msg)
        with open(self.log_name, 'a', encoding='utf-8') as log_file:
            log_file.write('%s\n' % msg)

    def print_message_nocli(self, msg):
        with open(self.log_name, 'a', encoding='utf-8') as log_file:
            log_file.write('%s\n' % msg)

