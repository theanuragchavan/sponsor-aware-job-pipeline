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
| Workable | `https://apply.workable.com/api/v1/widget/accounts/{token}` | **[tested]** 200 |
| SmartRecruiters | `https://api.smartrecruiters.com/v1/companies/{token}/postings` | **[tested]** 200 ⚠ see traps |
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

---

## Tier 1 — Free, needs a self-serve key (the ask list)

| Source | Signup | Auth | Env var |
|---|---|---|---|
| Adzuna | *already held* | query params | `ADZUNA_APP_ID` / `ADZUNA_APP_KEY` |
| Reed | reed.co.uk/developers/jobseeker | HTTP Basic, key as username, **blank password** | `REED_API_KEY` |
| Careerjet | careerjet.co.uk/partners | `affid` query param | `CAREERJET_AFFID` |
| Jooble | jooble.org/api/about (request form) | key in POST URL path | `JOOBLE_API_KEY` |
| Findwork.dev | findwork.dev/developers | `Authorization: Token {key}` | `FINDWORK_API_KEY` |
| The Muse | themuse.com/developers/api/v2 | `api_key` param (optional, raises limits) | `THEMUSE_API_KEY` |

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

## The structural finding: probe, don't map

Every public list of "which company uses which ATS" is incomplete and stale. It is
not needed. Slugify a company name and probe it against the five Tier-0 ATS
endpoints; keep the hits.

This makes the **gov.uk sponsor register a discovery input, not just a filter.**
Today the register is used to check a job *after* Adzuna surfaces it. Probing
inverts it: start from sponsor-licensed employers, find the ones with readable
public boards, and poll those directly. Postings appear there before they reach
any aggregator, and every result is sponsor-confirmed by construction.

Proven on real targets — `palantir` → Lever, 308 postings, 38 UK/London, including
all four target roles (FDAE, FDSE, FDRE, FDEE). One keyless GET.

Implementation notes for the prober:
- Slug variants per company: lowercase, strip `ltd|limited|plc|group|uk`,
  strip spaces, and try hyphenated form.
- Cache resolved company→ATS pairs; re-probe unresolved ones monthly, not daily.
- Rate-limit politely and set a real User-Agent, as `adzuna_client.py` already does.
- Gate every hit on parsed content per the two traps above.

---

## Priority order

1. **ATS prober over the sponsor register** — free, zero credentials, highest yield.
2. **Reed client** — the one genuinely additive aggregator; has a real
   `contract_type` filter, which Adzuna lacks.
3. **HN "Who's Hiring" monthly parse** — cheap, startup roles that never reach
   aggregators.
4. Careerjet / Jooble — only if recall still looks short after 1–3.
