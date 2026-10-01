# Meeting Prep Agent: Mock Test Harness

Exercises the **shipping** automation prompts against a 100% synthetic Workspace. No real calendar, mailbox, Drive file, or email is ever touched.

Every person, company, document, and ticket number in the fixtures is fictional, and all domains end in `.example.com`.

## How It Works

```text
sidecars/<id>/sidecar.json  ──►  build_test_prompt.py  ──►  TEST MODE prompt  ──►  agent
      (real prompt)               rewrites tool paths                               │
                                                                                    ▼
   fixtures/world.json  ◄──────  mock_tool.py  ◄────────────  gcalendar/gmail/gdocs/...
     (synthetic world)            (fake CLIs)                                       │
                                                                                    ▼
                                                    /tmp/meeting_prep_outbox/<scenario>/{emails,docs}
                                                                                    │
                                                        check_output.py  ◄──────────┘
                                                        (grades the run)
```

`build_test_prompt.py` reads the `sidecar.json` definition, substitutes the synthetic user's email and timezone from `fixtures/world.json`, and string-replaces each Workspace CLI command with `python3 mock_tool.py <tool>`. That means **the harness always tests whatever prompt is actually shipped**: edit the automation and the tests follow automatically.

Fixture times are relative (`next business day 10:30`, `now + 75 min`) and are resolved to concrete `Asia/Singapore` timestamps at call time, so the scenarios never expire.

## Running a Scenario

```bash
cd test

bash run_scenario.sh next_day_mixed          # launches an agent via agentapi
python3 check_output.py --scenario next_day_mixed --show
```

Pin the clock for a deterministic run:

```bash
bash run_scenario.sh next_day_mixed --now 2026-09-23T17:00:00+08:00
```

Inspect the mock world directly without spawning an agent:

```bash
MP_SCENARIO=next_day_mixed python3 mock_tool.py gcalendar readonly events --json
MP_SCENARIO=next_day_mixed python3 mock_tool.py people alex.chen@acme.example.com
```

## Scenarios (36 Automated Checks)

| Scenario | Automation | What It Proves |
| :--- | :--- | :--- |
| `next_day_mixed` | `meeting-prep-dossier` | **Happy path + filtering.** 7 events, only 2 qualify (1 external, 1 cross-functional); an own-team standup, a 312-person all-hands, focus time, a public holiday, and a *declined* vendor demo must all be excluded from dossier sections. |
| `next_day_quiet` | `meeting-prep-dossier` | **Silence.** Nothing qualifies, so zero emails and zero docs are created. Sending a cheerful "no meetings tomorrow!" email fails the test. |
| `next_day_cold_lead` | `meeting-prep-dossier` | **Zero-hallucination guard.** A real external meeting with zero prior history. Must emit `No prior context found` rather than inventing a recap, action items, or shared files. |
| `t1h_reuse` | `meeting-prep-reminder` | **Idempotence.** A dossier doc already exists in Drive (`Meeting Prep Dossier - <YYYY-MM-DD> - <Title>`); the reminder must link it and create no duplicate doc. |
| `t1h_window` | `meeting-prep-reminder` | **Stateless exactly-once window.** Meetings at `now+30m`, `now+75m`, and `now+150m`; only `now+75m` falls inside `[now+60m, now+120m)`. Replaces fragile local dedupe state files. |

## Grading & Hallucination Linter

`check_output.py` asserts email/doc counts, the doc title convention, heading-scoped exclusions, and required/forbidden substrings. It also runs an automated **hallucination lint**: it extracts every `Firstname Lastname`, `b/123456`, and URL from the generated output and flags anything that cannot be traced back to the fixture corpus (`--strict` turns warnings into hard failures).
