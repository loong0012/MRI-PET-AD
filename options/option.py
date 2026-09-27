import argparse
import os
from utils.utils import mkdirs


class Option:
    """This class defines options used during both training and CNN_PET_ADCN time. It also implements several helper
    functions such as parsing, printing, and saving the options. It also gathers additional options defined in
    <modify_commandline_options> functions in both dataset class and model class.
    """

    def __init__(self):
        """Reset the class; indicates the class hasn't been initialized"""
        self.parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
        self.opt = None

    def initialize(self, parser):
        """Define the common options that are used in both training and CNN_PET_ADCN."""
        # ===================== 基础 =====================
        parser.add_argument('--name', type=str, default='ADCN_TransMF',
                            help='name of the experiment. It decides where to store samples and models')
        parser.add_argument('--dataroot', type=str, default='./datasets/MRI PET图像')
        parser.add_argument('--aug', type=str, default='True')
        parser.add_argument('--mode', type=str, default='train')
        parser.add_argument('--dataset', type=str, default='ADNI')
        parser.add_argument('--model', type=str, default='Transformer', choices=['Transformer', 'CNN'],
                            help='主力模型建议 Transformer（CrossTransformer+对抗域对齐）')
        parser.add_argument('--randint', type=str, default='False')
        parser.add_argument('--extra_sample', type=str, default='False',
                            help='pMCIsMCI任务是否额外加入ADCN数据训练（原功能）')
        parser.add_argument('--checkpoints_dir', type=str, default='./checkpoints', help='models are saved here')
        parser.add_argument('--task', type=str, default='ADCN', choices=['ADCN', 'pMCIsMCI', 'MCICN', 'ALL4'])
        parser.add_argument('--batch_size', type=int, default=2, help='input batch size')

        # ===================== 优化 =====================
        parser.add_argument('--lr', type=float, default=5e-4, help='初始学习率，AdamW下推荐 3e-4 ~ 5e-4 (z-score 归一化后可适当提高)')
        parser.add_argument('--optimizer', type=str, default='AdamW', choices=['AdamW', 'Adam', 'SGD'],
                            help='推荐使用 AdamW（更适合医学图像 + weight_decay）')
        parser.add_argument('--adam_beta1', type=float, default=0.9)
        parser.add_argument('--adam_beta2', type=float, default=0.999)
        parser.add_argument('--lr_policy', type=str, default='cosine',
                            choices=['cosine', 'warmcos', 'multistep'],
                            help='学习率调度：cosine=余弦退火，warmcos=CosineAnnealingWarmRestarts，multistep=原版')
        parser.add_argument('--weight_decay', type=float, default=1e-4,
                            help='AdamW 需要，1e-4 ~ 5e-4 对 3D 医学影像通常更稳')
        parser.add_argument('--stage1_epochs', type=int, default=25, help='总训练 epoch 数 = stage1 + stage2')
        parser.add_argument('--stage2_epochs', type=int, default=25)

        # ===================== 损失 & 类平衡 =====================
        parser.add_argument('--loss_type', type=str, default='lsce', choices=['lsce', 'focal', 'ce'],
                            help='lsce=标签平滑CE(默认，稳), focal=Focal Loss(不平衡严重时用), ce=原版CE')
        parser.add_argument('--use_class_weight', type=str, default='True',
                            help='是否启用类别平衡权重（逆频率）。默认 True。')
        parser.add_argument('--use_weighted_sampler', type=str, default='True',
                            help='启用 WeightedRandomSampler：按类权重过采样少数类，'
                                 '让每个 batch 类别大致均衡。这是突破"全预测多数类"最有效的方法')
        parser.add_argument('--label_smooth', type=float, default=0.05,
                            help='Label Smoothing 系数，0.03~0.1 常见；0 关闭')
        parser.add_argument('--focal_gamma', type=float, default=2.0,
                            help='Focal Loss gamma，2 最常用；难样本多时可设 3')

        # ===================== 对抗域对齐分支 =====================
        parser.add_argument('--use_adversarial', type=str, default='True',
                            help='是否启用对抗域对齐分支(MRI vs PET判别器)。'
                                 '当 ad_loss 始终≈ln2(=0.693)说明对抗分支没学到，建议关 False 让主任务先收敛')
        parser.add_argument('--ad_loss_weight', type=float, default=0.1,
                            help='对抗损失 ad_loss 权重，默认 0.1（远小于 ce_loss=1.0）。'
                                 '过大易干扰主分类；0 等价于关闭对抗分支')

        # ===================== 训练稳定性/实用性 =====================
        parser.add_argument('--early_stop_patience', type=int, default=15,
                            help='基于验证集 AUC 的早停耐心轮（<=0 关闭）')
        parser.add_argument('--save_score', type=str, default='auc', choices=['auc', 'acc', 'loss'],
                            help='保存最佳权重与早停的主指标。auc 在医学二分类里通常比 acc 更靠谱')
        parser.add_argument('--use_ema', type=str, default='True',
                            help='使用 EMA 指数滑动平均，验证/测试更稳；通常 +0.5~1.5 AUC/ACC')
        parser.add_argument('--ema_decay', type=float, default=0.9998)
        parser.add_argument('--non_blocking', type=str, default='True',
                            help='.to(device, non_blocking=True) + pin_memory 加速数据搬运')
        parser.add_argument('--num_workers', type=int, default=2,
                            help='DataLoader workers，Windows 下 0~4 之间；0 最稳但略慢')
        parser.add_argument('--pin_memory', type=str, default='True')
        parser.add_argument('--persistent_workers', type=str, default='False',
                            help='num_workers>0 时是否持久化 worker；长 epoch 时可提速')
        parser.add_argument('--num_fold', type=int, default=5, help='KFold 折数')
        parser.add_argument('--save_csv_summary', type=str, default='True',
                            help='训练结束后在 checkpoints/name/ 下生成 results_summary.csv')
        parser.add_argument('--grad_clip_norm', type=float, default=5.0,
                            help='梯度裁剪范数（<=0 关闭），防止偶尔 3D 卷积梯度爆炸')

        # ===================== 模型 =====================
        parser.add_argument('--dim', type=int, default=128)
        parser.add_argument('--trans_enc_depth', type=int, default=3)
        parser.add_argument('--cross_attn_depth', type=int, default=3)
        parser.add_argument('--dropout', type=float, default=0.15,
                            help='0 容易过拟合；医学影像 0.1~0.2 通常合适')
        parser.add_argument('--init_type', type=str, default='kaiming',
                            choices=['normal', 'xavier', 'kaiming', 'orthogonal'],
                            help='network initialization（医学图像推荐 kaiming）')

        # ===================== 预训练 / 迁移学习（方案B：4分类预训练 -> ADCN 2分类微调）=====================
        parser.add_argument('--pretrained_path', type=str, default='',
                            help='预训练权重路径，空字符串表示从零训练不加载预训练')
        parser.add_argument('--pretrained_strict', type=str, default='False',
                            help='加载预训练权重时是否严格匹配 keys（默认 False，只加载特征提取器）')
        parser.add_argument('--pretrain_task', type=str, default='ALL4',
                            help='预训练任务类型，默认 ALL4（4分类）')
        parser.add_argument('--num_classes', type=int, default=2,
                            help='分类数：ALL4=4，ADCN/pMCIsMCI/MCICN=2')

        # ===================== 方案2：train+val 合并重训 + 固定 epoch + 快照集成 =====================
        parser.add_argument('--merge_train_val', type=str, default='False',
                            help='True 时把 val 并入 train（数据量 +20%），无独立验证集')
        parser.add_argument('--fixed_epochs', type=int, default=0,
                            help='>0 时训练固定 epoch 数，禁用早停与最佳模型选择，结束用 EMA 权重做 test')
        parser.add_argument('--snapshot_epochs', type=str, default='',
                            help='逗号分隔的快照保存 epoch，如 "40,45,50"，用于快照集成推理')
        parser.add_argument('--start_fold', type=int, default=0,
                            help='从第几个 fold 开始训练（之前的 fold 权重保留，用于断点续训）')

        # ===================== 方案 H：Mixup 数据增强 =====================
        parser.add_argument('--use_mixup', type=str, default='False',
                            help='启用 Mixup：batch 内样本插值 x_mix=λ·x_i+(1-λ)·x_j，'
                                 'loss 用双标签加权。缓解过拟合 + 抑制扫描仪捷径学习')
        parser.add_argument('--mixup_alpha', type=float, default=0.4,
                            help='Mixup Beta 分布参数 α，0.2~0.4 常见；越大混合越激进')

        # ===================== 方案 K：队列分层 CV =====================
        parser.add_argument('--cohort_split', type=str, default='False',
                            help='队列+标签双重分层 KFold：按 (ADNI1/ADNI2+, label) 4 组分层，'
                                 '保证每 fold 队列构成一致，降低 fold 间方差')

        # ===================== 方案 L：Gamma 非线性增强 =====================
        parser.add_argument('--use_gamma_aug', type=str, default='False',
                            help='启用随机 Gamma 校正：x->x^gamma，非线性对比度增强，'
                                 '模拟不同扫描仪对比度差异（IN3d 无法完全吸收）')
        parser.add_argument('--gamma_range', type=str, default='0.8,1.2',
                            help='Gamma 范围，逗号分隔，如 0.8,1.2')
        parser.add_argument('--gamma_prob', type=float, default=0.3,
                            help='每个样本应用 Gamma 的概率')
        return parser

    def print_options(self, opt):
        """Print and save options
        It will print both current options and default values(if different).
        It will save options into a text file / [checkpoints_dir] / opt.txt
        """
        message = ''
        message += '----------------- Options ---------------\n'
        for k, v in sorted(vars(opt).items()):
            comment = ''
            default = self.parser.get_default(k)
            if v != default:
                comment = '\t[default: %s]' % str(default)
            message += '{:>25}: {:<30}{}\n'.format(str(k), str(v), comment)
        print(message)

        # save to the disk
        expr_dir = os.path.join(opt.checkpoints_dir, opt.name)
        mkdirs(expr_dir)
        file_name = os.path.join(expr_dir, 'opt.txt')
        with open(file_name, 'wt') as opt_file:
            opt_file.write(message)
            opt_file.write('\n')
        print(f'Create opt file opt.txt')

    def parse(self):
        """Parse our options, create checkpoints directory suffix, and set up gpu device."""
        self.parser = self.initialize(self.parser)
        self.opt = self.parser.parse_args()
        self.print_options(self.opt)
        return self.opt
