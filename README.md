# Sponsor-aware job pipeline

Matching UK job postings against the Home Office register of licensed visa
sponsors, when the same employer is called three different things in three
different places.

---

## The problem

If you need a UK Skilled Worker visa, a job you cannot legally take is worse
than no job at all. It looks like an opportunity and costs you an application.
So every posting has to be checked against the Home Office register of licensed
sponsors before it is worth reading.

That check sounds like a join. It isn't, because the two sides never agree on
the name:

| The job board says | The register says |
|---|---|
| `Monzo` | `MONZO BANK` |
| `Deliveroo` | `ROOFOODS LTD T A DELIVEROO` |
| `Faculty` | `FACULTY SCIENCE` |
| `Zego` | `EXTRACOVER` |
| `Speechmatics` | `CANTAB RESEARCH` |

A job board prints the brand. The register records the legal entity, sometimes
with a trading-as clause, sometimes under a name with no visible relationship to
the brand at all. Get it wrong in one direction and you apply to a company that
cannot hire you. Get it wrong in the other and you never see a job you could
have taken.

Measured on real data: **74 of 492 employers that this pipeline had scored "not
a sponsor" plausibly *are* sponsors under a different legal name.**

---

## What it does

```
Adzuna API ─┐
Greenhouse ─┤
Ashby      ─┼─► normalise ─► sponsor match ─► human review ─► ranked shortlist
Lever      ─┘                     │                 │
                                  │                 └─► confirmed aliases
                                  └─► Home Office register (142,701 rows)
```

**Two independent ingestion families.** An aggregator API, plus **47 employer
job boards** pulled directly from Greenhouse, Ashby and Lever. Direct boards
give the employer's own apply URL, the full description, and a real application
deadline, none of which an aggregator provides.

**A register that moves.** gov.uk republishes the sponsor CSV under a new dated
filename most days, so no URL can be hardcoded. `sponsor_check.py` resolves the
current one through gov.uk's structured content API, with an HTML-scrape
fallback, and caches for seven days.

**Name normalisation before matching.** Uppercase, `&` → `AND`, punctuation
stripped, a leading `THE` dropped, and trailing legal-form tokens
(`LTD`/`LIMITED`/`PLC`/`LLP`) peeled off.

**A suggester that proposes and never decides.** Two deterministic rules:

- *Prefix at a word boundary*: `AMENTUM` matches `AMENTUM UK`, but not
  `AMENTUMX`. Without the boundary this silently becomes a substring rule.
- *Trading-as*: `DELIVEROO` matches `ROOFOODS LTD T A DELIVEROO`.

Everything it finds goes to a human. Confirmed decisions land in an alias
overlay that lives outside the store, so rebuilding the data can never destroy a
ruling someone made.

---

## The interesting decisions

**Fuzzy matching was tried and rejected on evidence.** `difflib` similarity
scoring is the obvious approach and it looked fine until it was measured against
the real register, where it produced `AMAZON` → `AMAZON CHARITABLE TRUST`,
`LEONARDO` → `LEONARDO BELGIUM SA` and `CATALYST` → `CATALYST CAPITAL`. Every
false positive costs a human decision, so precision beats recall here and the
looser rule made the review queue worse, not better. The two deterministic rules
above replaced it.

**A 410 means the listing is gone, not the job.** Delisted ads return HTTP 410
Gone; live ones return 200. But an aggregator listing expiring says nothing
about whether the employer is still hiring, so a dead link demotes the row and
points at the employer's own careers page rather than discarding the role.

**Liveness fails open.** A probe that errors returns `UNKNOWN`, and `UNKNOWN`
never drops a row. Showing a dead advert wastes thirty seconds; silently hiding
a live one is invisible and unbounded.

**Age is a guess; a 410 is a fact.** An age cut-off applied *before* the network
check binned a genuinely open role at 47 days. The proxy now stands down
whenever the real check can run. Rows from employer boards skip the age gate
entirely, because presence in a successful feed is direct evidence the role is
open.

**Direct probing gets you IP-blocked.** Five quick requests and the aggregator
returns 403 to everything afterwards, including URLs that answered 200 seconds
earlier, and it stays blocked at twelve-second spacing. A naive prober's 403s
are indistinguishable from real failures, so it would confidently bin live ads.
Liveness routes through a scraping API instead.

**Per-ATS quirks, all found the hard way.** Ashby answers **200 with an error
body** for an unknown board rather than 404. Lever returns a **bare JSON list**
and stamps `createdAt` as **epoch milliseconds as an integer**, which a naive
`[:10]` slice corrupts into a date in 1970.

---

## Tests

```
python tests/run_all.py
```

75 assertions across four modules, all runnable offline. Fixtures are literal
payloads captured from live responses, so no test needs a network or a key.

`tests/run_all.py` exists because `unittest discover` reported *"Ran 0 tests"*
against this layout: a silent pass that makes an untested project look tested.

The gates are mutation-tested. Removing the word-boundary from the prefix rule,
or the namespace prefix from job ids, turns the suite red on exactly the test
that guards it.

---

## Setup

```bash
pip install requests python-dotenv openpyxl
cp run_tracker.bat.example run_tracker.bat   # then edit the two paths
```

Create `.env`:

```
ADZUNA_APP_ID=...
ADZUNA_APP_KEY=...      # developer.adzuna.com/signup
FIRECRAWL_API_KEY_1=... # optional, for liveness verification
```

```bash
python main.py                                       # ingest from the aggregator
python ats_main.py --dry-run                         # employer boards, no writes
python pipeline/shortlist.py --top 10 --verify-live  # today's shortlist
python pipeline/sponsor_review.py                    # queue of names to confirm
```

`data/ats_boards.csv` is the curated board registry. Adding an employer is one
row. The slug is the last path segment of their careers URL.

---

## What this does not do, and what is still broken

- **It does not apply to anything.** Ranking and filtering only; every
  submission is manual and deliberate.
- **The sponsor flag is a starting point, not a verdict.** A register match
  means the organisation holds a licence, not that this specific role is open to
  sponsorship. Agency listings match the *agency's* licence, which tells you
  nothing about the end employer, so they are filtered out of the shortlist.
- **The alias overlay is designed but barely used.** The review queue currently
  has 45 companies waiting on a human decision and `sponsor_aliases.json` does
  not exist yet, so the 74-company gap above is still mostly open. The mechanism
  works; nobody has sat down and worked the queue.
- **The store is a spreadsheet.** Deliberately: it is edited by hand constantly,
  and a human-editable store beats a tidier one nobody opens. Hand edits
  round-trip through a regeneration, which is what `test_tracker_roundtrip.py`
  guards after a bug once wiped a month of them.
- **Employer-board ingestion is not on the schedule.** The merge is insert-only
  and never revisits an id, so a mapping bug would have to be undone by hand.
  It runs manually until the drop table has been boring for a week.
- **There is a logging bug in `main.py`** that throws a `TypeError` on every
  scheduled run, because one tier is a string and the format string expects an
  integer. It does not affect the data. It has also been there for weeks, which
  is its own kind of finding.

Personal data (the tracker itself, shortlists, and triage decisions) is not in
this repository.
