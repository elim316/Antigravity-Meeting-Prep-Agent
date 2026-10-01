#!/usr/bin/env python3
"""Build a TEST-MODE prompt from a real sidecar.json.

The point of this harness is that we test the *shipping* prompt, not a
hand-written copy of it. This script reads the automation's `sidecar.json`,
substitutes user/domain placeholders from `fixtures/world.json`, swaps every
Workspace tool command for the mock shim, prepends a short TEST MODE preamble,
and prints the result.

    python3 build_test_prompt.py --scenario next_day_mixed
    python3 build_test_prompt.py --scenario t1h_window --print-cmd
"""

import argparse
import json
import pathlib
import shlex
import sys

ROOT = pathlib.Path(__file__).resolve().parent
REPO_SIDECARS = ROOT.parent / "sidecars"
FALLBACK_SIDECARS = ROOT.parent.parent  # when placed inside ~/.gemini/config/sidecars/<id>/test
MOCK = ROOT / "mock_tool.py"
WORLD = ROOT / "fixtures" / "world.json"


def resolve_sidecar(name):
    candidate = REPO_SIDECARS / name / "sidecar.json"
    if candidate.exists():
        return candidate
    return FALLBACK_SIDECARS / name / "sidecar.json"


REAL_TO_MOCK = {
    "workspace-calendar": "gcalendar",
    "workspace-gmail": "gmail",
    "workspace-gdocs": "gdocs",
    "workspace-gdrive": "gdrive",
    "workspace-search": "csa_cli",
    "workspace-people": "people",
}

HEADER = """=== TEST MODE - SYNTHETIC DATA, NO REAL SIDE EFFECTS ===
You are running the Meeting Prep automation against a MOCK environment for a
demo/test run. Everything below is fake data; no real calendar, mailbox or
Drive is touched.

Rules for this run, which override the corresponding instructions further down:

1. Every Workspace command in the task below has already been rewritten to the
   mock shim (`mock_tool.py`). Execute those commands directly via bash.
2. Resolve every attendee directory entry with:
     {people_cmd} <email> [<email> ...]
   It returns name, title, team and company, or "NOT FOUND". Treat my own team
   as the team returned for {me}; anyone on a different team is
   cross-functional.
3. Write scratch markdown to /tmp as instructed. The mock gmail and gdocs
   commands capture their output to {outbox} instead of sending or creating
   anything real.
4. Do not ask me any questions and do not stop for confirmation - run the whole
   task autonomously, exactly as the unattended automation would.
5. Every path this task needs (/tmp and the mock outbox) is writable from inside
   the normal sandbox. Never retry a command with BypassSandbox - an unattended
   automation cannot get that approved, so it would hang forever. If a command
   genuinely fails, report the failure instead of escalating.
6. When you are completely finished, print a final line of the form:
     RESULT: <emails sent> email(s), <docs created> doc(s), <meetings> qualifying meeting(s)

=== END TEST MODE PREAMBLE - THE REAL AUTOMATION TASK FOLLOWS ===

"""


def load_prompt(sidecar_path):
    cfg = json.loads(pathlib.Path(sidecar_path).read_text())
    args = cfg["args"]
    if "--" not in args:
        sys.exit(f"{sidecar_path}: no '--' end-of-flags marker in args")
    return args[args.index("--") + 1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--outbox", default=None)
    ap.add_argument("--now", default=None, help="ISO override for 'now'")
    ap.add_argument(
        "--print-cmd",
        action="store_true",
        help="print an agentapi invocation instead of the bare prompt",
    )
    a = ap.parse_args()

    world = json.loads(WORLD.read_text())
    if a.scenario not in world["scenarios"]:
        sys.exit(
            f"unknown scenario {a.scenario!r}; known: "
            + ", ".join(sorted(world["scenarios"]))
        )
    sc = world["scenarios"][a.scenario]
    sidecar = resolve_sidecar(sc["automation"])
    outbox = pathlib.Path(a.outbox or f"/tmp/meeting_prep_outbox/{a.scenario}")

    env = f"MP_SCENARIO={a.scenario} MP_OUTBOX={outbox}"
    if a.now:
        env += f" MP_NOW={a.now}"
    prefix = f"{env} python3 {MOCK}"

    me = world["me"]
    domain = me.split("@", 1)[1] if "@" in me else "company.example.com"
    tz = world.get("timezone", "Asia/Singapore")

    prompt = load_prompt(sidecar)
    prompt = (
        prompt.replace("__USER_EMAIL__", me)
        .replace("__USER_DOMAIN__", domain)
        .replace("__USER_TIMEZONE__", tz)
    )
    for real, tool in REAL_TO_MOCK.items():
        prompt = prompt.replace(real, f"{prefix} {tool}")

    header = HEADER.format(
        people_cmd=f"{prefix} people",
        me=me,
        outbox=outbox,
    )
    full = header + prompt

    if a.print_cmd:
        print(f"agentapi new-conversation -- {shlex.quote(full)}")
    else:
        print(full)


if __name__ == "__main__":
    main()
