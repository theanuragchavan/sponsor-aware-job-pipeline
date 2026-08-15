"""
Saved-search configuration — the tiered keyword list from Section 2 of the brief.

Each saved search is a dict:
    {keywords, location, distance, contract_type, salary_include_unknown,
     category, max_days_old, tier}

Adzuna's `category` takes only ONE tag per call, so Tier 2 is run twice —
once as `it-jobs`, once as `graduate-jobs` — and dedupe-by-id merges them.
Tiers 1, 3 and 4 leave `category` unset so the auto-categoriser can't silently
drop AI-adjacent / niche titles.
"""

# --- Run-level settings -----------------------------------------------------
MAX_PAGES_PER_SEARCH = 2      # 1-2 pages per search keeps us well inside budget
SLEEP_SECONDS = 2.5           # between API calls (limit is 25/min)
DAILY_CALL_CAP = 240          # refuse to run past this many calls in a day
JOBS_XLSX = "jobs_tracker_beautified.xlsx"  # the store: one beautified workbook
JOBS_CSV = "jobs_tracker.csv"               # legacy raw store (no longer written)

# --- Shared filters across every tier --------------------------------------
# Location: search the whole UK (GB endpoint already scopes to Great Britain),
# then prioritise London at sort time (sponsor-confirmed first, then London,
# then salary). An empty `location` means "no `where` filter" = nationwide;
# `distance` only has meaning relative to a named city, so it's unset here.
SHARED = {
    "location": "",                 # nationwide — London is prioritised in sort
    "distance": None,               # radius only applies to a specific city
    "contract_type": "permanent",
    "salary_include_unknown": True, # don't drop grad posts that omit salary
    "max_days_old": 7,
    # salary_min intentionally left unset
}

# --- Tier keyword lists -----------------------------------------------------
TIER1 = [  # Exact-match Forward Deployed roles (rare; alert-tier)
    "Forward Deployed Engineer",
    "Forward Deployed Software Engineer",
    "Deployment Engineer",
    "Solutions Engineer",
    "Technical Solutions Engineer",
]

TIER2 = [  # Standard new-grad software engineering (volume tier)
    "Graduate Software Engineer",
    "Software Engineer Graduate Scheme",
    "Junior Software Engineer",
    "Software Engineer",
]
TIER2_CATEGORIES = ["it-jobs", "graduate-jobs"]  # run each Tier 2 keyword twice

TIER3 = [  # AI-adjacent (where dissertation + Mavera work differentiate)
    "AI Engineer",
    "Machine Learning Engineer Graduate",
    "Applied AI Engineer",
    "NLP Engineer",
    "Conversational AI Engineer",
]

TIER4 = [  # Backup/breadth (Python-heavy safety net)
    "Python Developer Graduate",
    "Automation Engineer",
    "Junior Data Engineer",
]

TIER5 = [  # Adjacent GTM track — WATCH ONLY (Section 10). Don't apply yet;
           # logged for visibility only. Category unset (niche title Adzuna may
           # misfile under Sales/Marketing).
    "GTM Engineer",
    "Go-to-Market Engineer",
]
TIER5_TAG = "5-watch"  # string tag so these rows stand out from Tiers 1-4


# Noise filter: jobs whose TITLE contains any of these whole words are dropped
# (trades / construction / physical-engineering false positives the loose Tier-1
# keyword match drags in). Matched against the TITLE ONLY — Adzuna's API-side
# `what_exclude` matches the description too and silently drops real roles
# (e.g. an "AI/ML Solutions Engineer" whose JD mentions "maintenance"), so we
# filter client-side where we can see exactly what we're cutting.
TITLE_EXCLUDE = [
    "fire", "civil", "structural", "geotechnical", "mechanical", "electrical",
    "hvac", "plumbing", "scaffolding", "lift", "elv", "maintenance", "revit",
    "bim", "field service", "cad technician", "quantity surveyor",
]

# Suitability filter: the target profile is a new graduate, so the loose keyword searches
# ("Software Engineer", "Solutions Engineer", "AI Engineer") drag in a lot of
# SENIOR / management roles he can't apply to yet. Any of these whole words in a
# job TITLE drops the row (title-only + logged, same discipline as TITLE_EXCLUDE
# — never the API-side `what_exclude`, which matches the description and
# silently drops real targets). Grad/junior titles never contain these, so this
# is safe. Tune the list here if it's ever too aggressive.
SENIORITY_EXCLUDE = [
    "senior", "snr", "sr", "principal", "staff", "lead", "leader",
    "manager", "management", "head", "director", "vp", "vice president",
    "chief", "architect", "expert",
]

# --- Apply-shortlist signals (used ONLY to build the "Apply Shortlist" view,
# never to drop a row from the main store) ----------------------------------
# A sponsor_match="yes" row is a *sort aid*, not a genuine target: ~70% of the
# flagged list is recruiter/agency postings (which match the agency's licence,
# not the employer's), or roles that won't sponsor in practice. These lists
# collapse the noisy flag into the clean shortlist that is actually worth applying to.
#
# 1) Recruiters / staffing agencies. A posting from one of these matches the
#    AGENCY's sponsor licence, not the end-employer's — so it can't be treated
#    as sponsor-confirmed. Whole-word keywords catch most; the explicit names
#    below catch the agencies whose names contain none of those words.
RECRUITER_KEYWORDS = [
    "recruit", "recruits", "recruiter", "recruiters", "recruitment",
    "resourcing", "staffing", "selection", "talent", "consultants",
    "consultancy", "search", "resource", "headhunt",
]
RECRUITER_COMPANIES = [   # matched as case-insensitive substrings
    "hays", "robert walters", "robert half", "michael page", "reed",
    "harnham", "noir", "searchability", "henderson scott", "nigel frank",
    "tenth revolution", "big red", "nova source", "ncounter", "selby jennings",
    "understanding recruitment", "spectrum it", "microtech global",
    "pioneer", "proactive global", "daniel-scott", "dunraven", "describe.me",
    "transparency technology", "franklin fitch", "adecco", "randstad",
    "manpower", "experis", "lorien", "sthree", "huxley", "opus recruitment",
    "information tech consultants", "rullion",
]

# 1b) Positive role gate. The tier keyword searches drag construction/admin
#     roles from sponsor-licensed firms (Kier "Multi-Skilled Engineer", "Document
#     Controller", "Hire Administrator", "Quality Advisor") into the store. Bare
#     "engineer" is the trap word, so the shortlist requires the TITLE to name an
#     actual software/AI/data/solutions role — an allowlist, not another endless
#     blocklist. Whole-word/phrase matched on TITLE. (Never drops from the store.)
ROLE_INCLUDE_TERMS = [
    "software", "developer", "programmer", "sde",
    "frontend", "front end", "front-end", "backend", "back end", "back-end",
    "full stack", "full-stack", "fullstack", "web developer",
    "data scientist", "data engineer", "data science", "data analyst",
    "machine learning", "deep learning", "artificial intelligence",
    "ai engineer", "ml engineer", "applied ai", "conversational ai",
    "nlp", "llm",
    "devops", "dev ops", "sre", "site reliability", "cloud engineer",
    "platform engineer", "infrastructure automation",
    "solutions engineer", "solution engineer", "pre-sales", "presales",
    "sales engineer", "forward deployed", "deployment engineer",
    "automation engineer", "qa engineer", "qa automation", "test engineer",
    "test automation", "systems engineer",
    "python", "java developer", "integration engineer",
    "technology analyst", "technology associate", "technology consultant",
    "graduate programme", "graduate scheme", "graduate technology",
]

# 2) Won't-sponsor-in-practice / not-a-grad-role title terms. Security-cleared
#    roles (eDV/DV/SC) are usually UK-nationals only; apprenticeships and
#    internships need existing right to work. Whole-word matched on TITLE.
APPLY_BLOCK_TERMS = [
    "apprentice", "apprenticeship", "edv", "dv cleared", "sc cleared",
    "developed vetting", "security cleared", "placement programme",
    "internship", "intern", "work experience", "no experience needed",
]


def build_saved_searches():
    """Expand the tier lists into the full saved-search dict list."""
    searches = []

    for kw in TIER1:
        searches.append({**SHARED, "keywords": kw, "category": None, "tier": 1})

    for kw in TIER2:                      # doubled across two categories
        for cat in TIER2_CATEGORIES:
            searches.append({**SHARED, "keywords": kw, "category": cat, "tier": 2})

    for kw in TIER3:
        searches.append({**SHARED, "keywords": kw, "category": None, "tier": 3})

    for kw in TIER4:
        searches.append({**SHARED, "keywords": kw, "category": None, "tier": 4})

    for kw in TIER5:                      # watch-only adjacent track
        searches.append({**SHARED, "keywords": kw, "category": None,
                         "tier": TIER5_TAG})

    return searches


SAVED_SEARCHES = build_saved_searches()


if __name__ == "__main__":
    print(f"{len(SAVED_SEARCHES)} saved searches "
          f"({len(TIER1)} + {len(TIER2)}x{len(TIER2_CATEGORIES)} + "
          f"{len(TIER3)} + {len(TIER4)} + {len(TIER5)}):")
    for s in SAVED_SEARCHES:
        cat = s["category"] or "-"
        print(f"  T{s['tier']}  [{cat:>13}]  {s['keywords']}")
