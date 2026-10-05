#!/usr/bin/env python3
"""Build a TEST-MODE prompt from a real sidecar.json.

Reads the automation's `sidecar.json`, substitutes user/domain/timezone
values from `fixtures/world.json` (working identically on both repo templates
and already-personalised live sidecars), swaps every Workspace tool token for
the mock shim (`mock_tool.py`), prepends a TEST MODE preamble, and prints it.
"""

import argparse
import json
import pathlib
import re
import shlex
import sys

ROOT = pathlib.Path(__file__).resolve().parent
REPO_SIDECARS = ROOT.parent / "sidecars"
FALLBACK_SIDECARS = ROOT.parent.parent  # when placed inside ~/.gemini/config/sidecars/<id>/test
MOCK = ROOT / "mock_tool.py"
WORLD = ROOT / "fixtures" / "world.json"

REAL_TO_MOCK = {
    "workspace-calendar": "gcalendar",
    "workspace-gmail": "gmail",
    "workspace-gdocs": "gdocs",
    "workspace-gdrive": "gdrive",
    "workspace-search": "csa_cli",
    "workspace-people": "people",
    "/google/bin/releases/gemini-agents-gcalendar/gcalendar": "gcalendar",
    "/google/bin/releases/gemini-agents-gmail/gmail": "gmail",
    "/google/bin/releases/gemini-agents-gdocs/gdocs": "gdocs",
    "/google/bin/releases/gemini-agents-gdrive/gdrive": "gdrive",
    "/google/bin/releases/csa-cli/csa_cli.par": "csa_cli",
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


def resolve_sidecar(name):
    candidate = REPO_SIDECARS / name / "sidecar.json"
    return candidate if candidate.exists() else FALLBACK_SIDECARS / name / "sidecar.json"


def load_prompt(sidecar_path):
    cfg = json.loads(pathlib.Path(sidecar_path).read_text(encoding="utf-8"))
    args = cfg.get("args", [])
    if "--" not in args:
        sys.exit(f"{sidecar_path}: no '--' end-of-flags marker in args")
    return args[args.index("--") + 1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--outbox", default=None)
    ap.add_argument("--now", default=None, help="ISO override for 'now'")
    ap.add_argument("--print-cmd", action="store_true", help="print an agentapi invocation")
    a = ap.parse_args()

    world = json.loads(WORLD.read_text(encoding="utf-8"))
    if a.scenario not in world["scenarios"]:
        sys.exit(f"unknown scenario {a.scenario!r}; known: {', '.join(sorted(world['scenarios']))}")
    sc = world["scenarios"][a.scenario]
    outbox = pathlib.Path(a.outbox or f"/tmp/meeting_prep_outbox/{a.scenario}")

    env_parts = [f"MP_SCENARIO={shlex.quote(a.scenario)}", f"MP_OUTBOX={shlex.quote(str(outbox))}"]
    if a.now:
        env_parts.append(f"MP_NOW={shlex.quote(a.now)}")
    prefix = f"{' '.join(env_parts)} python3 {shlex.quote(str(MOCK))}"

    me = world["me"]
    domain = me.split("@", 1)[1] if "@" in me else "company.example.com"
    tz = world.get("timezone", "Asia/Singapore")

    prompt = (
        load_prompt(resolve_sidecar(sc["automation"]))
        .replace("__USER_EMAIL__", me)
        .replace("__USER_DOMAIN__", domain)
        .replace("__USER_TIMEZONE__", tz)
    )
    # Normalise already-personalised live sidecar prompts to the synthetic world user
    prompt = re.sub(r"(Generator|reminder) for [^\s.]+\.", rf"\1 for {me}.", prompt)
    prompt = re.sub(r"email domain is not [^\s;]+;", f"email domain is not {domain};", prompt)
    prompt = re.sub(r"internal attendees \([^)]+\)", f"internal attendees ({domain})", prompt)
    for real, tool in REAL_TO_MOCK.items():
        prompt = prompt.replace(real, f"{prefix} {tool}")

    full = HEADER.format(people_cmd=f"{prefix} people", me=me, outbox=outbox) + prompt
    print(f"agentapi new-conversation -- {shlex.quote(full)}" if a.print_cmd else full)


if __name__ == "__main__":
    main()
