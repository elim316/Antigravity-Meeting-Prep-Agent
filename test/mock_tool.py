#!/usr/bin/env python3
"""Mock Workspace CLIs for the Meeting Prep agent test harness.

Stands in for calendar, gmail, gdocs, gdrive, cross-corpus search, and people
directory commands so the shipping automation prompt can be exercised end-to-end
against synthetic data without touching any real Workspace account.
"""

import datetime
import functools
import hashlib
import json
import os
import pathlib
import re
import sys
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).resolve().parent
WORLD_PATH = pathlib.Path(os.environ.get("MP_WORLD", ROOT / "fixtures" / "world.json"))
SCENARIO = os.environ.get("MP_SCENARIO", "")
OUTBOX = pathlib.Path(os.environ.get("MP_OUTBOX", f"/tmp/meeting_prep_outbox/{SCENARIO or 'default'}"))

_VALUE_FLAGS = {
    "--date", "--start", "--end", "--max", "--timezone", "--title",
    "--subject", "--body", "--name-contains", "--query", "--user_prompt",
    "--allowed_corpora", "--latency_budget_seconds",
}


def die(msg):
    print(f"mock_tool: {msg}", file=sys.stderr)
    sys.exit(2)


@functools.lru_cache(maxsize=4)
def _load_world_cached(path_str):
    path = pathlib.Path(path_str)
    if not path.exists():
        die(f"world file not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def world():
    return _load_world_cached(str(WORLD_PATH))


def get_tz():
    return ZoneInfo(world().get("timezone", "Asia/Singapore"))


def now():
    tz = get_tz()
    raw = os.environ.get("MP_NOW")
    if raw:
        dt = datetime.datetime.fromisoformat(raw)
        return dt.astimezone(tz) if dt.tzinfo else dt.replace(tzinfo=tz)
    return datetime.datetime.now(tz)


def scenario():
    w = world()
    if SCENARIO not in w["scenarios"]:
        die(f"unknown MP_SCENARIO={SCENARIO!r}; known: {', '.join(sorted(w['scenarios']))}")
    return w, w["scenarios"][SCENARIO]


def next_business_day(d):
    d += datetime.timedelta(days=1)
    while d.weekday() >= 5:
        d += datetime.timedelta(days=1)
    return d


def resolve_when(when, ref):
    """Turn a relative fixture spec into concrete (start, end, all_day)."""
    kind = when["kind"]
    dur = int(when.get("duration_min", 60))
    tz = ref.tzinfo or get_tz()
    if kind == "offset_minutes":
        start = (ref + datetime.timedelta(minutes=int(when["value"]))).replace(second=0, microsecond=0)
        return start, start + datetime.timedelta(minutes=dur), False
    if kind in ("next_business_day", "today", "tomorrow", "days_ago"):
        if kind == "next_business_day":
            day = next_business_day(ref.date())
        elif kind == "tomorrow":
            day = ref.date() + datetime.timedelta(days=1)
        elif kind == "days_ago":
            day = ref.date() - datetime.timedelta(days=int(when["value"]))
        else:
            day = ref.date()
        if when.get("all_day"):
            s = datetime.datetime.combine(day, datetime.time(0, 0), tz)
            return s, datetime.datetime.combine(day + datetime.timedelta(days=1), datetime.time(0, 0), tz), True
        hh, mm = (int(x) for x in when.get("time", "10:00").split(":"))
        start = datetime.datetime.combine(day, datetime.time(hh, mm), tz)
        return start, start + datetime.timedelta(minutes=dur), False
    die(f"unknown when.kind={kind!r}")


def build_event(spec, ref, me, me_name="Alex Morgan", tz_name="Asia/Singapore"):
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
    for i in range(max(0, spec.get("attendee_count", 0) - len(attendees))):
        attendees.append({
            "email": f"attendee{i+1:03d}@{domain}",
            "displayName": f"Attendee {i+1:03d}",
            "responseStatus": "accepted",
        })
    ev_id = spec.get("id") or hashlib.md5(spec["summary"].encode("utf-8")).hexdigest()[:16]
    ev = {
        "id": ev_id,
        "summary": spec["summary"],
        "status": spec.get("status", "confirmed"),
        "eventType": spec.get("eventType", "default"),
        "attendees": attendees,
        "attendeeCount": len(attendees),
        "organizer": spec.get("organizer", {"email": me, "displayName": me_name}),
        "start": {"date": start.date().isoformat()} if all_day else {"dateTime": start.isoformat(), "timeZone": tz_name},
        "end": {"date": end.date().isoformat()} if all_day else {"dateTime": end.isoformat(), "timeZone": tz_name},
        "_startLocal": start.strftime("%Y-%m-%d %H:%M %Z"),
        "_minutesFromNow": int((start - ref).total_seconds() // 60),
        "htmlLink": spec.get("htmlLink", f"https://calendar.google.com/calendar/event?eid={ev_id}"),
    }
    for key in ("location", "description", "hangoutLink"):
        if spec.get(key):
            ev[key] = spec[key]
    return ev


def outbox_dir(*parts):
    p = OUTBOX.joinpath(*parts)
    p.mkdir(parents=True, exist_ok=True)
    return p


def drive_index():
    path = OUTBOX / "drive_index.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def save_drive_index(idx):
    outbox_dir()
    (OUTBOX / "drive_index.json").write_text(json.dumps(idx, indent=2), encoding="utf-8")


def render_placeholders(text, ref):
    if not text:
        return text
    return text.replace("{{TODAY}}", ref.date().isoformat()).replace(
        "{{NEXT_BUSINESS_DAY}}", next_business_day(ref.date()).isoformat()
    )


def all_drive(sc):
    ref = now()
    seeded = [
        {
            "id": f["id"],
            "title": render_placeholders(f["title"], ref),
            "url": f.get("url", f"https://docs.google.com/document/d/{f['id']}/edit"),
            "content": render_placeholders(f.get("content", ""), ref),
            "seeded": True,
        }
        for f in sc.get("drive", [])
    ]
    return seeded + drive_index()


def get_all_flags(args, name):
    out = []
    for i, a in enumerate(args):
        if a == name and i + 1 < len(args):
            out.append(args[i + 1])
        elif a.startswith(name + "="):
            out.append(a.split("=", 1)[1])
    return out


def get_flag(args, name, default=None):
    vals = get_all_flags(args, name)
    return vals[0] if vals else default


_BOOL_FLAGS = {"--json", "--md"}


def positional_args(args, skip=()):
    """Extract non-flag positional arguments, properly skipping `--flag value` pairs."""
    out, i = [], 0
    while i < len(args):
        a = args[i]
        if a in _VALUE_FLAGS or (a.startswith("--") and "=" not in a and a not in _BOOL_FLAGS):
            i += 2
            continue
        if not a.startswith("-") and a not in skip:
            out.append(a)
        i += 1
    return out


def best_match(entries, query):
    q = (query or "").lower()
    best, best_score = None, 0
    for e in entries:
        score = sum(1 for kw in e.get("match", []) if kw.lower() in q)
        if score > best_score:
            best, best_score = e, score
    return best


def tool_gcalendar(args):
    w, sc = scenario()
    me = w["me"]
    me_name = w.get("people", {}).get(me, {}).get("name", "Alex Morgan")
    tz_name = w.get("timezone", "Asia/Singapore")
    ref = now()
    events = sorted(
        (build_event(e, ref, me, me_name, tz_name) for e in sc.get("events", [])),
        key=lambda e: e["start"].get("dateTime", e["start"].get("date", "")),
    )

    if "search" in args:
        pos = positional_args(args, ("readonly", "search"))
        query = pos[0] if pos else ""
        past = [build_event(e, ref, me, me_name, tz_name) for e in sc.get("past_events", [])]
        hits = [e for e in past if query.lower() in e["summary"].lower()] if query else past
        print(json.dumps(hits, indent=2))
        return

    if "get" in args:
        pos = positional_args(args, ("readonly", "get"))
        ident = pos[0] if pos else ""
        hit = next((e for e in events if e["id"] == ident), {"error": "not found", "id": ident})
        print(json.dumps(hit, indent=2))
        return

    if any(k in args for k in ("workloc", "working-hours", "freebusy")):
        print(json.dumps({"timezone": tz_name, "note": "mock"}, indent=2))
        return

    date = get_flag(args, "--date") or (ref.date().isoformat() if "today" in args else None)
    if date:
        events = [e for e in events if (e["start"].get("dateTime", "")[:10] or e["start"].get("date", "")) == date]
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
    print(json.dumps({e: w["people"].get(e, "NOT FOUND - no directory entry for this person") for e in emails}, indent=2))


def tool_csa(args):
    _, sc = scenario()
    hit = best_match(sc.get("csa", []), get_flag(args, "--user_prompt", ""))
    print(hit["response"] if hit else "No relevant results found across the requested corpora.")


def tool_gmail(args):
    _, sc = scenario()
    if "send-self" in args or "send" in args:
        subject = get_flag(args, "--subject", "(no subject)")
        body = get_flag(args, "--body", "")
        d = outbox_dir("emails")
        path = d / f"{len(list(d.glob('*.md'))) + 1:02d}.md"
        path.write_text(f"SUBJECT: {subject}\n\n{body}\n", encoding="utf-8")
        print(f"Message sent to self. Subject: {subject}\n[mock] captured at {path}")
        return
    if "search" in args:
        pos = positional_args(args, ("readonly", "search"))
        hit = best_match(sc.get("gmail", []), pos[0] if pos else "")
        print(hit["results"] if hit else "No messages matched the query.")
        return
    print("No messages matched the query.")


def tool_gdocs(args):
    _, sc = scenario()
    if "import-md" in args:
        title = get_flag(args, "--title", "Untitled")
        pos = [a for a in positional_args(args, ("mutate", "import-md")) if a.endswith(".md")]
        src = pathlib.Path(pos[0]) if pos else None
        content = src.read_text(encoding="utf-8") if src and src.exists() else ""
        doc_id = "mock-" + hashlib.md5(title.encode("utf-8")).hexdigest()[:10]
        url = f"https://docs.google.com/document/d/{doc_id}/edit"
        safe = re.sub(r"[^A-Za-z0-9]+", "_", title)[:80]
        (outbox_dir("docs") / f"{safe}.md").write_text(f"# {title}\n\n{content}", encoding="utf-8")
        idx = drive_index()
        idx.append({"id": doc_id, "title": title, "url": url, "content": content})
        save_drive_index(idx)
        print(f"Created document: {title}\n{url}")
        return
    if "read" in args:
        pos = positional_args(args, ("readonly", "read"))
        ident = pos[0] if pos else ""
        for f in all_drive(sc):
            if ident and (ident == f["id"] or ident in f["url"]):
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
        files = [
            f for f in files
            if any(all(t.lower() in f["title"].lower() for t in n.split() if t != "-") for n in needles)
        ]
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
