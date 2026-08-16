"""What Anurag shares with someone, and how a shared thing is decided.

A referral lands when the person on the other end has a reason to read past the
first line. Shared university, shared city, shared language — these are the
reasons, and they are worth ranking by.

**Signals are matched against what a person has published about themselves.**
Their stated location, an institution or language named in their bio, the
organisation they belong to. Never an inference from a surname.

That is a practical rule, not a moral one. Guessing heritage from a name is
wrong often enough to waste his time, and an outreach message that opens on a
shared background the recipient does not have is worse than sending nothing at
all. Measured: across the public members of Palantir, Monzo and Faculty, **zero
list an Indian location**, so that signal cannot come from GitHub whatever the
approach — it has to come from LinkedIn, where people state it themselves and
where he does the searching.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Signal:
    """One thing worth having in common."""
    key: str
    label: str          # what the UI says: "also in London"
    weight: int         # how much it should move a contact up the list
    pattern: re.Pattern


def _p(*words: str) -> re.Pattern:
    return re.compile(r"\b(" + "|".join(words) + r")\b", re.IGNORECASE)


# Ordered by how much a stranger would care. A shared university is a genuine
# opening; a shared city is a weak but real one.
SIGNALS: tuple[Signal, ...] = (
    Signal("nottingham", "also University of Nottingham", 50,
           _p(r"university of nottingham", r"nottingham", r"uon")),
    Signal("gtu", "also Gujarat Technological University", 50,
           _p(r"gujarat technological university", r"gtu")),
    Signal("marathi", "speaks Marathi", 40, _p(r"marathi")),
    Signal("gujarati", "speaks Gujarati", 40, _p(r"gujarati")),
    Signal("hindi", "speaks Hindi", 30, _p(r"hindi")),
    Signal("maharashtra", "Maharashtra connection", 30,
           _p(r"maharashtra", r"mumbai", r"pune", r"nagpur")),
    Signal("gujarat", "Gujarat connection", 30,
           _p(r"gujarat", r"ahmedabad", r"surat", r"vadodara", r"baroda")),
    Signal("india", "based in India", 20,
           _p(r"india", r"bengaluru", r"bangalore", r"hyderabad", r"delhi",
              r"chennai", r"kolkata")),
    Signal("london", "also in London", 25, _p(r"london")),
    Signal("uk", "also in the UK", 15,
           _p(r"united kingdom", r"u\.k\.", r"uk", r"england", r"scotland",
              r"wales", r"britain")),
)

# Which of the above are worth a LinkedIn search. Those searches run in his own
# browser, where people DO state education and languages, so the signals GitHub
# cannot supply are exactly the ones worth querying there.
LINKEDIN_SIGNALS = ("nottingham", "gtu", "marathi", "gujarati", "hindi",
                    "maharashtra", "gujarat", "india")

SIGNAL_QUERY = {
    "nottingham": "University of Nottingham",
    "gtu": "Gujarat Technological University",
    "marathi": "Marathi",
    "gujarati": "Gujarati",
    "hindi": "Hindi",
    "maharashtra": "Maharashtra",
    "gujarat": "Gujarat",
    "india": "India",
}


def match(*texts: str | None) -> list[Signal]:
    """Signals present in any of the given self-published strings.

    `uk` is dropped when `london` already matched, so a London-based person
    shows one clear reason rather than two overlapping ones.
    """
    blob = " ".join(t for t in texts if t)
    if not blob.strip():
        return []
    found = [s for s in SIGNALS if s.pattern.search(blob)]
    keys = {s.key for s in found}
    if "london" in keys:
        found = [s for s in found if s.key != "uk"]
    if keys & {"maharashtra", "gujarat", "nottingham", "gtu"}:
        # A specific place beats the generic country.
        found = [s for s in found if s.key != "india"]
    return sorted(found, key=lambda s: -s.weight)


def score(signals: list[Signal]) -> int:
    return sum(s.weight for s in signals)
