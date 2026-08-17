"""Google X-Ray searches. Built here, run by him.

Same rule as `linkedin.py`: this module makes URLs and stops. Nothing fetches
Google, and nothing parses a result page.

Why bother when `linkedin.py` already exists — LinkedIn's own search applies
commercial-use limits that cut you off after a certain number of profile views
in a month, and it ranks by its own idea of relevance. Google's index of the
same public profiles has neither constraint, and `site:linkedin.com/in/` with
two quoted terms is a sharper instrument than LinkedIn's keyword box.

The pairs worth searching are his, and they are the ones GitHub cannot supply:
university, language, home state. People write those on a LinkedIn profile and
almost never on a GitHub one — measured, across 71 public org members at
Palantir, Monzo and Faculty, not one named a university.
"""
from __future__ import annotations

from urllib.parse import quote_plus

GOOGLE = "https://www.google.com/search?q="

#: Ordered by how much a stranger would care, same reasoning as profile.SIGNALS.
PAIRS: tuple[tuple[str, str, str], ...] = (
    ("nottingham", "University of Nottingham",
     "Shared university is the strongest opening there is."),
    ("gtu", "Gujarat Technological University",
     "The other alumni network, and far less contested at UK employers."),
    ("marathi", "Marathi", "People who list the language on their profile."),
    ("gujarati", "Gujarati", "Same, and common in UK tech."),
    ("hindi", "Hindi", "Broadest of the three."),
    ("maharashtra", "Maharashtra", "Home state, stated on the profile."),
    ("gujarat", "Gujarat", "Where you studied and lived."),
)


def _q(*terms: str) -> str:
    return GOOGLE + quote_plus(" ".join(terms))


def searches_for(company: str, role: str = "") -> list[dict]:
    """X-Ray searches for one employer.

    `-inurl:dir` drops LinkedIn's directory index pages, which otherwise fill
    the first page of results with lists of names rather than profiles.
    """
    company = company.strip()
    if not company:
        return []

    base = f'site:linkedin.com/in/ "{company}"'
    out: list[dict] = []

    for key, term, why in PAIRS:
        out.append({
            "key": f"xray-{key}",
            "label": f'{company} + {term}',
            "why": why,
            "url": _q(base, f'"{term}"', "-inurl:dir"),
        })

    if role.strip():
        out.append({
            "key": "xray-role",
            "label": f'{company} + {role.strip()}',
            "why": "People already doing the job you are applying for — the "
                   "peers who can tell you what the team actually needs.",
            "url": _q(base, f'"{role.strip()}"', "-inurl:dir"),
        })

    out.append({
        "key": "xray-hiring-manager",
        "label": f"{company} engineering managers",
        "why": "The person who owns the requisition, rather than the recruiter "
               "who posted it.",
        "url": _q(base, '("Engineering Manager" OR "Head of Engineering" '
                        'OR "Tech Lead")', '"London"', "-inurl:dir"),
    })

    # GitHub profiles carry a free-text company field that GitHub's own search
    # does not index well, but Google does.
    out.append({
        "key": "xray-github",
        "label": f"{company} engineers on GitHub",
        "why": "Catches people whose GitHub says where they work but who never "
               "made their org membership public, so the org listing misses them.",
        "url": _q(f'site:github.com "{company}"', '"London"'),
    })
    return out
