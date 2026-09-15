# ACIDE-Watch — engineering log and handover

**Audience:** whoever (or whatever) picks this project up next, cold, with no
memory of the sessions that built it. It is written for a language model as
much as for a person: it states not only what the code does but *why each
decision was made, what was tried and abandoned, and which mistakes were
expensive*, because several of them are the kind that look reasonable and cost
a night of compute.

**Status at time of writing:** branch `claude/acide-watch-job-portal-58yifc`,
head `bef4816`+, CI green, 451 backend tests + 97 portal tests, ruff clean.
No pull request has ever been opened.

**Provenance of the numbers below.** Statistics come from real runs over the
operator's private 631-organization list, recorded in commit messages at the
time. The report files themselves are gitignored (they contain private
research), so they cannot be re-derived from this repository. Numbers marked
`~` are approximate or were read from a report that no longer exists in the
tree.

---

## 0. The five facts that explain everything else

If you read nothing else, read these.

1. **There are two completely separate phases.** *Resolution* turns a list of
   company careers-page URLs into indexable "targets" (a source type + a board
   token). *Indexing* polls those targets for postings. Resolution is a
   one-off, human-supervised, browser-heavy activity. Indexing is scheduled,
   cheap and — with one deliberate exception — never opens a browser.

2. **Crawling and scoring are now separate operations, and this was learned the
   hard way.** An overnight run crawled 469 sources for four hours, produced
   2,649 postings, scored zero of them because of a malformed JSON schema, and
   **discarded all 2,649** — because the storage function required a verdict.
   Postings are now written to the database the instant they are found,
   unscored. `acide score` judges the backlog afterwards.

3. **The operator wants *everything*, and will filter in the portal.** Early
   design filtered at collection time with `spider.search_terms`. The operator
   explicitly redirected: gather all postings from all organizations, filter
   later in the web application. `search_terms` still exists and still works,
   but the default intent is now an empty list.

4. **The scope boundary on crawling is a hard rule, not a preference.** Every
   connector reads a *published, documented, keyless* feed. The one browser
   source renders public pages one at a time at human pace, honours robots.txt,
   and **never** spoofs fingerprints, patches `navigator.webdriver`, solves
   challenges, or rotates addresses. A 403 is recorded as the answer. Do not
   relax this to raise the resolution rate.

5. **The development environment cannot reach the internet.** Everything was
   verified against `respx` mocks, local fixture servers over real sockets, and
   a real headless Chromium pointed at local fixtures. "The connector matches
   the provider's documented payload shape and is pinned by tests" is a
   different claim from "the endpoint answers for this tenant", and the
   distinction is maintained throughout.

---

## 1. What the system is for

The operator is a cybersecurity/aerospace engineer with a curated list of **631
organizations** — defence primes, space agencies, EU institutions, security
vendors, national CERTs. They want a self-hosted job portal that:

- indexes the public career feeds of all 631 organizations;
- scores each posting on **two independent vectors**:
  - `experience_fit_score` — how well the role matches the CV as it stands;
  - `interest_fit_score` — how well it matches where they want to *go*
    (the pivot/growth vector);
- lets them browse and filter everything in a faang.watch-style grid;
- emails digest alerts when something crosses both thresholds.

**Why two scores, and why they must never be averaged.** A senior role the
operator could do today and a junior role in a field they want to move into are
both interesting, for opposite reasons. A single blended score hides exactly
the postings worth seeing. `ScoringConfig` has two independent thresholds
(default 75/75) and the portal has two independent sliders.

Companies explicitly named as wanted: Thales, Airbus, Leonardo, Telespazio,
Saab, Rheinmetall, BAE, Dassault — "I can see myself working for those
companies, I prefer to filter them afterwards."

---

## 2. Hard constraints

### 2.1 Credentials — read this before touching anything

- **Two credentials leaked into chat transcripts during this project**: an
  OpenRouter API key (`sk-or-v1-efc783bc…`) and a Gmail app password
  (`gadv rjpg jooc rpup`). Neither was ever used. **Both must be revoked by the
  operator.** Treat them as compromised.
- **Never ask the operator to send an API key.** The code reads it from
  `data/setup.json` on their machine. When the operator offered a key for
  agentic crawling work, the correct answer was given: decline, and explain
  that the key should not leave their machine.
- **The operator must never send `data/setup.json`.** It contains the
  OpenRouter key and the SMTP password.
- No test, fixture or example may touch the operator's real account.

### 2.2 Crawling scope — the boundary that does not move

Documented in `backend/acide/browser_discovery.py`'s module docstring and in
the README. Restating it because it is load-bearing:

- Every ATS connector reads a **published public feed** — the same endpoint the
  employer's own careers page calls, without a key. Never a scraped page.
- The `browser` source is the single exception and renders *public pages the
  operator could open themselves*, one at a time, with a pause between them.
- robots.txt is fetched and honoured, on posting pages as well as list pages.
- **Not done, ever:** fingerprint spoofing, `navigator.webdriver` patching,
  CAPTCHA or challenge solving, IP rotation, retrying around a refusal.
- A 403 or 429 **is the answer** and is recorded as such.
- `--cdp-url` attaches to the operator's own Chrome. That is not camouflage:
  it is their browser, with their profile, under their control.

### 2.3 The repository is public

The operator's 631-organization list is private research. `.gitignore` covers
`companies*.json`, `*companies*.csv`, `careers-page-verdicts.csv`,
`import-report.json`, `url-check.json`, `setup.json`, `data/`, `*.log`, the
SQLite database and CV files.

**A verdicts CSV containing all 631 organizations was once committed by a broad
`git add -A` and pushed to this public repository** (removed in `9b99ac5`; it
remains in branch history until that history is rewritten). Never run a wide
`git add` without checking what it swept up.

### 2.4 What "verified" means in this codebase

The development sandbox blocks outbound access to careers sites, ATS APIs and
OpenRouter. `WebFetch` is blocked; `WebSearch` works. So:

- Connectors are tested against `respx` mocks built from each provider's
  **documented** payload shape.
- Multi-step flows (link checking, probing, browser discovery) are tested
  against **local fixture servers over real sockets** — a simulated internet.
- Browser code is tested against **real headless Chromium** driving local
  fixture pages, including a deliberately hostile one (list built by
  JavaScript, three of five roles behind a "Load more" button, no structured
  data, every role linked three times).

This is why several bugs only appeared on the operator's real runs. The
pattern that worked: **the operator runs it, sends the report, and every defect
the report exposes becomes a test built from the exact token, URL or payload
that caused it.**

---

## 3. Architecture

### 3.1 Layout

```
backend/acide/
  __main__.py          923 lines — the CLI: serve, import-companies, check-urls,
                       probe, adopt-browser, inspect, score, sources
  main.py              FastAPI app; serves the API and the built portal
  api/                 config.py, jobs.py, alerts.py, spider.py
  models.py            Pydantic v2 models: SetupConfig, TargetSource,
                       RawPosting, JobEvaluation, Job, SOURCE_TYPES
  db.py                713 lines — SQLite (WAL), schema, migrations, queries
  llm.py               415 lines — OpenRouter client, strict structured output,
                       model catalogue, preflight, handshake
  config.py            loads/saves data/setup.json
  resume.py            the operator's CV, as text for the prompt
  alerts.py, mailer.py digest emails, SMTP with Gmail-specific diagnostics
  scheduler.py         periodic inspection
  logbus.py            in-process pub/sub, streamed to the portal console
  compensation.py      parses pay out of prose when the feed has none
  watchlist.py         528 lines — curated list → resolved targets
  discovery.py         305 lines — read the ATS out of a page's markup
  linkcheck.py         353 lines — find where a rotted careers URL went
  browser_discovery.py 649 lines — Playwright: render, robots, hops, load-more
  pagestructure.py     270 lines — GENERIC job-list finder (see §6.3)
  probe.py             239 lines — what an unresolved page actually contains
  spider/
    base.py            connector registry, shared HTTP, iso_date, strip_html
    greenhouse.py lever.py ashby.py workday.py teamtailor.py personio.py
    recruitee.py workable.py smartrecruiters.py breezy.py
    jsonld.py          schema.org JobPosting, on the page itself
    rendered.py        the browser source
    runner.py          303 lines — one inspection pass
frontend/src/          Vite + React 19 + TypeScript + Tailwind + TanStack Query
scripts/overnight.sh   prechecked, detached, unattended run
docs/                  PRIVACY.md, TERMS.md, this file
```

### 3.2 The two phases

```
PHASE 1 — RESOLUTION (one-off, supervised, browser allowed)

  companies.json (631 orgs, careers_page URL each)
        │
        ├─ acide check-urls ──────► repair rotted URLs (redirect → site nav → guess)
        │
        ├─ acide import-companies ─► fetch each careers page, read the ATS out
        │      │                     of its markup → greenhouse:token, lever:token…
        │      └─ --browser ───────► render it first, for pages that inject the
        │                            board link at runtime (about half of them)
        │
        ├─ acide probe ───────────► what is on the pages that still did not
        │                            resolve — cheap, plain HTTP, no browser
        │
        └─ acide adopt-browser ───► turn "renders its own list" pages into
                                     `browser` targets
        │
        ▼
  data/setup.json  ·  targets: [{company, source_type, board_token, enabled}]

PHASE 2 — INDEXING (repeatable, scheduled or one-shot)

  acide inspect [--no-score] [--retry-failed] [--source-type …] [--limit N]
        │
        ├─ per target: connector.fetch() → RawPosting[]
        ├─ db.store_posting()  ← written immediately, unscored
        ├─ db.record_source_run()  ← status/detail/count, per source
        └─ (unless --no-score) score this source's new postings now

  acide score [--limit N] [--yes]   ← judge the backlog, in batches
  acide sources                      ← what happened; what to re-run
  acide serve                        ← portal + API
```

### 3.3 Data model

`jobs` — one row per posting, keyed by a hash of
`(source_type, company, external_id)`:

| column | note |
| --- | --- |
| `id`, `external_id`, `source_type`, `company` | identity |
| `title`, `location`, `apply_url`, `date_posted` | facts from the feed |
| `seniority`, `category`, `years_experience_min` | evaluator's reading |
| `rate`, `currency`, `amount` | pay; a published figure beats the model's guess |
| `experience_fit_score`, `interest_fit_score` | **the two vectors, never merged** |
| `category_type` | Direct Match / Pivot / Unrelated |
| `transferable_skills`, `skills_to_learn` | JSON arrays |
| `alert_summary` | one line for the digest email |
| `scored` | **0 until judged.** The column that makes crawl-once possible |
| `saved`, `dismissed` | operator-owned; never overwritten by a crawl |
| `created_at`, `updated_at` | |

`source_runs` — one row per `(source_type, board_token)`, written on both the
success and the failure path:

```sql
CREATE TABLE IF NOT EXISTS source_runs (
    source_type  TEXT NOT NULL,
    board_token  TEXT NOT NULL,
    company      TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'ok',      -- 'ok' | 'error'
    detail       TEXT NOT NULL DEFAULT '',        -- the failure message
    postings     INTEGER NOT NULL DEFAULT 0,
    attempted_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (source_type, board_token)
);
```

This table is what makes `acide inspect --retry-failed` possible: it filters out
every source whose `(source_type, board_token.lower())` is in
`db.succeeded_source_keys()`.

Also: `alert_subscriptions`, `alert_dispatches` (guarantees a subscriber is
never emailed the same posting twice), `meta`.

**Migrations.** `CREATE TABLE IF NOT EXISTS` does nothing to a table that
already exists, so added columns need explicit handling:
`_ADDED_COLUMNS: tuple[tuple[table, column, definition], ...]` plus
`_apply_added_columns()`. When `scored` was added, everything already in the
database was marked scored — which is what the old code guaranteed, since
nothing could be stored without a verdict. Verified against a database built
in the old shape: rows, saved flags and scores all preserved, `init_db` still
idempotent.

### 3.4 Configuration — `data/setup.json`

```jsonc
{
  "openrouter": {
    "api_key": "…",                 // never leaves the operator's machine
    "model": "google/gemini-2.5-flash",
    "max_concurrency": 4,
    "temperature": 0.1,
    "reasoning_effort": "none",     // only models advertising it honour this
    "max_tokens": 10000
  },
  "email":   { "smtp_server": "smtp.gmail.com", "sender_password": "…" },
  "scoring": { "experience_threshold": 75, "interest_threshold": 75 },
  "spider": {
    "enabled": false,
    "interval_minutes": 360,
    "request_delay_seconds": 1.5,
    "max_jobs_per_source": 120,     // ≤ 5000
    "search_terms": []              // EMPTY = index everything (current intent)
  },
  "interests": ["…"],               // feeds the interest_fit vector
  "targets": [
    {"company": "NVISO", "source_type": "greenhouse", "board_token": "nviso",
     "enabled": true, "search_terms": []}
  ]
}
```

A target may override the global `search_terms` with its own, for an employer
whose titles use a vocabulary of their own.

---

## 4. The resolution pipeline, command by command

### 4.1 `acide check-urls FILE [--write FIXED.json]`

**Problem it solves.** A hand-researched list decays. The real 631-entry list
contained **136 careers URLs answering 404** and **20 domains that no longer
resolve** — about a quarter of it had rotted. Those companies are still hiring;
the path moved.

**Repair, in descending order of trust** (`linkcheck.py`):

1. **Follow redirects** — a 301 is the site telling us where the page went.
2. **Read the site's own navigation** — fetch the homepage, find the link a
   visitor would click. This is the reliable route. It is **multilingual**
   because a European list is full of `lavora-con-noi`, `karriere`,
   `carrieres`, `vacatures`, `offene-stellen`, `werken-bij`.
3. **Try conventional paths** — `/careers`, `/jobs` and friends. Last, because
   it is guessing.

**Every candidate is fetched and confirmed before being proposed.** A
suggestion that 404s is worse than no suggestion, so `broken` is a real verdict
the tool will give. A 403 is reported, not repaired: a WAF refusing a script is
not evidence that a page moved.

**The redirect trap (fixed in `b16389d`).** Any 2xx after redirects was
accepted and the final URL proposed. But **retiring `/careers` by pointing it
at the homepage is common**, and a homepage is not a careers page — so a merely
stale URL was being replaced with a definitely wrong one, and a browser was
then sent to index it. A redirect is now trusted only when the destination
still carries a careers signal in host or path
(`careers.thalesgroup.com/global/en` does, via the host; `nviso.eu/jobs` via the
path; `acme.com/` does not). **191 of 631 came back "moved"**, so this was not
a rare corner.

```python
def looks_like_careers_url(url: str) -> bool:
    parsed = urlparse(url)
    haystack = f"{parsed.netloc}{parsed.path}".lower().replace("_", "-")
    return any(word in haystack for word in CAREERS_WORDS)
```

### 4.2 `acide import-companies FILE [--apply] [--browser]`

Fetches each careers page once and reads the ATS out of its markup
(`discovery.py`): Greenhouse embeds and board links, Lever handles, Ashby
boards, and the other six. **Whatever it finds is verified against that board's
own API before being written**, so a stale link on a careers page never becomes
a permanently failing target.

- Existing targets always win on merge: a token the operator fixed, or a
  company they disabled, survives a re-import. Re-running adds nothing the
  second time.
- `--guess` (board tokens from company name/domain) exists but is **off by
  default**: it turns one request per company into several, nearly all 404s
  against somebody else's API.
- `--category` and `--limit` keep a 600-entry list workable in batches.
- Unsupported platforms are **named** (SuccessFactors, Taleo, EPSO, BambooHR,
  Pinpoint, HiBob, Werecruit, Jobvite) rather than reported as "no ATS found",
  which turns a dead end into a worklist entry saying which connector would
  unlock how many companies.

**Duplicate careers pages are reported, not collapsed** (`00e324b`). 631
entries are **581 distinct careers URLs**, and the duplicates are two different
situations that only the operator can tell apart:

- Eight Thales divisions, five Airbus entities, three Leonardo, three
  Telespazio, three Saab genuinely share one corporate careers system. One
  target is right; the division is a property of the posting.
- Eight EU bodies sharing `eu-careers.europa.eu` is the other kind. CINEA,
  HaDEA, EISMEA and the Chips Joint Undertaking each publish their own
  vacancies on their own site; the shared portal is where nobody found the
  specific page. **Those want splitting.**

### 4.3 `--browser` — resolution for pages that need JavaScript

**The finding that reshaped the project.** A real 631-organization import
resolved **23**. The breakdown was not what was predicted:

```
304  page loaded, no ATS link in the raw HTML     ← the whole game
136  404, the listed URL is stale
 58  403, refused a non-browser request
~60  Workday / SuccessFactors / EPSO / others
```

The missing Workday connector accounted for 19 — a prediction that the largest
gap was a connector gap was **wrong**. Anduril, Wiz, Snyk, Cloudflare and
SentinelOne are all in that first group and all of them *do* use Greenhouse,
Lever or Ashby: **the link is injected at runtime**, so fetching raw HTML can
never see it however the request is dressed up.

The browser reads the token from two places:

1. **Network requests the page makes while rendering.** To show a board it
   *must* call the ATS — `boards-api.greenhouse.io/v1/boards/<token>/jobs`,
   `api.ashbyhq.com/posting-api/job-board/<token>`. This is exact, not guessed.
2. **The rendered DOM**, iframes included, once scripts have run.

Flags: `--cdp-url` (attach to the operator's own Chrome), `--chrome-path`,
`--show-browser`, `--profile-dir` (persists cookie-consent state between runs
so sites stop covering the board with an interstitial), `--retry-report`.

### 4.4 `acide probe REPORT [--all]`

Browser mode over six hundred pages takes more than an hour. Before spending
that, most of the question can be answered in a couple of minutes over plain
HTTP: does the page link to a board now recognised; does it publish schema.org
`JobPosting`; or does it genuinely render everything with JavaScript — the only
case where a browser is the answer. Writes nothing to `setup.json`, contacts no
board.

### 4.5 `acide adopt-browser REPORT [--apply] [--limit N]`

Turns "renders its own list" probe verdicts into `browser` targets. Rehearsed
against the real 523-page probe report: **265 pages adoptable**.

---

## 5. The twelve source types

| type | endpoint | notes |
| --- | --- | --- |
| `greenhouse` | `boards-api.greenhouse.io/v1/boards/{t}/jobs?content=true` | full advert in the listing |
| `lever` | `api.lever.co/v0/postings/{t}?mode=json` | |
| `ashby` | `api.ashbyhq.com/posting-api/job-board/{t}` | `includeCompensation` gives pay bands |
| `workday` | `POST {host}/wday/cxs/{tenant}/{site}/jobs` | see below — the awkward one |
| `teamtailor` | `https://{host}/jobs.rss?per_page=200` | RSS, **not** `jobs.json` |
| `personio` | `https://{t}.jobs.personio.de/xml` | XML; `.de`/`.com` fallback |
| `recruitee` | `https://{t}.recruitee.com/api/offers/` | careers-site API, not the keyed ATS API |
| `workable` | `apply.workable.com/api/v1/widget/accounts/{t}?details=true` | |
| `smartrecruiters` | `api.smartrecruiters.com/v1/companies/{t}/postings` | 100/page cap; takes `q` |
| `breezy` | `https://{t}.breezy.hr/json?verbose=true` | one request for a whole board |
| `jsonld` | the careers page URL itself | schema.org `JobPosting` |
| `browser` | the careers page URL itself | **the only source that renders while indexing** |

### Per-connector lessons

**Workday** — the largest and most awkward. Its endpoint is addressed by
careers host *and* career-site name, so its token carries both
(`nxp.wd3.myworkdayjobs.com/careers`) with any locale segment dropped. It
answers a **POST, not a GET**, which is why a Workday page looks empty to a
plain fetch and why people conclude no API exists. **It returns an empty page
with no error for any page size above 20**, so pages are requested at
`PAGE_SIZE = 20` and walked — asking for 100 silently yields nothing. Listings
carry no advert text, so each posting's own document is fetched for it.

**Teamtailor** — *the connector that shipped broken.* Ten tenants were
discovered through `jobs.json` and every single one refused: the only platform
with a connector that resolved nothing at all. **`jobs.json` does not exist.**
It is repeated by scraper vendors; Teamtailor's own support documentation
describes an **RSS feed** reached by appending `.rss` to the jobs page,
accepting `per_page` and `offset`. Four tests had been written against the
imaginary endpoint, and they are precisely what let it through. After the
rewrite: **7 organizations across 6 boards**, junk tokens gone, boards
discovered-but-refusing down from 14 to 7.

RSS carries metadata rather than the whole advert, so descriptions are shorter
than a JSON board's. That is the trade for an endpoint that answers.

The token may also be a **full host**, because many Teamtailor career sites run
on the employer's own domain (`careers.sateliot.com`, `aerospace-jobs.sener`).
When markup names no tenant, discovery falls back to the page's own host —
and `feed_urls()` tries the www-stripped host, then `careers.`, then `jobs.`,
because falling back to the *marketing* host (`www.exotrail.com`) was a
mistake that cost five of the seven remaining failures.

**Personio** — the only XML feed. Some tenants are served from
`.jobs.personio.com` rather than `.de`, so both hosts are tried before a tenant
is called dead.

**Recruitee** — runs two APIs. `api.recruitee.com` is the ATS API and needs a
per-customer token. The **careers-site API**, served from the customer's own
subdomain, is public. Use that one.

**SmartRecruiters and Workday both take a keyword**, so when `search_terms` is
set the narrowing happens server-side — one request per term rather than a
hundred pages of listings, deduplicated across terms. This matters most where
it costs most: both need a second request per posting, so filtering first turns
two thousand detail fetches into a few dozen.

**A posting whose advert cannot be read is still reported with its title**
rather than dropped. Losing the role entirely is worse than losing its
description.

**`jsonld`** — employers who want their roles in Google for Jobs emit
schema.org `JobPosting` as JSON-LD. Postings are found however a page nests
them: bare, in an array, under `@graph`, or as an `ItemList` of `ListItem`s. A
page that is only an index is followed to each posting, bounded by
`DEFAULT_FOLLOW_LIMIT = 40` and `max_jobs_per_source`. One malformed JSON-LD
block does not cost the rest of the page.

**Resolution order matters and is deliberate:** a board API beats reading a
page, so a careers page carrying both resolves to the board; but structured
postings beat *naming* an unsupported platform, because "runs on successfactors"
is a worklist entry while postings on the page are readable today.

---

## 6. Browser rendering and generic list detection

This is the deepest part of the system and the part most worth understanding.

### 6.1 The problem

After all ten ATS connectors, **~250 of 631 organizations** still had nothing:
no ATS, no feed, and no structured data on the landing page. They render a list
of roles with JavaScript, sometimes behind a "Load more" button, in a layout
written for that company alone. It was the largest remaining group by a wide
margin.

The **first attempt failed completely**: reading schema.org markup over plain
HTTP found **zero of 523**. The prediction that employers emit `JobPosting` on
their careers landing pages was wrong — **that markup lives on posting pages,
not landing pages.** This is stated plainly because the corrected model is what
`read_posting()` is built on.

### 6.2 What one page visit does

`BrowserSession.render(url, *, expand, robots)`:

1. check robots.txt for the host (see §6.3);
2. `goto(wait_until="domcontentloaded")`, then **wait for `networkidle`**,
   bounded at `2 × DEFAULT_SETTLE_MS` (5s) — the earlier code waited a fixed
   2.5s after `domcontentloaded` and never scrolled, so a board that finished a
   moment later, or that sits below the fold and does not fetch until reached,
   was simply missed;
3. scroll (`mouse.wheel` × 3, 200ms apart);
4. click a "load more" control while one keeps appearing, up to
   `MAX_EXPANSIONS = 8`;
5. a final `DEFAULT_SETTLE_MS` settle, then return `page.content()`.

`DEFAULT_DELAY_SECONDS = 2.0` between pages — a person opening tabs does not go
faster than this.

**Follow one link deeper** (`deeper_board_links`). A careers landing page often
never calls the ATS at all; the page behind "See our open positions" does. The
most promising link that promises actual roles is followed once. Multilingual,
because this list is full of "Offene Stellen", "Posizioni aperte", "Vacatures",
"Nos offres". Normally the hop stays on the company's own host so a LinkedIn
mirror is not mistaken for its board — **with one exception: a link straight to
a recognised ATS host, because that *is* the board.** That exception is what
"Discover our positions here" → `telespazio-be.breezy.hr` needed.

**Visit a shared page once per run.** 631 entries are 581 distinct URLs, so ~50
visits were re-fetching a page whose answer was already known. Results are
cached per URL for the run: every organization still gets its own answer, from
one visit.

**`host_variant()`** — try the same URL with `www.` added or removed. Worth one
request: a certificate frequently covers the bare domain but not `www`, or the
reverse, and a page that fails with `ERR_CERT_COMMON_NAME_INVALID` on one form
loads on the other.

### 6.3 robots.txt — RFC 9309, and the bug that hid a fifth of the list

A real 631-entry browser run reported **125 organizations as "disallowed by
robots.txt"** — almost all of them security vendors: Cloudflare, Akamai,
CrowdStrike, Fortinet, Tenable, Qualys, SentinelOne, Check Point, Arctic Wolf,
Bitdefender. Companies that run WAFs, and that very much want their job pages
indexed.

**None of them had forbidden anything.** Two bugs compounded:

1. **robots.txt was fetched as `Python-urllib/3.11`** — a User-Agent WAFs
   reject on sight. So on exactly the hosts most likely to run one, robots.txt
   could never be read at all. It is now fetched with `ROBOTS_USER_AGENT`:
   browser-shaped, but still identifying itself as ACIDE-Watch.
2. **A 403 on the fetch was treated as disallow-all.** Python's
   `RobotFileParser` follows the pre-RFC convention of reading 401/403 as a
   blanket refusal. **RFC 9309 §2.3.1.4 treats every 4xx as "no robots.txt
   applies"** — the site has not forbidden anything, we simply could not ask.

Now: 4xx allows, 5xx assumes disallow as the standard directs, and
`RobotsDecision` says which of the two happened rather than calling both a
refusal. **A robots.txt that can be read is obeyed, and a `Disallow` is still a
`Disallow`.** This change makes the crawler *more* standards-compliant, not
less.

### 6.4 `pagestructure.posting_links()` — the generic list finder

**The insight:** every posting in a list shares a URL path prefix and differs
only in its final segment — `/careers/p/747f-it-administrator`,
`/careers/p/6f5d-data-engineer`. That holds for Breezy, for a hand-built Vue
page, and for anything that gives one URL per role. So cluster links on **URL
shape**, not on any site's markup.

```python
def _shape(url):
    parsed = urlparse(url)
    segments = [s for s in parsed.path.split("/") if s]
    # everything but the final segment is the prefix every sibling shares
    return (parsed.netloc.lower(), "/".join(segments[:-1]).lower(), len(segments))
```

Three things make this work rather than nearly work:

1. **A navigation bar shares a shape too**, so the group's link text must read
   like job titles: at least two words, ≥6 characters, not in the `_CHROME`
   set ("About", "Privacy", "Contact", social names, language switchers). The
   median text length must say *sentence* rather than *label* (≥15 chars gets a
   1.5× weight).

2. **A job-shaped path outranks a merely larger group.** In the first version a
   **twelve-item newsroom beat a four-item job list on size alone**. Path is
   strong evidence; size is weak. Size now only breaks ties *within* the
   stronger class:

   ```python
   job_shaped = [entry for entry in candidates if entry[0]]
   pool = job_shaped or candidates
   return max(pool, key=lambda entry: entry[1])[2][:limit]
   ```

3. **Whole path segments are matched exactly, never as substrings.** `p` is
   Breezy's posting prefix, and testing for it inside a segment made
   **"company" and "press" job-shaped**. `_JOB_SEGMENTS` is matched exactly;
   only the longer `_JOB_STEMS` ("career", "vacanc", "emploi", "lavor",
   "stelle", …) are matched inside a segment.

**A role linked three times over** — title, location, contract type, which is
exactly what Breezy's markup does — is collapsed to one posting under the
longest of the three texts, which is the title.

### 6.5 `pagestructure.read_posting()` — one posting page

Descending order of how much is really known, and **which reading was used is
recorded in `PageFacts.source`**, so a weak reading is visible as weak rather
than passing for structured data:

1. **schema.org `JobPosting`** — structured, and what Google for Jobs consumes,
   so a *posting* page very often carries it.
2. **Open Graph** — a page emits it for its own link previews.
3. **The visible heading** plus body text — a guess about layout, labelled as
   one.

A trailing site name is stripped: "SOC Analyst | Acme" is one role.

**A location is never guessed from the title.** An early version put "Platform"
in the location field from "Platform Engineer". A wrong location is worse than
none, because the portal filters on it.

### 6.6 `RenderedConnector` (`spider/rendered.py`)

`source_type = "browser"`, `DEFAULT_POSTING_LIMIT = 60`. Renders the list page,
finds the list, applies `search_terms` **to the list before opening any role**
— on a board of sixty roles with one match that is two renders instead of
sixty-one — then opens each remaining role and reads it. robots.txt is checked
on every posting as well as on the list.

**A page that lists roles without linking each one is reported as such rather
than guessed at.** That residue is where reading a page with a language model
would earn its cost — see §12.

**Generic list detection succeeded on ~97% of rendered pages**: ~13 failures
out of ~395.

---

## 7. Scoring

### 7.1 The contract

One model call per posting, through OpenRouter, with **strict structured
output**. `JobEvaluation` has 12 fields; `RESPONSE_SCHEMA` must describe all of
them.

### 7.2 The schema bug that cost four hours

```python
# WRONG — what was there
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": { ...12 fields... },
    "required": [ ...9 fields, hand-written... ],   # missing rate, currency, amount
    "additionalProperties": False,
}
```

**`strict: true` structured output requires `required` to list *every* key in
`properties`.** Written by hand, it was missing `rate`, `currency` and
`amount`. The provider rejected every request with **HTTP 400 before generating
a single token**. The overnight run made 2,649 such requests.

```python
# RIGHT — the two cannot drift apart again
_RESPONSE_PROPERTIES: dict[str, Any] = { ...12 fields... }

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": _RESPONSE_PROPERTIES,
    "required": list(_RESPONSE_PROPERTIES),
    "additionalProperties": False,
}
```

A test asserts the schema covers exactly the fields `JobEvaluation` has.

### 7.3 `preflight()` vs `handshake()` — why the existing check did not catch it

`handshake()` sends a **plain completion** and reports the model, the reasoning
budget and the token usage. It **passed** the whole time the structured path
was broken, because a plain completion carries no `response_format`.

`preflight()` evaluates **one synthetic posting through the structured path** —
the path every real posting takes. `acide inspect` and `acide score` both run
it before committing to anything, and `--skip-preflight` opts out. A broken
schema now costs one request, not four hours.

`_empty_content_reason()` diagnoses the other common failure: a reasoning model
that spends its entire `max_tokens` budget on reasoning and returns no answer.
The message names the two settings that fix it.

### 7.4 The prompt

`build_prompt()` takes the CV text (capped at 6,000 chars), the operator's
stated interests, and the posting (description capped at 8,000 chars). The
model is asked for the two fit scores independently. `parse_response()` is
defensive about fenced code blocks and surrounding prose even though strict
mode should make that unnecessary.

**Published compensation beats the model's guess.** In `upsert_job`:

```python
amount   = posting.amount if posting.amount else evaluation.amount
currency = posting.currency or evaluation.currency
rate     = posting.rate or evaluation.rate
```

`compensation.py` parses pay out of prose for feeds that publish none.

---

## 8. Chronology of real runs

Every row is a run the operator executed on their own machine against the real
631-organization list, and the change it forced.

| # | What ran | Result | What it exposed |
| --- | --- | --- | --- |
| 1 | `import-companies` (plain HTTP) | **23 of 631 resolved** | 304 pages inject the board link at runtime. Prediction that Workday was the gap: wrong (Workday = 19). → build `--browser` |
| 2 | `check-urls` | 136 × 404, 20 dead domains, **191 "moved"** | ~¼ of the list had rotted. → repair ladder; redirect-to-homepage trap |
| 3 | `import-companies --browser` | **125 "disallowed by robots.txt"** | Not one of them had forbidden anything. → RFC 9309 fix, browser-shaped robots fetch |
| 4 | `--browser` again | "Page rendered, no ATS link" = **200** | 403s were only 6 — it was *not* bot detection. → settle-on-quiet, scroll, one hop deeper, per-URL cache |
| 5 | with 6 new connectors | **100 orgs / 80 boards**, up from 18 | Teamtailor resolved 0/10 — `jobs.json` does not exist. Junk tokens: `tt`, `careers-analytics`, `2Fstark`, epoch suffixes |
| 6 | after Teamtailor RSS rewrite | Teamtailor **7 orgs / 6 boards**; refusals 14 → 7 | 5 of the 7 remaining were the marketing-host fallback. → `feed_urls()` |
| 7 | `probe` over 523 unresolved | 265 adoptable; **124 "404" + 55 "403"** | **126 of those had been fetched by a browser minutes earlier.** → "refuses plain HTTP" verdict |
| 8 | JSON-LD over 523 landing pages | **0** | The markup is on *posting* pages. → `read_posting()`, the browser source |
| 9 | `adopt-browser` (all three commands pasted at once) | **469 targets** | A command block with `#` comments was pasted wholesale and executed every variant. My fault: the block was written to be pasted |
| 10 | **overnight, 469 sources, ~4 h** | **2,649 postings, 0 scored, all lost** | Schema HTTP 400 + storage required a verdict. Also 13 × "no job list could be identified" |
| 11 | *(next)* | — | crawl-only pass with `search_terms` cleared |

Resolution progression across the whole arc: **18 → 100 → 108 organizations**;
~87 distinct boards; 469 configured targets, 395 of them rendered pages.

---

## 9. Every bug, with root cause and fix

This section exists because the failure modes are more instructive than the
code. Format: **symptom → root cause → fix → what pins it now.**

### 9.1 Data-loss and correctness bugs

**Nothing was stored unless it was scored.**
`upsert_job(posting, evaluation)` required a verdict, so a posting only reached
the database once judged. A scoring failure therefore discarded the entire
crawl. → `store_posting()` writes the facts a source just restated, the moment
they are found, and never touches an existing evaluation, `scored` flag, or the
operator's `saved`/`dismissed` choices. → Tests that previously asserted
"nothing is stored without a verdict" were **rewritten to assert the new
contract** rather than deleted.

**A crawl against an older database died on its first posting.**
`sqlite3.OperationalError: table jobs has no column named scored`, nine seconds
into a 469-source pass. The migration was correct and tested; **nothing in the
crawl path ever ran it.** `init_db()` was called by `acide serve`, by
`acide sources`, and by `acide inspect` *only under `--retry-failed`* — so the
one command that writes thousands of rows was the one that never brought the
schema up to date, and `acide score` would have failed the same way. → Schema
creation and migration now happen on the **first connection to a database**,
memoised per path, so no entry point has to remember. `init_db()` remains, as a
force. → Two tests build a database in the exact pre-`scored` shape and assert a
crawl migrates it, keeping the old rows' verdicts and `saved` flags; both fail
against the previous code with the same `OperationalError`.

**The importer wrote nothing until all 631 pages were done.**
Over an hour single-threaded; the run was killed part-way and everything it had
learned went with it. `--retry-report` existed to resume but had no report to
resume from. → Resolutions verified as each page finishes, handed to the caller
as they happen, written every 10 organizations and again on the way out
(Ctrl-C, browser crash, closed laptop). The saved report lists organizations
**never reached**, not just those that failed, so resuming covers the whole
remainder.

**The inspection run collected every source before scoring any.**
An interruption at hour four lost hour one. → Each source's new postings are
scored and written as that source finishes. The OpenRouter client is opened
**once for the whole run** and passed down — building and tearing down a
connection pool 469 times is its own cost.

**`--write` dropped every field it did not know about.**
The corrected list was rebuilt from four fields, silently discarding the
operator's `id` and anything kept alongside it. → It now starts from the entry
as written and changes only `careers_page`.

**`--retry-report` lost every website.**
`organizations_from_report` read a `website` key that `Resolution` never wrote,
so each retry ran with *less* to go on than the first attempt. → `Resolution`
carries it.

### 9.2 Bugs that made a working thing look broken

**125 organizations "disallowed by robots.txt"** — §6.3. Two compounding bugs;
a fifth of the list wrongly refused.

**The probe reported 126 refusals as dead pages.**
124 "HTTP 404" and 55 "HTTP 403" — but cross-referencing the browser run on the
same URLs showed **126 of those had been fetched successfully with a browser
minutes earlier** (CybExer, EclecticIQ, NVISO, Toreon, genua, CERT-EU, ENISA
and a hundred more). Many sites refuse a plain request with **404 rather than
403**, which reads as "deleted" and is not. Reporting those as dead argues for
removing entries that are fine and hides the real residue. → `Organization`
carries `previous_detail`; a 4xx or connection failure on a page an earlier
browser run *did* fetch is labelled **"refuses plain HTTP"** with both facts in
the note. Refusals are counted separately in the summary.

**A newsroom beat the job list.** §6.4 — 12 items beat 4 on size.

**`"p" in prefix` matched "company" and "press".** §6.4 — whole-segment matching.

**A redirect to a homepage was trusted.** §4.1 — 191 of 631 were "moved".

**Title-derived location.** "Platform Engineer" → location "Platform". Removed:
a wrong location is worse than none, because the portal filters on it.

**`iso_date` could not parse RFC 822.** Teamtailor's RSS uses it, so **every
RSS posting would have arrived dateless**. Caught when the connector was
rewritten.

**Four junk token classes**, each fixed with a test built from the exact token
the real run wrote: `tt` (Teamtailor's *asset* host), `careers-analytics` (a
Recruitee tracking pixel), `2Fstark` (a URL-encoded fragment), and
epoch-suffixed asset hosts.

**Teamtailor custom-domain fallback used the marketing host.**
`www.exotrail.com` is not a careers host. → `feed_urls()` tries www-stripped,
then `careers.`, then `jobs.`.

**The model catalogue failed on exactly the first run.**
With no key saved, the `Authorization` header was built as `"Bearer "` with an
empty value, which httpx rejects as an illegal header — so the public catalogue
was unreachable precisely when it was first needed. → No key means no header.

**The summary printed one fault fifteen times and hid the other one.**
The overnight run ended `2798 error(s); the first few:` followed by fifteen
identical OpenRouter 400s and `… and 2783 more`. There were *two* problems that
night — a rejected response schema, and one careers URL that had rotted — and
the second was inside the 2,783 lines the summary had no room for. → Errors are
grouped by cause (`failures.py`): the message with the URL removed is the
signature, so the same fault counts once however many postings hit it, and 2,798
lines become two. Numbers are deliberately *not* normalised: a 404 and a 403 are
different diagnoses, and §6.3 is what a fifth of the list once turned on.
`acide sources` groups the same way.

**A target's URL had no repair path once resolution was over.**
`TU Munchen: https://www.tum.de/en/about-tum/working-at-tum: HTTP 404` — the
page had moved, and `check-urls` reads a *companies file* while the crawl reads
`setup.json`, so fixing one URL meant running the whole resolution pipeline
again. → `acide check-urls --targets` applies the same repair ladder to the
configured `browser` and `jsonld` targets, `--failed` narrows it to the sources
whose last crawl failed, and `--apply` writes the URLs back. A page shared by
several targets is fetched once. A source the last crawl *read* is never
repointed on the strength of a plain fetch, however dead that fetch says it is
— §9.2's 126 refusals are exactly this, and a `browser` target is read by a
browser. `acide sources` names the command when a rendered source is among the
failures.

**The settings field stripped every comma as it was typed.**
Rendering the parsed list back into a comma-separated input meant the
`search_terms` list could never be extended past its first term. → The field
holds the raw text (`searchTermsText`); the parsed list is what gets saved.
**Found by driving the real control in a test, not by reading the code.**

### 9.3 Operational and self-inflicted

**`systemd-inhibit` exists but fails without a systemd user bus.**
It is on `PATH` in plenty of environments where it does not work — over SSH, in
a container, with no user bus — and there it exits immediately **and takes the
run down with it**. The log then contains one line, "Failed to connect to bus",
and nothing else. → `scripts/overnight.sh` probes it for real
(`systemd-inhibit --what=sleep --why="probe" true`), falls back to
`caffeinate`, and otherwise says plainly that suspend would pause the run.

**`mkdir -p logs` was in a pasted block that did not run.**
The redirect had nowhere to write, nothing started, and the only evidence was a
missing file. → A run left overnight needs **a script in the repository**, not
a block in a chat message. That is what `scripts/overnight.sh` is.

**A command block with `#` comments was pasted wholesale**, executing all three
`--apply` variants and creating 469 targets. My fault — the block was written
in a form that invited it.

**`--retry-failed` and `--skip-done` did identical filtering.** I wrote both in
the same commit. → Collapsed to one flag: *"Never attempted counts as
unfinished, so the same flag resumes a pass stopped half way rather than
restarting it."*

**Ctrl-C dumped a traceback over the resume instructions.** → Return 130.

**A test asserted on `RobotsCache._parsers`** — a private attribute. It broke
on a refactor without catching anything. → Replaced with one that asserts the
behaviour: one robots.txt request per host, however the cache is built.

**A `respx` bare-host pattern matches every path on that host.** My redirect
test looped. → Register the specific path first.

**I told the operator the 2,649 postings were already in the database and could
be scored without re-crawling.** They were not: the storage fix came *after*
that run. Corrected in the next message. The lesson worth keeping: **a fix does
not apply retroactively to data that was never written.**

---

## 10. Current state

### Works, verified

- 12 source types; all ten ATS connectors pinned against their documented
  payload shapes.
- Resolution pipeline end to end: `check-urls` → `import-companies [--browser]`
  → `probe` → `adopt-browser`.
- Generic job-list detection on rendered pages: **~97% of ~395 pages**.
- robots.txt handling, RFC 9309-correct.
- Crawl and score fully separated; `--no-score`, `acide score`.
- Per-source outcome memory; `--retry-failed`, `acide sources`.
- Unattended runs: `scripts/overnight.sh`, prechecked and detached.
- Portal: grid, two independent fit sliders, `scored`/`pending` filter, saved
  and dismissed, settings, model picker, alert subscriptions.
- Email digests with per-subscriber dedupe.
- 451 backend tests, 97 portal tests, ruff clean, CI green.

### Does not work / not done

- **No posting has ever been successfully scored in a real run.** The schema
  fix is tested but has not yet met the live provider.
- 13 rendered pages defeat list detection (§11).
- ~60 organizations sit on platforms with no connector (SuccessFactors, Taleo,
  EPSO, BambooHR, Pinpoint, HiBob, Werecruit, Jobvite) — **named, not
  indexed**.
- The eight EU bodies sharing `eu-careers.europa.eu` still need splitting to
  their own pages.
- LLM-assisted parsing of unstructured pages: **deliberately deferred** by the
  operator to "after this phase".
- The database has no postings in it right now: the only large crawl was
  discarded (§9.1).

---

## 11. The 13 pages that defeat list detection

From the overnight log's `no job list could be identified` lines. These are the
organizations named there; treat the list as indicative rather than exact, since
the log itself is gitignored:

Intigriti · BAE Systems · Dassault Aviation · Rafael · CCN-CERT ·
DIS/AISE/AISI · UK Space Agency · PhysicsX · Telecom Italia · Terra Quantum ·
European XFEL · RISE (+1)

**The shapes that defeat URL clustering**, and what each would need:

| shape | why clustering fails | likely fix |
| --- | --- | --- |
| roles are not links at all — an accordion or modal opens in place | there is no per-role URL to cluster | read the repeated DOM block, not the links |
| roles differ only by fragment (`/jobs#role-1`) | `split("#")[0]` collapses them to one URL, so the group has one member | cluster repeated DOM blocks instead of links |
| the list is inside an iframe from a third party | the outer page has no links | render the iframe's own URL |
| a government portal with a search form and no default listing | nothing is listed until a query is submitted | out of scope for generic detection |
| fewer than two roles currently open | `len(titled) < 2` correctly rejects it | not a bug — but should be reported as "no roles" rather than "no list" |

Verified against the real function while writing this: a listing whose roles
differ only by **query string** (`/jobs/detail?id=123`) *is* clustered
correctly — four such links yield four postings — so that is not one of the
failure shapes, despite looking like one. A listing whose roles differ only by
**fragment** yields zero.

**The next concrete step here is to get two or three of these pages' rendered
HTML** and classify them against this table, rather than guessing. A saved
fixture per shape is what would turn each into a test.

---

## 12. Next steps, in order

### 12.1 Immediate — the operator's next action

1. **Clear `spider.search_terms` to `[]`.** The reason they were set (every
   posting billed to the evaluator during the crawl) no longer exists, because
   crawling makes no model calls. With storage and scoring separated, an empty
   list costs a bigger backlog and a longer run, not money — and it is what
   "gather everything, filter in the portal" requires.
2. **Run `scripts/overnight.sh --no-score`.** Expect ~4 hours for 469 sources,
   395 of them rendered.
3. **`acide sources`** in the morning: succeeded / found-nothing / failed /
   never-attempted, with the failures grouped by cause rather than listed by
   company.
4. **`acide check-urls --targets --failed`** for whatever failed with a dead
   page, then `--apply` and `acide inspect --retry-failed` — a rendered source
   fails because its URL moved far more often than because its page changed.
5. **`acide score --limit N --yes`** in batches.

### 12.2 Then, in rough priority order

**a. Verify scoring against the live provider.** Nothing has been scored end to
end. `acide score --limit 5 --yes` is the cheap proof; do that before a large
batch.

**b. Work the `acide sources` failure list.** This is exactly what
`--retry-failed` was built for: fix a connector, re-run 30 sources instead of
469. The failure messages are already grouped by frequency.

**c. Classify the 13 unparseable pages** (§11) and add a fixture per shape.
The single highest-value generic improvement is probably to **fall back to
clustering repeated DOM blocks** when no link group qualifies — the same
shape-not-markup idea one level down. That covers accordions, modals, and
fragment-only links in one change, which between them are most of the residue.
Rendering a third-party iframe by its own URL is a smaller, separate fix.

**d. LLM-assisted page parsing** — explicitly deferred by the operator until
after the crawl phase. The design that follows from what is already built:
   - it is a **fallback**, reached only when `posting_links()` returns nothing
     and `read_posting()` cannot produce a title;
   - it receives `_main_text(html)` — already implemented, already strips
     script/style/nav/header/footer/aside and caps at 12,000 chars — not raw
     HTML;
   - it returns the same `PageFacts` shape, with `source` set to something
     that says a model read it, so a weak reading stays visibly weak;
   - it must be **cached per URL**, because the whole point is that pages are
     crawled once;
   - the operator suggested `openai/gpt-5.6-luna` with max reasoning effort via
     OpenRouter, and mentioned `browser-use` as prior art. The key stays in
     `data/setup.json`.

**e. Split the shared careers pages.** Eight EU bodies on
`eu-careers.europa.eu` publish their own vacancies on their own sites; the
shared portal is a placeholder. The Thales/Airbus/Leonardo/Telespazio/Saab
duplicates are the *other* kind and should stay collapsed.

**f. Connectors for the named-but-unsupported platforms**, in descending order
of organizations unlocked: SuccessFactors, Taleo, BambooHR, Pinpoint, Jobvite,
HiBob, Werecruit. Same boundary as always: only if the provider publishes a
keyless feed its own careers pages read.

**g. Scoring selection.** `acide score` currently takes only `--limit`. With a
large backlog, being able to score *a chosen slice first* (by company, by title
keyword) would let the operator spend on Thales and Airbus before everything
else. This is a small, obvious addition and is not yet built.

**h. Re-enable the scheduler** (`spider.enabled`, `interval_minutes`) once a
full pass is known-good, so the portal stays current without manual runs.

---

## 13. Runbook

```bash
# ---- setup -----------------------------------------------------------------
python -m venv backend/.venv
backend/.venv/bin/pip install -e 'backend/[browser]'
backend/.venv/bin/playwright install chromium
(cd frontend && npm install && npm run build)

# ---- resolution (one-off) --------------------------------------------------
acide check-urls companies.json --write companies-fixed.json --report url-check.json
acide import-companies companies-fixed.json --report import-report.json          # dry run
acide import-companies companies-fixed.json --apply
acide import-companies import-report.json --retry-report --browser --apply       # hours
acide probe import-report.json --report probe-report.json                        # minutes
acide adopt-browser probe-report.json --limit 5                                  # dry run
acide adopt-browser probe-report.json --apply

# ---- indexing --------------------------------------------------------------
scripts/overnight.sh --no-score            # crawl only, detached, prechecked
scripts/overnight.sh --retry-failed        # only what failed or was never reached
acide inspect --source-type browser        # just the slow ones
acide inspect --source-type greenhouse,ashby,lever --limit 20
acide sources                              # what happened; what to re-run, by cause
acide check-urls --targets --failed        # a rendered page whose URL moved
acide check-urls --targets --failed --apply
tail -f logs/overnight-latest.log          # symlink to the run in flight
kill $(cat logs/overnight.pid)             # safe: each source is saved as it finishes

# ---- scoring ---------------------------------------------------------------
acide score                                # dry run: the size of the backlog
acide score --limit 500 --yes              # judge 500

# ---- portal ----------------------------------------------------------------
acide serve                                # http://127.0.0.1:8000

# ---- development -----------------------------------------------------------
backend/.venv/bin/python -m pytest backend/tests -q
backend/.venv/bin/ruff check backend/
(cd frontend && npm test && npm run typecheck)
```

**Useful greps on an overnight log.** Use `overnight-latest.log`, not the glob:
once more than one run has happened, `logs/overnight-*.log` expands to several
files, and GNU `tail` rejects the obsolete `-40` form with more than one
operand — *"option used in invalid context"*. `tail -n 40` works either way.

```bash
LOG=logs/overnight-latest.log
grep -c "no job list could be identified" "$LOG"
grep    "no job list could be identified" "$LOG" | head -30
grep -E "\[[0-9]+/[0-9]+\]" "$LOG" | tail -n 20      # progress
tail -n 40 "$LOG"
```

---

## 14. Rules for whoever picks this up

**Do not:**

- Relax the crawling boundary (§2.2) to raise the resolution rate. It is a
  stated commitment, not a tuning parameter.
- Ask the operator for an API key, or accept one if offered. It lives in
  `data/setup.json` on their machine.
- Ask the operator to send `data/setup.json`.
- Commit anything matching the gitignored family — the company list, reports,
  verdict CSVs, the database, logs. Check what a wide `git add` swept up.
- Average the two fit scores, or let one gate the other.
- Merge crawling and scoring back together. They were separated for a reason
  that cost four hours to learn.
- Write a "paste this block" of shell into a chat message when the operator
  needs to run something unattended. Put a script in the repository.
- Delete a test that now asserts the wrong contract — **rewrite it** to assert
  the new one, so the coverage survives the change.

**Do:**

- Turn every defect a real run exposes into a test built from the **exact**
  token, URL or payload that caused it. Roughly a third of the test suite came
  from this, and it is the single most effective practice in the project.
- State plainly when a prediction turns out wrong (the Workday prediction, the
  JSON-LD prediction, the "already in the database" claim). The corrected model
  is more valuable than the appearance of consistency.
- Distinguish "matches the documented shape and is pinned by tests" from
  "answers for this tenant". The sandbox cannot establish the second.
- Prefer a property-level test over a plumbing-level one: *a crawl-only pass
  leaves an evaluator that answers 500 untouched* survives refactors that
  *`run_once` was called with score=False* does not.
- Read `acide sources` before proposing any re-crawl. The whole point of that
  table is that the expensive work is not repeated to find out what failed.

---

## 15. Where to look first

| question | file |
| --- | --- |
| What commands exist, and what do they print? | `backend/acide/__main__.py` |
| How does one inspection pass work? | `backend/acide/spider/runner.py` |
| How is a job list found on an arbitrary page? | `backend/acide/pagestructure.py` |
| What does the browser actually do, and what won't it do? | `backend/acide/browser_discovery.py` |
| How is a posting stored, and what is never overwritten? | `backend/acide/db.py` |
| What does the evaluator return, and why is the schema derived? | `backend/acide/llm.py` |
| How is an ATS read out of a careers page? | `backend/acide/discovery.py` |
| How is a rotted URL repaired? | `backend/acide/linkcheck.py` |
| Why was any of this done this way? | `git log` — the commit messages are the design record |

The commit messages are deliberately long and are the primary design record.
`git log --format='%n===== %h %ad %s%n%b' --date=short` reads as a narrative of
the whole project.
