# MetroOps Response

A local, offline-first incident response dashboard for a metro operations team. An
operator reports an incident; the system validates it, scores its priority with a
transparent rule-based engine, stores it in a local database, and tracks it through
a fixed response workflow.

## Problem and intended users

Station and control-room staff need a fast, dependable way to log incidents, see which
ones matter most, and track their handling — without relying on cloud services or a
network connection. This prototype is a single-operator decision-support tool. It does
**not** control any trains, doors, escalators, or safety equipment, and its priority
scores are advisory, not validated operating procedures.

## Architecture and data flow

```
Browser (static/)                  FastAPI app (main.py)              Storage
-----------------                  ---------------------              -------
index.html + app.js  --POST /incidents-->  validate (Pydantic)
                                           score  (incident_engine)
                                           store  (incident_repo) --> SQLite (data/)
                     <--- JSON record ----  (id, analysis, history)
                     --GET  /incidents-->   list_all             <--  SQLite
                     --PATCH .../status-->  check transition
                                            apply in one txn      --> SQLite
```

- `main.py` — FastAPI routes, request validation, and the authoritative status rules.
- `incident_engine.py` — deterministic, rule-based priority scoring (no AI/ML).
- `incident_repo.py` — SQLite persistence; parameterized SQL; one transaction per write.
- `static/` — vanilla HTML/CSS/JS dashboard, served locally by the app.
- `test_metroops.py` — 50 standard-library `unittest` tests.

Endpoints: `GET /` (dashboard), `GET /health`, `GET /incidents`,
`POST /incidents`, `PATCH /incidents/{id}/status`.

## Requirements and startup

Python 3.12. Dependencies are pinned in `requirements.txt`
(FastAPI, Uvicorn, Pydantic — all installed locally, no runtime network access).

```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
```

Then open http://127.0.0.1:8000 in a browser. The server binds to loopback
(`127.0.0.1`) only, so it is not reachable from other machines.

## Local storage and persistence

Incidents live in a single SQLite file, `data/metroops.db` by default. Set the
`METROOPS_DB` environment variable to use a different path. The schema is created on
startup if the file does not exist. Two tables are used: `incidents` (one row per
report, with the scoring analysis stored as JSON) and `status_history` (one row per
lifecycle change). The database file and its journal/WAL sidecars are git-ignored and
never committed.

Each write (create, status change) runs in a single transaction. A status change uses a
compare-and-swap update, so a stale or concurrent change is rejected rather than
overwriting newer state. To restart: stop the server (Ctrl+C) and run the start command
again — all incidents and their history remain, because they are on disk.

## Scoring factors and severity rules

Scoring is deterministic and rule-based (see `incident_engine.py`). The score is the sum
of three factors, clamped to 0–100:

- **Category weight:** Passenger Safety 40, Infrastructure 35, Equipment Failure 25,
  Service Disruption 25, Crowding 20, Other 10.
- **Passengers affected:** 0 → 0, 1–50 → 5, 51–200 → 10, 201–500 → 15, 501+ → 20.
- **Safety concern flag:** +40 when the operator marks the incident a safety concern.

Severity is derived from the score: 80+ Critical, 60+ High, 35+ Medium, else Low. If the
safety-concern flag is set, severity is floored to at least High. The response also
includes a plain-language summary, the factor breakdown, and recommended actions. These
are advisory decision support, not validated operating procedures.

## Status lifecycle

Incidents move forward only, one step at a time:

```
Active → Acknowledged → In Progress → Resolved
```

The server enforces these transitions; any other move returns HTTP 409. The dashboard
shows a single button for the next valid step and lists the full status history.

## Offline design and what was tested

The dashboard uses only vanilla HTML/CSS/JS served by the app itself — no CDNs, web
fonts, analytics, or external URLs (verified by searching `static/`). All browser
`fetch()` calls are same-origin. The Python side uses only FastAPI, Uvicorn, Pydantic,
and the standard library; nothing makes outbound network calls at runtime.

Tested automatically (50 `unittest` tests, standard library only): scoring, input
validation, the status workflow and its illegal-transition rejection, SQLite round
trips, transaction rollback on a bad write, compare-and-swap on stale updates, data
survival across reconnection, and loud failures on unavailable/invalid databases.

Tested manually in this environment: the documented startup command, the `/health`,
`/`, `/static/*`, and `/incidents` endpoints over localhost, a genuine two-process
restart confirming an acknowledged incident persisted, and `netstat` confirming the
listener is bound to `127.0.0.1` only.

Not tested here (reported as a limitation, not claimed as passing): the running UI in a
real browser. No browser-automation tool (Playwright/Selenium/Puppeteer) is installed,
and none was added. The host's network could not be physically disconnected, so offline
behavior is established by dependency/asset inspection and the loopback-only bind rather
than a disconnected-network run. The frontend's rendering uses `textContent` throughout
(no `innerHTML`), so a description containing HTML is shown as literal text; this is
verified by code inspection and a stored-verbatim persistence test, not by a browser.

## Synthetic demo scenarios

Enter these through the dashboard form (they are clearly synthetic — no real passenger
data). Expected results are computed directly from `incident_engine.py`:

| Scenario | Category | Passengers | Safety? | Score | Severity |
|----------|----------|-----------:|:-------:|------:|----------|
| Smoke reported in a train saloon | Passenger Safety | 20 | Yes | 85 | Critical |
| Escalator fault at concourse | Equipment Failure | 30 | Yes | 70 | High |
| Signalling delay on a line | Service Disruption | 300 | No | 40 | Medium |
| Platform overcrowding at peak | Crowding | 600 | No | 40 | Medium |

Demo procedure: submit the scenarios above, use the status filter to show each lifecycle
stage, advance one incident through Acknowledge → In Progress → Resolve and watch the
history grow, then reload the page (or restart the server) to confirm everything
persisted.

## Limitations

- **No authentication or authorization.** Anyone who can reach the local port can read
  and change incidents. It is meant for a single trusted operator on one machine.
- **Single process / single operator.** SQLite serializes writes and the app assumes one
  operator; it is not built for many concurrent users.
- **Advisory scoring.** Priority scores and recommended actions are a transparent
  rule-based baseline for decision support, not validated safety procedures, and the
  system controls no physical equipment.
