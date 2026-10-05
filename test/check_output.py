#!/usr/bin/env python3
"""Grade a mock run of the Meeting Prep automation.

Checks the captured outbox against the scenario's expectations and runs a
hallucination lint ensuring every person name, bug ID, and URL in the output
traces back to the synthetic fixture world.
"""

import argparse
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent
WORLD = ROOT / "fixtures" / "world.json"
GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"

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

NAME_LEADING_STOPWORDS = {
    "Answer", "Ask", "Agree", "Bring", "Check", "Close", "Confirm", "Discuss",
    "Follow", "Get", "Give", "Lead", "Lock", "Note", "Open", "Prepare", "Push",
    "Raise", "Review", "Send", "Share", "Start", "Take", "Walk", "Last", "Next",
    "First", "Also", "Both", "This", "That", "With", "From", "When", "While",
    "After", "Before", "During", "Since", "Given", "Nothing", "Every", "Full",
    "Understand", "Introduce", "Frame", "Surface", "Clarify", "Align", "Decide",
}

_NAME_RE = re.compile(r"\b[A-Z][a-z]{2,} [A-Z][a-z]{2,}\b")
_BUG_RE = re.compile(r"\bb/\d+\b")
_URL_RE = re.compile(r"https?://[^\s)\]\"'>]+")
_ISSUE_ID_RE = re.compile(r"(?:b\.corp\.google\.com)?/(?:issues/)?(\d+)$")
_HEADING_RE = re.compile(r"(?m)^#{1,6}\s+(.*)$")


def load(scenario):
    w = json.loads(WORLD.read_text(encoding="utf-8"))
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
        parts.append(f"{f['title']} {f.get('content', '')} {f.get('url', '')}")
    for key in ("events", "past_events"):
        for e in sc.get(key, []):
            parts.append(f"{e['summary']} {e.get('description', '')} {e.get('hangoutLink', '')}")
            for a in e.get("attendees", []):
                parts.append(f"{a.get('email', '')} {a.get('displayName', '')}")
    return "\n".join(parts).lower()


def read_outbox(outbox):
    edir, ddir, idx_path = outbox / "emails", outbox / "docs", outbox / "drive_index.json"
    emails = [p.read_text(encoding="utf-8") for p in sorted(edir.glob("*.md"))] if edir.exists() else []
    docs = [p.read_text(encoding="utf-8") for p in sorted(ddir.glob("*.md"))] if ddir.exists() else []
    idx = json.loads(idx_path.read_text(encoding="utf-8")) if idx_path.exists() else []
    return emails, docs, idx


def lint_hallucinations(text, corpus):
    findings = []
    for name in sorted(set(_NAME_RE.findall(text))):
        if name not in NAME_STOPWORDS and name.lower() not in corpus and name.split()[0] not in NAME_LEADING_STOPWORDS:
            findings.append(("name", name))
    for bug in sorted(set(_BUG_RE.findall(text))):
        if bug.lower() not in corpus:
            findings.append(("bug", bug))
    for url in sorted(set(_URL_RE.findall(text))):
        clean = url.rstrip(".,;:")
        if "mock-" in clean or clean.lower() in corpus:
            continue
        m = _ISSUE_ID_RE.search(clean)
        if m and f"b/{m.group(1)}" in corpus:
            continue
        findings.append(("url", clean))
    return findings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--outbox", default=None)
    ap.add_argument("--strict", action="store_true", help="treat hallucination findings as failures")
    ap.add_argument("--show", action="store_true", help="print the captured email(s)")
    a = ap.parse_args()

    world, sc = load(a.scenario)
    outbox = pathlib.Path(a.outbox or f"/tmp/meeting_prep_outbox/{a.scenario}")
    exp = sc.get("expect", {})
    emails, docs, idx = read_outbox(outbox)
    blob = "\n".join(emails + docs)
    results = []

    def check(name, ok, detail=""):
        results.append((name, bool(ok), detail))

    check(f"emails sent == {exp.get('emails', 0)}", len(emails) == exp.get("emails", 0), f"got {len(emails)}")
    check(f"docs created == {exp.get('docs', 0)}", len(idx) == exp.get("docs", 0), f"got {len(idx)}: {[d['title'] for d in idx]}")

    if exp.get("doc_title_regex"):
        rx = re.compile(exp["doc_title_regex"])
        bad = [d["title"] for d in idx if not rx.match(d["title"])]
        check("doc titles follow the dedupe convention", not bad, f"bad: {bad}")

    blob_lower = blob.lower()
    for needle in exp.get("must_contain", []):
        check(f"output contains {needle!r}", needle.lower() in blob_lower)
    for needle in exp.get("must_not_contain", []):
        check(f"output omits {needle!r}", needle.lower() not in blob_lower)

    headings = [m.group(1).lower() for m in _HEADING_RE.finditer(blob)]
    for needle in exp.get("must_not_head", []):
        check(f"no dossier section for {needle!r}", not any(needle.lower() in h for h in headings), f"headings: {headings}")

    findings = lint_hallucinations(blob, corpus_for(world, sc)) if blob else []
    if a.strict:
        check("no unsupported entities in output", not findings, str(findings))

    width = max((len(r[0]) for r in results), default=10) + 2
    print(f"\n{DIM}scenario:{RESET} {a.scenario}  {DIM}outbox:{RESET} {outbox}\n{DIM}{sc['description']}{RESET}\n")
    passed = sum(1 for _, ok, _ in results if ok)
    for name, ok, detail in results:
        mark = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
        suffix = "" if ok or not detail else f"  {DIM}({detail}){RESET}"
        print(f"  {mark}  {name:<{width}}{suffix}")
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
