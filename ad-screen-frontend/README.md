# AD-Screen 阿尔茨海默病早期筛查与干预系统（前端）

基于 **MRI + PET 多模态影像融合 AI 模型** 的医疗影像 B/S 端筛查系统前端，
对标联影 / 推想 / 深睿医疗神经影像工作站的交互规范，覆盖临床医生、科研管理员、超级管理员多角色权限，
包含病例管理、多模态阅片、AI 分析、干预方案编辑、报告导出、模型科研配置、系统权限管理全流程。

> **医疗免责声明：本系统所有 AI 结果均为辅助筛查结论，不可替代临床诊断。**

---

## 一、技术栈

| 类别 | 选型 | 说明 |
| --- | --- | --- |
| 框架 | Vue 3.4（Composition API + `<script setup>`） | 全部业务组件使用 TS 严格类型，无 any |
| 构建 | Vite 5 | 开发热更新 + 生产按路由分包 |
| 语言 | TypeScript 5（strict 模式） | `vue-tsc` 全量类型校验 |
| UI 组件库 | Element Plus 2.x | 统一按钮 / 表格 / 弹窗 / 表单风格 |
| 原子样式 | Tailwind CSS 3 | 低饱和度蓝灰医疗主题（`#2F6DA3` 主色） |
| 可视化 | ECharts 5 | 趋势折线 / 环形分布 / 雷达 / 训练曲线，适配医用大屏 |
| 状态管理 | Pinia | 用户会话 + 权限点持久化（localStorage） |
| 路由 | Vue Router 4 | 登录守卫 + 角色权限点过滤菜单与路由 |
| 图标 | @element-plus/icons-vue | 全站统一线性图标 |

仅适配 **PC 端**（医用电脑 / 高清阅片大屏，推荐分辨率 1920×1080 及以上），未做移动端适配。

---

## 二、环境要求

- Node.js ≥ 18.0
- npm ≥ 9（或 pnpm / yarn 均可）
- 浏览器：Chrome ≥ 90 / Edge ≥ 90 / Firefox ≥ 90（医院内网白名单浏览器）

---

## 三、快速启动（开发模式）

```bash
cd ad-screen-frontend
npm install          # 安装依赖（若网络受限可先执行: npm config set registry https://registry.npmmirror.com）
npm run dev          # 启动开发服务器，默认 http://localhost:5173
```

开发模式下默认走 **Mock 数据**（无需后端即可完整演示全部业务流程），
通过根目录 `.env` 文件控制：

```ini
VITE_USE_MOCK=true     # true=纯前端 Mock 演示；false=对接真实后端
```

### 演示账号（Mock 模式）

| 角色 | 账号 | 密码 | 可见菜单 |
| --- | --- | --- | --- |
| 放射科医师 | radiologist | 123456 | 工作台 / 病例库 / 阅片分析 / 报告 |
| 神经内科医师 | neurologist | 123456 | 工作台 / 病例库 / 阅片分析 / AI详情干预 / 报告 |
| 科研管理员 | researcher | 123456 | 工作台 / 病例库 / 模型科研配置 |
| 超级管理员 | admin | 123456 | 全部菜单（含系统权限管理） |

登录页需输入图形验证码（Mock 模式本地生成校验）。

---

## 四、生产构建与部署

```bash
npm run build        # 类型检查（vue-tsc）+ 打包，产物输出至 dist/
```

构建产物为纯静态文件，任选一种部署方式：

### 方式 A：Nginx（推荐，医院内网常用）

```nginx
server {
    listen       80;
    server_name  ad-screen.hospital.local;

    # 前端静态资源
    location / {
        root  /var/www/ad-screen/dist;
        index index.html;
        try_files $uri $uri/ /index.html;   # history 路由回退
    }

    # 后端 API 反向代理（与 vite.config.ts 中 /api 代理一致）
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        client_max_body_size 512m;           # DICOM 影像上传需放大
    }
}
```

### 方式 B：Node 静态服务（临时演示）

```bash
npm install -g serve
serve -s dist -l 8080
```

> 部署后若前端与后端不同源，需在 Nginx 层统一代理 `/api`，
> 或修改 `.env` 中 `VITE_API_BASE` 指向后端完整地址。

---

## 五、目录结构

```
ad-screen-frontend/
├── index.html                     # HTML 入口（医疗字体 / 大屏基准样式）
├── vite.config.ts                 # Vite 配置（@ 别名、/api 代理、大依赖分包）
├── tailwind.config.js             # 医疗蓝灰主题色板 + 字体层级
├── tsconfig.json                  # TS 严格模式配置
├── .env                           # VITE_USE_MOCK 等环境变量
└── src/
    ├── main.ts                    # 应用入口（Element Plus / 路由 / Pinia 注册）
    ├── App.vue
    ├── router/index.ts            # 路由表 + 登录守卫 + 角色权限点过滤
    ├── stores/user.ts             # 用户会话 / 角色权限 Pinia store
    ├── types/                     # 全局 TS 类型（病例 / 分析 / 模型 / 用户 / 日志）
    ├── utils/                     # request / captcha / 影像合成 / 导出 / 格式化
    ├── mock/db.ts                 # Mock 数据库（1041 例规模演示数据 + 推理模拟）
    ├── api/                       # 按域拆分的 API 模块（Mock 与真实请求同构）
    │   ├── auth.ts  case.ts  analysis.ts  model.ts  dashboard.ts  report.ts
    ├── components/                # 全局通用组件
    │   ├── MedicalViewport.vue    # 专业阅片视口（窗宽窗位/缩放/平移/测量/滚动）
    │   ├── ChartBase.vue          # ECharts 封装（自适应 + 统一医疗配色）
    │   ├── ConfirmDialog.vue      # 操作确认弹窗
    │   ├── RiskWarningDialog.vue  # 风险警告弹窗
    │   ├── AiLoadingDialog.vue    # AI 推理进度弹窗（阶段文案 + 进度条）
    │   ├── DisclaimerBar.vue      # 免责提示条（AI 结果页面强制内置）
    │   └── RiskTag.vue            # 四级风险标签（低风险/MCI/AD早期/AD中晚期）
    ├── layouts/MainLayout.vue     # 顶栏 + 左侧固定菜单 + 主内容分区
    └── views/
        ├── LoginView.vue              # 1. 登录页（多角色 + 验证码）
        ├── dashboard/DashboardView.vue # 2. 工作台仪表盘
        ├── cases/CaseListView.vue      # 3. 病例库管理
        ├── viewer/ViewerView.vue       # 4. 四分区阅片 + AI 分析（核心）
        ├── analysis/AnalysisDetailView.vue # 5. AI 详情 + 干预方案
        ├── report/ReportView.vue       # 6. 报告预览导出
        ├── model/ModelConfigView.vue   # 7. 模型参数科研配置
        └── system/SystemManageView.vue # 8. 系统权限管理
```

---

## 六、页面功能说明

### 1. 登录页
- 居中医用卡片布局：用户名 / 密码 / 图形验证码，登录失败即时提示；
- 支持四类角色登录，登录后按角色权限点动态生成菜单与路由；
- 底部展示系统版本号（v1.0.0）与《隐私协议》入口。

### 2. 工作台仪表盘
- 四张统计卡片：待分析病例 / 已完成筛查 / AD 高风险 / 今日推理，附较昨日变化；
- 近 6 个月筛查量趋势折线图 + 四级风险分布环形图（ECharts）；
- 近期任务列表与病例快捷入口，一键跳转阅片 / 详情 / 报告页。

### 3. 病例库管理
- 表格字段：病例 ID、患者信息、影像模态、检查时间、AI 风险等级、诊断状态；
- 关键词搜索 + 高级多条件筛选（模态 / 风险等级 / 状态 / 科室）；
- DICOM（MRI/PET）影像上传建档、CSV 批量导入（附模板下载）、分页组件；
- 行操作：查看影像（跳阅片页）、启动 AI 分析（全局加载弹窗）、生成报告。

### 4. 多模态阅片 + AI 分析页（核心）
- **四分区固定布局**：左上 MRI 原始影像 / 右上 PET 原始影像 /
  左下 MRI-PET 配准融合影像（海马区、颞叶 ROI 高亮分割标注）/ 右下 AI 量化结果面板；
- 阅片交互：切片滚动、窗宽窗位调节、缩放、平移、长度测量、视图重置；
- 结果面板：患者信息、0-100 风险评分、MCI/AD 病程分期、海马体积 / 脑代谢等
  量化指标、异常脑区明细、模型推理置信度；
- 操作：启动多模态融合分析 / 重新推理 / 保存结果 / 生成报告，推理过程全局加载弹窗。

### 5. AI 分析详情 + 干预方案页
- 上半部：影像关键截图、AI 综合诊断摘要、四级风险标签、全量指标表格；
- 下半部四个干预模块标签页：认知干预 / 生活方式干预 / 随访复查 / 临床参考，
  医生可手动编辑 AI 生成方案；
- 支持保存、PDF 导出、打印预览、随访时间配置。

### 6. 报告预览导出页
- 左侧 A4 实时预览：患者信息、影像截图、量化数据、风险结论、干预建议，
  带医疗免责水印；右侧操作栏：模板切换、PDF 导出、在线打印、云端保存。

### 7. 模型参数科研配置页（科研管理员）
- 特征级 / 像素级融合策略切换、模型权重自定义、AD 筛查风险阈值、ROI 检测范围配置；
- 模型性能可视化（准确率 / AUC 等指标图 + 训练曲线）；
- 历史推理日志查询表格。

### 8. 系统权限管理页（超级管理员）
- Tab1 用户管理：新增账号、启用/禁用（禁用后不可登录）；
- Tab2 角色权限：四类角色 × 功能模块权限点勾选配置；
- Tab3 审计日志：系统操作日志 / 病例操作记录 / AI 推理日志，全行为可溯源，支持 CSV 导出。

### 9. 全局通用组件
- AI 推理加载弹窗（分阶段进度）、操作确认弹窗、风险警告弹窗、免责提示条、
  风险标签、ECharts 封装、专业阅片视口，全站样式与交互统一。

---

## 七、对接真实后端

1. `.env` 中设置 `VITE_USE_MOCK=false`；
2. 各 `src/api/*.ts` 中已按 Mock 与真实请求同构编写（`httpGet/httpPost` 封装于
   `src/utils/request.ts`，自动携带 token 与统一错误拦截），后端按现有接口路径
   `/auth/*`、`/cases/*`、`/analysis/*`、`/model/*`、`/dashboard/*`、`/report/*` 实现即可；
3. 开发环境 `/api` 代理目标在 `vite.config.ts` 的 `server.proxy` 中修改。

---

## 八、注意事项

- 所有 AI 结果页面均已内置免责提示，二次开发时请勿移除；
- 涉及患者数据，生产部署必须运行在医院内网并遵循数据安全合规要求；
- DICOM 上传接口需在后端限制单文件大小（前端已限制 `.dcm` 后缀与数量）；
- 若 `npm install` 报证书过期，切换镜像源：`npm config set registry https://registry.npmmirror.com`。
