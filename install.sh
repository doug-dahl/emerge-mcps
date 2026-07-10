#!/usr/bin/env bash
#
# clip-editor local installer — sets up the video engine to run on this Mac
# (no server, no OOM). Self-contained: downloads a static ffmpeg WITH libass
# directly (no Homebrew, no sudo), sets up Python, registers the local
# clip-editor MCP with Claude Desktop (and Claude Code if present), and prompts
# once for the access key. Idempotent — safe to run again anytime.
#
# Normally launched by double-clicking "Set Up clip-editor.command"; can also be
# run directly:  bash install.sh
#
set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLIP_DIR="$REPO_DIR/clip-editor"
STATE_DIR="$HOME/.emerge"
# venv lives OUTSIDE the code dir so self-updates (which replace the code) don't
# blow it away — the interpreter + packages persist across code updates.
VENV="$STATE_DIR/venv"
BIN_DIR="$STATE_DIR/bin"
ENV_FILE="$STATE_DIR/clip-editor.env"
MARKER="$STATE_DIR/clip-editor.installed"
OUTPUT_DIR="$HOME/Movies/EmergeClips"
SKILLS_SRC="$REPO_DIR/skills"
SKILLS_DST="$HOME/.claude/skills"
DEFAULT_DRIVE_ID="0AIgasummO4n6Uk9PVA"   # Student Interviews shared drive
DESKTOP_CFG="$HOME/Library/Application Support/Claude/claude_desktop_config.json"

say()  { printf '\033[1;36m▸ %s\033[0m\n' "$1"; }
ok()   { printf '\033[1;32m✓ %s\033[0m\n' "$1"; }
warn() { printf '\033[1;33m! %s\033[0m\n' "$1"; }
die()  { printf '\033[1;31m✗ %s\033[0m\n' "$1" >&2; gui "clip-editor setup failed" "$1"; exit 1; }
# Best-effort GUI dialog (works even when double-clicked); no-op if osascript absent.
gui()  { command -v osascript >/dev/null 2>&1 && osascript -e "display dialog \"$2\" with title \"$1\" buttons {\"OK\"} default button 1" >/dev/null 2>&1 || true; }

mkdir -p "$STATE_DIR" "$BIN_DIR" "$OUTPUT_DIR"

# 1. ffmpeg + ffprobe — static builds with libass, downloaded straight to $BIN_DIR
_extract_one() {  # url  outname  workdir
  local url="$1" out="$2" wd="$3"
  curl -fsSL -o "$wd/dl.zip" "$url" || return 1
  ( cd "$wd" && unzip -oq dl.zip ) || return 1
  [ -f "$wd/$out" ] || return 1
  mv -f "$wd/$out" "$BIN_DIR/$out"
  chmod +x "$BIN_DIR/$out"
  xattr -dr com.apple.quarantine "$BIN_DIR/$out" 2>/dev/null || true
  rm -f "$wd/dl.zip"
}

install_ffmpeg() {
  if "$BIN_DIR/ffmpeg" -hide_banner -version >/dev/null 2>&1 \
     && "$BIN_DIR/ffprobe" -hide_banner -version >/dev/null 2>&1; then
    ok "ffmpeg already installed ($BIN_DIR)"
    return 0
  fi
  local arch tmp; arch="$(uname -m)"; tmp="$(mktemp -d)"
  say "Downloading ffmpeg with caption support (one time, ~40 MB)…"
  if [ "$arch" = "arm64" ]; then
    if _extract_one "https://www.osxexperts.net/ffmpeg71arm.zip"  ffmpeg  "$tmp" \
       && _extract_one "https://www.osxexperts.net/ffprobe71arm.zip" ffprobe "$tmp"; then
      :
    else
      warn "Native build unavailable — using Intel build via Rosetta"
      softwareupdate --install-rosetta --agree-to-license >/dev/null 2>&1 || true
      arch="x86_64"
    fi
  fi
  if [ "$arch" = "x86_64" ]; then
    _extract_one "https://evermeet.cx/ffmpeg/getrelease/ffmpeg/zip"  ffmpeg  "$tmp" || die "Could not download ffmpeg."
    _extract_one "https://evermeet.cx/ffmpeg/getrelease/ffprobe/zip" ffprobe "$tmp" || die "Could not download ffprobe."
  fi
  rm -rf "$tmp"
  "$BIN_DIR/ffmpeg" -hide_banner -filters 2>/dev/null | grep -qw subtitles \
    && ok "ffmpeg installed (with libass captions)" \
    || warn "ffmpeg installed but caption filter missing."
}
install_ffmpeg

# 2. Python toolchain via uv — downloads a standalone Python; no system Python,
#    no Xcode, no admin needed.
UV="$(command -v uv || true)"
[ -z "$UV" ] && [ -x "$HOME/.local/bin/uv" ] && UV="$HOME/.local/bin/uv"
if [ -z "$UV" ]; then
  say "Installing the Python toolchain (uv — no admin needed)…"
  curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1 || die "Could not install the Python toolchain (uv)."
  UV="$HOME/.local/bin/uv"
fi
[ -x "$UV" ] || die "Python toolchain not found after install."
ok "Python toolchain ready"

# 3. Python engine (uv downloads a standalone Python the first time)
say "Setting up the video engine (first run downloads Python — a moment)…"
"$UV" venv "$VENV" --python 3.12 >/dev/null 2>&1 || die "Could not create the Python environment."
"$UV" pip install --python "$VENV/bin/python" -q -r "$CLIP_DIR/requirements.txt" || die "Failed to install Python dependencies."
ok "Engine ready"

# 4. Config file (non-secret settings + ffmpeg paths); preserves any existing key
VENV="$VENV" BIN_DIR="$BIN_DIR" ENV_FILE="$ENV_FILE" OUTPUT_DIR="$OUTPUT_DIR" \
DEFAULT_DRIVE_ID="$DEFAULT_DRIVE_ID" "$VENV/bin/python" - <<'PY'
import os
env = os.environ["ENV_FILE"]
cur = {}
if os.path.isfile(env):
    for ln in open(env):
        ln = ln.strip()
        if ln and not ln.startswith("#") and "=" in ln:
            k, _, v = ln.partition("="); cur[k] = v
cur.setdefault("DRIVE_SA_KEY", "")
cur["CLIP_EDITOR_TRANSPORT"] = "stdio"
cur["STUDENT_INTERVIEWS_DRIVE_ID"] = cur.get("STUDENT_INTERVIEWS_DRIVE_ID") or os.environ["DEFAULT_DRIVE_ID"]
cur["TEMP_DIR"] = os.environ["OUTPUT_DIR"]
cur["FFMPEG_BIN"] = os.environ["BIN_DIR"] + "/ffmpeg"
cur["FFPROBE_BIN"] = os.environ["BIN_DIR"] + "/ffprobe"
order = ["CLIP_EDITOR_TRANSPORT","STUDENT_INTERVIEWS_DRIVE_ID","TEMP_DIR","FFMPEG_BIN","FFPROBE_BIN","DRIVE_SA_KEY"]
with open(env, "w") as fh:
    fh.write("# clip-editor local config — keep private (holds the access key).\n")
    for k in order:
        fh.write(f"{k}={cur.get(k,'')}\n")
os.chmod(env, 0o600)
PY

# 5. Access key — prompt once (GUI) if not already set
if ! grep -q '^DRIVE_SA_KEY=.\+' "$ENV_FILE"; then
  KEY=""
  if command -v osascript >/dev/null 2>&1; then
    KEY="$(osascript -e 'text returned of (display dialog "Paste the clip-editor access key (from your teammate):" default answer "" with title "clip-editor setup" with hidden answer)' 2>/dev/null || true)"
  fi
  if [ -n "$KEY" ]; then
    ENV_FILE="$ENV_FILE" KEY="$KEY" "$VENV/bin/python" - <<'PY'
import os
env, key = os.environ["ENV_FILE"], os.environ["KEY"]
lines = [l for l in open(env).read().splitlines() if not l.startswith("DRIVE_SA_KEY=")]
lines.append("DRIVE_SA_KEY=" + key)
open(env, "w").write("\n".join(lines) + "\n"); os.chmod(env, 0o600)
PY
    ok "Access key saved"
  else
    warn "No key entered — paste it into $ENV_FILE later (DRIVE_SA_KEY=)."
    NEEDS_KEY=1
  fi
else
  ok "Access key already set"
fi

# 6. Register the local MCP with whichever Claude app is present
REGISTERED=0
if [ -d "$(dirname "$DESKTOP_CFG")" ]; then
  say "Connecting to Claude Desktop"
  CLIP_DIR="$CLIP_DIR" VENV="$VENV" ENV_FILE="$ENV_FILE" DESKTOP_CFG="$DESKTOP_CFG" \
    "$VENV/bin/python" - <<'PY'
import json, os, shutil
cfg = os.environ["DESKTOP_CFG"]; data = {}
if os.path.isfile(cfg):
    shutil.copy(cfg, cfg + ".bak")
    try: data = json.load(open(cfg))
    except Exception: data = {}
data.setdefault("mcpServers", {})
data["mcpServers"]["clip-editor"] = {
    "command": f"{os.environ['VENV']}/bin/python",
    "args": [f"{os.environ['CLIP_DIR']}/server.py"],
    "env": {"CLIP_EDITOR_TRANSPORT": "stdio", "CLIP_EDITOR_ENV": os.environ["ENV_FILE"]},
}
os.makedirs(os.path.dirname(cfg), exist_ok=True)
json.dump(data, open(cfg, "w"), indent=2)
PY
  ok "Connected to Claude Desktop"
  REGISTERED=1
fi
if command -v claude >/dev/null 2>&1; then
  claude mcp remove clip-editor --scope user >/dev/null 2>&1 || true
  claude mcp add clip-editor --scope user \
    -e CLIP_EDITOR_TRANSPORT=stdio -e "CLIP_EDITOR_ENV=$ENV_FILE" \
    -- "$VENV/bin/python" "$CLIP_DIR/server.py" >/dev/null 2>&1 \
    && ok "Connected to Claude Code" && REGISTERED=1
fi
[ "$REGISTERED" = "0" ] && warn "No Claude app found — install Claude Desktop (https://claude.ai/download) and re-run."

# 7. Skills (Claude Code reads these; harmless otherwise)
if [ -d "$SKILLS_SRC" ]; then
  mkdir -p "$SKILLS_DST"
  for d in "$SKILLS_SRC"/*/; do
    [ -f "${d}SKILL.md" ] || continue
    name="$(basename "$d")"; rm -rf "${SKILLS_DST:?}/$name"; cp -R "$d" "$SKILLS_DST/$name"
  done
fi

# 8. Marker
COMMIT="$(git -C "$REPO_DIR" rev-parse --short HEAD 2>/dev/null || echo unknown)"
printf 'repo_dir=%s\ncommit=%s\nenv_file=%s\noutput_dir=%s\n' \
  "$REPO_DIR" "$COMMIT" "$ENV_FILE" "$OUTPUT_DIR" > "$MARKER"

echo
ok "clip-editor is installed. Videos will save to $OUTPUT_DIR"
MSG="Setup complete! Now fully quit and reopen Claude Desktop, then ask it to make a video."
[ "${NEEDS_KEY:-0}" = "1" ] && MSG="Almost done — paste the access key into ~/.emerge/clip-editor.env, then quit and reopen Claude Desktop."
say "$MSG"
gui "clip-editor is ready" "$MSG"
