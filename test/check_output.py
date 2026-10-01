#!/usr/bin/env python3
"""Grade a mock run of the Meeting Prep automation.

Checks the captured outbox against the scenario's expectations and runs a
"hallucination lint": every person name, bug id and URL that appears in the
generated output must trace back to something that actually exists in the
fixture world.

    python3 check_output.py --scenario next_day_mixed
    python3 check_output.py --scenario next_day_cold_lead --strict
"""

import argparse
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent
WORLD = ROOT / "fixtures" / "world.json"

GREEN, RED, YELLOW, DIM, RESET = (
    "\033[32m",
    "\033[31m",
    "\033[33m",
    "\033[2m",
    "\033[0m",
)

# Phrases that look like "Firstname Lastname" but are structural, not people.
NAME_STOPWORDS = {
    "Meeting Prep", "Prep Dossier", "Last Scanned", "Quick Context",
    "Last Discussed", "Outstanding Action", "Action Item", "Recently Shared",
    "Shared File", "Recommended Talking", "Talking Points", "Suggested Agenda",
    "Open Blockers", "Attendee Context", "Meeting Recap", "Shared Artifacts",
    "Google Doc", "Google Meet", "Google Calendar", "Full Briefing",
    "Briefing Doc", "No Prior", "Prior Context", "Context Found",
    "Meeting Details", "Team Company", "Role Unknown", "Status Pending",
    "Code Freeze", "Security Review", "Staging Cutover", "Deep Work",
    "Last Meeting", "Last Sync", "Next Steps", "Prep Ready", "Google Cloud",
    "Google Workspace", "Google Drive", "Example Corp",
}

# A capitalised bigram starting with one of these is prose ("Answer Tom ...",
# "Lead with ...", "Confirm QA ..."), not a person's name.
NAME_LEADING_STOPWORDS = {
    "Answer", "Ask", "Agree", "Bring", "Check", "Close", "Confirm", "Discuss",
    "Follow", "Get", "Give", "Lead", "Lock", "Note", "Open", "Prepare", "Push",
    "Raise", "Review", "Send", "Share", "Start", "Take", "Walk", "Last", "Next",
    "First", "Also", "Both", "This", "That", "With", "From", "When", "While",
    "After", "Before", "During", "Since", "Given", "Nothing", "Every", "Full",
    "Understand", "Introduce", "Frame", "Surface", "Clarify", "Align", "Decide",
}


def load(scenario):
    w = json.loads(WORLD.read_text())
    if scenario not in w["scenarios"]:
        sys.exit(f"unknown scenario {scenario!r}")
    return w, w["scenarios"][scenario]


def corpus_for(world, sc):
    """Everything the agent was legitimately allowed to learn."""
    parts = [json.dumps(world["people"])]
    for key in ("csa", "gmail"):
        for e in sc.get(key, []):
            parts.append(e.get("response", "") + e.get("results", ""))
    for f in sc.get("drive", []):
        parts.append(f["title"] + " " + f.get("content", "") + " " + f.get("url", ""))
    for key in ("events", "past_events"):
        for e in sc.get(key, []):
            parts.append(e["summary"] + " " + e.get("description", "") + " " + e.get("hangoutLink", ""))
            for a in e.get("attendees", []):
                parts.append(a.get("email", "") + " " + a.get("displayName", ""))
    return "\n".join(parts).lower()


def read_outbox(outbox):
    emails, docs = [], []
    edir, ddir = outbox / "emails", outbox / "docs"
    if edir.exists():
        emails = [p.read_text() for p in sorted(edir.glob("*.md"))]
    if ddir.exists():
        docs = [p.read_text() for p in sorted(ddir.glob("*.md"))]
    idx = []
    if (outbox / "drive_index.json").exists():
        idx = json.loads((outbox / "drive_index.json").read_text())
    return emails, docs, idx


def lint_hallucinations(text, corpus):
    findings = []
    for name in sorted(set(re.findall(r"\b[A-Z][a-z]{2,} [A-Z][a-z]{2,}\b", text))):
        if name in NAME_STOPWORDS or name.lower() in corpus:
            continue
        if name.split()[0] in NAME_LEADING_STOPWORDS:
            continue
        findings.append(("name", name))
    for bug in sorted(set(re.findall(r"\bb/\d+\b", text))):
        if bug.lower() not in corpus:
            findings.append(("bug", bug))
    for url in sorted(set(re.findall(r"https?://[^\s)\]\"'>]+", text))):
        clean = url.rstrip(".,;")
        # Docs this run legitimately created carry a mock- id.
        if "mock-" in clean or clean.lower() in corpus:
            continue
        # Linkifying a bug number that really exists in the fixture is not a fabrication.
        m = re.search(r"/(?:issues/)?(\d+)$", clean)
        if m and f"b/{m.group(1)}" in corpus:
            continue
        findings.append(("url", clean))
    return findings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--outbox", default=None)
    ap.add_argument("--strict", action="store_true",
                    help="treat hallucination findings as failures")
    ap.add_argument("--show", action="store_true", help="print the captured email(s)")
    a = ap.parse_args()

    world, sc = load(a.scenario)
    outbox = pathlib.Path(a.outbox or f"/tmp/meeting_prep_outbox/{a.scenario}")
    exp = sc.get("expect", {})
    emails, docs, idx = read_outbox(outbox)
    blob = "\n".join(emails + docs)
    results = []

    def check(name, ok, detail=""):
        results.append((name, ok, detail))

    check(
        f"emails sent == {exp.get('emails', 0)}",
        len(emails) == exp.get("emails", 0),
        f"got {len(emails)}",
    )
    check(
        f"docs created == {exp.get('docs', 0)}",
        len(idx) == exp.get("docs", 0),
        f"got {len(idx)}: {[d['title'] for d in idx]}",
    )

    if exp.get("doc_title_regex"):
        rx = re.compile(exp["doc_title_regex"])
        bad = [d["title"] for d in idx if not rx.match(d["title"])]
        check("doc titles follow the dedupe convention", not bad, f"bad: {bad}")

    for needle in exp.get("must_contain", []):
        check(f"output contains {needle!r}", needle.lower() in blob.lower())
    for needle in exp.get("must_not_contain", []):
        check(f"output omits {needle!r}", needle.lower() not in blob.lower())

    # A filtered meeting may legitimately be named in a one-line "filtered out"
    # footer. What must never happen is it getting its own dossier section.
    headings = [m.group(1) for m in re.finditer(r"(?m)^#{1,6}\s+(.*)$", blob)]
    for needle in exp.get("must_not_head", []):
        check(
            f"no dossier section for {needle!r}",
            not any(needle.lower() in h.lower() for h in headings),
            f"headings: {headings}",
        )

    findings = lint_hallucinations(blob, corpus_for(world, sc)) if blob else []
    if a.strict:
        check("no unsupported entities in output", not findings, str(findings))

    width = max((len(r[0]) for r in results), default=10) + 2
    print(f"\n{DIM}scenario:{RESET} {a.scenario}  {DIM}outbox:{RESET} {outbox}")
    print(f"{DIM}{sc['description']}{RESET}\n")
    passed = 0
    for name, ok, detail in results:
        mark = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
        suffix = "" if ok or not detail else f"  {DIM}({detail}){RESET}"
        print(f"  {mark}  {name:<{width}}{suffix}")
        passed += ok
    if findings and not a.strict:
        print(f"\n  {YELLOW}WARN{RESET}  possible hallucinations (not in fixtures):")
        for kind, val in findings:
            print(f"        {kind}: {val}")
    print(f"\n  {passed}/{len(results)} checks passed\n")

    if a.show:
        for i, e in enumerate(emails, 1):
            print(f"{DIM}--- email {i} {'-' * 50}{RESET}\n{e}\n")

    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
