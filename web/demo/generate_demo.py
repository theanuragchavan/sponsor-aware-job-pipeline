"""Generate the synthetic dataset the public demo runs on.

Nothing here is real. Not the companies, not the jobs, and **not the register** —
a fabricated register in the genuine schema is safer than a real extract, because
a visitor can never mistake a demo verdict for advice about a real employer.

What *is* real is the shape of the problem. The whole point of this project is
that the same employer appears under different names in different places, so a
demo where every name matches cleanly would quietly misrepresent it as solved.
The generator therefore builds five deliberate resolution shapes:

    exact       55%   NORTHWIND SYSTEMS          the easy case
    suffix      12%   COBALT HARBOUR UK          the prefix rule fires
    trading-as   8%   ORION LTD T A DOVETAIL     the trading-as rule fires
    trap        10%   MERIDIAN CHARITABLE TRUST  a plausible, WRONG suggestion
    absent      15%   nothing on the register    a correct "no"

The trap rows carry the argument. Without them the app looks like it resolves
names automatically; with them a visitor watches the machine propose rubbish and
a human refuse it, which is the actual thesis.

Deterministic: same seed, same rows. Run:

    python web/demo/generate_demo.py
"""
from __future__ import annotations

import json
import random
import sys
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ADZUNA_HOME = HERE.parent.parent
sys.path.insert(0, str(ADZUNA_HOME))

import tracker  # noqa: E402

OUT = HERE / "data"
SEED = 20260815

# --- vocabulary -------------------------------------------------------------
# Compound names in the same style as the existing test fixtures, so the demo
# and the test suite read as one project.
FIRST = ["Northwind", "Cobalt", "Dovetail", "Meridian", "Aldercroft", "Bramble",
         "Cindermere", "Drakewell", "Elmgate", "Fenwick", "Glassmoor",
         "Harrowfield", "Ironvale", "Juniper", "Kestrel", "Larkspur",
         "Marlowe", "Netherby", "Oakhurst", "Pemberton", "Quillon", "Ravenswood",
         "Silverbeck", "Thornbury", "Underhill", "Vantage", "Westmere",
         "Yarrow", "Ashcombe", "Brackenridge", "Coldharbour", "Dunmore",
         "Eastgate", "Fairhaven", "Grindlow", "Highmoor", "Inglewood",
         "Jarrowfield", "Kingsmead", "Lowfield"]
SECOND = ["Systems", "Analytics", "Robotics", "Data", "Labs", "Technologies",
          "Dynamics", "Logic", "Compute", "Networks", "Digital", "Software",
          "Instruments", "Automation", "Intelligence", "Platforms"]
AGENCY_SECOND = ["Resourcing", "Talent", "Recruitment", "Search Partners",
                 "Staffing", "Consultants"]

# A deliberately separate namespace for register filler. No word here appears in
# FIRST or SECOND, so filler can never collide with a company's intended shape.
FILLER_FIRST = ["Barrowmead", "Calderstone", "Duncastle", "Everleigh",
                "Foxbourne", "Granthorpe", "Hollowmere", "Ilminster",
                "Keldwick", "Langmoor", "Mortlake", "Newlyn", "Oswaldby",
                "Prioryfield",
                "Quenby", "Rushmere", "Stanbrook", "Tealby", "Ulverston",
                "Verewood", "Whitmarsh", "Yealand", "Aberfoyle", "Bickleigh",
                "Cranmore", "Dunsfold", "Eldersley", "Fairbourne", "Gatcombe",
                "Hensingham", "Ingoldsby", "Jedburgh", "Kirkbride", "Lyneham"]
FILLER_SECOND = ["Foods", "Care Services", "Logistics", "Property", "Energy",
                 "Insurance", "Motors", "Healthcare", "Retail", "Building",
                 "Packaging", "Textiles", "Chemicals", "Haulage", "Catering",
                 "Agriculture", "Pharmacy", "Dental Practice", "Nurseries",
                 "Veterinary", "Recycling", "Security", "Print", "Marine"]
FILLER_TAIL = ["", "", "", " Ltd", " Group", " UK", " Holdings", " Services",
               " (North)", " (South West)", " International", " Partners"]

TOWNS = ["London", "Manchester", "Bristol", "Leeds", "Cambridge", "Edinburgh",
         "Birmingham", "Reading", "Glasgow", "Oxford", "Sheffield", "Cardiff"]
COUNTIES = ["", "Greater London", "Cambridgeshire", "West Yorkshire", "Avon"]

# Titles chosen so the real classifiers have something to do: some pass every
# gate, some hit SENIORITY_EXCLUDE, some hit CLEARANCE, some hit EARLY_BLOCK.
GOOD_TITLES = [
    "Forward Deployed Software Engineer, New Grad",
    "Graduate Software Engineer", "Junior Software Engineer",
    "Associate Solutions Engineer", "Technical Solutions Engineer",
    "Machine Learning Engineer", "AI Engineer", "Data Engineer",
    "Platform Engineer", "Backend Engineer (Python)", "Full Stack Developer",
    "Deployment Engineer", "Applied AI Engineer", "NLP Engineer",
    "Graduate Data Scientist", "Site Reliability Engineer",
]
SENIOR_TITLES = ["Senior Software Engineer", "Principal Data Engineer",
                 "Lead Platform Engineer", "Engineering Manager",
                 "Head of Data", "Staff Machine Learning Engineer"]
CLEARANCE_TITLES = ["Software Engineer - UK Government", "Platform Engineer (SC Cleared)",
                    "Software Engineer - eDV", "Systems Engineer - NATO"]
BLOCKED_TITLES = ["Software Engineer, Internship",
                  "Digital and Technology Solutions Apprenticeship Level 6",
                  "Graduate Placement Programme"]
NON_TECH_TITLES = ["Document Controller", "Hire Administrator",
                   "Quality Advisor", "Warehouse Operative"]

CLEARANCE_HINT_TEXT = (
    "Because of the nature of the work we do with our Government clients, you "
    "may need to be eligible for UK Developed Vetting (DV) and willing to work "
    "on site.")
PLAIN_DESC = (
    "We are looking for engineers who enjoy working directly with the people "
    "who use what they build. You will ship to production in your first weeks, "
    "and you will own what you ship.")

RATINGS = ["Worker (A rating)", "Worker (A rating)", "Worker (A rating)",
           "Worker (B rating)", "Worker (A (Premium))", "Worker (A (SME+))"]
OTHER_ROUTES = ["Temporary Worker - Creative Worker",
                "Global Business Mobility - Senior or Specialist Worker",
                "Temporary Worker - Charity Worker",
                "Scale-up Worker"]

SHAPES = (["exact"] * 55 + ["suffix"] * 12 + ["trading_as"] * 8
          + ["trap"] * 10 + ["absent"] * 15)


def build(n_companies: int = 260, n_jobs: int = 1200) -> dict:
    rng = random.Random(SEED)

    # --- companies, each with a resolution shape ---------------------------
    names, used = [], set()
    while len(names) < n_companies:
        name = f"{rng.choice(FIRST)} {rng.choice(SECOND)}"
        if name not in used:
            used.add(name)
            names.append(name)

    companies, taken = [], set()
    for i, name in enumerate(names):
        shape = SHAPES[i % len(SHAPES)]

        # A trap has to be a name the prefix rule will actually reach, and the
        # prefix rule matches `^<company>\b`. A two-token name like "Meridian
        # Data" can never be a prefix of "MERIDIAN CHARITABLE TRUST", so a trap
        # built on one produces no suggestion at all and quietly stops being a
        # trap. The real cases are all single-token brands — AMAZON, CATALYST,
        # LEONARDO — so traps use the bare first token.
        if shape == "trap":
            name = name.split()[0]
        elif i % 12 == 5:
            # A slice of the population is named like a recruitment agency, so
            # the not_an_agency validator has something real to refuse.
            name = f"{name.split()[0]} {rng.choice(AGENCY_SECOND)}"

        if name in taken:
            continue
        taken.add(name)
        companies.append({"name": name, "shape": shape})
    rng.shuffle(companies)

    # --- register ----------------------------------------------------------
    register: list[dict] = []

    def add(org: str, route: str = "Skilled Worker") -> None:
        register.append({
            "Organisation Name": org,
            "Town/City": rng.choice(TOWNS),
            "County": rng.choice(COUNTIES),
            "Type & Rating": rng.choice(RATINGS),
            "Route": route,
        })

    for c in companies:
        name, shape = c["name"], c["shape"]
        if shape == "exact":
            add(name)
            c["register_name"] = name.upper()
        elif shape == "suffix":
            add(f"{name} UK")
            c["register_name"] = f"{name} UK".upper()
        elif shape == "trading_as":
            legal = f"{rng.choice(FIRST)} Holdings Ltd"
            add(f"{legal} T/A {name}")
            c["register_name"] = f"{legal} T A {name}".upper()
        elif shape == "trap":
            # Two entries that begin with this company's name and are different
            # organisations entirely. The prefix rule will offer both; a human
            # has to say no to each. Mirrors AMAZON -> AMAZON CHARITABLE TRUST.
            add(f"{name} Charitable Trust")
            add(f"{name} Capital Partners")
            c["register_name"] = None
        else:                                   # absent
            c["register_name"] = None

    # Filler, so the register is a haystack rather than a list of answers.
    #
    # Two constraints, both learned by getting it wrong: the filler must draw
    # from a namespace DISJOINT from the company names, and it must refuse any
    # name a company has already claimed. Sharing the pool meant the filler
    # accidentally registered 87 of the 95 unresolved companies under their
    # exact name, which silently converted them into exact matches and emptied
    # the review queue — the one thing the demo exists to show.
    claimed = {c["name"].upper() for c in companies}
    claimed |= {r["Organisation Name"].upper() for r in register}

    filler_seen: set[str] = set()
    guard = 0
    while len(register) < 5000 and guard < 60000:
        guard += 1
        org = (f"{rng.choice(FILLER_FIRST)} {rng.choice(FILLER_SECOND)}"
               f"{rng.choice(FILLER_TAIL)}")
        key = org.upper()
        if key in claimed or key in filler_seen:
            continue
        filler_seen.add(key)
        add(org, "Skilled Worker" if rng.random() < 0.8
            else rng.choice(OTHER_ROUTES))

    rng.shuffle(register)

    # --- jobs --------------------------------------------------------------
    rows, today = [], date.today()
    for i in range(n_jobs):
        c = companies[i % len(companies)]
        roll = rng.random()
        if roll < 0.70:
            title = rng.choice(GOOD_TITLES)
        elif roll < 0.80:
            title = rng.choice(SENIOR_TITLES)
        elif roll < 0.87:
            title = rng.choice(CLEARANCE_TITLES)
        elif roll < 0.93:
            title = rng.choice(BLOCKED_TITLES)
        else:
            title = rng.choice(NON_TECH_TITLES)

        # Board rows carry a namespace; aggregator rows are bare 10-digit ids in
        # a range that cannot collide with a real Adzuna id. Keeping them bare
        # means the age gate still fires and its drop reason stays visible.
        source = rng.choice(["adzuna"] * 6 + ["gh", "ashby", "lever"])
        jid = (f"{9990000000 + i}" if source == "adzuna"
               else f"{source}:{9990000000 + i}")

        posted = today - timedelta(days=rng.randint(0, 120))
        row = {name: "" for name in tracker.FIELDNAMES}
        row.update({
            "id": jid,
            "title": title,
            "company": c["name"],
            "location": rng.choice(TOWNS),
            "salary_min": str(rng.choice([0, 30000, 35000, 40000])),
            "salary_max": str(rng.choice([45000, 55000, 65000, 75000, 90000])),
            "contract_type": "permanent",
            "category": "it-jobs",
            "tier": str(rng.choice([1, 1, 2, 2, 2, 3, 4])),
            "posted_date": posted.isoformat(),
            # .invalid is reserved by RFC 2606 and can never resolve, so no
            # link in the demo can accidentally point at a real listing.
            "redirect_url": f"https://example.invalid/jobs/{jid}",
            "date_first_seen": posted.isoformat(),
            "status": tracker.DEFAULT_STATUS,
            "sponsor_match": "no",
            "sponsor_rating": "",
            "sponsor_route": "",
            "description": (CLEARANCE_HINT_TEXT if rng.random() < 0.12
                            else PLAIN_DESC),
            "source": source,
            "apply_deadline": "",
        })
        rows.append(row)

    return {"companies": companies, "register": register, "rows": rows}


def stamp_exact_matches(rows, companies) -> None:
    """Pre-mark the rows an exact register match already resolves.

    Without this every company starts unresolved and the queue is 260 long,
    which is not what the pipeline actually looks like after a run.
    """
    exact = {c["name"] for c in companies if c["shape"] == "exact"}
    for row in rows:
        if row["company"] in exact:
            row["sponsor_match"] = "yes"
            row["sponsor_rating"] = "A"
            row["sponsor_route"] = "Skilled Worker"


def seed_decision_log() -> int:
    """Make a few real decisions so a cold instance has a history to show.

    Driven through the actual action layer rather than written by hand. A
    hand-written log would need a hand-computed hash chain, and a demo whose
    "chain verified" badge is green because the fixture was crafted to make it
    green is worth nothing. These entries are genuine: same validators, same
    writeback, same hashes.
    """
    sys.path.insert(0, str(ADZUNA_HOME / "web"))
    from web.api.actions import ActionError, Actions
    from web.api.audit import DecisionLog
    from web.api.registry import Registry
    from web.api.settings import Settings
    from web.api.store import TrackerStore
    from web.api import views

    settings = Settings(
        mode="demo", tracker_path=OUT / "demo_tracker.xlsx",
        aliases_path=OUT / "demo_aliases.json",
        rejections_path=OUT / "demo_rejections.json",
        decision_log_path=OUT / "demo_decision_log.jsonl",
        register_csv=OUT / "demo_register.csv",
        actor_id="demo", actor_name="Demo visitor",
        cors_origins=(), rate_limit_per_min=0, max_log_entries=0)

    store = TrackerStore(settings.tracker_path)
    registry = Registry(settings.register_csv, settings.aliases_path,
                        settings.rejections_path)
    log = DecisionLog(settings.decision_log_path)
    actions = Actions(store, registry, log, settings)

    queue = views.review_queue(store.rows().values(), registry)
    # Only the genuinely correct shapes: a company whose suggestion is its own
    # name plus a suffix. Seeding a trap confirmation would be teaching the
    # wrong lesson in the one artefact people read first.
    good = [q for q in queue
            if q.suggestions and q.suggestions[0].register_name.startswith(
                q.company.upper() + " ")
            and not q.suggestions[0].register_name.endswith(
                ("CHARITABLE TRUST", "CAPITAL PARTNERS"))]

    reasons = [
        "Same registered address on Companies House; the board prints the brand.",
        "The careers page footer names this legal entity.",
        "Group filing lists this as the employing entity for UK engineering.",
    ]
    made = 0
    for item, reason in zip(good[:3], reasons):
        try:
            actions.resolve_company(
                company=item.company,
                register_name=item.suggestions[0].register_name,
                rationale=reason, source_rule=item.suggestions[0].why)
            made += 1
        except ActionError:
            continue
    return made


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    data = build()
    rows, companies, register = data["rows"], data["companies"], data["register"]
    stamp_exact_matches(rows, companies)

    # register CSV, in the genuine column order
    import csv
    reg_path = OUT / "demo_register.csv"
    with open(reg_path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=[
            "Organisation Name", "Town/City", "County", "Type & Rating", "Route"])
        writer.writeheader()
        writer.writerows(register)

    # the store, written through the public API so it gets both sheets
    tracker.save_tracker(str(OUT / "demo_tracker.xlsx"),
                         {r["id"]: r for r in rows})

    # a handful of pre-confirmed aliases, so the demo does not start empty
    seeded = [c for c in companies
              if c["shape"] in ("suffix", "trading_as") and c["register_name"]][:8]
    aliases = {c["name"]: {"register_name": c["register_name"], "rating": "A",
                           "confirmed": "2026-08-10", "actor": "demo",
                           "rationale": "Confirmed against Companies House."}
               for c in seeded}
    (OUT / "demo_aliases.json").write_text(
        json.dumps(aliases, indent=2, sort_keys=True), encoding="utf-8")
    (OUT / "demo_rejections.json").write_text("{}", encoding="utf-8")
    (OUT / "demo_decision_log.jsonl").write_text("", encoding="utf-8")

    seeded_log = seed_decision_log()

    shapes: dict[str, int] = {}
    for c in companies:
        shapes[c["shape"]] = shapes.get(c["shape"], 0) + 1
    print(f"{len(rows)} jobs · {len(companies)} companies · "
          f"{len(register)} register rows")
    print("shapes:", dict(sorted(shapes.items())))
    print(f"pre-confirmed aliases: {len(aliases)}  (+{seeded_log} seeded decisions)")
    print(f"written to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
