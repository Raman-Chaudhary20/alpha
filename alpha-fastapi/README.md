

> **Alpha 2.0:** Alpha now reads every conversation it is allowed to (channels, and the DMs of people who sign in), removes repeats, keeps a list of projects and people, and answers with Claude on Amazon Bedrock instead of Groq. How to ask it questions, how to sign in, and how to set it up: **[ALPHA-GUIDE.md](ALPHA-GUIDE.md)**. Parts of this README below still describe the earlier Groq version.

<div align="center">

# 🤖 Alpha — Slack AI Bot

**A Groq-powered AI assistant that lives in your Slack workspace**

[![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Groq](https://img.shields.io/badge/Groq-AI-F55036?style=for-the-badge&logo=groq&logoColor=white)](https://groq.com)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?style=for-the-badge&logo=docker&logoColor=white)](https://docker.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge)](LICENSE)
[![Slack](https://img.shields.io/badge/Slack-Socket_Mode-4A154B?style=for-the-badge&logo=slack&logoColor=white)](https://api.slack.com)

*Mention @alpha in any channel — it already knows what your team has been talking about.*

</div>

---

## 📋 Table of Contents

- [Overview](#-overview)
- [Alpha in Action](#-alpha-in-action)
- [Architecture](#-architecture)
- [Quick Start](#-quick-start-5-minutes)
- [Configuration Reference](#-configuration-reference)
- [API Documentation](#-api-documentation)
- [Slack Setup Walkthrough](#-slack-setup-walkthrough)
- [How It Works](#-how-it-works)
- [Project Structure](#-project-structure)
- [Docker Deployment](#-docker-deployment)
- [Testing](#-testing)
- [Contributing](#-contributing)
- [License](#-license)

---

## 🌟 Overview

**Alpha** is a production-ready AI assistant bot for Slack, built with Python and FastAPI. It continuously syncs your workspace's message history and answers questions with real context — not just generic LLM responses, but answers grounded in what your team actually said.

### ✨ Key Features

| Feature | Description |
|---|---|
| 🧠 **Context-Aware AI** | Answers with up to 30,000 chars of Slack history + member list + conversation memory |
| ⚡ **Real-Time Capture** | Listens to live Slack messages instantly without waiting for sync loops |
| 🔄 **Auto-Sync** | Periodically syncs message history every **2 minutes**, members every **10 minutes** |
| 💬 **Thread-Safe Replies** | Always replies in Slack threads — no channel clutter |
| 🧹 **Clean Output** | Strips markdown from AI replies for native Slack formatting |
| 🌐 **REST API** | Full HTTP API (supporting `/api/v1/...` and root `/...`) for Postman, integrations, and automation |
| ⚡ **Groq-Powered** | Ultra-fast inference via Groq's LPU™ hardware |
| 🐳 **Docker Ready** | Single `docker-compose up --build` to deploy anywhere |
| 🏥 **Health Checks** | Built-in health endpoint for orchestration platforms |

---


## 🏛️ Architecture

```mermaid
flowchart TD
    A[👤 Slack User] -->|@alpha mention| B[Slack Platform]
    B -->|WebSocket / Socket Mode| C[Slack Bolt Handler]
    C --> D{Alpha FastAPI App}

    D --> E[🧠 Groq AI Service]
    D --> F[📂 File Service]
    D --> G[🔄 APScheduler]

    G -->|every 2 min| H[Slack Message Sync]
    G -->|every 10 min| I[Member List Sync]

    H --> J[(slack_messages.txt)]
    I --> K[(slack_members.txt)]
    E --> L[(conversation.md)]
    F --> J
    F --> K
    F --> L

    E -->|reads context| J
    E -->|reads context| K
    E -->|reads history| L

    M[🌐 REST Client / Postman] -->|HTTP| N[FastAPI REST API]
    N --> D

    subgraph Context Window
        J
        K
        L
    end

    subgraph Storage
        J
        K
        L
        O[(slack_sync_state.json)]
    end
```

### Data Flow

```
Slack Messages ──► slack_messages.txt (30k chars)  ─┐
Member List    ──► slack_members.txt  (8k chars)   ──┼──► Groq AI ──► Answer
Conversation   ──► conversation.md   (10k chars)  ─┘
```

---

## 🚀 Quick Start (5 Minutes)

### Prerequisites

- Python 3.12+
- A [Groq API key](https://console.groq.com) (free tier available)
- A Slack workspace where you can install apps

### 1. Clone & Install

```bash
git clone https://github.com/your-org/alpha-fastapi.git
cd alpha-fastapi

python -m venv .venv
# On Windows:
.venv\Scripts\activate
# On macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt
```

### 2. Configure Environment

```bash
cp .env.example .env
```

Open `.env` and fill in your credentials:

```env
GROQ_API_KEY=gsk_your_groq_key_here
SLACK_BOT_TOKEN=xoxb-your-bot-token
SLACK_APP_TOKEN=xapp-your-app-token
```

> **Need Slack tokens?** See the [Slack Setup Walkthrough](#-slack-setup-walkthrough) below — it takes about 5 minutes.

### 3. Run

```bash
# On Linux / macOS:
uvicorn app.main:app --reload --port 8000

# On Windows:
python -m uvicorn app.main:app --reload --port 8000
```

Alpha is now running at `http://localhost:8000`. The bot will connect to Slack via Socket Mode automatically and begin syncing message history.

### 4. Invite the Bot

In Slack, type `/invite @alpha` in any channel you want it to monitor.

### 5. Talk to Alpha

```
@alpha what are we working on this sprint?
```

---

## ⚙️ Configuration Reference

All configuration is done via environment variables (or a `.env` file in the project root).

| Variable | Required | Default | Description |
|---|---|---|---|
| `GROQ_API_KEY` | ✅ Yes | — | Your Groq API key from [console.groq.com](https://console.groq.com) |
| `SLACK_BOT_TOKEN` | 🤖 Bot only | — | `xoxb-...` Bot OAuth token from Slack app settings |
| `SLACK_APP_TOKEN` | 🤖 Bot only | — | `xapp-...` App-level token (required for Socket Mode) |
| `GROQ_MODEL` | No | `openai/gpt-oss-120b` | Groq model to use for inference |
| `SLACK_SYNC_DAYS` | No | `7` | Days of message history to pull on first run |
| `SLACK_SYNC_MINUTES` | No | `2` | How often (in minutes) to sync new Slack messages |
| `PORT` | No | `8000` | HTTP port for the FastAPI server |
| `DEBUG` | No | `false` | Enable debug logging and FastAPI debug mode |

### `.env.example`

```env
# --- Required ---
GROQ_API_KEY=gsk_...

# --- Slack Bot (required for bot features) ---
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...

# --- Optional Tuning ---
GROQ_MODEL=openai/gpt-oss-120b
SLACK_SYNC_DAYS=7
SLACK_SYNC_MINUTES=2
PORT=8000
DEBUG=false
```

---

## 📡 API Documentation

The REST API supports both versioned endpoints at `/api/v1/` and legacy root paths (e.g. `/ask`, `/message`) for seamless backward compatibility. An interactive Swagger UI is available at `http://localhost:8000/docs`.

### Quick Reference

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/` | Health check |
| `POST` | `/api/v1/ask` (or `/ask`) | Ask the AI bot a question |
| `POST` | `/api/v1/message` (or `/message`) | Log an arbitrary message |
| `POST` | `/api/v1/sync` (or `/sync`) | Force an immediate Slack sync |
| `GET` | `/api/v1/history` (or `/history`) | Retrieve conversation history (Markdown) |
| `GET` | `/api/v1/slack-data` (or `/slack-data`) | Retrieve raw Slack messages (Plain text) |
| `GET` | `/docs` | Interactive Swagger API documentation |
| `GET` | `/redoc` | Alternative ReDoc API documentation |

---

### `POST /api/v1/ask` (or `/ask`) — Ask a Question

Ask Alpha anything. Uses the full Slack context window.

**Bash / Linux / macOS:**
```bash
curl -X POST http://localhost:8000/api/v1/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "What did the team decide about authentication?", "user": "Rahul"}'
```

**Windows PowerShell:**
```powershell
curl -X POST http://localhost:8000/api/v1/ask `
  -H "Content-Type: application/json" `
  -d '{\"question\": \"What did the team decide about authentication?\", \"user\": \"Rahul\"}'
```

**Request Body:**
```json
{
  "question": "What did the team decide about authentication?",
  "user": "Rahul"
}
```
*(Aliases supported: `"q"` or `"prompt"` can be used in place of `"question"`)*

**Response:**
```json
{
  "answer": "Based on the #backend channel discussion on Nov 14th, the team decided to use JWT tokens with a 24-hour expiry. @sarah is implementing it in the auth-service branch."
}
```

---

### `POST /api/v1/message` (or `/message`) — Save a Message

Manually inject a message into Alpha's context (useful for testing or custom integrations).

**Bash / Linux / macOS:**
```bash
curl -X POST http://localhost:8000/api/v1/message \
  -H "Content-Type: application/json" \
  -d '{"user": "Rahul", "text": "We shipped v2.1.0 today!", "channel": "general"}'
```

**Windows PowerShell:**
```powershell
curl -X POST http://localhost:8000/api/v1/message `
  -H "Content-Type: application/json" `
  -d '{\"user\": \"Rahul\", \"text\": \"We shipped v2.1.0 today!\", \"channel\": \"general\"}'
```

**Request Body:**
```json
{
  "user": "Rahul",
  "text": "We shipped v2.1.0 today!",
  "channel": "general"
}
```
*(Aliases supported: `"message"` can be used in place of `"text"`)*

**Response:**
```json
{
  "saved": true
}
```

---

### `POST /api/v1/sync` — Force Slack Sync

Trigger an immediate Slack message sync outside the normal 2-minute schedule.

```bash
curl -X POST http://localhost:8000/api/v1/sync
```

**Response:**
```json
{
  "synced": true
}
```

---

### `GET /api/v1/history` — Get Conversation History

Returns the full conversation log (`conversation.md`) as `text/markdown`.

```bash
curl http://localhost:8000/api/v1/history
```

**Response:** (Content-Type: `text/markdown`)
```markdown
## 2024-11-17 10:30 — Rahul asked:
What did the team decide about authentication?

**Alpha replied:**
Based on the #backend channel discussion...

---
```

---

### `GET /api/v1/slack-data` — Get Raw Slack Messages

Returns the synced Slack message dump (`slack_messages.txt`) as `text/plain`.

```bash
curl http://localhost:8000/api/v1/slack-data
```

**Response:** (Content-Type: `text/plain`)
```
[2024-11-17 09:15] #general | sarah: Good morning team!
[2024-11-17 09:22] #engineering | james: PR #142 is ready for review
...
```

---

### `GET /` — Health Check

Returns service status. Use for Docker health checks, load balancer probes, or uptime monitoring.

```bash
curl http://localhost:8000/
```

**Response:**
```json
{
  "status": "ok",
  "app": "Alpha - Slack AI Bot",
  "version": "1.0.0"
}
```

---

## 🔧 Slack Setup Walkthrough

Follow these steps to create and configure your Slack app. Total time: ~5 minutes.

### Step 1 — Create the Slack App

1. Go to [api.slack.com/apps](https://api.slack.com/apps)
2. Click **"Create New App"** → **"From scratch"**
3. Name it **Alpha** and select your workspace
4. Click **"Create App"**

### Step 2 — Enable Socket Mode

1. In the left sidebar, click **"Socket Mode"**
2. Toggle **"Enable Socket Mode"** to ON
3. Click **"Generate"** to create an App-Level Token
4. Name it (e.g., `alpha-socket-token`), add the `connections:write` scope
5. Click **"Generate"** — copy the `xapp-...` token → `SLACK_APP_TOKEN`

### Step 3 — Add Bot Token Scopes

1. In the sidebar, click **"OAuth & Permissions"**
2. Scroll to **"Bot Token Scopes"** and add all of these:

   | Scope | Purpose |
   |---|---|
   | `app_mentions:read` | Receive @mention events |
   | `channels:history` | Read public channel messages |
   | `channels:read` | List public channels |
   | `groups:history` | Read private channel messages |
   | `groups:read` | List private channels |
   | `im:history` | Read direct message (personal chat) messages |
   | `im:read` | List direct message channels |
   | `mpim:history` | Read group DM messages |
   | `mpim:read` | List group DM channels |
   | `users:read` | Fetch workspace member list |
   | `chat:write` | Post messages and replies |

### Step 4 — Subscribe to Bot Events

1. In the sidebar, click **"Event Subscriptions"**
2. Toggle **"Enable Events"** to ON
3. Under **"Subscribe to Bot Events"**, click **"Add Bot User Event"**
4. Add:
   - `app_mention`
   - `message.im` (real-time 1-on-1 direct messages with bot)
   - `message.mpim` (real-time group DMs)
   - `message.channels` (real-time public channel capture)
   - `message.groups` (real-time private channel capture)
5. Click **"Save Changes"**

### Step 5 — Install to Workspace

1. In the sidebar, click **"Install App"**
2. Click **"Install to Workspace"** → **"Allow"**
3. Copy the **Bot User OAuth Token** (`xoxb-...`) → `SLACK_BOT_TOKEN`

### Step 6 — Invite Alpha to Channels

In any Slack channel where you want Alpha to participate:

```
/invite @alpha
```

Alpha will start syncing that channel's history on the next sync cycle. ✅

---

## 🔬 How It Works

### The Sync Pipeline

```
Every 2 minutes:
  Slack API ──► fetch new messages from all joined channels
              ──► append to slack_messages.txt
              ──► update slack_sync_state.json (last-seen timestamps)

Every 10 minutes:
  Slack API ──► fetch workspace member list
              ──► overwrite slack_members.txt
```

Alpha uses `APScheduler` to run both jobs as async background tasks inside the FastAPI lifespan context. On **first run**, it backfills `SLACK_SYNC_DAYS` (default: 7) days of history.

### The AI Context Window

When Alpha receives a question (from Slack @mention or REST API), it builds this prompt:

```
System:
  Your name is Alpha. You are a helpful assistant inside a Slack workspace.

  <slack_messages>
    [last 30,000 characters from slack_messages.txt]
  </slack_messages>

  <slack_members>
    [last 8,000 characters from slack_members.txt]
  </slack_members>

  <conversation_history>
    [last 10,000 characters from conversation.md]
  </conversation_history>

User:
  [the question]
```

This gives the model **48,000 characters of workspace context** per query — enough for dozens of conversations and hundreds of messages.

### Reply Cleanup

Groq's model outputs markdown naturally. Alpha strips it before posting to Slack:

- `**bold**` → `bold`
- `*italic*` → `italic`
- `# headings` → plain text
- `` `code` `` → code (kept, since Slack renders it)

This keeps Slack replies clean and native-looking.

### Thread Awareness

Every Slack @mention arrives with a `thread_ts` (or creates one). Alpha always uses `thread_ts` in its reply, keeping all conversations properly threaded.

---

## 📁 Project Structure

```
alpha-fastapi/
├── app/
│   ├── __init__.py
│   ├── main.py                   # FastAPI app + lifespan (scheduler + Slack bot startup)
│   ├── config.py                 # pydantic-settings Settings class
│   ├── dependencies.py           # FastAPI dependency injection
│   │
│   ├── api/
│   │   ├── __init__.py
│   │   └── v1/
│   │       ├── __init__.py
│   │       ├── router.py         # APIRouter combining all v1 endpoints
│   │       └── endpoints/
│   │           ├── __init__.py
│   │           ├── ask.py        # POST /api/v1/ask
│   │           ├── messages.py   # POST /api/v1/message, GET /api/v1/slack-data
│   │           ├── sync.py       # POST /api/v1/sync
│   │           └── history.py    # GET /api/v1/history
│   │
│   ├── core/
│   │   ├── __init__.py
│   │   ├── logging.py            # Structured logging config
│   │   └── exceptions.py        # HTTP exception handlers + SlackNotConfiguredError
│   │
│   ├── models/
│   │   ├── __init__.py
│   │   └── schemas.py            # Pydantic v2 request/response models
│   │
│   ├── services/
│   │   ├── __init__.py
│   │   ├── groq_service.py       # Groq AI client + clean_reply + ask_bot
│   │   ├── slack_service.py      # Slack Bolt app + sync logic
│   │   └── file_service.py       # All flat-file I/O (pathlib)
│   │
│   └── tasks/
│       ├── __init__.py
│       └── scheduler.py          # APScheduler jobs (message sync, member sync)
│
├── data/                         # Runtime data (gitignored)
│   ├── slack_messages.txt        # Synced Slack messages
│   ├── slack_members.txt         # Workspace member list
│   ├── conversation.md           # Q&A history
│   └── slack_sync_state.json     # Last-sync timestamps per channel
│
├── tests/
│   ├── __init__.py
│   ├── conftest.py               # Shared fixtures (test client, mock settings)
│   ├── test_api/
│   │   ├── __init__.py
│   │   └── test_endpoints.py     # HTTP endpoint integration tests
│   └── test_services/
│       ├── __init__.py
│       └── test_file_service.py  # File service unit tests
│
├── .env.example                  # Environment variable template
├── .gitignore
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── pyproject.toml
```

---

## 🐳 Docker Deployment

### docker-compose (Recommended)

```bash
# 1. Configure environment
cp .env.example .env
# Edit .env with your credentials

# 2. Build and start
docker-compose up --build

# 3. Run in background (production)
docker-compose up --build -d

# 4. View logs
docker-compose logs -f

# 5. Stop
docker-compose down
```

### Standalone Docker

```bash
# Build
docker build -t alpha-slack-bot .

# Run
docker run -d \
  --name alpha \
  -p 8000:8000 \
  --env-file .env \
  -v $(pwd)/data:/app/data \
  alpha-slack-bot
```

### docker-compose.yml Overview

```yaml
version: "3.9"
services:
  alpha:
    build: .
    ports:
      - "${PORT:-8000}:8000"
    env_file: .env
    volumes:
      - ./data:/app/data     # Persist synced Slack data
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/"]
      interval: 30s
      timeout: 10s
      retries: 3
```

### Production Tips

- Mount `./data` as a volume so synced messages survive container restarts
- Use a reverse proxy (nginx/Caddy) for HTTPS termination
- Set `DEBUG=false` in production
- Monitor the health endpoint at `GET /` with your orchestration platform

---

## 🧪 Testing

Alpha ships with a `pytest` test suite covering API endpoints and core services.

### Run All Tests

```bash
pytest tests/ -v
```

### Run with Coverage

```bash
pytest tests/ -v --cov=app --cov-report=term-missing
```

### Run a Specific Test File

```bash
pytest tests/test_api/test_endpoints.py -v
pytest tests/test_services/test_file_service.py -v
```

### Test Structure

```
tests/
├── conftest.py              # TestClient fixture, mock Settings, temp data dir
├── test_api/
│   └── test_endpoints.py   # Tests for /ask, /message, /sync, /history, /slack-data, /
└── test_services/
    └── test_file_service.py # Unit tests for stamp(), tail(), save_conversation(), etc.
```

### Writing New Tests

```python
# tests/test_api/test_endpoints.py
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_health_check():
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"

def test_ask_endpoint(monkeypatch):
    async def mock_ask_bot(question: str) -> str:
        return "Mocked answer"

    monkeypatch.setattr("app.services.groq_service.ask_bot", mock_ask_bot)

    response = client.post("/api/v1/ask", json={
        "question": "What is the team working on?",
        "user": "test-user"
    })
    assert response.status_code == 200
    assert "answer" in response.json()
```

---

## 🤝 Contributing

Contributions are welcome! Here's how to get involved:

### Development Setup

```bash
git clone https://github.com/your-org/alpha-fastapi.git
cd alpha-fastapi
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
pip install pytest pytest-asyncio pytest-cov httpx
```

### Workflow

1. **Fork** the repository
2. **Create a feature branch**: `git checkout -b feature/my-awesome-feature`
3. **Make your changes** — keep them focused and well-documented
4. **Add tests** for any new functionality
5. **Run the test suite**: `pytest tests/ -v`
6. **Commit** with a clear message: `git commit -m "feat: add multi-channel sync filter"`
7. **Push** to your fork: `git push origin feature/my-awesome-feature`
8. **Open a Pull Request** — describe what you changed and why

### Guidelines

- Follow [PEP 8](https://peps.python.org/pep-0008/) and use type hints throughout
- All new endpoints must have corresponding tests
- Keep services stateless where possible — use `file_service` for persistence
- Do not commit `.env` files or API keys
- Update this README if you add new config variables or endpoints

### Ideas for Contributions

- 🔍 **Semantic search** over Slack messages (vector embeddings)
- 🌍 **Multi-workspace support** (multiple Slack tokens)
- 📊 **Usage dashboard** (FastAPI + simple HTML)
- 🔒 **API key authentication** for the REST endpoints
- 📬 **Proactive summaries** (daily digest sent to a channel)
- 🗂️ **SQLite backend** to replace flat-file storage

---

## 📄 License

This project is licensed under the **MIT License** — see the [LICENSE](LICENSE) file for details.

```
MIT License

Copyright (c) 2024 Alpha Contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
```

---

<div align="center">

**Built with ❤️ using [FastAPI](https://fastapi.tiangolo.com) · [Groq](https://groq.com) · [Slack Bolt](https://slack.dev/bolt-python/)**

*If Alpha saves your team time, consider giving this repo a ⭐*

</div>
