# ADScreen · 阿尔茨海默病一体化 MRI/PET 脑成像智能诊断系统
# 多阶段构建 Dockerfile
# - 阶段1：builder 构建前端静态资源
# - 阶段2：runtime 仅含 Python 后端 + Nginx 静态服务
# 体积从 ~2GB 压到 ~400MB（不含 PyTorch）

# ============================================================
# 阶段 1：前端构建
# ============================================================
FROM node:20-alpine AS frontend-builder

WORKDIR /build/frontend
COPY ad-screen-frontend/package*.json ./
RUN npm ci --no-audit --no-fund
COPY ad-screen-frontend/ ./
# 生产构建（生成 dist/）
RUN npm run build

# ============================================================
# 阶段 2：后端 + Nginx 运行时
# ============================================================
FROM python:3.11-slim AS runtime

# 安装系统依赖（Nginx 用于托管前端静态资源 + 反向代理后端 API）
RUN apt-get update && apt-get install -y --no-install-recommends \
    nginx \
    libglib2.0-0 \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

# ---------- 非 root 运行用户 ----------
# 容器逃逸即获宿主机 root 是医疗系统的合规红线（Dockerfile 原无 USER 指令）。
# 非 root 无法绑定 1024 以下端口，因此 Nginx 监听 8080（compose 端口映射同步调整）。
RUN groupadd --system --gid 10001 adscreen \
    && useradd --system --uid 10001 --gid adscreen --no-create-home adscreen

# 配置 Nginx：静态文件 + API 反向代理
RUN rm -f /etc/nginx/sites-enabled/default
# 移除 user 指令：非 root 运行时该指令无效且每次启动都告警
RUN sed -i '/^user /d' /etc/nginx/nginx.conf
COPY <<'EOF' /etc/nginx/conf.d/default.conf
server {
    listen 8080;
    server_name _;

    # 前端静态资源（SPA）
    root /app/frontend/dist;
    index index.html;

    # 静态资源缓存（Vite 产物带 hash，可长期缓存）
    location /assets/ {
        expires 1y;
        add_header Cache-Control "public, immutable";
    }

    # API 反向代理到 FastAPI
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Request-Id $request_id;
        # SSE 长连接超时延长（推理流式推送）
        proxy_read_timeout 300s;
        proxy_buffering off;
    }

    # SPA history fallback
    location / {
        try_files $uri $uri/ /index.html;
    }
}
EOF

# 安装 Python 依赖（不含 PyTorch，使用 CPU 模拟推理；如需 GPU 加速使用 docker-compose.gpu.yml）
WORKDIR /app/backend
COPY ad-screen-backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 复制后端代码
COPY ad-screen-backend/ ./

# 复制前端构建产物到 Nginx 静态目录
RUN mkdir -p /app/frontend/dist
COPY --from=frontend-builder /build/frontend/dist /app/frontend/dist

# 复制训练代码与 checkpoints 目录占位（运行时挂载卷）
# checkpoints 体积大，生产通过 volume 挂载，不打入镜像
COPY models/ /app/models/

# 环境变量默认值
ENV ADSCREEN_ENV=production \
    ADSCREEN_LOG_LEVEL=INFO \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# 目录属主移交非 root 用户（数据卷、日志、Nginx 运行时目录）
RUN mkdir -p /app/backend/data /app/backend/logs /var/cache/nginx /var/log/nginx /run \
    && chown -R adscreen:adscreen /app /var/cache/nginx /var/log/nginx /run

# 健康检查
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health', timeout=3)" || exit 1

# 启动：Nginx 守护进程 + FastAPI（uvicorn）
# 用 bash 同时拉起两个进程（生产建议用 supervisor 或 systemd，这里简化）
COPY <<'EOF' /app/start.sh
#!/bin/sh
set -e

# 后台启动 Nginx
nginx -g "daemon off;" &
NGINX_PID=$!

# 前台启动 FastAPI（uvicorn）
cd /app/backend
exec uvicorn main:app --host 0.0.0.0 --port 8000 --workers ${UVICORN_WORKERS:-1}
EOF
RUN chmod +x /app/start.sh

# 以非 root 用户运行（Nginx 监听 8080，见 default.conf）
USER adscreen

EXPOSE 8080
CMD ["/app/start.sh"]
