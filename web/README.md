# web/ — 研究 Agent 前端（React 18 + TS + Vite + Tailwind）

阶段 ① 最小闭环：提交调研主题 → 实时进度流（SSE）→ Markdown 报告。

## 开发运行（两进程）

1. 启动后端（假模式，不碰 LLM）：

   ```bash
   cd ..
   RESEARCH_AGENT_FAKE=1 uvicorn api.main:app --port 8000
   ```

2. 启动前端：

   ```bash
   npm install
   npm run dev
   ```

   打开 Vite 提示的地址（默认 http://localhost:5173）。`/api` 请求经 Vite proxy 转发到 :8000，免 CORS。

## 触发失败态（演示 error 帧）

在输入框里提交含 `__fail__` 的 query，或后端启动时加 `RESEARCH_AGENT_FAKE_FAIL=1`。

## 目录

- `src/api/client.ts` — fetch 封装（创建会话 / 提交研究 / 取结果）
- `src/api/useJobStream.ts` — EventSource hook（进度流 + 按 id 去重 + done 后取完整结果）
- `src/components/` — QueryBar / ProgressLog / ReportView
