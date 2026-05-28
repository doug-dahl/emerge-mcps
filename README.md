# emerge-mcps

Monorepo for Emerge Career's MCP servers. Each subdirectory is a self-contained MCP server that builds to its own Docker image and deploys as its own Railway service.

## Servers

| Server | Purpose | Status |
|---|---|---|
| [`clip-editor/`](./clip-editor) | Transcript-based video editing for student interview clips (Google Drive + FFmpeg) | scaffolded |

## Adding a new MCP server

1. Create a new subdirectory (e.g. `something-else/`).
2. Self-contained: its own `server.py`, `requirements.txt`, `Dockerfile`, `README.md`, `.env.example`.
3. Add a row to the table above.
4. Create a new Railway service pointing at the subdirectory as its root.

Don't pre-extract a `shared/` package — wait until two servers actually duplicate something before factoring it out. Premature sharing creates coupling that makes the next MCP harder to write, not easier.

## Local development

Each server has its own setup; see its README. The typical loop:

```bash
cd clip-editor
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # fill in secrets
python server.py
```

## Deployment

Each server deploys independently to Railway. Point a Railway service at the subdirectory; Railway picks up the Dockerfile automatically. Set the env vars listed in that server's `.env.example`.
