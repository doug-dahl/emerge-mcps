#!/usr/bin/env bash
#
# Double-click to install OR update the clip-editor video engine.
# It downloads the latest version each time (only when something changed), then
# runs the setup. You can hand a teammate just this one file.
#
set -uo pipefail

REPO="doug-dahl/emerge-mcps"       # public repo
BRANCH="main"
APP_DIR="$HOME/.emerge/app"        # where the latest code is kept
SHA_FILE="$HOME/.emerge/app.sha"

clear
echo "Setting up clip-editor — checking for the latest version…"
echo
mkdir -p "$HOME/.emerge"

# Latest commit on main (no auth needed for a public repo).
REMOTE_SHA="$(curl -fsSL "https://api.github.com/repos/$REPO/commits/$BRANCH" 2>/dev/null \
  | grep -m1 '"sha"' | sed -E 's/.*"sha": *"([0-9a-f]+)".*/\1/')"

need_download=1
if [ -n "$REMOTE_SHA" ] && [ -f "$SHA_FILE" ] && [ -d "$APP_DIR" ] \
   && [ "$(cat "$SHA_FILE")" = "$REMOTE_SHA" ]; then
  need_download=0
  echo "Already up to date."
fi

if [ "$need_download" = "1" ]; then
  echo "Downloading the latest version…"
  tmp="$(mktemp -d)"
  if curl -fsSL "https://github.com/$REPO/archive/refs/heads/$BRANCH.tar.gz" -o "$tmp/app.tgz" \
     && tar xzf "$tmp/app.tgz" -C "$tmp"; then
    src="$(find "$tmp" -maxdepth 1 -type d -name '*-*' | head -1)"
    if [ -n "$src" ]; then
      rm -rf "$APP_DIR"
      mv "$src" "$APP_DIR"
      [ -n "$REMOTE_SHA" ] && echo "$REMOTE_SHA" > "$SHA_FILE"
      echo "Updated to the latest version."
    fi
  fi
  rm -rf "$tmp"
  if [ ! -f "$APP_DIR/install.sh" ]; then
    echo "Could not download clip-editor. Check your internet connection and try again."
    osascript -e 'display dialog "Could not download clip-editor. Check your internet connection and try again." with title "clip-editor setup" buttons {"OK"} default button 1' >/dev/null 2>&1 || true
    echo "You can close this window."; exit 1
  fi
fi

echo
bash "$APP_DIR/install.sh"
echo
echo "You can close this window."
