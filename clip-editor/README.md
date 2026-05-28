# clip-editor MCP server

Transcript-based video editing for Emerge Career's student interview clips. Claude reads timestamped transcripts, decides what to keep, and calls this server to do the cut. The video bytes never pass through Claude's context.

## Tools

| Tool | What it does |
|---|---|
| `parse_transcript` | Read a transcript `.txt` from Drive → structured segments |
| `preview_edit` | Show estimated duration + kept segments for a proposed cut (no rendering) |
| `edit_clip` | Download the mp4, cut it via FFmpeg, return a download URL |
| `list_clips` | Walk a student folder and pair mp4s with their transcript siblings |

## Architecture

```
Claude.ai ──MCP/HTTP──► server.py (FastMCP + Starlette)
                            │
                            ├── tools.py     ── orchestration
                            ├── drive.py     ── Google Drive (service account)
                            ├── transcript.py── cal.com [MM:SS.S] parser
                            ├── editor.py    ── FFmpeg subprocess
                            └── downloads.py ── temp file mgmt + TTL cleanup
```

The MCP endpoint and the `/downloads/{token}/{filename}` endpoint share the same ASGI app and the same process.

## Local development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # fill in GOOGLE_SERVICE_ACCOUNT_JSON, STUDENT_INTERVIEWS_DRIVE_ID, DOWNLOAD_BASE_URL
export $(grep -v '^#' .env | xargs)  # or use direnv / dotenv
python server.py
```

FFmpeg must be on `PATH`. On macOS: `brew install ffmpeg`.

The server starts on `PORT` (default 8000). Endpoints:
- `GET /health` — health check
- `GET /downloads/{token}/{filename}` — serves rendered clips
- MCP streamable HTTP endpoint — Starlette mount at `/` (see FastMCP docs for the exact path; typically `/mcp`)

## Google Drive setup

This server reuses the **`enrollment-clip-uploader`** service account that already has Manager access to the "Student Interviews" shared drive (used by `functions/clients/drive` in the emerge-career-dev repo). No new SA setup is needed.

The key lives in Google Secret Manager as **`DRIVE_SA_KEY`** (base64-encoded JSON). To wire it up:

```bash
# Pull the value, then paste it into Railway as DRIVE_SA_KEY:
gcloud secrets versions access latest --secret=DRIVE_SA_KEY --project=<project-id>
```

`drive.py` accepts both base64-encoded JSON (Railway/Secret Manager format) and raw JSON (local dev convenience), so you can paste the secret as-is.

Drive scope used: `https://www.googleapis.com/auth/drive.readonly` — this MCP only reads. All calls pass `supportsAllDrives=True` / `includeItemsFromAllDrives=True`.

## Railway deployment

1. New Railway service → connect this repo, set root directory to `clip-editor`.
2. Railway picks up the `Dockerfile` automatically.
3. Set env vars from `.env.example`. `PORT` is set by Railway — don't override.
4. Set `DOWNLOAD_BASE_URL` to the public Railway URL once it's assigned (re-deploy after).

## Connecting from Claude.ai

Claude.ai Settings → Connectors → Add custom MCP server. Use the public Railway URL with the MCP path (e.g. `https://clip-editor.up.railway.app/mcp`). The four tools appear in Claude's available tools.

No auth is configured between Claude.ai and this server by default. If you want a bearer token check, wrap the MCP mount with Starlette middleware that inspects the `Authorization` header.

## Editing model

- **Padding**: when `pad=true` (default), each kept range is extended by 150ms before the start and 250ms after the end, clamped to file bounds. Reduces choppy cuts.
- **Re-encoding**: every cut re-encodes with `libx264 -preset fast -crf 23` + AAC 128k. Stream-copy is faster but only frame-accurate at I-frames — re-encoding makes cuts land exactly where the transcript says.
- **Concat**: when keeping more than one range, parts are concatenated via the FFmpeg concat demuxer with `-c copy` (all parts share encoding params).

## Limits

- Source mp4s over **500 MB** are rejected before download. Trim the source first.
- Rendered files live under `TEMP_DIR` (default `/tmp/clip-editor`) for `DOWNLOAD_TTL_HOURS` (default 24) before a background loop deletes them.

## Error surfaces

| Failure | What Claude sees |
|---|---|
| Drive file not found / no access | `DriveError` with the file ID |
| Source mp4 over 500 MB | `ValueError` with the actual size, suggesting trim |
| Transcript parse fails | `ValueError` with the first 500 chars of the file |
| FFmpeg crashes | `FFmpegError` with stderr captured |

## Out of scope

- Transcription (cal.com produces transcripts already).
- Searching the Drive corpus (that's the `student-highlights` Claude skill + Google Drive MCP).
- Captions, titles, graphics.
- Uploading the result back to Drive.
