"""Build LinkedIn searches. Never run them.

This module makes URLs and stops. Nothing here fetches, parses or stores
anything from LinkedIn, and nothing should ever be added that does: scraping
breaches their terms and risks the account he needs for the job hunt itself.

Handing him a link is also simply better. LinkedIn's own search knows who
studied where, who speaks what and who works at which company today — none of
which is available from any public API. He clicks the link, it opens in his
browser where he is already logged in, and he sees results no scraper would
match.

This is where the signals GitHub cannot supply pay off. Measured: zero of the
71 public GitHub members across Palantir, Monzo and Faculty list an Indian
location, and none list a university at all. On LinkedIn people state both.
"""
from __future__ import annotations

from urllib.parse import quote_plus

from .profile import LINKEDIN_SIGNALS, SIGNAL_QUERY

PEOPLE_SEARCH = "https://www.linkedin.com/search/results/people/?keywords="


def _url(*terms: str) -> str:
    return PEOPLE_SEARCH + quote_plus(" ".join(t for t in terms if t))


def searches_for(company: str) -> list[dict]:
    """Ordered searches to run for one employer, strongest opening first.

    Keyword search rather than LinkedIn's structured company/school filters,
    because those need internal numeric ids that would have to be looked up —
    which would mean querying LinkedIn, which is the thing this module exists
    not to do.
    """
    company = company.strip()
    if not company:
        return []

    out = [{
        "key": "alumni-nottingham",
        "label": f"{company} + University of Nottingham",
        "why": "Shared university is the strongest cold opening there is, and "
               "alumni reply to alumni.",
        "url": _url(company, "University of Nottingham"),
    }, {
        "key": "alumni-gtu",
        "label": f"{company} + Gujarat Technological University",
        "why": "The other alumni network, and far less contested than "
               "Nottingham at UK employers.",
        "url": _url(company, "Gujarat Technological University"),
    }]

    for key in LINKEDIN_SIGNALS:
        if key in ("nottingham", "gtu"):
            continue
        term = SIGNAL_QUERY[key]
        out.append({
            "key": key,
            "label": f"{company} + {term}",
            "why": f"People who mention {term} on their own profile.",
            "url": _url(company, term),
        })

    out.append({
        "key": "recruiter",
        "label": f"{company} recruiters",
        "why": "A recruiter cannot refer you, but they can tell you whether the "
               "role is real and still open.",
        "url": _url(company, "recruiter talent acquisition"),
    })
    return out
