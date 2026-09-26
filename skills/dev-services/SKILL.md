---
name: dev-services
description: >-
  Manage long-running dev processes (dev servers, databases, tunnels,
  watchers) through the machine-global `svc` manager instead of starting
  them directly. Use whenever asked to start/stop/restart a dev server,
  DB, or any long-running process; when a port is already in use; when
  checking whether a server is running; or before running anything that
  would outlive this session. Works across git worktrees — each worktree
  gets its own instance and port. Read it on machines WITHOUT svc too
  (macOS, CI, a box where install.sh never ran): its first section is the
  no-svc fallback, which sends you to the repo's own documented procedure
  and says what to report about a process nothing will be tracking.
---

# dev-services (svc)

Long-running processes are owned by the systemd user manager via `svc`,
never by your session. Processes you start directly die or dangle when the
session ends; `svc`-managed ones are shared with every other session,
worktree, and the human user.

## Step 0 — is this session reading the CURRENT skill text?

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/plugin-freshness.sh" "${CLAUDE_PLUGIN_ROOT}"
```

Local, no network, and **silent** unless something is wrong. Exit **3** — this session is serving
an older cached copy of THIS plugin than the one installed; a session pins its version at the first
call and never moves, so a mid-session `claude plugin update` never reaches it. Put it to the user
(`AskUserQuestion`): reload (`/reload-plugins`) and re-run, or carry on knowingly. Asks once per
plugin per session. Exit **2** — could not determine, which is not a pass. Exit **4** — this call
is wired wrong and checked nothing; report it.

Skipped automatically when this skill is not running from a plugin install (a dev symlink or a
vendored copy), so it costs nothing in those modes.

It runs BEFORE the no-svc check below, not after, and the section below is why: that fallback was
added to this skill in a later version than some sessions are serving. A stale copy would send a
Mac user straight into rules that forbid the only thing that works there — which is precisely the
class of miss this step exists to catch.

## First: is svc even here?

```bash
command -v svc
```

**Nothing? Then this skill does not apply — stop reading at the end of this
section.** svc is Linux-only (it is a thin layer over the systemd user
instance) and there is no systemd on macOS, so on a Mac, on a CI runner, or
on any box where nobody ran `install.sh`, the rules below are not in force
and following them leaves you unable to start anything at all.

What to do instead, in order:

1. **Follow the repo's own documented procedure** if it has one — a CLAUDE.md
   / AGENTS.md "running the app" section, a README, a `make dev`. That prose
   usually carries traps a service definition cannot (rebuild flags, health
   polls, env switches that decide whether mocks are on). Prefer it over
   anything you would invent.
2. **Containers**: `docker compose up` as that procedure says.
3. **Bare processes** (a dev server, a watcher): start one normally, in the
   background. This is explicitly ALLOWED here — the ban below is a rule
   about svc-managed hosts, not a rule about backgrounding.
4. **Say what you started, because nothing else will.** Without svc there is
   no registry, so the next session — or you, tomorrow — cannot tell whose
   process owns a port. Report the URL, the **port**, the **working
   directory**, and the **commit** it is serving, and say that it will
   outlive this session and how to stop it (`kill <pid>`).

That last point is the whole cost of not having svc. Pay it explicitly
rather than leaving a process nobody can identify.

## Hard rules (only where `svc` exists — see above)

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
- `svc: command not found`, or `systemctl failed (needs a systemd user
  instance; Linux only)` → you are on a host svc does not serve. Do not try
  to install it to get past this: go to the no-svc fallback at the top.
  `install.sh` refuses on such a host by design and says so.
- A repo has `services.toml` but you are on a Mac → the file still tells you
  the COMMAND for each service; run it by hand. It is a declaration, not a
  wrapper, so nothing in it needs svc to be readable.

---
To change this skill, do not edit the plugin cache copy: change it in this repository, push, then bump its entry in `mzvonar/claude-skills-public`. See `/dev-tools:update-skill`.
