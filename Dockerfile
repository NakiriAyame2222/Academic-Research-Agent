# 阶段④：单容器部署。多阶段构建 —— node 构建 web/dist，python 运行时只跑 FastAPI。
# 线上不需要 Node（StaticFiles 托管前端产物，见 api/main.py）。
FROM node:22-slim AS web-build
WORKDIR /build
COPY web/package.json web/package-lock.json* ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.10-slim
WORKDIR /app
# PyMuPDF 等依赖需要系统库；chromadb 可选，容器里装上
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY agent/ ./agent/
COPY rag/ ./rag/
COPY tools/ ./tools/
COPY memory/ ./memory/
COPY mcp_server/ ./mcp_server/
COPY api/ ./api/
COPY app.py config.py prompts.py report_templates.py ./
COPY --from=web-build /build/dist ./web/dist
# data/ 卷：checkpoints、papers、reports、vector_store、memory.db 都落这里
VOLUME ["/app/data"]
EXPOSE 8000
CMD ["python", "app.py", "--web-host", "0.0.0.0", "--web-port", "8000"]
