# Job Source Landscape

Compiled 2026-08-21. Endpoint statuses marked **[tested]** were verified live with
curl on that date, not read from documentation. Marked **[untested]** = plausible
from docs but not yet confirmed by me.

Selection criteria: free, UK/London coverage, permits automated daily polling,
and no per-call cost inside the 9am run.

---

## Tier 0 — Zero credentials, usable immediately

No key, no signup, no account. Public JSON that the companies' own careers pages
call on every page load.

| Source | Endpoint | Status |
|---|---|---|
| Greenhouse | `https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true` | **[tested]** 200 |
| Lever | `https://api.lever.co/v0/postings/{token}?mode=json` | **[tested]** 200 |
| Ashby | `https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true` | **[tested]** 200 |
| ~~Workable~~ | `https://apply.workable.com/api/v1/widget/accounts/{token}` | **DEAD** — see below |
| SmartRecruiters | `https://api.smartrecruiters.com/v1/companies/{token}/postings` | **[tested]** real data ⚠ see traps |
| Personio | `https://{token}.jobs.personio.de/xml` | **[tested]** 200 (XML) |
| Workday CxS | POST `https://{tenant}.wd{N}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs` | **[tested]** mixed ⚠ |
| HN "Who's Hiring" | `https://hn.algolia.com/api/v1/search?tags=story&query=...` | **[tested]** 200 |
| Arbeitnow | `https://www.arbeitnow.com/api/job-board-api` | **[tested]** 200 |
| Remotive | `https://remotive.com/api/remote-jobs` | **[tested]** 200 |
| RemoteOK | `https://remoteok.com/api` | **[tested]** 200 |
| Himalayas | `https://himalayas.app/jobs/api` | **[tested]** 200 |
| Jobicy | `https://jobicy.com/api/v2/remote-jobs` | **[tested]** 200 |
| The Muse | `https://www.themuse.com/api/public/jobs?page=1` | **[tested]** 200 (key optional) |

**Workday caveat:** AstraZeneca returned 200; Vodafone and JPMC returned 422. The
tenant, data-centre number (wd1/wd3/wd5) and site name must all be exact, and they
differ per employer. Each Workday employer is a manual discovery job — worth it only
for a specific target, not for breadth.

### ⚠ Two verified false-positive traps

Both found by control-testing the nonsense slug `zzqqxnotacompany999`:

1. **SmartRecruiters returns `HTTP 200 {"totalFound":0,"content":[]}` for any slug.**
   Status-code probing marks every company as a hit. Gate on `totalFound > 0`.
   Worse — its `palantir` board is a *different* Palantir: a C# consultancy in
   Westhill, Aberdeen. Always match `content[].company.name` too.
2. **Workable returns a real account with `"jobs":[]`.** `monzo` exists but is an
   empty legacy account. Gate on a non-empty jobs array.

General rule for this whole tier: **never treat HTTP 200 as a hit — parse and count.**


### ⚠ Workable serves no job data (2026-08-21)

Retested properly and it is not usable. Twelve accounts returned a valid
envelope with an empty jobs array, across all three endpoint variants:

    /api/v1/widget/accounts/{slug}                -> {"name": ..., "jobs": []}
    /api/v1/widget/accounts/{slug}?details=true   -> {"name": ..., "jobs": []}
    POST /api/v3/accounts/{slug}/jobs             -> {"total": 0, "results": []}

Accounts exist and return their real display name; the jobs simply are not
there. **No Workable client was written**, and the earlier "[tested] 200" note
above was wrong: it recorded a status code, not a payload, which is precisely
the mistake the traps section warns about. Reactivation trigger: a Workable
board that returns a non-empty jobs array.

### SmartRecruiters is real, with two live traps

Implemented as `ats.clients.fetch_smartrecruiters` / `ats.mapping.smartrecruiters_row`
(row prefix `sr`, tests in `tests/test_ats_ingest.py`). It returns rich data:
city, country, remote/hybrid flags, employment type, function label, release date.

It has no 404 at all — an unknown slug is byte-identical to an empty board —
so it can never be used for slug DISCOVERY, only for slugs added by hand. And
its `palantir` board is a C# consultancy in Westhill, Aberdeen, not Palantir
Technologies, so `company.name` must be checked against `register_name`.


---

## Tier 1 — Free, needs a self-serve key (the ask list)

| Source | Signup | Auth | Env var |
|---|---|---|---|
| Adzuna | *already held* | query params | `ADZUNA_APP_ID` / `ADZUNA_APP_KEY` |
| Reed ✅ | reed.co.uk/developers/jobseeker | HTTP Basic, key as username, **blank password** | `REED_API_KEY` |
| Careerjet | careerjet.co.uk/partners | `affid` query param | `CAREERJET_AFFID` |
| Jooble ⚠ | jooble.org/api/about (request form) | key in POST URL path, **500 req LIFETIME cap** | `JOOBLE_API_KEY` |
| Findwork.dev | findwork.dev/developers | `Authorization: Token {key}` | `FINDWORK_API_KEY` |
| The Muse | themuse.com/developers/api/v2 | `api_key` param (optional, raises limits) | `THEMUSE_API_KEY` |

**⚠ Jooble is capped at 500 requests for the life of the key** (confirmed
2026-08-21 against Jooble's own help-centre docs: "an absolute lifetime quota,
not a monthly limit"). It therefore **must never enter the daily 9am run** — that
would exhaust it in about two weeks and then fail silently. Treat it as a one-off
backfill or an on-demand query only. Quota-increase request drafted at
`drafts/jooble_quota_request.md`.

Reed is the only one here with genuinely strong UK coverage. Careerjet and Jooble
are aggregators-of-aggregators — high overlap with Adzuna, useful mainly as a
recall safety net. Findwork/Muse are thin on UK.

---

## Tier 2 — Dead ends (documented so they are not re-investigated)

| Source | Why not |
|---|---|
| **Indeed** | Publisher API shut down 2023–24. Partner-only, approval-gated. Every "Indeed API" on sale is a third-party scrape. |
| **Glassdoor** | Public API closed 2021; enterprise-only since 2024. Same owner as Indeed (Recruit Holdings). |
| **LinkedIn** | Standing red line — no scraping, no dummy accounts, no automation. Not negotiable. |
| **DWP "Find a Job"** | **Powered by Adzuna.** Already covered; adding it duplicates rows. |
| **Y Combinator / Wellfound** | No public API. Wellfound hosts YC's listings; both are scrape-only. |
| **CV-Library / Totaljobs** | No self-serve jobseeker API; employer/partner side only. |

---

## Tier 3 — Paid, deliberately not adopted

Recorded so the reasoning is not re-litigated. All violate the "no per-call cost in
the daily run" constraint.

- **SerpApi Google Jobs** — 100 free searches, then paid. Best single proxy for
  Indeed/LinkedIn coverage if that ever becomes worth paying for.
- **JSearch (RapidAPI)** — ~200 free calls/month, then paid.
- **TheirStack** — 200 free credits/month.
- **Apify actors** — many ATS/HN/Wellfound scrapers exist and the Apify MCP is
  already connected, but actor runs consume paid credits. Reactivation trigger:
  a one-off bulk backfill where the credit cost is bounded and known in advance.

---

## Discovery: already evaluated and rejected

`ats/boards.py` documents why the registry is hand-curated rather than
discovered, and the reasoning still holds. The sponsor register has five
columns (Organisation Name, Town/City, County, Type & Rating, Route) across
122,767 Skilled Worker entries. No website, no domain, no sector. **It can
verify a name; it cannot find a job board.** Filtering it by tech-sounding
tokens yields 4,600 organisations headed by "0xA Technologies Ltd" and
"1 WAY TECH SOLUTIONS LIMITED" — two-person consultancy shells, not employers
worth probing.

The direction is therefore slug-first, register-as-gate, and `register_name`
is the column that makes it work. Adding a board costs about thirty seconds
because the slug is the last part of a careers URL.

SmartRecruiters independently confirms the decision from the other side: it has
no 404, so a probe cannot tell a real board from a nonexistent one there at all.

Palantir was already in `data/ats_boards.csv` (lever, `PALANTIR TECHNOLOGIES UK`,
308 jobs) before this session started. Re-finding it live proved the endpoint
works; it did not find anything the tracker was missing.

## Priority order

1. ~~ATS prober~~ — dropped; discovery was already evaluated and rejected above.
2. ~~Reed client~~ — **BUILT** 2026-08-21 (`reed_client.py`, 9 tests). Not yet
   wired into `main.py`; that is the next step.
3. ~~SmartRecruiters~~ — **BUILT** 2026-08-21 into the existing `ats/` module.
   No boards added to `ats_boards.csv` yet, so nothing fetches it in anger.
4. **HN "Who's Hiring" monthly parse** — cheap, startup roles that never reach
   aggregators.
5. Jooble — one-off use only (lifetime cap). Careerjet dropped.
