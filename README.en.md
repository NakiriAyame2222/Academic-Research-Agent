# Academic Research Agent

[中文说明 / Chinese README](README.md)

A lightweight academic research agent for **paper QA**, **topic research**, **arXiv search**, and **citation-grounded answers**.

This project helps you:
- ask questions about a local paper (`.pdf`, `.md`, `.txt`)
- research a topic and collect representative papers
- search arXiv and summarize recent work
- keep short-term / long-term memory across sessions
- output answers with source citations

---

## Overview

Academic Research Agent is a lightweight agent for academic workflows, including:

- **Paper QA** over local files
- **Topic research** and literature discovery
- **arXiv search** and recent-paper summarization
- **Citation-grounded answers**
- **Session memory** for multi-turn research

This project is useful as a:
- paper reading assistant
- literature review assistant
- academic agent demo / portfolio project

## Features

- Works with local `.pdf`, `.md`, and `.txt` files
- Automatically routes requests into:
  - `paper_qa`
  - `topic_research`
- Supports OpenAI-compatible LLM and embedding APIs
- CLI-based interactive workflow
- Optional source / citation output

## Installation

```bash
pip install -r requirements.txt
```

## Configure API / Model / Base URL

This project uses **OpenAI-compatible** endpoints. You need to provide:

- `LLM_MODEL`
- `API_KEY`
- `BASE_URL`
- `EMBEDDING_MODEL`
- `EMBEDDING_API_KEY`
- `EMBEDDING_BASE_URL`

### Option 1: Recommended — use the default config file

Default config file location: `data/llm_config.env`

Example:

```env
LLM_MODEL=your-llm-model
API_KEY=your-api-key
BASE_URL=https://your-provider-url

EMBEDDING_MODEL=your-embedding-model
EMBEDDING_API_KEY=your-embedding-api-key
EMBEDDING_BASE_URL=https://your-provider-url
```

### Option 2: Use the template file in this repo

A sample template is provided in [cong.txt](cong.txt).

After filling in the placeholders, run:

```bash
python app.py --config-file cong.txt
```

### Option 3: Pass config from the command line

```bash
python app.py --model your-llm-model --api-key your-api-key --base-url https://your-provider-url
```

## Usage

### 1. Ask questions about a local paper

```bash
python app.py "What is the core contribution of this paper?" --file data/papers/your_paper.pdf --show-sources
```

You can also use `.md` or `.txt` files:

```bash
python app.py "Summarize the method and experimental results." --file data/papers/your_note.md --show-sources
```

### 2. Run topic research

```bash
python app.py "Help me research the latest progress in llm agent memory"
```

### 3. Start interactive chat mode

```bash
python app.py --chat
```

### 4. Resume a previous session

```bash
python app.py --resume your-session-id
```

## Common CLI Options

- `--file`: specify a local paper or text file
- `--chat`: start interactive mode
- `--resume`: resume an existing session
- `--show-sources`: print citations after the answer
- `--top-k`: control retrieval depth
- `--rebuild-index`: force index rebuild
- `--config-file`: load model / API / URL config from a file
- `--save-config`: persist the provided config