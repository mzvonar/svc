#!/usr/bin/env bash
# Install svc on a Linux machine with a systemd user instance.
# Symlinks the CLI/web from this checkout (git pull = upgrade), copies units,
# enables the gc timer + web socket, and turns on lingering so the user
# manager (and your dev processes) survive logout.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! systemctl --user show-environment >/dev/null 2>&1; then
  echo "error: no systemd user instance reachable — svc is Linux-only." >&2
  echo "       (on macOS keep using docker compose / pm2 directly)" >&2
  exit 1
fi

mkdir -p ~/.local/bin ~/.local/lib/svc ~/.config/systemd/user

ln -sf "$SRC/bin/svc" ~/.local/bin/svc
ln -sf "$SRC/web/svc-web.py" ~/.local/lib/svc/svc-web.py
cp "$SRC"/units/svc-gc.service "$SRC"/units/svc-gc.timer \
   "$SRC"/units/svc-web.socket "$SRC"/units/svc-web.service \
   ~/.config/systemd/user/

systemctl --user daemon-reload
systemctl --user enable --now svc-gc.timer svc-web.socket

# keep the user manager alive when no session is logged in (VM use case)
loginctl enable-linger "$USER" 2>/dev/null || true

case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) echo "note: add ~/.local/bin to PATH" ;;
esac

echo "installed: svc CLI, gc timer (1min), web UI socket on 127.0.0.1:9099"
echo "try: svc --help && svc status"
