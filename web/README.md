# web/ — 研究 Agent 前端（React 18 + TS + Vite + Tailwind）

2026-10-08 精简：顶部「研究设置」可保存默认输出偏好；研究结束后可主动「保存研究笔记」。侧栏合并为来源证据和研究状态，工具轨迹按需展开。详见 [功能精简说明](../docs/simplification-20261008.md)。

Web 已统一为唯一研究交互入口。完整启动方式见 [项目 README](../README.md)，迁移细节见 [Web 统一说明](../docs/web-unification-20261008.md)。

输入框支持上传论文或选择已有论文；「本次检索设置」可指定数量、重建所选论文索引。历史会话恢复论文选择，问答和调研结果都可下载。

## 开发运行（两进程）

1. 启动后端（假模式，不碰 LLM）：

   ```bash
   cd ..
   RESEARCH_AGENT_FAKE=1 uvicorn api.main:app --port 8000
   ```

2. 启动前端：

   ```bash
   npm ci
   npm run dev
   ```

   打开 Vite 提示的地址（默认 http://localhost:5173）。`/api` 请求经 Vite proxy 转发到 :8000，免 CORS。

## 触发失败态（演示 error 帧）

在输入框里提交含 `__fail__` 的 query，或后端启动时加 `RESEARCH_AGENT_FAKE_FAIL=1`。

## 目录

- `src/api/client.ts` — fetch 封装（创建会话 / 提交研究 / 发送控制指令 / 断点续跑 / 取结果）
- `src/api/useJobStream.ts` — EventSource hook（进度流 + 子主题状态跟踪 + 歧义澄清交互）
- `src/components/` — QueryBar / ProgressLog / TurnBlock / ReportView / Sidebar

## 交互式多主题异步调研

在调研进行中，前端支持动态输入与单点控制：
- **动态输入**：底栏 QueryBar 在调研进行中保持可用，可输入自然语言（如 `增加调研主题 d` 或 `取消调研主题 b`）动态调整调研范围，无需中断或刷新当前任务；
- **单点取消**：推理时间线中实时展示各子主题卡片，运行中与排队中的主题均提供独立“取消”按钮；
- **歧义交互**：输入模糊取消名称时，时间线顶部会弹出候选主题按钮，支持一键点击消除歧义；
- **重连与断点续跑**：刷新页面或重新进入会话时自动恢复当前调研流；若服务中断，界面会显示“继续执行 (Resume)”提示条，保留已完成成果并仅续跑未完成的主题。
