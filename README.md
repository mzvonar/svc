# svc

Worktree-aware manager for long-running dev processes (dev servers, DBs,
tunnels), built as a thin layer over the **systemd user instance** — the
daemon that already runs on every Linux box. Made for machines where several
Claude Code / agent sessions and a human share the same repos and worktrees.

- **One global registry.** `svc status` shows every managed process on the
  machine, whichever session or worktree started it. Sessions reuse instead
  of re-spawning; nothing dies with a session.
- **Definitions in the repo.** `services.toml` at the repo root declares
  `dev-server`, `db`, `tunnel`, … Committed, shared, per-repo.
- **Worktrees are first-class.** Each worktree of a repo gets its own unit
  (`svc-dev-server@repo-branch-ab12`) and a stable, distinct `$PORT`
  (`port_base` + allocation). `singleton = true` opts a service out.
- **Ad-hoc with an owner.** `svc run -- cmd` tags the unit with the starting
  (Claude) process's PID + start-time; a 1-minute gc timer reaps units whose
  owner is gone, and anything whose worktree was deleted.
- **Humans included.** Same CLI, plus a tiny web UI on `127.0.0.1:9099` —
  socket-activated, so it costs zero resources until opened and exits when
  idle. Remote: `ssh -L 9099:127.0.0.1:9099 <host>`.
- **Nothing dangles.** systemd cgroups: stopping a unit kills the whole
  process tree, double-forked daemons included. Idle footprint of svc
  itself: zero (no daemon of its own).

## Install (Linux)

```bash
git clone <this repo> ~/Development/svc
~/Development/svc/install.sh      # symlinks CLI, enables gc timer + web socket, enables linger
```

Requires: systemd ≥ 246 (user instance), python3, git. macOS is not
supported (no systemd) — use docker compose / pm2 there.

## Use

```bash
svc status                # what's running, machine-wide
svc up                    # start everything in ./services.toml
svc up dev-server         # or one service; idempotent; prints PORT
svc logs dev-server -f
svc restart dev-server
svc down                  # stop this worktree's processes
svc run --name tun -- npx localtunnel --port 3000   # ad-hoc, auto-reaped
```

`services.toml`:

```toml
[dev-server]
cmd = "npm run dev"        # receives PORT / SVC_PORT
port_base = 3000

[db]
compose = "docker/docker-compose.yml"   # passthrough to docker compose

[tunnel]
cmd = "cloudflared tunnel run dev"
singleton = true           # one per machine
```

## Claude Code integration

`skill/` contains a `dev-services` skill teaching sessions to check
`svc status` first and never start long-running processes directly.
Symlink it into your profile:

```bash
ln -s ~/Development/svc/skill ~/.claude/skills/dev-services
```

## Layout

```
bin/svc            CLI (python3, stdlib only)
web/svc-web.py     web UI (stdlib only, socket-activated)
units/             svc-gc.{service,timer}, svc-web.{socket,service}
skill/SKILL.md     Claude Code skill
install.sh
```

State lives in `~/.local/state/svc/ports.json` (port allocations);
everything else is queried live from systemd. Logs via
`journalctl --user -u 'svc-*'`.
