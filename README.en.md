# Academic Research Agent

[中文说明](README.MD) ｜ [Demo video demo.mp4](demo.mp4) ｜ [Sample report](data/reports/demo.md)

A **full-stack deep-research agent** for academic workflows. Upload a paper to ask questions about it, or enter a research topic and let the agent decompose it into subtopics, search arXiv concurrently, download and deep-read full texts, then generate a structured report with **citation auditing**. Follow-up questions on finished reports, session memory, **mid-run dynamic control**, a ready-to-use Web UI, and an MCP extension are all built in.

## Demo Video

<video src="demo.mp4" controls width="100%"></video>

> If inline playback is unavailable, [download demo.mp4](demo.mp4). The video shows one complete deep-research run: topic planning → concurrent per-subtopic evidence collection → live progress stream → mid-run commands → report generation with citation highlighting → report discussion.

## Key Features

- **Paper QA**: upload PDF / MD / TXT and ask questions about a single paper; answers carry `[Sx]` evidence markers traceable to page, section and chunk.
- **Topic Research (Deep Research)**: the LLM plans the search and splits the topic into subtopics; the scheduler runs an independent pipeline per subtopic — arXiv metadata search → candidate normalization/dedup & selection → PDF download & full-text deep reading → structured notes — then aggregates everything into a survey report.
- **Report Discussion**: after a report is done, keep asking follow-ups. The agent classifies the intent (question about the report / needs new evidence / starts new research) and pulls on-demand evidence from existing full-text indexes without re-downloading.
- **Mid-run Dynamic Control**: while a research run is active, send natural-language commands — add a subtopic, cancel a subtopic, cancel the whole run, resume an interrupted run; ambiguous targets return a clarification candidate list.
- **Resume After Interruption**: subtopic-level checkpoints let the agent restart only unfinished topics after a server restart, keeping all finished work.
- **Citations & Auditing**: a unified evidence pool with `S` numbering; after writing, citations are validated, invalid references are annotated, a Sources section is appended, and audit metrics such as `valid_reference_rate` are returned.
- **Hybrid Retrieval**: Chroma vector index + TF-IDF sparse retrieval (jieba tokenization for Chinese), with corpus / single-file / per-session index scopes; degrades to sparse-only when no embedding API is configured.
- **Memory**: short-term JSON checkpoints + long-term SQLite (user memories, paper notes, session artifacts; ranked by embedding/keyword/recency); LLM-based context compression for long conversations; long-term memory candidates are confirmed in the Web UI before being persisted.
- **Web UI**: React SPA with SSE live progress (subtopic status cards, tool-call traces, reflection results), rendered reports with citation highlighting, paper upload & library management, and an online settings dialog.
- **MCP Server**: exposes the local paper library to MCP clients such as Claude Desktop.
- **Graceful Degradation**: endpoints without tool-calling/JSON-mode support are auto-detected and fall back to a deterministic evidence pipeline; without an LLM key the agent runs in an explicit demo mode instead of fabricating answers.

## Architecture

```
 web/ Frontend (React 18 + Vite + Tailwind)
   session sidebar · query/upload bar · SSE progress log · report view + citation highlight · evidence/tool/memory panels
        │  REST + SSE (submit -> progress stream -> result)
        ▼
 api/ FastAPI adapter
   JobManager runs blocking work in a thread pool; events are delivered to SSE via loop.call_soon_threadsafe
   /api/sessions · /api/jobs · /api/papers · /api/settings · /api/health
        │
        ▼
 agent/ Core
   runtime.py            entry: intent routing -> paper_qa / topic_research / report_discussion
   graph.py (LangGraph)  planner -> evidence -> researcher -> reflect (loop) -> writer -> memory
   topic_scheduler.py    multi-subtopic scheduler: concurrency · dynamic commands · retries · resume · report aggregation
   tool_loop.py          OpenAI tool-calling loop (deterministic fallback when unsupported)
   citation_check.py     citation audit: validity check · invalid annotation · Sources section
        │                     │                        │
        ▼                     ▼                        ▼
 tools/ Tools           rag/ Retrieval            memory/ Memory
 arXiv search/throttle/  PDF parsing · chunking ·   short-term checkpoints (JSON)
 download · corpus       indexing · Chroma /        long-term SQLite ·
 search · tool registry  TF-IDF sparse fallback     compression · preferences
```

## Quick Start

### 1. Install

Requires Python 3.10+.

```bash
pip install -r requirements.txt
```

`chromadb` and `jieba` are optional enhancements — without them retrieval automatically falls back to the built-in store / sparse-only mode.

### 2. Configure Models

Recommended: edit the default config file `data/llm_config.env` (a template ships with the repo — just replace the placeholders):

```env
LLM_MODEL=your-llm-model
API_KEY=your-api-key
BASE_URL=https://your-provider-url

Embedding_MODEL=your-embedding-model
Embedding_API_KEY=your-embedding-api-key
Embedding_BASE_URL=your-embedding-provider-url
```

Or pass a config file at startup:

```bash
python app.py --config-file your-config.env
```

You can also fill everything in the Web UI "Settings" dialog after startup (keys are masked; saving writes `data/llm_config.env` and takes effect immediately).

No embedding config? Retrieval degrades to sparse-only. No LLM key? The agent runs in an explicit demo mode and tells you so instead of making things up.

### 3. Run

```bash
python app.py
# Web UI: http://127.0.0.1:8000 (Ctrl+C to stop)
```

`app.py` arguments:

| Argument | Default | Description |
|---|---|---|
| `--web-host` | `127.0.0.1` | Bind host |
| `--web-port` | `8000` | Bind port |
| `--config-file` | - | Load model config from a KEY=VALUE file |

Deployment is single-process: FastAPI serves the built `web/dist` bundle directly, so Node is not needed in production. A single worker is enforced because Chroma and the task scheduler share local state.

## Web UI

- **Session sidebar**: create / switch / delete research sessions; the last session is restored on refresh.
- **Query bar**: enter a topic to start research, or upload a PDF/MD/TXT (or pick from the paper library) to switch to single-paper QA; supports `top_k` and per-paper index rebuild options.
- **During a run**: the progress log streams live, each subtopic shown as a status card (queued / running / completed / failed / cancelled); the query bar switches to command mode for dynamic commands.
- **Result area**: Markdown report rendering; clicking an `[Sx]` marker highlights its source in the sidebar; the report can be downloaded as Markdown.
- **Right sidebar**: source evidence, per-paper evidence status, tool-call traces (expandable), reflection results, and pending long-term memory candidates (checkbox-confirmed).
- **Resume**: when an interrupted run is detected, a "Continue" banner offers to finish only the remaining subtopics.
- **Settings dialog**: change LLM / Embedding models and keys, max research rounds and per-round paper count online; a health bar surfaces backend capabilities (LLM availability, tool calling, vector backend, degradation notices).

## Working Modes

### Paper QA
Upload or select a local paper → a dedicated index is built for that file → a tool-calling evidence loop retrieves inside the paper (`search_local_papers` / `read_local_file`) → answer with `[Sx]` citations.

### Topic Research
The LLM produces a search plan and query batches → the scheduler fans out subtopics concurrently: arXiv metadata search → survey-paper search → candidate normalization & dedup (cap 30 per subtopic, 60 selected globally) → LLM selection → full-text download & deep reading → evidence reflection (another collection round if insufficient, up to `MAX_RESEARCH_ROUNDS`) → aggregate evidence → outline → section writing → citation audit → Markdown report persisted to disk.

### Report Discussion
Ask follow-ups about a completed report: intents are classified automatically, retrieval is scoped to "current report + selected paper notes + on-demand full-text evidence", new evidence continues the original citation numbering, and the discussion history is kept in the session.

## Mid-run Dynamic Control

While a research run is active, type natural-language commands into the query bar (structured JSON commands are also supported):

| Example command | Effect |
|---|---|
| `add subtopic "diffusion models for protein structure prediction"` | Adds a new subtopic to the schedule |
| `cancel subtopic b` / `cancel topic <name>` | Cancels the given subtopic; ambiguous matches return a clarification list |
| `cancel the whole run` | Cooperatively stops the scheduler and all subtopic workers |
| `resume` / `continue` | Resumes from the checkpoint, running only unfinished subtopics |

Each subtopic retries on failure (2 attempts by default); subtopics cancelled before aggregation are removed from the final report.

## Memory & Context

| Layer | Implementation | Contents |
|---|---|---|
| Short-term memory | JSON checkpoints (`data/checkpoints/`) | Full session state for multi-turn resume and interruption recovery |
| Conversation log | JSON (`data/conversations/`) | Each turn's query + full result, powering the Web history |
| Long-term memory | SQLite (`data/memory.db`) | User-confirmed memory items, ranked by embedding + keyword + recency |
| Paper notes | SQLite | Structured notes per deep-read paper (problem/method/experiments/summary) |
| Context compression | LLM | Middle-message summarization that preserves the initial question and recent turns |
| Output preferences | Rule-based | Language / format / tone requirements detected from the current query |

## Retrieval & Citations

- **Parsing & chunking**: PDF parsed with both pypdf and PyMuPDF, section titles auto-detected, 1000/200 overlapping chunks.
- **Index backends**: in `auto` mode Chroma is preferred; without it (or without an embedding API) retrieval falls back to the built-in vector store + TF-IDF sparse retrieval (jieba tokenization).
- **Citation system**: evidence enters a unified pool with `S` numbering; every core claim must carry `[Sx]`; before persisting, invalid references are annotated, the Sources section is auto-appended, and audit metrics are returned with the result.

## REST API

| Method | Path | Description |
|---|---|---|
| GET | `/api/health` | Capability probe: LLM availability, tool calling, vector backend, degradation notices |
| POST | `/api/sessions` | Create a session |
| GET | `/api/sessions` | List sessions |
| GET | `/api/sessions/{id}` | Session detail (latest research result included) |
| DELETE | `/api/sessions/{id}` | Delete a session and its conversation log |
| GET | `/api/sessions/{id}/conversation` | Multi-turn conversation history |
| POST | `/api/sessions/{id}/research` | Submit a research task; returns `202 {job_id}` immediately |
| GET | `/api/sessions/{id}/memories` | Pending long-term memory candidates |
| POST | `/api/sessions/{id}/memories` | Confirm and save selected memories |
| GET | `/api/sessions/{id}/report` | Download the session's Markdown report (restricted to `data/`) |
| GET | `/api/jobs/{job_id}` | Job view: status / subtopics / command records / result (polling fallback for SSE) |
| GET | `/api/jobs/{job_id}/events` | SSE progress stream: `progress` / `done` / `error` / `cancelled`, with `Last-Event-ID` resume |
| POST | `/api/jobs/{job_id}/cancel` | Cancel a job |
| GET / POST | `/api/settings` | View / update model and retrieval settings (keys returned masked) |
| POST | `/api/papers/upload` | Upload a paper (extension whitelist, <= 50MB, sanitized filename) |
| GET | `/api/papers` | List the local paper library |

Research request body (`POST /api/sessions/{id}/research`): `query`, `file_path` (optional — switches to Paper QA), `top_k` (1–20), `rebuild_index`, `resume` (session continuation).

## MCP Server

The local paper library is exposed as an MCP server for clients such as Claude Desktop (see [mcp_server/README.md](mcp_server/README.md)):

| Tool | Purpose |
|---|---|
| `search_local_papers(query, top_k)` | Hybrid search over the paper library, returning evidence snippets with page/chunk locations |
| `list_local_papers()` | List searchable paper files |
| `read_local_file(file_path)` | Read files inside `data/` (path escape is rejected) |
| `search_arxiv_papers(query, max_results)` | Search arXiv by English keywords |
| `get_paper_notes(topic, limit)` | Read paper notes previously written to SQLite by the agent |

```bash
pip install -r requirements-mcp.txt
python -m mcp_server.server                      # stdio by default (Claude Desktop)
python -m mcp_server.server --transport http     # HTTP transport
python -m mcp_server.server --rebuild-index      # rebuild the library index before serving
```

## Project Structure

```
├── app.py                    # entry: launch uvicorn (API + serves web/dist)
├── config.py                 # config loading, LLM/embedding clients, capability probing & degradation
├── prompts.py                # all LLM prompts
├── agent/                    # agent core
│   ├── runtime.py            # run_research entry: intent routing / init / report discussion
│   ├── graph.py              # LangGraph state graph (with reflection loop)
│   ├── nodes.py              # planner / evidence / researcher / reflect / writer / memory nodes
│   ├── topic_scheduler.py    # multi-subtopic scheduler (dynamic commands, retries, resume, report aggregation)
│   ├── tool_loop.py          # tool-calling evidence loop
│   ├── deep_research_pipeline.py  # candidate normalize/dedup/select/review parsing + stage timing
│   ├── report_discussion.py  # follow-up Q&A: intent classification, on-demand evidence, context building
│   ├── citation_check.py     # citation auditing
│   ├── research_artifacts.py # working document: search/note/outline/section tracking
│   ├── intent.py             # paper_qa / topic_research intent scoring (bilingual)
│   ├── llm.py / progress.py / state.py / json_utils.py / research_service.py
├── api/                      # FastAPI adapter (agent core untouched)
│   ├── jobs.py               # JobManager: thread-pool execution + SSE stream + command channel
│   ├── routes/               # sessions / jobs / papers / settings / health
│   └── schemas.py / serialize.py / conversation.py / fake.py
├── tools/                    # arXiv search & download, local corpus search, tool registry, file reading
├── rag/                      # document parsing & chunking, retrieval orchestration, Chroma / sparse backends, tokenizer
├── memory/                   # short-term checkpoints, long-term SQLite, compression, preferences, notes
├── mcp_server/               # MCP extension (exposes the paper library)
├── skills/                   # lightweight YAML workflow definitions (literature review / report)
├── web/                      # React frontend (Vite + Tailwind), see web/README.md
└── data/                     # runtime data
    ├── llm_config.env        # model config (template ships with the repo)
    ├── papers/               # local paper library
    ├── vector_store/         # retrieval indexes (corpus / files / sessions)
    ├── checkpoints/          # session checkpoints
    ├── conversations/        # conversation logs
    ├── reports/              # generated reports (demo.md is a real generated example)
    └── memory.db             # long-term memory
```


## License

[MIT](LICENSE)
