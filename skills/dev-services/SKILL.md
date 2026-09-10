---
name: dev-services
description: >-
  Manage long-running dev processes (dev servers, databases, tunnels,
  watchers) through the machine-global `svc` manager instead of starting
  them directly. Use whenever asked to start/stop/restart a dev server,
  DB, or any long-running process; when a port is already in use; when
  checking whether a server is running; or before running anything that
  would outlive this session. Works across git worktrees — each worktree
  gets its own instance and port.
---

# dev-services (svc)

Long-running processes are owned by the systemd user manager via `svc`,
never by your session. Processes you start directly die or dangle when the
session ends; `svc`-managed ones are shared with every other session,
worktree, and the human user.

**Only on Linux hosts with `svc` installed** (`command -v svc`). If absent
(e.g. macOS), fall back to docker compose for containers and tell the user
long-running bare processes can't be shared.

## Hard rules

- Never run dev servers / DBs / tunnels directly (`npm run dev &`,
  `docker run`, `nohup ...`). Never use your own background-bash for
  anything that should outlive the session.
- Before starting anything: `svc status` — it is usually already running.
  Reuse it; read its port from the PORT column.
- Never kill PIDs of managed processes. Use `svc stop` / `svc restart`.
- Do not stop services in OTHER worktrees/instances unless asked — they
  belong to other sessions.

## Commands

```
svc status [--json]      # everything on this machine, all worktrees
svc up [name]            # start service(s) defined in ./services.toml (idempotent)
svc restart <name>       # after changing config/env of a service
svc stop <name>          # bare name = this worktree's instance
svc down                 # stop everything of THIS worktree
svc logs <name> [-n 500] # journalctl underneath; add -f only if user asks
svc run --name x -- cmd  # tracked ad-hoc process (see below)
svc config               # parsed services.toml + this worktree's slug/ports
svc web                  # URL of the human web UI
```

## Services (services.toml at repo root)

```toml
[dev-server]
cmd = "npm run dev"      # started with PORT env set per worktree
port_base = 3000         # worktree gets 3000, next worktree 3001, ...

[db]
compose = "docker/docker-compose.yml"  # passthrough to docker compose

[tunnel]
cmd = "cloudflared tunnel run dev"
singleton = true         # one per machine, shared by all worktrees
```

The dev server must read its port from `$PORT`. Each worktree of the same
repo gets a distinct instance (`svc status` column INSTANCE) and a stable
distinct port. If a repo has no `services.toml` and needs one, propose it.

## Ad-hoc processes

For a long-running process not worth a services.toml entry (temporary
tunnel, one-off watcher):

```
svc run --name my-probe -- <command...>
```

It is tagged with this session's PID and reaped automatically by the gc
timer once the session exits — safe to "forget" it. Do NOT use `svc run`
for short commands (builds, tests); run those normally.

## Troubleshooting

- Service failed → `svc logs <name>`, fix, `svc restart <name>`.
- "port already in use" → someone runs it outside svc; `svc status` +
  `lsof -i :<port>`, report to user instead of killing blindly.
- Stale allocations / removed worktrees are cleaned by `svc gc`
  (runs every minute from a timer; manual run is safe).

---
To change this skill, do not edit the plugin cache copy: change it in this repository, push, then bump its entry in `mzvonar/claude-skills-public`. See `/dev-tools:update-skill`.
