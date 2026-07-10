---
name: clip-editor-local
description: Ensures the local clip-editor video engine is installed, configured, and up to date on this machine, then hands off to video-production for the actual edit. Runs the one-time setup FOR the user if it hasn't been done. Use this FIRST whenever someone asks to edit / cut / stitch / caption / render a video and the clip-editor tools aren't already available, when a clip-editor tool errors with a connection or auth problem, or when someone new is getting set up ("set me up", "install the video editor", "get me running"). This is the preflight for running clip-editor on your own machine instead of the server.
---

# clip-editor-local — self-healing local setup

The clip-editor engine (ffmpeg-based) runs **locally** on this machine as a Claude
Code MCP server, so there is no cloud server to run out of memory. This skill makes
sure that local engine is present, configured, and current — running the installer
for the user when needed — then hands the actual editing to **video-production**.

## Step 0 — Are the tools already available?

If you can see `clip-editor` tools (e.g. `clip-editor:list_clips`,
`clip-editor:stitch_clips`) in this session, the engine is installed and loaded.
**Skip to "Keep it current" below, then proceed with the edit** (use the
`video-production` / `creative-director` skills). Do not reinstall.

If the `clip-editor` tools are **not** present, continue.

## Step 1 — Locate the repo

Find the checkout of the `emerge-mcps` repo (it contains `install.sh` and the
`clip-editor/` engine). Look in this order:

1. Read `~/.emerge/clip-editor.installed` — if it exists, use its `repo_dir=` value.
2. Otherwise check common spots: the current working directory, `~/emerge-mcps`,
   `~/code/emerge-mcps`, `~/conductor/**/emerge-mcps`.
3. Confirm the directory has both `install.sh` and `clip-editor/server.py`.

**If you can't find it**, the machine hasn't been bootstrapped yet. Tell the user to
run this one command in a terminal, then come back:

```
git clone https://github.com/doug-dahl/emerge-mcps.git ~/emerge-mcps && bash ~/emerge-mcps/install.sh
```

Stop here until they've done that.

## Step 2 — Run the installer

Run it with the Bash tool (it is **idempotent** — safe to run every time):

```
bash <repo_dir>/install.sh
```

It installs the Python engine + dependencies, checks ffmpeg, writes config to
`~/.emerge/clip-editor.env`, registers the local `clip-editor` MCP with Claude
Code, and installs the skills. Read its output and relay anything important.

## Step 3 — Two things that may need the user

- **Service-account key.** If the installer says `DRIVE_SA_KEY` is missing, the
  engine can't reach the Student Interviews drive yet. Tell the user to open
  `~/.emerge/clip-editor.env` and paste the key into `DRIVE_SA_KEY=` (base64 or raw
  JSON; a teammate has it, or `gcloud secrets versions access latest --secret=DRIVE_SA_KEY`).
- **Restart to load the tools.** An MCP server that was *just* registered only
  becomes available after Claude Code reloads. If the installer registered the MCP
  for the first time, tell the user plainly: **"Setup's done — fully quit and
  reopen Claude Code (or run `/mcp`), then ask me to edit the video again."** The
  tools cannot appear mid-session on a first install.

## Keep it current

When the engine is already installed, pull the latest engine + skills before a big
job so everyone runs the same code:

```
git -C <repo_dir> pull --ff-only && bash <repo_dir>/install.sh
```

`install.sh` is idempotent, so re-running it just syncs dependencies and config.
(New engine code takes effect the next time the MCP subprocess starts, i.e. the
next Claude Code session — no restart needed for a normal render.)

## Notes

- **Captions need libass.** If burned-in captions fail, the local ffmpeg lacks
  libass. Fix with `brew install ffmpeg` (the full Homebrew build includes it).
- **Where renders go.** Finished videos are written under `~/Movies/EmergeClips`
  and each tool returns the absolute file path (not a URL) — offer to reveal or
  open it for the user.
- **Do the edit with the right skill.** This skill only gets the engine ready.
  For the actual cut/stitch/caption/branding work, follow `video-production`; for
  choosing clips and shaping a narrative first, `creative-director`.
