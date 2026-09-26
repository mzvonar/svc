#!/usr/bin/env bash
# Install svc on a Linux machine with a systemd user instance.
# Symlinks the CLI/web from this checkout (git pull = upgrade), copies units,
# enables the gc timer + web socket, and turns on lingering so the user
# manager (and your dev processes) survive logout.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# `systemctl --user` finds the manager through XDG_RUNTIME_DIR, and the usual export sits in
# ~/.bashrc BELOW the `[ -z "$PS1" ] && return` guard — so every non-interactive shell (an agent's
# tool call, `ssh host ./install.sh`, cron) arrives without it and the probe below fails with
# "Failed to connect to bus: No medium found", which reads identically to "there is no user
# manager". That is the trap `ensure_user_bus` in bin/svc exists for; the installer probed BEFORE
# filling it in, so it answered "svc is Linux-only" on a Linux box with a running manager. Fill it
# in on the same terms as bin/svc: only a socket that exists, and never over an env that set it.
if [ -z "${XDG_RUNTIME_DIR:-}" ] && [ -d "/run/user/$(id -u)" ]; then
  export XDG_RUNTIME_DIR="/run/user/$(id -u)"
fi

if ! systemctl --user show-environment >/dev/null 2>&1; then
  echo "error: no systemd user instance reachable — svc is Linux-only." >&2
  echo "       (on macOS keep using docker compose / pm2 directly)" >&2
  echo "       XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-<unset>}" >&2
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
