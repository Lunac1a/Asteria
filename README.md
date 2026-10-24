# Asteria

**English** | [简体中文](README.zh-CN.md)

**Your personal learning copilot.**

Bring your learning materials, questions, and ideas into one workspace. Start with a question, build understanding through explanations, practice, and feedback, and pick up where you left off with saved notes and recaps.

## What you can do

- **Organize learning workspaces:** group PDFs, Markdown, and text files by course or topic, alongside your conversations.
- **Ask questions:** explore concepts, discuss ideas, or find answers grounded in your own materials.
- **Learn with guidance:** work toward a goal through explanations, hints, exercises, and feedback. Ask for a direct answer, skip a question, or change focus at any time.
- **Keep your learning context:** review and edit your next step, current focus, and notes during a session. Save a recap to continue later.

## Features

- **Context-aware retrieval:** the system decides when to use workspace materials, without requiring a retrieval-mode switch.
- **Verifiable sources:** inspect cited passages and original PDF page numbers in the sources panel.
- **Flexible learning:** switch between Q&A and guided learning without a mandatory sequence of stages.
- **English and Chinese:** both interface languages are supported. Responses follow the conversation context and explicit language requests; saved history is preserved.
- **Bring your own model:** configure an OpenAI-compatible endpoint, API key, and model name.
- **Local storage:** documents are parsed and indexed locally, and learning records are stored in your local database. Context needed to answer a question is sent to your configured model service.

## Run locally

The supported local workflow uses **Windows / PowerShell, Python 3.12, Node.js 22.13+, and Docker Desktop**, with an available OpenAI-compatible model service.

### First-time setup

Clone the repository:

```powershell
git clone https://github.com/Lunac1a/Asteria.git
cd Asteria
```

Create a local PostgreSQL instance for a new environment:

```powershell
docker run -d --name asteria-local-mvp-db -p 127.0.0.1:55432:5432 -e POSTGRES_USER=asteria -e POSTGRES_PASSWORD=local-development-only -e POSTGRES_DB=asteria_local -v asteria-local-pg:/var/lib/postgresql postgres:18-alpine
```

Install the backend, generate local configuration, and prepare the database and embedding model:

```powershell
Push-Location backend
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe app/cli/local.py configure
.venv/Scripts/python.exe app/cli/local.py init
.venv/Scripts/python.exe app/cli/local.py prepare
Pop-Location
```

Install and build the frontend:

```powershell
Push-Location frontend
npm ci
npm run build
Pop-Location
```

These steps are for a new installation. If you already have configuration and a database, use the startup workflow below. Preparing the embedding model for the first time requires a download.

### Start and stop

For Persistent Worker configuration, readiness checks, and controlled graceful shutdown, use the foreground workflow in the [Windows deployment guide](backend/docs/windows-deployment.md). The shortcut below uses forced termination when stopping and does not replace graceful-shutdown verification.

Open Docker Desktop, then run from the repository root:

```powershell
.\Start-Asteria.ps1
```

Open **[http://localhost:3001](http://localhost:3001)**, register or sign in, and configure your API endpoint, API key, and model name in **Settings**. Use the API base URL, for example `https://api.example.com/v1`.

Add the endpoint to `LLM_ALLOWED_BASE_URLS` in `backend/.env` and restart the backend after changing it. Enter your model key through Settings; do not commit it to Git.

Stop the application:

```powershell
.\Stop-Asteria.ps1
```

The database stays running by default. To stop it too, use `Stop-Asteria.ps1 -Database`; its data volume is retained.

### Your first learning session

1. Create a workspace for a course or topic.
2. Upload readable PDFs, UTF-8 Markdown, or text files and wait for processing to finish.
3. Choose **Q&A** to ask directly, or **Learn** to describe a learning goal.
4. Open Sources to check supporting passages and edit Learning Notes as you go.
5. Review and save the Recap at the end, then continue from your home page or conversation history.

## Architecture and deployment scope

```text
Next.js → same-origin /api → FastAPI (single process) → PostgreSQL
                               ├─ FastEmbed retrieval + Persistent Query Worker
                               └─ Provider subprocess per call → OpenAI-compatible API
```

The verified deployment scope is **one Windows API process with PostgreSQL**. The Worker Supervisor uses Windows Job Objects to manage process trees, bound recovery attempts, and stop recovery when cleanup cannot be confirmed. Linux, multiple API instances, and production capacity are outside the verified scope. Enable the Persistent Worker and optional session concurrency explicitly using the deployment guide; benchmark settings are not application defaults.

The backend includes request idempotency and Singleflight, Session/Workspace coordination, transactional persistence, HTTP stage timings, and Worker lifecycle events. The guides below cover deployment, backup, rollback, and incident response.

## Benchmark findings

A controlled import optimization was evaluated using real TCP HTTP, isolated PostgreSQL 18.6, offline FastEmbed, fixed synthetic documents, and a deterministic local Mock LLM, while preserving Provider subprocess isolation:

| Matched A/B metric | Result |
| --- | --- |
| Mean Provider duration | 1623.98 → 1492.16 ms (8.12% lower) |
| Mean server latency for successful Chat requests | 1766.80 → 1635.38 ms (7.44% lower) |
| Provider CPU time per call | 10.30% lower |
| Successful throughput at 1.5 offered req/s | 0.9917 req/s in both versions |
| HTTP 429 rejection rate | 33.33% in both versions |

Latency and CPU cost improved, but **capacity improvement was not demonstrated at this offered load**. These fixed-load, local-Mock results do not represent real-model or production performance. See the [benchmark summary](backend/docs/benchmark-summary.md) for methodology, correctness checks, and limitations.

## Managing conversations and workspaces

Use the **···** menu in a list or conversation header to delete an item. Deleting a conversation retains its workspace and documents. Deleting a workspace also removes its documents and conversations; this operation currently requires administrator-confirmed, single-instance maintenance mode and is rejected during normal operation by default. There is no recycle bin. Check the scope before deleting, and follow the [workspace recovery guide](backend/docs/workspace-recovery.txt) for maintenance and recovery.

## Development and operations

- [Development guide](backend/docs/development.md): repository structure, test commands, and material-use policies.
- [Windows deployment](backend/docs/windows-deployment.md): configuration, startup, readiness, and graceful shutdown.
- [Backup and restore](backend/docs/backup-restore.md) and [release and rollback](backend/docs/release-rollback.md).
- [Incident response](backend/docs/incident-response.md) and [Worker lifecycle events](backend/docs/worker-lifecycle-events.md).
- [Tests and evidence policy](backend/docs/verification.md) and [benchmark workload design](backend/docs/benchmark-workload-design.md).

Executable regression tests live in `backend/tests/` and `frontend/tests/`; reusable experiment tools live in `backend/tests/experiments/`. Detailed historical reports are archived locally under `docs/`. Raw experiment outputs remain in the ignored `backend/tests/experiments/evidence/` and `.local/` directories and are not distributed with the current repository. Public documentation retains curated methods, results, and limitations.

## License

[MIT](LICENSE)
