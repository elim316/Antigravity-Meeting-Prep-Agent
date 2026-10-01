#!/usr/bin/env python3
"""Mock Workspace CLIs for the Meeting Prep agent test harness.

Stands in for the real calendar, gmail, gdocs, gdrive, cross-corpus search, and
people directory binaries so the *unmodified* automation prompt can be exercised
end-to-end against synthetic data. Nothing here touches a real calendar,
mailbox or Drive.

Usage:
    python3 mock_tool.py <tool> <args...>

Tools: gcalendar | gmail | gdocs | gdrive | csa_cli | people

Environment:
    MP_SCENARIO  scenario key in fixtures/world.json (required)
    MP_OUTBOX    directory for captured emails/docs (default: /tmp/meeting_prep_outbox/<scenario>)
    MP_WORLD     path to world.json (default: fixtures/world.json)
    MP_NOW       ISO-8601 override for "now", for deterministic runs
"""

import datetime
import hashlib
import json
import os
import pathlib
import re
import sys
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Singapore")
ROOT = pathlib.Path(__file__).resolve().parent
WORLD_PATH = pathlib.Path(os.environ.get("MP_WORLD", ROOT / "fixtures" / "world.json"))
SCENARIO = os.environ.get("MP_SCENARIO", "")
# NOTE: the outbox must live somewhere the agent's sandbox can write. Anything
# under ~/.gemini or a source workspace forces an unsandboxed escalation, which
# an unattended agent cannot get approved - it just hangs.
OUTBOX = pathlib.Path(
    os.environ.get("MP_OUTBOX", f"/tmp/meeting_prep_outbox/{SCENARIO or 'default'}")
)


def die(msg):
    print(f"mock_tool: {msg}", file=sys.stderr)
    sys.exit(2)


def now():
    raw = os.environ.get("MP_NOW")
    if raw:
        dt = datetime.datetime.fromisoformat(raw)
        return dt.astimezone(TZ) if dt.tzinfo else dt.replace(tzinfo=TZ)
    return datetime.datetime.now(TZ)


def world():
    if not WORLD_PATH.exists():
        die(f"world file not found: {WORLD_PATH}")
    return json.loads(WORLD_PATH.read_text())


def scenario():
    w = world()
    if SCENARIO not in w["scenarios"]:
        die(
            f"unknown MP_SCENARIO={SCENARIO!r}; "
            f"known: {', '.join(sorted(w['scenarios']))}"
        )
    return w, w["scenarios"][SCENARIO]


# --------------------------------------------------------------------------
# time helpers
# --------------------------------------------------------------------------


def next_business_day(d):
    d = d + datetime.timedelta(days=1)
    while d.weekday() >= 5:  # 5=Sat, 6=Sun
        d += datetime.timedelta(days=1)
    return d


def resolve_when(when, ref):
    """Turn a relative fixture spec into concrete (start, end, all_day)."""
    kind = when["kind"]
    dur = int(when.get("duration_min", 60))
    if kind == "offset_minutes":
        start = (ref + datetime.timedelta(minutes=int(when["value"]))).replace(
            second=0, microsecond=0
        )
        return start, start + datetime.timedelta(minutes=dur), False
    if kind in ("next_business_day", "today", "tomorrow"):
        if kind == "next_business_day":
            day = next_business_day(ref.date())
        elif kind == "tomorrow":
            day = ref.date() + datetime.timedelta(days=1)
        else:
            day = ref.date()
        if when.get("all_day"):
            return (
                datetime.datetime.combine(day, datetime.time(0, 0), TZ),
                datetime.datetime.combine(
                    day + datetime.timedelta(days=1), datetime.time(0, 0), TZ
                ),
                True,
            )
        hh, mm = (int(x) for x in when["time"].split(":"))
        start = datetime.datetime.combine(day, datetime.time(hh, mm), TZ)
        return start, start + datetime.timedelta(minutes=dur), False
    if kind == "days_ago":
        day = ref.date() - datetime.timedelta(days=int(when["value"]))
        hh, mm = (int(x) for x in when.get("time", "10:00").split(":"))
        start = datetime.datetime.combine(day, datetime.time(hh, mm), TZ)
        return start, start + datetime.timedelta(minutes=dur), False
    die(f"unknown when.kind={kind!r}")


def build_event(spec, ref, me, me_name="Alex Morgan"):
    start, end, all_day = resolve_when(spec["when"], ref)
    domain = me.split("@", 1)[1] if "@" in me else "company.example.com"
    attendees = []
    for a in spec.get("attendees", []):
        entry = {
            "email": a["email"],
            "displayName": a.get("displayName", ""),
            "responseStatus": a.get("responseStatus", "accepted"),
        }
        if a["email"] == me:
            entry["self"] = True
        if a.get("organizer"):
            entry["organizer"] = True
        attendees.append(entry)
    if spec.get("attendee_count") and len(attendees) < spec["attendee_count"]:
        # Large broadcast meetings: pad with anonymous attendees.
        for i in range(spec["attendee_count"] - len(attendees)):
            attendees.append(
                {
                    "email": f"attendee{i+1:03d}@{domain}",
                    "displayName": f"Attendee {i+1:03d}",
                    "responseStatus": "accepted",
                }
            )
    ev = {
        "id": spec.get("id") or hashlib.md5(spec["summary"].encode()).hexdigest()[:16],
        "summary": spec["summary"],
        "status": spec.get("status", "confirmed"),
        "eventType": spec.get("eventType", "default"),
        "attendees": attendees,
        "attendeeCount": len(attendees),
        "organizer": spec.get("organizer", {"email": me, "displayName": me_name}),
    }
    if all_day:
        ev["start"] = {"date": start.date().isoformat()}
        ev["end"] = {"date": end.date().isoformat()}
    else:
        ev["start"] = {"dateTime": start.isoformat(), "timeZone": "Asia/Singapore"}
        ev["end"] = {"dateTime": end.isoformat(), "timeZone": "Asia/Singapore"}
    for key in ("location", "description", "hangoutLink"):
        if spec.get(key):
            ev[key] = spec[key]
    ev["_startLocal"] = start.strftime("%Y-%m-%d %H:%M %Z")
    ev["_minutesFromNow"] = int((start - ref).total_seconds() // 60)
    # Real Calendar API responses carry htmlLink; without it agents tend to
    # manufacture a calendar URL from the event id.
    ev.setdefault("htmlLink", f"https://calendar.google.com/calendar/event?eid={ev['id']}")
    return ev


# --------------------------------------------------------------------------
# outbox
# --------------------------------------------------------------------------


def outbox_dir(*parts):
    p = OUTBOX.joinpath(*parts)
    p.mkdir(parents=True, exist_ok=True)
    return p


def drive_index():
    path = OUTBOX / "drive_index.json"
    if path.exists():
        return json.loads(path.read_text())
    return []


def save_drive_index(idx):
    outbox_dir()
    (OUTBOX / "drive_index.json").write_text(json.dumps(idx, indent=2))


def render_placeholders(text, ref):
    """Resolve date placeholders used in seeded Drive fixtures."""
    if not text:
        return text
    return text.replace("{{TODAY}}", ref.date().isoformat()).replace(
        "{{NEXT_BUSINESS_DAY}}", next_business_day(ref.date()).isoformat()
    )


def seeded_drive(sc):
    ref = now()
    out = []
    for f in sc.get("drive", []):
        out.append(
            {
                "id": f["id"],
                "title": render_placeholders(f["title"], ref),
                "url": f.get(
                    "url", f"https://docs.google.com/document/d/{f['id']}/edit"
                ),
                "content": render_placeholders(f.get("content", ""), ref),
                "seeded": True,
            }
        )
    return out


def all_drive(sc):
    return seeded_drive(sc) + drive_index()


# --------------------------------------------------------------------------
# flag parsing
# --------------------------------------------------------------------------


def get_flag(args, name, default=None):
    """Supports `--flag value` and `--flag=value`."""
    for i, a in enumerate(args):
        if a == name and i + 1 < len(args):
            return args[i + 1]
        if a.startswith(name + "="):
            return a.split("=", 1)[1]
    return default


def get_all_flags(args, name):
    out = []
    for i, a in enumerate(args):
        if a == name and i + 1 < len(args):
            out.append(args[i + 1])
        elif a.startswith(name + "="):
            out.append(a.split("=", 1)[1])
    return out


def best_match(entries, query):
    """Pick the fixture entry whose `match` keywords best cover the query."""
    q = (query or "").lower()
    best, best_score = None, 0
    for e in entries:
        score = sum(1 for kw in e.get("match", []) if kw.lower() in q)
        if score > best_score:
            best, best_score = e, score
    return best


# --------------------------------------------------------------------------
# tools
# --------------------------------------------------------------------------


def tool_gcalendar(args):
    w, sc = scenario()
    me = w["me"]
    me_name = w.get("people", {}).get(me, {}).get("name", "Alex Morgan")
    ref = now()
    events = [build_event(e, ref, me, me_name) for e in sc.get("events", [])]
    events.sort(key=lambda e: e["start"].get("dateTime", e["start"].get("date", "")))

    if "search" in args:
        query = next(
            (
                a
                for a in args
                if not a.startswith("-") and a not in ("readonly", "search")
            ),
            "",
        )
        past = [build_event(e, ref, me, me_name) for e in sc.get("past_events", [])]
        hits = (
            [e for e in past if query.lower() in e["summary"].lower()] if query else past
        )
        print(json.dumps(hits, indent=2))
        return

    if "get" in args:
        ident = next(
            (a for a in args if not a.startswith("-") and a not in ("readonly", "get")),
            "",
        )
        for e in events:
            if e["id"] == ident:
                print(json.dumps(e, indent=2))
                return
        print(json.dumps({"error": "not found", "id": ident}))
        return

    if "workloc" in args or "working-hours" in args or "freebusy" in args:
        print(json.dumps({"timezone": "Asia/Singapore", "note": "mock"}, indent=2))
        return

    # events / today
    date = get_flag(args, "--date")
    if "today" in args and not date:
        date = ref.date().isoformat()
    if date:
        events = [
            e
            for e in events
            if (e["start"].get("dateTime", "")[:10] or e["start"].get("date", ""))
            == date
        ]
    start_f, end_f = get_flag(args, "--start"), get_flag(args, "--end")
    if start_f:
        events = [e for e in events if e["start"].get("dateTime", "9999") >= start_f]
    if end_f:
        events = [e for e in events if e["start"].get("dateTime", "0000") <= end_f]
    max_n = get_flag(args, "--max")
    if max_n and max_n.isdigit():
        events = events[: int(max_n)]
    print(json.dumps(events, indent=2))


def tool_people(args):
    w, _ = scenario()
    emails = [a for a in args if "@" in a]
    if not emails:
        print(json.dumps(w["people"], indent=2))
        return
    out = {}
    for e in emails:
        out[e] = w["people"].get(e, "NOT FOUND - no directory entry for this person")
    print(json.dumps(out, indent=2))


def tool_csa(args):
    _, sc = scenario()
    prompt = get_flag(args, "--user_prompt", "")
    hit = best_match(sc.get("csa", []), prompt)
    if not hit:
        print("No relevant results found across the requested corpora.")
        return
    print(hit["response"])


def tool_gmail(args):
    _, sc = scenario()
    if "send-self" in args or "send" in args:
        subject = get_flag(args, "--subject", "(no subject)")
        body = get_flag(args, "--body", "")
        d = outbox_dir("emails")
        n = len(list(d.glob("*.md"))) + 1
        path = d / f"{n:02d}.md"
        path.write_text(f"SUBJECT: {subject}\n\n{body}\n")
        print(f"Message sent to self. Subject: {subject}")
        print(f"[mock] captured at {path}")
        return
    if "search" in args:
        query = next(
            (
                a
                for a in args
                if not a.startswith("-") and a not in ("readonly", "search")
            ),
            "",
        )
        hit = best_match(sc.get("gmail", []), query)
        print(hit["results"] if hit else "No messages matched the query.")
        return
    print("No messages matched the query.")


def tool_gdocs(args):
    _, sc = scenario()
    if "import-md" in args:
        title = get_flag(args, "--title", "Untitled")
        src = next(
            (
                a
                for a in args
                if not a.startswith("-")
                and a not in ("mutate", "import-md")
                and a.endswith(".md")
            ),
            None,
        )
        content = ""
        if src and pathlib.Path(src).exists():
            content = pathlib.Path(src).read_text()
        doc_id = "mock-" + hashlib.md5(title.encode()).hexdigest()[:10]
        url = f"https://docs.google.com/document/d/{doc_id}/edit"
        d = outbox_dir("docs")
        safe = re.sub(r"[^A-Za-z0-9]+", "_", title)[:80]
        (d / f"{safe}.md").write_text(f"# {title}\n\n{content}")
        idx = drive_index()
        idx.append({"id": doc_id, "title": title, "url": url, "content": content})
        save_drive_index(idx)
        print(f"Created document: {title}")
        print(url)
        return
    if "read" in args:
        ident = next(
            (
                a
                for a in args
                if not a.startswith("-") and a not in ("readonly", "read")
            ),
            "",
        )
        for f in all_drive(sc):
            if ident in (f["id"], f["url"]) or ident in f["url"]:
                print(f"# {f['title']}\n\n{f['content']}")
                return
        print(f"Document not found: {ident}")
        return
    print("[mock] gdocs: unsupported subcommand")


def tool_gdrive(args):
    _, sc = scenario()
    needles = get_all_flags(args, "--name-contains") + get_all_flags(args, "--query")
    files = all_drive(sc)
    if needles:
        matched = []
        for f in files:
            for n in needles:
                toks = [t for t in re.split(r"\s+", n.strip()) if t not in ("-", "")]
                if all(t.lower() in f["title"].lower() for t in toks):
                    matched.append(f)
                    break
        files = matched
    if not files:
        print("No files matched.")
        return
    print(f"{'TITLE':<70} URL")
    for f in files:
        print(f"{f['title'][:68]:<70} {f['url']}")


TOOLS = {
    "gcalendar": tool_gcalendar,
    "gmail": tool_gmail,
    "gdocs": tool_gdocs,
    "gdrive": tool_gdrive,
    "csa_cli": tool_csa,
    "csa": tool_csa,
    "people": tool_people,
}


def main():
    if len(sys.argv) < 2:
        die(f"usage: mock_tool.py <{'|'.join(TOOLS)}> <args...>")
    tool = sys.argv[1]
    if tool not in TOOLS:
        die(f"unknown tool {tool!r}; known: {', '.join(TOOLS)}")
    if not SCENARIO:
        die("MP_SCENARIO is not set")
    TOOLS[tool](sys.argv[2:])


if __name__ == "__main__":
    main()
