"""Find real people at a company via their public GitHub organisation.

Public, permitted, and free: 5,000 authenticated calls an hour on the `gh`
token already on this machine. Measured on the employers that matter —

    Palantir   33 public members · 33 with real names · 10 in the UK
    Monzo      33 · 32 named · 17 in the UK
    Faculty     5 ·  4 named ·  2 in the UK
    Wayve      no public org at all

Two honest limits, both worth stating in the UI rather than hiding:

**Only people who made their membership public appear.** A company of 4,000
engineers may show 33. This is a starting point, not a directory.

**Org membership is employment; repo contribution is not.** Someone with 200
commits to palantir/blueprint may work there or may be an outside contributor
who liked the project. Members and contributors are therefore returned as
separate, differently-labelled groups — collapsing them would quietly present
strangers as colleagues.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass, field

from . import profile
from .profile import Signal

#: Company name -> GitHub org, where guessing gets it wrong. Everything not
#: listed is tried as a lowercased, despaced version of the name first.
ORG_OVERRIDES = {
    "faculty": "facultyai",
    "snowflake": "snowflakedb",
    "improbable": "improbable-eng",
    "elevenlabs": "elevenlabs",
    "the trade desk": "thetradedesk",
    "man group": "manahl",
    "g-research": "g-research",
    "starling bank": "starlingbank",
}

_CACHE: dict[str, tuple[float, list]] = {}
CACHE_SECONDS = 6 * 60 * 60


@dataclass
class Person:
    login: str
    name: str
    location: str
    bio: str
    blog: str
    company: str
    url: str
    relationship: str                    # "member" | "contributor"
    contributions: int = 0
    signals: list[Signal] = field(default_factory=list)

    @property
    def score(self) -> int:
        # A confirmed employee outranks a contributor with the same signals,
        # because only one of them can actually refer you.
        base = 10 if self.relationship == "member" else 0
        return profile.score(self.signals) + base

    def as_dict(self) -> dict:
        return {
            "login": self.login, "name": self.name or self.login,
            "location": self.location, "bio": self.bio, "blog": self.blog,
            "url": self.url, "relationship": self.relationship,
            "contributions": self.contributions, "score": self.score,
            "signals": [{"key": s.key, "label": s.label} for s in self.signals],
        }


class GitHubUnavailable(RuntimeError):
    """gh is missing or unauthenticated. Not fatal — other sources still work."""


def _gh(path: str) -> list | dict | None:
    """One API call through the gh CLI, which already holds the token.

    Using gh rather than raw HTTP means no token is read, stored or passed
    around by this code at any point.
    """
    try:
        # encoding must be explicit. `text=True` alone decodes with the Windows
        # ANSI codepage, and GitHub bios are full of accented names and emoji
        # that cp1252 cannot represent — it raised UnicodeDecodeError on the
        # very first real org. errors="replace" keeps one odd character from
        # dropping an entire person.
        out = subprocess.run(["gh", "api", path], capture_output=True,
                             text=True, encoding="utf-8", errors="replace",
                             timeout=30)
    except FileNotFoundError as exc:
        raise GitHubUnavailable("the gh CLI is not installed") from exc
    except subprocess.TimeoutExpired:
        return None
    if out.returncode != 0:
        # 404 for "no such org" is an ordinary answer, not a failure.
        return None
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError:
        return None


#: What GitHub actually permits in an org name: alphanumerics and single
#: hyphens, 1-39 characters, no hyphen at either end.
_ORG_OK = re.compile(r"^[a-z0-9](?:[a-z0-9]|-(?=[a-z0-9])){0,38}$")


def candidate_orgs(company: str) -> list[str]:
    """Slugs worth trying for this employer, guaranteed safe to put in a path.

    Company names come from job boards — external data this code does not
    control. The first version built one candidate as `"-".join(key.split())`,
    which preserved everything except spaces: a company called
    `../../user/octocat` produced exactly that slug, and `orgs/../../user/octocat`
    resolves to a different GitHub endpoint entirely.

    No shell is involved (subprocess takes a list), so this was never command
    injection. It was path traversal into somebody else's API surface, which is
    still not something to leave open. Every candidate is now validated against
    what GitHub itself allows, and anything that fails is dropped rather than
    sanitised into a different company's name.
    """
    key = company.strip().lower()
    if key in ORG_OVERRIDES:
        return [ORG_OVERRIDES[key]]

    flat = "".join(ch for ch in key if ch.isalnum())
    dashed = "-".join("".join(ch for ch in word if ch.isalnum())
                      for word in key.split())
    dashed = dashed.strip("-")

    # Suffixes only when there is a base to attach them to. Without this guard
    # a company name of "" or ".." yields ["hq"] — an org that exists on GitHub
    # and has nothing to do with the employer, so the panel would fill with
    # strangers under a plausible-looking heading.
    candidates = [flat, dashed]
    if flat:
        candidates += [flat + "hq", flat + "-oss"]
    return [c for c in dict.fromkeys(candidates) if c and _ORG_OK.match(c)]


def find_org(company: str) -> str | None:
    for slug in candidate_orgs(company):
        if _gh(f"orgs/{slug}") is not None:
            return slug
    return None


def _person(login: str, relationship: str, contributions: int = 0) -> Person | None:
    data = _gh(f"users/{login}")
    if not isinstance(data, dict):
        return None
    p = Person(
        login=data.get("login", login),
        name=(data.get("name") or "").strip(),
        location=(data.get("location") or "").strip(),
        bio=(data.get("bio") or "").strip(),
        blog=(data.get("blog") or "").strip(),
        company=(data.get("company") or "").strip(),
        url=data.get("html_url", f"https://github.com/{login}"),
        relationship=relationship,
        contributions=contributions,
    )
    p.signals = profile.match(p.location, p.bio, p.name, p.company)
    return p


def people_at(company: str, limit: int = 40,
              include_contributors: bool = False) -> dict:
    """Everyone findable at `company`, best match first.

    Cached for six hours: org membership does not change hourly, and re-fetching
    forty profiles every time the panel opens would burn the rate limit for no
    new information.
    """
    cache_key = f"{company}|{include_contributors}"
    hit = _CACHE.get(cache_key)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return {"org": hit[1][0], "people": hit[1][1], "cached": True}

    org = find_org(company)
    if not org:
        result = (None, [])
        _CACHE[cache_key] = (time.time(), result)
        return {"org": None, "people": [], "cached": False}

    people: list[Person] = []
    seen: set[str] = set()

    members = _gh(f"orgs/{org}/members?per_page=100") or []
    for m in members[:limit]:
        login = m.get("login")
        if not login or login in seen:
            continue
        seen.add(login)
        p = _person(login, "member")
        if p:
            people.append(p)

    if include_contributors and len(people) < limit:
        repos = _gh(f"orgs/{org}/repos?per_page=5&sort=updated") or []
        for repo in repos:
            name = repo.get("name")
            if not name:
                continue
            for c in (_gh(f"repos/{org}/{name}/contributors?per_page=20") or []):
                login = c.get("login", "")
                # Bots are not people and cannot refer anyone.
                if not login or login in seen or login.endswith("[bot]"):
                    continue
                seen.add(login)
                p = _person(login, "contributor", c.get("contributions", 0))
                if p:
                    people.append(p)
                if len(people) >= limit:
                    break
            if len(people) >= limit:
                break

    people.sort(key=lambda p: (-p.score, -p.contributions, p.login.lower()))
    payload = [p.as_dict() for p in people]
    _CACHE[cache_key] = (time.time(), (org, payload))
    return {"org": org, "people": payload, "cached": False}
