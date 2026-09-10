# ACIDE-Watch

**Autonomous Career Intelligence Engine — Portal Edition**

A self-hosted job portal and automated scouting platform. It indexes public
employer career feeds, scores every posting against your CV *and* against
where you want your career to go, and emails you the ones worth your time.

<p align="center">
  <em>Deep-emerald portal · dual-vector fit scoring · scheduled inspection · email digests</em>
</p>

---

## Why two scores

Most job boards ask one question: *can this person do this job?* That misses
the roles people actually want. ACIDE-Watch scores each posting on two
independent vectors:

| Vector | Question it answers |
| --- | --- |
| **Experience fit** | How much of this role can you already do, based on your CV? |
| **Pivot fit** | How strongly does this role move you toward the work you said you want? |

Keeping them separate is the whole point. A role can be a 95% experience
match and a 20% pivot match — a lateral move. Or 35%/96% — a stretch into a
new field, which is exactly the posting a single blended score would bury.
The portal shows both numbers on every card and lets you filter on either.

---

## Quick start

```bash
git clone https://github.com/danielegalasso/TrackJobPosting.git
cd TrackJobPosting

# 1. Backend
python3 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements.txt

# 2. Portal
cd frontend && npm install && npm run build && cd ..

# 3. Run — serves the API and the portal from one process
backend/.venv/bin/python -m acide
```

Open <http://127.0.0.1:8000>, go to **Settings**, and fill in:

1. **Candidate profile** — upload your CV (`.pdf`, `.md`, `.txt`) and list your
   pivot interests, one per line.
2. **OpenRouter** — paste an API key from <https://openrouter.ai/keys> and pick
   a model. `google/gemini-2.5-flash` is a good default: cheap and fast enough
   to score a few hundred postings without thinking about it.
3. **Career feeds** — add the companies you want watched (see below).
4. **Email delivery** — optional; only needed for alert digests.

Then open **Inspector** and press **Run now**. Postings appear in the grid as
they are scored.

Nothing works without step 2: with no API key the inspector will fetch
postings but refuse to score them, and it will say so in the console.

---

## Adding a career feed

Each target is one company's public job board. The *board token* is the
company handle in its job-board URL:

| Source | Example URL | Board token |
| --- | --- | --- |
| `greenhouse` | `job-boards.greenhouse.io/stripe` | `stripe` |
| `lever` | `jobs.lever.co/ramp` | `ramp` |
| `ashby` | `jobs.ashbyhq.com/linear` | `linear` |

### Scope of the inspector

ACIDE-Watch reads each provider's **own published job-board API** — the same
documented JSON endpoint the employer's careers page calls. That is a
deliberate boundary:

- It does **not** drive a headless browser.
- It does **not** attempt to bypass bot defences, challenges, or rate limits.
- A source that declines to serve us is logged as an error and skipped.

Requests are spaced out (`request_delay_seconds`, default 1.5s), capped per
source, and identify themselves with a descriptive user agent. The default
polling interval is six hours. Please leave it that way — you are a guest on
someone else's server, and the Acceptable Use section of `docs/TERMS.md` is
part of the deal.

---

## How a run works

```
   ┌──────────────┐   public job-board APIs
   │  Inspector   │──────────────────────────► Greenhouse / Lever / Ashby
   └──────┬───────┘   (rate limited, polite)
          │ postings not seen before
          ▼
   ┌──────────────┐
   │  Evaluator   │──────────────────────────► OpenRouter  →  two fit scores,
   └──────┬───────┘   CV + interests + posting    transferable skills, gaps
          │
          ▼
   ┌──────────────┐        ┌──────────────┐
   │   SQLite     │───────►│ Alert daemon │──► SMTP digest (never twice)
   └──────┬───────┘        └──────────────┘
          │
          ▼
   ┌──────────────┐
   │   Portal     │  filter matrix · 3-column grid · bookmarks
   └──────────────┘
```

Only postings the engine has never seen are sent for scoring — inference is
the expensive step, so a second run over the same board costs nothing.

---

## Configuration

Everything lives in `data/setup.json`, created on first save. Start from
`setup.example.json` if you prefer to write it by hand.

Secrets are never sent to the browser: the settings screen shows `••••••••`
for the API key and SMTP password, and saving the masked form leaves the
stored values untouched. The file is written `0600`.

Environment variables override the file, which is handy for containers:

| Variable | Effect |
| --- | --- |
| `ACIDE_DATA_DIR` | Where state lives (default `./data`) |
| `ACIDE_OPENROUTER_API_KEY` | Overrides the stored key |
| `ACIDE_SMTP_PASSWORD` | Overrides the stored SMTP password |
| `ACIDE_PUBLIC_URL` | Base URL used in digest links (default `http://localhost:8000`) |
| `ACIDE_HOST` / `ACIDE_PORT` | Bind address (default `127.0.0.1:8000`) |
| `ACIDE_CORS_ORIGINS` | Extra allowed origins, comma separated |

### Compensation filtering

The `Rate` / `Currency` / `Amount` row compares postings quoted in different
units by annualising each figure and converting it to USD. The exchange rates
are a coarse static table — good enough for "at least about 150k", not a
pricing source. Tick **Match posting currency** to compare like for like and
take exchange rates out of the comparison entirely.

Postings with no published salary are **kept**, not hidden: ATS feeds omit pay
far more often than they publish it, and filtering them out empties the grid.

---

## Email alerts

Press **Alert** to turn the current filter state into a subscription. After
each inspection run, every active subscription gets one digest containing the
matching postings it has not already been sent — tracked in a dispatch ledger,
so nothing arrives twice even if a posting is re-scored.

A posting is only emailed if it clears one of the two thresholds in Settings
(default 75% on either vector). Every digest carries a working one-click
unsubscribe link.

Gmail needs an **app password**, not your account password.

---

## Development

```bash
# Backend: tests and lint
backend/.venv/bin/pip install -r backend/requirements-dev.txt
cd backend && .venv/bin/python -m pytest && .venv/bin/ruff check .

# Portal: tests, typecheck, dev server with hot reload
cd frontend
npm test          # vitest
npm run typecheck
npm run dev       # http://localhost:5173, proxying /api to the backend
```

Both suites run on every push via `.github/workflows/ci.yml`.

Run the API separately while developing the portal:

```bash
backend/.venv/bin/python -m acide --reload
```

### Layout

```
backend/acide/
  main.py          FastAPI app; serves the API, the SPA and the legal docs
  models.py        Pydantic schemas shared by every layer
  db.py            SQLite storage + the one query builder behind all filtering
  compensation.py  Rate/currency normalisation and salary parsing
  llm.py           OpenRouter client and the dual-vector evaluator
  mailer.py        SMTP delivery and the HTML digest
  alerts.py        Subscription matching and dispatch
  scheduler.py     Background loop for scheduled runs
  logbus.py        In-memory fan-out behind the live SSE console
  spider/          Connectors: base, greenhouse, lever, ashby, runner
  api/             Routers: jobs, alerts, config, spider
frontend/src/
  App.tsx          View state, filters, infinite job query
  components/      Header, FilterMatrix, JobCard, JobGrid, modals, Settings
  hooks/           Persisted filter state and debouncing
```

### Tests

**171 tests: 98 backend, 73 portal.**

The backend suite covers the filter query builder, compensation maths, all
three connectors (against recorded board payloads), the evaluator's handling of
malformed model output, alert deduplication, and the HTTP surface.

The portal suite covers query serialisation, every filter control, the card's
dual-vector rendering, dialog behaviour, the alert flow, the live SSE console,
and App-level integration against a stateful fake backend.

Three bugs that only appear when the whole thing runs have regression guards:
the SPA catch-all route (invisible to tests until a build exists), connector
registration (an empty registry outside the test process), and grid staleness
on a query-cache hit.

```bash
cd backend && .venv/bin/python -m pytest -q
cd frontend && npm test
```

---

## Deploying

ACIDE-Watch ships with **no authentication** and binds to `127.0.0.1` by
default. It is built for one person on one host. If you expose it, put a
reverse proxy with authentication in front of it — your OpenRouter key, SMTP
password and CV are all reachable through the settings API.

---

## Privacy in one paragraph

Your CV text and your pivot interests are sent to OpenRouter alongside each
job description, because that is what the scoring needs. Nothing else leaves
your host except the alert emails you configure. There is no analytics, no
tracking, and no third party beyond those two. Deleting `data/` removes
everything. The full text is in [`docs/PRIVACY.md`](docs/PRIVACY.md); terms of
use are in [`docs/TERMS.md`](docs/TERMS.md), and both are served to the portal's
footer modals from those files so the UI cannot drift from the documents.

---

## Disclaimer

ACIDE-Watch is an aggregator, not an employer or recruiter. It does not
guarantee that any listing is accurate, current, or real. Fit scores are a
triage aid produced by a language model — they are not an assessment of you or
of any role, and they will sometimes be wrong.
