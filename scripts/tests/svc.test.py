#!/usr/bin/env python3
"""Falsification for the svc internals whose defects are documented only in comments.

    python3 scripts/tests/svc.test.py

Two of these functions carry long docstrings about bugs that cost real debugging sessions, and
neither had ever been executed by a test:

  - `is_claude_proc` was `"claude" in proc_cmdline(pid)`, which matched an agent's tool-call shell
    because its cmdline contains "claude" inside a PATH. Every service an agent started was then
    owned by an ephemeral shell and `svc gc` was entitled to reap it.
  - `ensure_user_bus` fills in XDG_RUNTIME_DIR, which non-interactive shells never export because
    the usual line in ~/.bashrc sits below the `[ -z "$PS1" ] && return` guard.

`/proc` is read through `proc_stat` / `proc_cmdline`, so the process tree here is SYNTHETIC: the
tests monkeypatch those two readers and assert on the walk, rather than spawning anything.
"""
import importlib.util
import os
import pathlib
import re
import signal
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_loader("svcmod", importlib.machinery.SourceFileLoader("svcmod", str(HERE / "bin" / "svc")))
svc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(svc)

PASS = FAIL = 0


def ok(what):
    global PASS
    PASS += 1
    print(f"  ok   {what}")


def bad(what, detail):
    global FAIL
    FAIL += 1
    print(f"  FAIL {what}\n     {detail}")


def check(what, got, want):
    ok(what) if got == want else bad(what, f"got {got!r}, want {want!r}")


def fake_tree(procs):
    """procs: {pid: (ppid, cmdline, starttime)} — install it as /proc for the module."""
    svc.proc_cmdline = lambda pid: procs.get(pid, (0, "", "0"))[1]
    svc.proc_stat = lambda pid: (
        {"ppid": procs[pid][0], "starttime": procs[pid][2]} if pid in procs else None
    )


print("[ svc self-test ]")

# ---- is_claude_proc ---------------------------------------------------------
# The real cmdline of an agent's tool-call shell, which is what the substring test matched.
SHELL = "/bin/bash -c source /root/.claude/shell-snapshots/snapshot-bash-1.sh && eval 'svc status'"
CASES = [
    ("a real claude process", "/usr/bin/claude --model opus", True),
    ("an agent tool-call shell mentioning claude in a PATH", SHELL, False),
    ("argv0 with a trailing colon (kernel comm form)", "claude: --resume", True),
    ("a bare relative invocation", "claude", True),
    ("a different binary whose name merely contains it", "/usr/bin/claude-code serve", False),
    ("an editor holding a claude file open", "/usr/bin/vim /root/.claude/settings.json", False),
    ("an unreadable or dead pid", "", False),
]
for label, cmdline, want in CASES:
    fake_tree({7: (1, cmdline, "100")})
    check(f"is_claude_proc: {label}", svc.is_claude_proc(7), want)

# ---- find_owner: the walk ---------------------------------------------------
# svc -> tool-call shell -> claude. The owner must be the claude process, whose lifetime is the
# session's; stopping at the shell is the bug that made every agent-started service reapable.
fake_tree({
    500: (400, "python3 /root/.local/bin/svc up dev-server", "500"),
    400: (300, SHELL, "400"),
    300: (200, "/usr/bin/claude --model opus", "300"),
    200: (1, "/lib/systemd/systemd --user", "200"),
})
svc.os.getppid = lambda: 400
check("find_owner: walks PAST the tool-call shell to claude", svc.find_owner(), (300, "300"))

# No claude anywhere: the parent shell is the documented fallback, not pid 1 and not a crash.
fake_tree({
    400: (200, "/bin/bash -i", "400"),
    200: (1, "/lib/systemd/systemd --user", "200"),
})
svc.os.getppid = lambda: 400
check("find_owner: no claude in the ancestry → the parent shell", svc.find_owner(), (400, "400"))

# A truncated ancestry (the parent already gone) must not raise.
fake_tree({})
svc.os.getppid = lambda: 999
check("find_owner: vanished ancestry → parent pid with unknown starttime", svc.find_owner(), (999, "?"))

# A cycle would spin forever in an unbounded walk; the loop is bounded at 64. The guard has to be
# a WALL CLOCK, not an exception handler: replacing `for _ in range(64)` with `while True` raises
# nothing at all, so a try/except leaves the whole suite hanging — which reads as a stuck CI job
# rather than as a failure, and is the one outcome worse than having no row here.
fake_tree({10: (11, "sh", "10"), 11: (10, "sh", "11")})
svc.os.getppid = lambda: 10


def _cycle_timeout(_sig, _frm):
    raise TimeoutError("find_owner did not return within 5s — the walk is unbounded")


signal.signal(signal.SIGALRM, _cycle_timeout)
signal.alarm(5)
try:
    svc.find_owner()
    ok("find_owner: a ppid cycle terminates instead of spinning")
except (RecursionError, TimeoutError) as exc:
    bad("find_owner: a ppid cycle terminates instead of spinning", repr(exc))
finally:
    signal.alarm(0)

# ---- owner_alive: PID REUSE ------------------------------------------------
# The starttime is what distinguishes "my owner is alive" from "something else now holds that pid".
# Without it `svc gc` keeps a unit alive forever behind an unrelated process.
fake_tree({77: (1, "/usr/bin/claude", "12345")})
check("owner_alive: same pid, same starttime → alive", svc.owner_alive(77, "12345"), True)
check("owner_alive: same pid, DIFFERENT starttime → dead (pid reuse)", svc.owner_alive(77, "999"), False)
check("owner_alive: unknown starttime accepts any live pid", svc.owner_alive(77, "?"), True)
fake_tree({})
check("owner_alive: pid gone → dead", svc.owner_alive(77, "12345"), False)

# ---- ensure_user_bus -------------------------------------------------------
# Three cases, and the middle one is why the function exists at all.
saved = os.environ.get("XDG_RUNTIME_DIR")
try:
    os.environ["XDG_RUNTIME_DIR"] = "/run/user/already-set"
    svc.ensure_user_bus()
    check("ensure_user_bus: never second-guesses an env that set it",
          os.environ["XDG_RUNTIME_DIR"], "/run/user/already-set")

    with tempfile.TemporaryDirectory() as td:
        os.environ.pop("XDG_RUNTIME_DIR", None)
        sock = pathlib.Path(td) / "1000"
        sock.mkdir()
        real_getuid, real_isdir = svc.os.getuid, svc.os.path.isdir
        svc.os.getuid = lambda: 1000
        svc.os.path.isdir = lambda p: p == f"/run/user/1000" or real_isdir(p)
        svc.ensure_user_bus()
        check("ensure_user_bus: unset + socket present → filled in",
              os.environ.get("XDG_RUNTIME_DIR"), "/run/user/1000")

        os.environ.pop("XDG_RUNTIME_DIR", None)
        svc.os.path.isdir = lambda p: False
        svc.ensure_user_bus()
        check("ensure_user_bus: unset + NO socket → left unset, a real absence still reports",
              os.environ.get("XDG_RUNTIME_DIR"), None)
        svc.os.getuid, svc.os.path.isdir = real_getuid, real_isdir
finally:
    if saved is None:
        os.environ.pop("XDG_RUNTIME_DIR", None)
    else:
        os.environ["XDG_RUNTIME_DIR"] = saved

# ---- install.sh fills the bus in BEFORE probing ---------------------------
# The probe and the fill-in are one ordered pair: reversed, the installer answers "svc is
# Linux-only" on exactly the non-interactive shell ensure_user_bus was written for.
text = (HERE / "install.sh").read_text(encoding="utf-8")
i_fill = text.find("XDG_RUNTIME_DIR=")
i_probe = text.find("systemctl --user show-environment")
if i_fill == -1:
    bad("install.sh/order", "install.sh sets no XDG_RUNTIME_DIR fallback at all")
else:
    check("install.sh: fills XDG_RUNTIME_DIR BEFORE probing the bus", i_fill < i_probe, True)

# ---- UNIT_RE: adhoc vs plain, and a slug containing a dash ----------------
for unit, want in [
    ("svc-dev-server@uctoinak2-main.service", ("dev-server", "uctoinak2-main", None)),
    ("svc-adhoc-pnpm-test@uctoinak2-main.service", ("pnpm-test", "uctoinak2-main", "adhoc-")),
    ("svc-web.service", None),
    ("sshd.service", None),
]:
    m = svc.UNIT_RE.match(unit)
    got = (m.group("service"), m.group("slug"), m.group("adhoc")) if m else None
    check(f"UNIT_RE: {unit}", got, want)

# ---- the freshness-check WIRING --------------------------------------------
# svc's SKILL.md is the one copy of that block living outside the canonical repo, so the suite that
# pins the other nine (`scripts/tests/plugin-freshness.test.sh` in mzvonar/claude-skills-public)
# cannot see it. The original incident was not a bad path but a block that ran and was ignored, so
# these mirror that suite's invariant rather than merely grepping for the filename: the path
# resolves from a CACHE-shaped root, every invocation line is the canonical one, the exit-code
# contract is still stated, and the block precedes the skill's first real instruction.
CANON = 'bash "${CLAUDE_PLUGIN_ROOT}/scripts/plugin-freshness.sh" "${CLAUDE_PLUGIN_ROOT}"'
for skill_md in sorted(HERE.glob("skills/*/SKILL.md")):
    label = f"wiring: {skill_md.parent.name}"
    body = skill_md.read_text(encoding="utf-8")
    lines = body.splitlines()
    calls = [(n, l) for n, l in enumerate(lines, 1) if "plugin-freshness.sh" in l]
    if not calls:
        bad(label, "no freshness invocation found")
        continue
    problems = []
    for _, line in calls:
        if CANON not in line:
            problems.append(f"non-canonical invocation: {line.strip()}")
            continue
        # `${CLAUDE_PLUGIN_ROOT}` is the plugin root, and in a cache that root IS this checkout's
        # layout — scripts/ directly under it. A `../..` escape resolves in a repo and never in a
        # cache, which is exactly how the reverted rollout passed review.
        rel = line.split('"')[1].replace("${CLAUDE_PLUGIN_ROOT}", str(HERE))
        if not pathlib.Path(rel).is_file():
            problems.append(f"path does not resolve from the plugin root: {rel}")
    for needle, why in [("**3**", "no exit-3 instruction"),
                        ("**4**", "no exit-4 instruction"),
                        ("not a pass", "exit 2 is not described as 'not a pass'"),
                        ("session reading the CURRENT skill text", "heading does not introduce the check")]:
        if needle not in body:
            problems.append(why)
    first_instruction = next((n for n, l in enumerate(lines, 1)
                              if re.match(r"^\s*(#{2,3} )?\*{0,2}1\.", l) or l.startswith("## First")), None)
    if first_instruction and calls[0][0] > first_instruction:
        problems.append(f"block at line {calls[0][0]} comes AFTER the first instruction at {first_instruction}")
    if problems:
        bad(label, "; ".join(problems))
    else:
        ok(f"{label} resolves from the plugin root, is canonical, keeps its exit-code contract, "
           f"and precedes the first instruction")

# The script itself is a COPY of the canonical one. Nothing can enforce that from inside this repo,
# so when a checkout happens to sit beside this one, compare; when it does not, say SKIPPED. An
# absent check and a passing one must not read the same.
sibling = HERE.parent / "claude-skills-public" / "scripts" / "plugin-freshness.sh"
mine = HERE / "scripts" / "plugin-freshness.sh"


def code_only(p):
    """Every line that is not a whole-line comment — i.e. everything that can behave."""
    return [l for l in p.read_text(encoding="utf-8").splitlines() if l.strip() and not l.strip().startswith("#")]


if not sibling.exists():
    ok("copy parity: canonical checkout not beside this one — SKIPPED, not verified")
else:
    # CODE identity, not byte identity. A copy is allowed to add a provenance comment saying where
    # the canonical lives — refdiff's does — and disallowing that would make the honest copy fail
    # while teaching nothing. Every line that can BEHAVE still has to match exactly, so any logic
    # drift between the copies is caught; the rule is just stated where it can be read.
    import difflib
    d = [l for l in difflib.unified_diff(code_only(sibling), code_only(mine),
                                         "canonical", "svc", lineterm="", n=0)
         if l.startswith(("+", "-")) and not l.startswith(("+++", "---"))]
    if d:
        bad("copy parity", f"plugin-freshness.sh differs from the canonical one in {len(d)} "
                           f"executable line(s):\n     " + "\n     ".join(d[:12]))
    else:
        ok("copy parity: plugin-freshness.sh matches the canonical one line for line, comments aside")

print(f"\n  {PASS} passed, {FAIL} failed")
sys.exit(0 if FAIL == 0 else 1)
