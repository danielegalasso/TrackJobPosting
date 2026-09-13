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
| `teamtailor` | `acme.teamtailor.com`, or `careers.acme.com` | `acme`, or the full host |
| `personio` | `acme.jobs.personio.de` | `acme` |
| `recruitee` | `acme.recruitee.com` | `acme` |
| `workable` | `apply.workable.com/acme` | `acme` |
| `smartrecruiters` | `careers.smartrecruiters.com/Acme` | `Acme` |
| `workday` | `nxp.wd3.myworkdayjobs.com/careers` | `nxp.wd3.myworkdayjobs.com/careers` |
| `breezy` | `acme.breezy.hr` | `acme` |
| `jsonld` | *any careers page* | `https://acme.com/careers` |
| `browser` | *any careers page, rendered* | `https://acme.com/careers` |

Every one of these is a **published feed the employer's own careers page
reads**, without a key — the same boundary throughout: a documented public
endpoint, never a scraped page.

Workday is the odd one. Its endpoint is addressed by careers host *and*
career-site name, so the token carries both; a full URL works too, locale
segment and all. It answers a POST rather than a GET, which is why a Workday
page looks empty to a plain fetch, and it silently returns nothing at all for
a page size above 20 — so pages are requested at 20 and walked.

Teamtailor is read through the RSS feed its own documentation describes
(`/jobs.rss`), not the `jobs.json` that scraper vendors repeat and that does
not exist — a real run found ten tenants through the latter and every one
refused. RSS carries metadata rather than the whole advert, so Teamtailor
descriptions are shorter than a JSON board's; that is the trade for an
endpoint that answers. Many Teamtailor career sites run on the employer's own
domain, so the token may be a full host.

Personio publishes XML rather than JSON, and some tenants live on
`.jobs.personio.com` instead of `.de`; both are tried. SmartRecruiters and
Workday carry no advert text in their listings, so each posting's own
document is fetched for it — a posting whose advert cannot be read is still
reported, with its title, rather than dropped.

### Pages with no ATS at all

`jsonld` is not an ATS. It reads a page's **own** postings out of its
schema.org `JobPosting` markup — the structured data Google for Jobs consumes,
which employers who want their roles found have every reason to publish. Title,
description, location, posting date and salary all come from it, already
structured, so this is not scraping a layout that changes next week.

On a real 631-entry list, "page rendered, no ATS link" was 377 organizations —
the largest failure by a wide margin. Two things account for much of it:

- **A platform nobody recognised.** Telespazio Belgium's careers page names no
  ATS, but its "Discover our positions here" button points at
  `telespazio-be.breezy.hr`. Breezy is now a connector, so the link on the page
  is enough — nothing has to be clicked. The same was true of BambooHR,
  Pinpoint, HiBob, Werecruit and Jobvite, which are now at least named instead
  of being reported as no ATS at all.
- **The page publishing its own postings.** Those become `jsonld` targets,
  whose token is the careers page URL.

Two page shapes are read. Postings on the page are taken in one request. A page
that is only an index — an `ItemList` of links — is followed to each posting,
bounded by `max_jobs_per_source` and paced like any other request.

A board API always wins over reading a page, so a careers page carrying both
resolves to the board. But structured postings beat *naming* a platform with no
connector: "runs on successfactors" is a worklist entry, while postings on the
page are readable today.

The honest limit: a page that builds its list with JavaScript and embeds no
JSON-LD until it does is reported as empty rather than guessed at. Reading it
would need a browser at indexing time, which scheduled indexing deliberately
does not do.

### Pages with an interface of their own

Some employers have no ATS, no feed, and no structured data: the careers page
renders a list with JavaScript, sometimes behind a "Load more" button, in a
layout written for that company alone. On a real 631-entry list that is about
250 organizations — the largest group left by a wide margin.

`browser` indexes those. Its token is the careers page URL, and per page it:

1. renders the page, waits for its requests to go quiet, scrolls, and clicks a
   "load more" control while one keeps appearing (multilingual — *Mehr laden*,
   *Carica altri*, *Voir plus*);
2. finds the job list **generically**, by clustering links on the shape of
   their URL. Every posting in a list shares a path prefix and differs only in
   its last segment, which holds for Breezy, for a hand-built Vue page, and for
   anything that gives one URL per role. A navigation bar shares a shape too,
   so the link text must read like job titles, and a job-shaped path (`/p/`,
   `/careers/jobs/`, `/vacatures/`) outranks a merely larger group — a newsroom
   is routinely longer than the job list;
3. opens each role and reads it: its own schema.org `JobPosting` first, then
   Open Graph, then the visible heading. Which reading was used is recorded, so
   a weak one is visible as weak.

Search terms are applied to the **list**, before any role is opened, so
narrowing costs one render rather than one per role.

> **This is the only source that runs a browser while indexing.** Every other
> connector reads a published endpoint, and a scheduled run starts no browser
> unless a `browser` target is configured — then one is started and shared. It
> is opt-in per target for that reason: rendering is slow, heavier, and a weaker
> contract than an API.

The boundary is unchanged: public pages, one at a time, at human pace,
`robots.txt` honoured on every posting as well as the list, no fingerprint
spoofing, no challenge solving. A refusal is recorded as the answer.

A page whose roles are listed without linking each one is reported as such
rather than guessed at. That residue — and only that — is where reading a page
with a language model earns its cost.

### Narrowing a corporate board

A corporate careers site is not a startup board. Thales, Airbus, Accenture and
Booz Allen each publish around two thousand roles worldwide, and Workday caps
its own reported total at 2,000 — so the real number may be higher. Indexing
`max_jobs_per_source` of those gives an arbitrary slice, and every one of them
is sent to the evaluator and billed for.

`spider.search_terms` fixes that. Only roles whose title contains one of the
terms are indexed, case-insensitively, as substrings — so `cyber` catches
*Cybersecurity Engineer* and *Cyber Defence Analyst*:

```json
"spider": {
  "search_terms": ["cyber", "security", "soc", "threat", "incident", "pentest"]
}
```

Where the provider has a search of its own — **Workday** and
**SmartRecruiters** — the narrowing happens server-side: one request per term
instead of walking a hundred pages, and their search covers the advert as well
as the title, so a *Security Engineer* found by searching `cyber` is kept.
Everywhere else the filter is applied to the listing, before any per-posting
request, so a large board costs one listing rather than hundreds of fetches.

Filtering happens **before** the cap, which is the whole point: a cap of 120
against a board whose first 120 roles are all logistics would otherwise yield
nothing relevant at all. A board bigger than the cap says so in the log rather
than quietly truncating.

Leave `search_terms` empty to take every posting. A single target can override
the global list with its own `search_terms`, for an employer whose titles use a
vocabulary of their own.

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

Each page is given a fair chance to render: the run waits for the page's own
requests to go quiet, scrolls (a board below the fold does not fetch until it
is reached), and only then reads it. If the careers page still names no ATS,
**one link deeper is followed** — "See our open positions", "Offene Stellen",
"Posizioni aperte" — because a landing page often never calls the ATS at all
and the page behind its button does. That single shape was the largest
failure on a real 631-entry list: 200 of them said "no ATS link found".

A page shared by several organizations is fetched **once per run**. Eight
Thales divisions list the same careers site, as do eight EU bodies; each
organization still gets its own answer, from one visit rather than eight.

To use your own Chrome — your profile, your logins, your cookies — start it
with remote debugging and attach:

```bash
google-chrome --remote-debugging-port=9222 --user-data-dir="$HOME/chrome-agent-profile"
backend/.venv/bin/acide import-companies companies.json --browser --cdp-url http://localhost:9222
```

`--chrome-path /usr/bin/google-chrome` drives an installed browser without
CDP; `--show-browser` runs it headed so you can watch; and `--profile-dir
~/.acide-chrome` keeps a profile between runs, so cookie-consent choices
persist instead of a banner covering the board on every visit.

If pages are refusing you, reach for `--cdp-url` before anything else. A
headless bundled Chromium is the configuration sites treat with most
suspicion; your own Chrome, with your profile and your address, is not
imitating a person's browser — it is one.

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

### What is actually on the unresolved pages

A browser pass over six hundred pages takes over an hour. Most of the question
— is there anything readable here at all — is answerable over plain HTTP in a
couple of minutes:

```bash
backend/.venv/bin/acide probe data/import-report.json -v
```

It re-judges each unresolved careers page with the current discovery rules and
sorts them into four answers:

| verdict | what it means |
| --- | --- |
| links a board we can read | a connector arrived since the last run; re-import |
| publishes its own postings | schema.org JobPosting on the page — a `jsonld` target |
| runs on *platform* | named, but no connector yet |
| needs a browser | the list really is built by JavaScript |

Nothing is written and no board is contacted — it is a dry run of discovery,
and the last row is the only one a browser pass would help with. `--all` probes
a whole companies list rather than only the unresolved entries of a report.

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
   │  Inspector   │──────────────────────────► 9 public ATS job feeds
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
  pagestructure.py Find the job list on an unfamiliar page; read one posting
  probe.py         Dry run of discovery over plain HTTP
  spider/          Connectors: base, greenhouse, lever, ashby, workday,
                   breezy, jsonld, rendered,
                   teamtailor, personio, recruitee, workable,
                   smartrecruiters, runner
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
