# 图 1 复现说明与图题图注

## 图题

**图 1　TransMF 多模态融合网络总体架构与关键模块细节**

英文（本图内标注为全英文，图题建议用英文以便国际期刊复用）：

**Fig. 1　Overall architecture and key module details of the TransMF multimodal fusion network**

## 图注

注：(a) 总体架构，含 MRI / PET 双支路卷积编码器、梯度反转对抗域判别支路与分类头；(b) sNet
编码器逐层配置；(c) 跨模态交叉注意力层。图中 Q 取自本模态 token，K、V 取自另一模态 token；
融合层深度为 3，拼接向量维度为 4 × 128 = 512。MRI 与 PET 立方体表面为真实被试
sub-ADNI002S4262 的轴位切片。

## 复现来源

原图：Zhang Y, Sun K, Liu Y, Shen D. *Transformer-Based Multimodal Fusion for Early Diagnosis
of Alzheimer's Disease Using Structural MRI and PET*. IEEE ISBI 2023, pp. 1–5. Fig. 1。

按 `09_T1级工科绘图审美与复现规范.md` 第 5 节"重绘式复现"处理：保留原图的问题对象、
子图结构与图形语言，不逐像素复制。

## 影像来源与处理

| 项 | 值 |
| --- | --- |
| 被试 | `sub-ADNI002S4262`（ADNI，Group = **CN**） |
| 数据源 | `datasets/MRI PET图像/{MRI,PET}/sub-ADNI002S4262.nii` |
| 朝向 | axcodes = RAS，由 affine 判定轴位轴 = 2 |
| 切片 | MRI 轴位第 45 层；PET 轴位第 46 层（各取脑组织像素最多的层） |
| 体素尺寸 | 1.54 × 1.83 × 1.48 mm |

处理步骤（见 `scripts/extract_case_slices.py`）：

1. 按 `nibabel.aff2axcodes` 判定轴位轴，不假设第 2 轴即为 S 方向；
2. 选取脑组织像素数最多的轴位层；
3. **按 NIfTI zooms 做各向同性重采样**——R 方向 1.54 mm/px、A 方向 1.83 mm/px，
   若直接 `resize` 成正方形会造成 **19% 的方向性拉伸**，脑形态会被压扁，故先换算到
   1 mm/px 等比网格再输出；
4. 裁到脑组织外接框，补零成正方形，输出 900 × 900 灰度图；
5. 出图时把正面渲染为**物理正方形**（8.4 × 8.4 mm），数据坐标下宽 4.94 单位、高 6.72 单位，
   因为画布 170 × 125 mm 对应 100 × 100 数据单位，x/y 单位物理长度不同（1.70 / 1.25 mm）。

**该影像为去标识化 ADNI 被试影像，引用与再分发请遵守 ADNI 数据使用条款（ADNI Data
Use Agreement），并在论文中致谢 ADNI。**

## 保留了什么

- **(a)(b)(c) 三面板结构与图号关系**，面板内元素的空间组织与原图一致。
- **全英文标注**，与原图一致。
- **配色**：逐像素采样原图得到，非目测。
  - 面板底色：`(a) #FFF5EF`、`(b) #FFFBEB`、`(c) #EFF1F5`
  - MRI 主色 `#70AD46`，填充 `#A9D18F` / `#BBDBA5`
  - PET 主色 `#4470C5`，填充 `#688FD1` / `#849FD4`
- **图形语言**：所有方框均为"无填充 + 描边"。逐像素采样确认原图编码器框内 81% 的像素是
  面板底色 `#FFF5EF`，**并不存在浅绿/浅蓝填充**——缩略图上的"浅绿底"来自描边在缩放后的
  视觉印象。本图照此实现。
- **(b) 的 11 个算子逐层顺序**：Conv 32 → Maxpool → Conv 32 → Conv 64 → Maxpool →
  Conv 64 → Conv 128 → Maxpool → Conv 256 → Conv 128@1×1×1 → Avgpool。
- **(c) 的交叉注意力连线拓扑**：上块 Q_M → 上注意力，K_M/V_M → 下注意力；
  下块 Q_P → 下注意力，K_P/V_P → 上注意力。
- **(a) 的三段区域划分**：Feature Extraction / Feature Fusion / Classification Head。

## 改动了什么（相对原图）

| 项 | 原图 | 本图 | 依据 |
| --- | --- | --- | --- |
| 编码器标注 | `Conv, 32@3x3x3` | 增加第二行 `IN+LReLU` | `sNet` 每个 Conv3d 后接 `InstanceNorm3d + LeakyReLU` |
| 域判别器 | 仅 `Discriminator` | 增加 `128→128→2`、`domain: MRI=1, PET=0` | `models/mymodel.py` 的 `D`；`kfold_train_adversarial.py` 的 `mri_gt=1 / pet_gt=0` |
| 梯度反转 | `Reverse Gradient` | `Reverse Gradient  revgrad(α=2)` | `models/gradient_reversal/functional.py`，α=2 |
| 对抗损失 | `L_a` | 增加 `weight 0.1` | `options/option.py` 的 `ad_loss_weight=0.1` |
| 融合模块 | 仅 `Multimodal Transformer Encoder` | 增加 `CrossTransformer` / `MOD_AVG` / `×3 layers dim=128 heads=4` | `models/networks.py` 的 `CrossTransformer_MOD_AVG` |
| 分类头 | 仅 `Prediction` | 增加 `Classifier fc_cls` / `512→512→64→2` / `LN+ReLU+Dropout` | `models/mymodel.py` 的 `fc_cls` |
| 面板 (c) 标题 | `Transformer Encoder Layer x l` | `Transformer Encoder Layer × 3` | 本项目 `depth=3`，给出确定值而非符号 |
| 体数据立方体 | 真实被试影像（未注明被试） | 真实被试 `sub-ADNI002S4262`（CN）轴位切片 | 本数据集中该被试 MRI 无颅骨环伪影，脑实质对比最好 |

## 示意与近似内容（不得作为实验依据引用）

- **(c) 的残差支路走线**为版式近似：原图残差线贴框内沿，本图沿各块顶部走线，
  电气拓扑（attention 残差 + FFN 残差）一致。
- **(b) 的流线括号**为版式近似：原图括号的折返路径在低分辨率截图中无法完全辨清，
  本图按"col1 末端 → 下方 → 左侧竖线 → 上方 → 进入 col2 顶部"重绘，表达含义一致。
- **(a) 的拼接向量条**沿用原图的两段式（绿 = MRI，蓝 = PET）。本项目代码的真实拼接顺序为
  `torch.cat([mri_cls_avg, pet_cls_avg, mri_cls_max, pet_cls_max])`，即
  `[MRI_GAP | PET_GAP | MRI_GMP | PET_GMP]`，4 × 128 = 512 维。该细节在正文或图注中说明，
  未在图中展开，以免箭头交叉。
- **(c) 的 K/V 跨模态走线**将 K 与 V 合并为单条总线后进入对侧注意力（原图亦为汇聚走线），
  表示"K、V 均来自另一模态"。
- 体内立方体的顶面与右侧面为**灰阶示意**（表示体积），非真实三维重建。

## 数据性质声明

**本图为网络结构示意图，唯一的真实数据是两张被试切片；不含任何实验数据、性能指标或统计结果。**
图中所有超参数（dim=128、depth=3、heads=4、dim_head=32、mlp_dim=512、α=2、
ad_loss_weight=0.1、label_smooth=0.05）均取自本项目源码，对应位置见上表，未做推测性填写。

## 输出文件

| 文件 | 说明 |
| --- | --- |
| `fig1_transmf_paper.pdf` | 矢量，170 × 125 mm，排版首选；文字字体已内嵌 TrueType 子集；含 2 个位图对象（真实切片） |
| `fig1_transmf_paper.svg` | 矢量，文字已转路径，可在 Illustrator / Inkscape / Visio 二次编辑 |
| `fig1_transmf_paper.png` | **600 dpi**，4015 × 2952 px |
| `assets/mri_face.png` | 真实 MRI 切片，900 × 900 |
| `assets/pet_face.png` | 真实 PET 切片，900 × 900 |
| `draw_transmf_paper_fig1.py` | 可复现出图脚本（项目 `scripts/` 内同名） |

## 重新生成

```bash
python scripts/extract_case_slices.py      # 先抽真实切片（依赖数据集 NIfTI）
python scripts/plot_transmf_paper_fig1.py  # 再出图
```

出图脚本内置自检：字形缺失、文字越出画布、文字互撞，生成时打印结果。
