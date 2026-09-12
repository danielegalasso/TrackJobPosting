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

# 1. Backend — editable install, so `acide` works from any directory
python3 -m venv backend/.venv
backend/.venv/bin/pip install -e backend/

# 2. Portal
cd frontend && npm install && npm run build && cd ..

# 3. Run — serves the API and the portal from one process
backend/.venv/bin/acide
```

`pip install -e backend/` (not `-r backend/requirements.txt`) is what makes
step 3 work from the repo root: it installs the runtime dependencies *and*
registers `acide` as an importable package and a console script, rather than
leaving it importable only from inside `backend/`. `backend/.venv/bin/python
-m acide` works the same way, from anywhere.

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

### Importing a list of companies

If you already have a curated list, hand it over instead of typing tokens:

```bash
backend/.venv/bin/acide import-companies companies.json          # report only
backend/.venv/bin/acide import-companies companies.json --apply  # add the feeds
```

The file is a JSON array of `{organization, category, website, careers_page}`.
A careers page is *not* a board endpoint, so each one is fetched once and read
for the ATS it is wired to — one polite request per company. Anything that
resolves is verified against the board's own API before being written, so a
stale link never becomes a target that 404s on every run.

Everything else is written to `data/import-report.json` with the reason,
grouped by platform, so the list stays a worklist rather than silently
shrinking. The summary names what the rest are running on — `workday`,
`successfactors`, `taleo`, `eu-careers` and so on — which tells you which
connector would unlock the most companies next, instead of just "unresolved".

Expect the plain HTTP pass to resolve only a minority — around 4% on a
real 631-entry list. The dominant reason is not a missing connector: it is
that roughly **half of careers pages are JavaScript applications**, and the
board link simply is not in the HTML a plain fetch receives. Another fifth
are stale URLs that 404.

### Browser mode, for the pages that need JavaScript

```bash
pip install -e 'backend/[browser]' && playwright install chromium

# retry only what failed last time, in a browser
backend/.venv/bin/acide import-companies data/import-report.json \
    --retry-report --browser -v
```

The page is opened in a real browser, and the board token is read from two
places: the **network requests** the page makes while rendering (to show a
board it must call the ATS, token and all — no guessing), and the DOM once
scripts have run. Stale URLs also get a second chance at the usual careers
paths on the same domain.

To use your own Chrome — your profile, your logins, your cookies — start it
with remote debugging and attach:

```bash
google-chrome --remote-debugging-port=9222 --user-data-dir="$HOME/chrome-agent-profile"
backend/.venv/bin/acide import-companies companies.json --browser --cdp-url http://localhost:9222
```

`--chrome-path /usr/bin/google-chrome` drives an installed browser without
CDP; `--show-browser` runs it headed so you can watch.

**It saves as it goes.** Six hundred pages at human pace is well over an
hour, so the report is written every ten organizations, and again if the run
stops early — Ctrl-C, a browser crash, a closed laptop. The saved report
lists the organizations that were never reached as well as the ones that
failed, so resuming picks up the whole remainder rather than only the
failures:

```bash
backend/.venv/bin/acide import-companies data/import-report.json \
    --retry-report --browser -v
```

`-v` progress appears as it happens even when you redirect it to a file.

**What browser mode does not do.** It renders public pages you could open
yourself, one at a time, with a pause between them, and it honours
`robots.txt` — a `Disallow` it can read is obeyed. It does distinguish that
from a `robots.txt` it could not read: a WAF answering the fetch with 403 has
forbidden nothing, and RFC 9309 treats every 4xx as "no robots.txt applies".
A 5xx is taken as the standard says, assume disallow. (Python's own
`RobotFileParser` follows the older convention of reading 401/403 as
disallow-all, which made the sites most likely to run a WAF look like the
ones refusing us — 125 of a real 631-entry list.) It does not spoof fingerprints, patch `navigator.webdriver`,
solve challenges or rotate addresses — a 403 or 429 is recorded as the
answer, not something to get around. The browser is only used for
*discovery*: once a board token is known, the ordinary JSON connector takes
over, so no browser is involved in recurring indexing.

Useful flags: `--category "Defense Tech,Space Economy"` to work in batches,
`--limit` to try a handful first, `-v` to see each company, and `--guess` to
also probe likely board tokens when a page gives nothing away. Guessing is
off by default because it turns one request per company into several, nearly
all of them 404s against somebody else's API.

Re-running is safe: existing targets are never overwritten, so a token you
corrected or a company you disabled by hand stays that way.

### When the list itself has rotted

A hand-researched list decays. On a real 631-entry list, 136 careers URLs
answered 404 and 20 domains no longer resolved. `check-urls` finds where
those pages went:

```bash
backend/.venv/bin/acide check-urls companies.json -v \
    --write companies.fixed.json
```

For each dead link it follows redirects, then reads the **site's own
navigation** for the link a visitor would click — multilingual, because this
kind of list is full of `lavora-con-noi`, `karriere`, `carrieres` and
`vacatures` — and only then falls back to guessing conventional paths. Every
candidate is fetched and checked before it is proposed, so a suggestion is
never just a plausible-looking URL.

`--write` produces a corrected copy of your list; the original is untouched,
and every field it does not understand — an `id`, your own notes — is carried
through unchanged. A 403 is reported rather than repaired: a WAF refusing a
script does not mean the page moved.

A redirect is trusted only when it lands somewhere that still looks like a
careers page, by host or by path. Retiring `/careers` by pointing it at the
homepage is common, and taking that at face value would swap a merely stale
URL for a definitely wrong one — so those are repaired from the site's own
navigation instead, and the redirect destination is kept only when nothing
better exists. The summary says how many ended up in that last category.

### Scope of the inspector

The recurring inspector reads each provider's **own published job-board
API** — the same documented JSON endpoint the employer's careers page calls.
That is a deliberate boundary:

- The scheduled indexing **never** drives a browser. A browser is used only
  once, during import, to discover which board a careers page belongs to;
  after that the JSON API does all the work.
- It does **not** attempt to bypass bot defences, challenges, or rate limits,
  in either mode.
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
# Backend: editable install + dev deps, tests and lint
backend/.venv/bin/pip install -e backend/ -r backend/requirements-dev.txt
cd backend && .venv/bin/python -m pytest && .venv/bin/ruff check .

# Portal: tests, typecheck, dev server with hot reload
cd frontend
npm test          # vitest
npm run typecheck
npm run dev       # http://localhost:5173, proxying /api to the backend
```

Both suites run on every push via `.github/workflows/ci.yml`.

Run the API separately while developing the portal — works from any
directory once `acide` is installed:

```bash
backend/.venv/bin/acide --reload
```

### Layout

```
backend/acide/
  main.py          FastAPI app; serves the API, the SPA and the legal docs
  models.py        Pydantic schemas shared by every layer
  discovery.py     Reads a careers page to find which ATS it runs on
  browser_discovery.py  The same, for pages that need JavaScript to run
  watchlist.py     Imports a curated company list into career feeds
  linkcheck.py     Checks careers URLs and repairs the dead ones
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

**316 tests: 221 backend, 95 portal.**

The backend suite covers the filter query builder, compensation maths, all
three connectors (against recorded board payloads), the evaluator's handling of
malformed model output, alert deduplication, company-list import, and the HTTP
surface. The mailer is exercised against a real SMTP server running
in-process — no test contacts an external provider.

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
