#!/usr/bin/env bash
# plugin-freshness.sh — is the skill text THIS SESSION is serving the current one?
#
#   plugin-freshness.sh <skill-or-plugin-dir> [--quiet] [--json] [--verbose]
#
# PASS THE DIRECTORY. `${CLAUDE_PLUGIN_ROOT}` is a token Claude Code substitutes into SKILL.md
# TEXT when a skill loads; it is NOT an exported environment variable (measured: fifteen CLAUDE_*
# vars reach a Bash call and that is not one of them). A skill therefore calls:
#
#   bash "${CLAUDE_PLUGIN_ROOT}/scripts/plugin-freshness.sh" "${CLAUDE_PLUGIN_ROOT}"
#
# The env var is still honoured if it happens to be set, but nothing may rely on it: this usage
# block used to read "so a skill calls it with no arguments", and thirteen skills wired to that
# sentence did nothing at all while looking installed.
#
# WHY THIS EXISTS, and why check-drift.sh does not cover it.
#
# check-drift.sh compares the INSTALL RECORD against the marketplace CATALOG: "is what is
# installed behind what is published". That is one of three versions in play, and it is blind
# to the one that actually bites.
#
#   catalog    what the marketplace publishes
#   installed  what `claude plugin update` last wrote into the install record
#   loaded     the cache directory THIS SESSION resolved when it first loaded the skill
#
# `loaded` is pinned at session start and never moves again. The cache keeps every version
# side by side (nine directories for one plugin, measured), so a long session goes on serving
# the text it booted with while an update writes a new directory beside it and the record
# points at that one instead. Nothing reports this: the update prints success, check-drift
# prints "current", and the session keeps reading the old file.
#
# Measured 2026-09-25: a session served refdiff 1.4.0 for its entire length while the install
# records read 1.6.0 and 1.6.1. The two rules that session most needed — a comp's frames FORK,
# and re-run every pair after a shared-component fix — landed in 1.6.1 and were absent from
# the 1.4.0 text it was reading. It followed them only because a human had restated them in a
# handoff. The tell was one line nobody looks at: the "Base directory for this skill" the Skill
# tool prints on invocation.
#
# Exit: 0 = current (and SILENT — nothing to say) · 3 = ask the user (a version is behind) ·
#       2 = could not determine · 4 = the CALLER is wired wrong (see below).
#
# 4 is separate from 2 on purpose. "I could not read the record" and "you called me wrong" both
# mean no answer, but only one of them is the caller's bug — and folding them together is exactly
# how the first rollout of this check did nothing for thirteen skills while every one of them
# documented the result as "undetermined, carry on". A wiring bug must be loud.
# 3 rather than 1 on purpose: the skill still MEASURES correctly, it may simply not know a
# newer rule, so "carry on" stays a legitimate answer and the only thing ruled out is settling
# it silently. That is the same contract refdiff's preflight uses for `action = ask`.
#
# 2 IS NOT A PASS and a caller must not treat it as one. It means this run could not answer —
# the record was absent, malformed, or named no version for this plugin — so the session may be
# serving stale text and nobody knows. Until 2026-09-26 every one of those inputs printed
# "CURRENT" and exited 0 instead, which is the same silence-reads-as-clean failure the script
# was written to catch, inside the script itself. Both are now distinguishable, both are tested.
set -uo pipefail

DIR="" ; QUIET=0 ; JSON=0 ; VERBOSE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --quiet)   QUIET=1; shift ;;
    --json)    JSON=1; shift ;;
    --verbose) VERBOSE=1; shift ;;
    -h|--help) sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    # 4, not 2: a flag this script does not know is the CALLER being wrong, and every skill block
    # documents 2 as "could not determine — carry on". A typo'd flag used to produce a gate that
    # never fired and never complained.
    -*) echo "plugin-freshness: WIRING BUG — unknown option $1" >&2; exit 4 ;;
    *) DIR="$1"; shift ;;
  esac
done

# BROKEN WIRING IS NOT "COULD NOT DETERMINE", and collapsing the two is how the first version of
# this check managed to do nothing at all for thirteen skills. `${CLAUDE_PLUGIN_ROOT}` is a token
# Claude Code substitutes into SKILL.md TEXT when a skill loads; it is NOT an exported environment
# variable (measured: 15 CLAUDE_* vars reach a Bash call and that is not one of them). So a caller
# that passes no argument, or that passes the placeholder unexpanded, has a wiring bug — and must
# hear about it, because the alternative is a gate that silently never fires.
case "${DIR:-}" in
  *'${CLAUDE_PLUGIN_ROOT}'*|*'$CLAUDE_PLUGIN_ROOT'*)
    echo "plugin-freshness: WIRING BUG — the caller passed the literal placeholder '$DIR'." >&2
    echo "  \${CLAUDE_PLUGIN_ROOT} is substituted into SKILL.md text when a skill loads." >&2
    echo "  If you see it unexpanded, pass the skill's own base directory instead." >&2
    exit 4 ;;
esac
DIR="${DIR:-${CLAUDE_PLUGIN_ROOT:-}}"
if [ -z "$DIR" ]; then
  echo "plugin-freshness: WIRING BUG — no directory argument, and CLAUDE_PLUGIN_ROOT is not set" >&2
  echo "  in this process (it is a SKILL.md text substitution, not an exported variable)." >&2
  echo "  Call it as: bash …/scripts/plugin-freshness.sh \"\${CLAUDE_PLUGIN_ROOT}\"" >&2
  exit 4
fi
# Keep the ORIGINAL in a second variable: a failed command substitution assigns "" to DIR before
# the `||` body runs, so the error used to name an empty path.
DIR_IN="$DIR"
DIR="$(cd "$DIR" 2>/dev/null && pwd)" || {
  echo "plugin-freshness: WIRING BUG — no such directory: $DIR_IN" >&2; exit 4; }

# Facts print when there is something to say, or on --verbose. A clean run is SILENT: the old
# shape printed five fact lines on every success while thirteen skills documented it as "silent
# when current", which is a claim the code contradicted on every single invocation.
FACTS=""
fact() { FACTS="${FACTS}$1"$'\n'; }
say() { [ "$QUIET" = 1 ] || printf '%s\n' "$*"; }
flush_facts() { [ "$QUIET" = 1 ] || printf '%s' "$FACTS"; }

# ---- parse …/plugins/cache/<marketplace>/<plugin>/<version>/… ---------------
# Anything not under a plugin cache is a dev symlink or a vendored copy; those have their own
# freshness story (the owning skill's preflight) and are not this script's business.
case "$DIR" in
  */plugins/cache/*) ;;
  *) [ "$VERBOSE" = 1 ] && say "  skipped: not a plugin install ($DIR)"; exit 0 ;;
esac
# `##`, not `#`: a path containing `/plugins/cache/` twice belongs to the INNERMOST one.
REST="${DIR##*/plugins/cache/}"
# Require three components BEFORE splitting. `${REST#*/}` returns the string unchanged when there
# is no `/`, so an emptiness test can never fail here — the guard that used to sit below this was
# unreachable, and `…/plugins/cache/csp` parsed as marketplace=csp, plugin=csp, version=csp, then
# reported CURRENT with `loaded = csp` on screen. Silence reading as clean, in the script whose
# whole job is to stop exactly that.
case "$REST" in
  */*/*) ;;
  *) echo "plugin-freshness: not a <marketplace>/<plugin>/<version> path: $DIR" >&2; exit 2 ;;
esac
MP="${REST%%/*}"        ; REST="${REST#*/}"
PLUGIN="${REST%%/*}"    ; REST="${REST#*/}"
LOADED="${REST%%/*}"

PLUGROOT="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins"

# Name this cause SEPARATELY, because it is the likeliest one on a machine that is not this one.
# Both readers below go through python3 and swallow their own failure, so without this an agent on
# a Mac with no Command Line Tools — or a slim container — is told the INSTALL RECORD is absent,
# unreadable or malformed and sent to inspect a file that is perfectly fine, on every invocation
# of every wired skill. `dev-services` explicitly targets such hosts.
if ! command -v python3 >/dev/null 2>&1; then
  say "  plugin             = $PLUGIN@$MP"
  say "  loaded             = $LOADED"
  say ""
  say "UNKNOWN: python3 is not on PATH, and both version readers need it — freshness UNVERIFIED for $PLUGIN@$MP. This is NOT a pass: the session may be serving stale text and this run cannot tell. The install record itself is fine; do not go looking at it."
  [ "$JSON" = 1 ] && printf '{"plugin":"%s","marketplace":"%s","loaded":"%s","installed":"","catalog":"","action":"unknown"}\n' "$PLUGIN" "$MP" "$LOADED"
  exit 2
fi

# ---- installed: the highest version recorded for this plugin, any scope -----
# Several records are normal (a plugin installed at project AND local scope, as measured), and
# they can disagree, so take the highest — that is the one an update just wrote.
INSTALLED="$(python3 - "$PLUGROOT/installed_plugins.json" "$PLUGIN@$MP" <<'PY' 2>/dev/null || true
import json, sys
try:
    d = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    sys.exit(0)
recs = (d.get("plugins") or {}).get(sys.argv[2]) or []
if isinstance(recs, dict):
    recs = [recs]
vs = [r.get("version") for r in recs if isinstance(r, dict) and r.get("version")]
print(max(vs, key=lambda v: [int(x) for x in v.split(".") if x.isdigit()]) if vs else "")
PY
)"

# ---- catalog: what the marketplace publishes -------------------------------
MP_DIR="$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1],encoding="utf-8")); print(d.get(sys.argv[2],{}).get("installLocation",""))' \
  "$PLUGROOT/known_marketplaces.json" "$MP" 2>/dev/null || true)"
[ -n "$MP_DIR" ] || MP_DIR="$PLUGROOT/marketplaces/$MP"
CATALOG="$(python3 - "$MP_DIR/.claude-plugin/marketplace.json" "$PLUGIN" <<'PY' 2>/dev/null || true
import json, sys
try:
    d = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    sys.exit(0)
for p in d.get("plugins") or []:
    if p.get("name") == sys.argv[2]:
        print(p.get("version") or "")
        break
PY
)"

behind() { # behind A B  → true when A < B
  [ -n "$1" ] && [ -n "$2" ] && [ "$1" != "$2" ] && \
    [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | head -1)" = "$1" ]
}

fact "  plugin             = $PLUGIN@$MP"
fact "  loaded             = $LOADED"
fact "  installed          = ${INSTALLED:-unknown}"
fact "  catalog            = ${CATALOG:-unknown}"

# UNKNOWN IS NOT CURRENT, and conflating them is the whole bug class this script exists for.
# Both readers end in `|| true`, so a missing config dir, a corrupt record, a renamed marketplace
# or no python3 on PATH all yield an empty INSTALLED — and an empty operand makes `behind` false,
# which used to fall through to "CURRENT" and exit 0. Measured before this guard: a corrupt
# installed_plugins.json with the catalog reading 1.7.0 and the session serving 1.4.0 printed
# `action = proceed` / `CURRENT`, and refdiff's preflight turned that into the affirmative
# `current (serving 1.4.0)` — a claim neither of them had measured.
#
# INSTALLED is the one that must be known: it is the comparison the session-skew check rests on.
# A missing CATALOG only costs the second, weaker arm, so it degrades to a warning rather than
# taking the whole answer down with it.
ACTION="proceed"; MSG=""
if [ -z "${INSTALLED:-}" ]; then
  ACTION="unknown"
  MSG="Could not read an installed version for $PLUGIN@$MP from $PLUGROOT/installed_plugins.json (absent, unreadable, malformed, or no record for this plugin). This is NOT a pass: the session may be serving stale text and this run cannot tell. Treat it as undetermined."
elif behind "$LOADED" "$INSTALLED"; then
  ACTION="ask"
  MSG="This session is SERVING $PLUGIN $LOADED while $INSTALLED is installed. The version a session loads is pinned when it first runs the skill and never moves, so an update made during the session does not reach it. PUT IT TO THE USER: reload (/reload-plugins, or restart) and re-run, so the newer text is the one being followed -- or carry on with $LOADED, which still works and may simply not know a newer rule."
elif [ -n "${CATALOG:-}" ] && behind "$INSTALLED" "$CATALOG"; then
  ACTION="ask"
  MSG="$PLUGIN $INSTALLED is installed while the catalog publishes $CATALOG. PUT IT TO THE USER: update (claude plugin update $PLUGIN@$MP, or scripts/check-drift.sh --update) then reload -- or carry on."
fi

# THIS SCRIPT IS STATELESS, DELIBERATELY. It reports what it measures, every time, and never
# remembers that it reported it.
#
# A "once per session" acknowledgement lived here between 2026-09-26 and the same day's review, as
# a stamp file under TMPDIR keyed on session+plugin+version. It was removed with four independent
# defects, all of which followed from ONE mistake: a stamp records that A CALL HAPPENED, while the
# thing worth remembering is THAT THE USER WAS ASKED — and a script cannot observe that.
#
#   - The extra `asked-already` action was not `ask`, so refdiff's preflight fell through to
#     `current (serving <stale>)` — an affirmative claim about a session it had just measured as
#     stale. Exactly the regression the commit before it existed to remove.
#   - Only the ask path was stamped, so a PERSISTENT cause (no python3, a corrupt record) still
#     repeated on every invocation. It covered the transient case and left the persistent one.
#   - The key omitted WHICH arm fired, so after "update, then reload" the follow-up "you are now
#     serving stale text" was suppressed — in the one workflow that reliably causes it.
#   - Subagents inherit the parent's CLAUDE_CODE_SESSION_ID and have no way to ask the user, so a
#     subagent consumed the single ask and the user was never asked at all.
#
# The cascade it was meant to stop is real, and is handled where the knowledge actually lives: the
# calling skill's step 0 tells the agent to note a repeat and carry on. The agent knows whether it
# has already put the question this session; this script never can.
fact "  action             = $ACTION"

if [ "$JSON" = 1 ]; then
  printf '{"plugin":"%s","marketplace":"%s","loaded":"%s","installed":"%s","catalog":"%s","action":"%s"}\n' \
    "$PLUGIN" "$MP" "$LOADED" "${INSTALLED:-}" "${CATALOG:-}" "$ACTION"
fi

case "$ACTION" in
  ask)
    flush_facts
    say ""
    say "ASK: $MSG"
    exit 3 ;;
  unknown)
    flush_facts
    say ""
    say "UNKNOWN: $MSG"
    exit 2 ;;
esac
# A DEGRADED RUN IS NEVER SILENT, even on the clean path. This note used to sit inside the
# --verbose guard, which made "the catalog could not be read, so only half the check ran"
# byte-for-byte identical to "both checks passed" — an absent check reading as a passing one, the
# failure this whole script exists to remove, reproduced in its own success path.
[ -n "${CATALOG:-}" ] || say "  note: catalog version unknown — only the session-vs-installed check ran"
if [ "$VERBOSE" = 1 ]; then
  flush_facts
  say "CURRENT"
fi
exit 0
