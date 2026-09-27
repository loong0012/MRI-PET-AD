# 项目网络结构图（docs/figures）

由 `scripts/plot_network_diagrams.py` 生成，素材全部取自仓库真实代码，未做任何臆造。

## 图清单

| 文件 | 内容 | 主要代码依据 |
| --- | --- | --- |
| `01_model_architecture.png` | **TransMF 模型网络结构**（主图）：MRI/PET 双分支 sNet 3D-CNN → token 化 → CrossTransformer_MOD_AVG ×3 跨模态注意力 → GAP+GMP 池化 → 分类头；并行梯度反转对抗域对齐分支与双损失 | `models/mymodel.py`（`model_ad`）、`models/networks.py`（`sNet`、`CrossTransformer_MOD_AVG`、`Attention`、`Transformer`）、`models/gradient_reversal/functional.py`、`options/option.py` |
| `02_ensemble_pipeline.png` | **快照集成推理链路**：影像输入 → MONAI 预处理 → N 个快照权重逐个前向 → 概率平均 → 温度校准与置信度量化 → CN/AD 决策 → SSE 流式回传；含 LRU 缓存、热重载、降级回退等旁路机制 | `ad-screen-backend/services/model_inference.py`、`ensemble_inference.py` |
| `03_system_architecture.png` | **平台整体架构**：用户角色 → 前端表现层 → 通信层 → 后端服务层 → AI 推理层 → 数据与存储层 → 运维监控层 | `README.md`、`ad-screen-backend/main.py`、`ad-screen-backend/routers/`、`prometheus.yml`、`docker-compose.yml` |
| `04_training_pipeline.png` | **K 折对抗训练流程**：数据与标签 → 队列分层 5 折 → 类别平衡采样与增强 → 前向 → 双损失 → AdamW/EMA → 早停与快照 → 每折评估与集成交付 | `kfold_train_adversarial.py`、`options/option.py` |

每个 PNG 都配套一份同名 `.mmd`（Mermaid 源码），可直接改文字后重新渲染，
适合贴进 Markdown / 论文 / 答辩 PPT。

## 期刊投稿版（灰度，`journal/`）

按《中文核心论文通用模板 · 06_中文核心图表公式细则》绘制，画法参照示例图 9
「卷积神经网络结构示意图」：双支路 3D 卷积切片 + 虚线分组 + 底部图例。

| 文件 | 尺寸 | 说明 |
| --- | --- | --- |
| `journal/fig1_transmf_architecture.{png,pdf,svg}` | 170 × 120 mm | 含图题与图注，可直接整图插入 |
| `journal/fig1_transmf_architecture_nocaption.{png,pdf,svg}` | 170 × 104 mm | 不含图题，供期刊自行排版图题时使用（与上版内容比例完全一致） |

规范落实情况：

- **灰度**：全图纯灰度（程序校验 RGB 三通道偏差 = 0），无彩色、无阴影、无渐变
- **字体**：中文宋体 SimSun，英文与数字 Times New Roman（图题用黑体）
- **线条**：细线，切片边线 0.5 pt、分组框 0.7 pt、箭头 0.7 pt
- **字号**：图题 8 pt、分组标题 6.8 pt、模块名与参数 6 pt（打印后均可辨识）
- **文字**：只保留模块名、数据流方向与关键参数（通道数、特征图尺寸），无长句
- **尺寸**：170 mm 通栏，600 dpi PNG + 矢量 PDF/SVG

重新生成：

```bash
python scripts/plot_journal_fig1.py
```

脚本内置自检：字形是否缺失（防止中文变成方框）、文字是否越出所属分组框、
文字是否互撞、是否越出画布，生成时会打印结果。


## 关键事实（与本仓库代码一致）

- 模型主类为 `models/mymodel.py` 中的 **`model_ad`**，后端与集成推理脚本均使用它。
- 默认超参：`dim=128`、`depth=3`、`heads=4`、`dim_head=32`、`mlp_dim=512`、`dropout=0.15`、`num_classes=2`。
- 编码器 `sNet` 用 **`InstanceNorm3d`**（非 BatchNorm），以适配 `batch_size=2` 的小批量医学影像场景。
- 融合模块 `CrossTransformer_MOD_AVG` 内部完成交叉注意力 + GAP/GMP 池化，输出 **512-d**（`4 × dim`）cls token。
- 对抗分支的域标签在代码中是 `mri_gt = 1`、`pet_gt = 0`（见 `kfold_train_adversarial.py`）。
- 后端 `EXP_DIRS` 为 5 个实验目录 × 5 折 × 3 个快照 epoch（40/45/50）；
  本工作区实际存在 **75 个** `snapshot_epoch{N}.pt`，代码注释中称“15 模型集成”。

## 重新生成

```bash
python scripts/plot_network_diagrams.py
```

脚本内置三项排版自检，生成时会打印结果：

1. **排版自检** —— 每段文字必须完整落在所属方框内（防止文字溢出方框）；
2. **边界自检** —— 任何文字不得越出画布；
3. **碰撞自检** —— 文字之间不重叠、文字不压在箭头上。

若输出 `[溢出告警]` / `[越界告警]` / `[碰撞告警]`，按提示调整对应方框的坐标或字号即可。
输出为 300 dpi PNG，可直接用于论文排版与答辩投影。
