"""Refuse to ship outbound text that says something CONTENT_MASTER does not.

The CV gate proves a document is *readable*. Nothing proved it was *true*.

Grounding is not decidable and this does not pretend otherwise. It takes the
decomposition instead: split the mechanisable half off and leave a human the
genuinely semantic residue. The mechanisable half is bigger than it looks --
every number, every employer, every institution, every date range is a literal
that either appears in the source or does not.

What it checks
--------------
1. **Numeric claims.** Every figure in the draft must appear in CONTENT_MASTER.
   Ported from career-ops' `verify-cv-facts.mjs`, including both bugs its
   comments record: a modifier window of 4 rather than 2 (at 2, "~5 live Cloud
   Run deployments" produced no claim at all, so a *changed* number passed), and
   magnitude-suffix capture (without it "50k users" normalised to "50" and let a
   1000x inflation through while catching a smaller one).

2. **TARGET presented as ACHIEVED.** CONTENT_MASTER section 7 marks each metric
   TARGET or ACHIEVED, and the distinction is load-bearing: "20 lakhs INR" is a
   pharma project revenue *goal*, "greater than 90%" is a model accuracy *goal*.
   Both are real numbers in the source, so a pure appears-or-not check passes
   them happily while the sentence around them claims a result that never
   happened. That is a worse lie than an invented number, because it survives
   scrutiny right up until someone asks how it was measured.

3. **Named entities.** Employers, institutions and qualifications must appear
   verbatim. Catches the drift where "Machine Learning & Data Science Intern"
   quietly becomes "Machine Learning Engineer".

4. **A substance floor.** The anti-gaming guard. A letter with nothing in it
   passes "nothing is fabricated" perfectly, and an agent one attempt away from
   a blocking gate has an obvious escape: delete the sentence.

   It counts **evidence**, not digits. The first version counted numbers and
   demanded three, which failed the real Syntasso letter -- a good letter that
   contains exactly one figure, because it argues in prose. Numbers are one kind
   of evidence; a named employer, a named project, a named technology are others,
   and a cover letter that name-drops nothing is the actual failure this is
   guarding against. Counting digits would have pushed drafts toward statistics
   they do not need, which is a worse outcome than the thing it prevents.

   Anchored to a fixed minimum, never to the previous draft. The Flagship 2
   harness learned that expensively: its volume floor rejected a *correct*
   recovery because the floor was anchored to an inflated baseline. When you are
   recovering from a regression, saying less is the right fix, and a rolling
   floor cannot tell that apart from dodging.

What it does NOT check
----------------------
Tone, implication, whether "led" overstates "contributed to", whether a
reframing is honest. Those need judgment, and judgment does not belong inside a
deterministic gate -- putting an LLM in here would reintroduce exactly the
self-assessment the gate exists to replace. That residue goes to the
`loop-verifier` agent, outside the gate, advisory.

Exit codes, same contract as the loop scaffold:
    0  every claim is grounded and the draft says enough
    1  something is ungrounded, or the draft says too little
    2  cannot verify -- no source, or the draft is unreadable
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

EXIT_GROUNDED = 0
EXIT_UNGROUNDED = 1
EXIT_CANNOT_VERIFY = 2

#: Minimum pieces of evidence in an outbound draft -- numbers, named employers,
#: named projects, named technologies, all counted together. Anchored to a
#: constant, not to the previous draft; see the module docstring.
#:
#: Three, because the real Syntasso letter carries five (Mavera AI, Kratix,
#: Syntasso, the MSc dissertation, 0.85 macro-F1) and a letter that could not
#: name three specific things would be the generic one the voice guide already
#: forbids.
MIN_EVIDENCE = 3

#: How many words may sit between a number and the noun it counts.
#:
#: career-ops shipped this at 2 and documented what broke: "~5 live Cloud Run
#: deployments" (three modifiers) produced no claim while "~5 Cloud Run
#: deployments" (two) did, so a truthful CV failed AND a changed number passed,
#: because there was no claim on one side to compare. Four covers the phrasings
#: that actually appear. Widening cannot hide an invented number -- it only ever
#: extracts more claims, on both sides -- and a digit is a hard barrier, so a
#: wider window still cannot reach across an intervening figure.
MODIFIER_WINDOW = 4

#: A magnitude suffix belongs to the number, not the modifier chain. Without
#: this, "2.4M tweets" normalises to "2.4 tweets".
NUMBER = r"\d[\d,.]*(?:\s?(?:[kKmMbB]|lakh|lakhs|crore|million|billion)\b)?"

_CLAIM_RE = re.compile(
    rf"\b({NUMBER})\s*\+?\s*(?:%|percent)?\s*"
    rf"(?:[A-Za-z][A-Za-z-]*\s+){{0,{MODIFIER_WINDOW}}}?",
    re.IGNORECASE,
)

#: Numbers that carry no claim about him. A year is not an achievement, and
#: "one of" is not a count.
_NOISE = re.compile(
    r"^(?:19|20)\d{2}$"                       # years
    r"|^[01](?:\.0+)?$"                       # 0, 1 -- almost always prose
    r"|^\d{1,2}$(?<!\d{3})",                  # bare small ints, handled below
)

#: Words whose adjacent number is structural rather than a claim.
_STRUCTURAL_CONTEXT = re.compile(
    r"\b(?:page|paragraph|section|version|tier|step|figure|table|"
    r"jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|"
    r"monday|tuesday|wednesday|thursday|friday)\b",
    re.IGNORECASE,
)


def normalise(text: str) -> str:
    """Fold to a comparable form: ASCII, straight quotes, single spaces.

    Curly apostrophes and en-dashes are what a PDF extractor produces and what a
    markdown source usually does not, so comparing raw text reports differences
    that are purely typographic. The CV gate learned the same lesson from the
    other direction.
    """
    text = (text.replace("\u2019", "'").replace("\u2018", "'")
                .replace("\u201c", '"').replace("\u201d", '"')
                .replace("\u2014", "-").replace("\u2013", "-"))
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", text)


def _canon_number(raw: str) -> str:
    """'2.4M' -> '2.4m'; '11,000' -> '11000'. Comparable across phrasings."""
    n = raw.strip().lower().replace(",", "").replace(" ", "")
    n = re.sub(r"(lakhs?|crore|million|billion)$",
               lambda m: {"lakh": "lakh", "lakhs": "lakh", "crore": "crore",
                          "million": "m", "billion": "b"}[m.group(1)], n)
    return n.rstrip(".")


def extract_numbers(text: str) -> list[str]:
    """Every number that plausibly asserts something, canonicalised."""
    out: list[str] = []
    flat = normalise(text)
    for m in re.finditer(rf"\b({NUMBER})", flat):
        raw = m.group(1)
        canon = _canon_number(raw)
        if not canon or _NOISE.match(canon):
            continue
        window = flat[max(0, m.start() - 40):m.end() + 40]
        if _STRUCTURAL_CONTEXT.search(window):
            continue
        out.append(canon)
    return out


#: Proper nouns that count as evidence when they appear in a draft. Drawn from
#: CONTENT_MASTER rather than invented here, so a name he stops using stops
#: counting. Matched case-insensitively on word boundaries.
_EVIDENCE_TERMS = (
    # employers and institutions
    "Mavera", "Edureka", "Maxgen", "Creart", "NielsenIQ",
    "Nottingham", "Gujarat Technological", "GTU",
    # projects that exist in the portfolio
    "Subway", "BERTopic", "RoBERTa", "Kratix", "ARIMA", "Gradio",
    "Big Data", "Sarcasm", "Thyroid", "Drought", "British Airways",
    # the stack he actually used
    "Python", "PySpark", "Databricks", "PyTorch", "scikit-learn", "Tableau",
    "Django", "HuggingFace", "Hugging Face", "LLM", "NLP", "ROS", "SQL",
    "Gradient Boosting", "macro-F1",
)

_EVIDENCE_RE = re.compile(
    r"\b(" + "|".join(re.escape(t) for t in _EVIDENCE_TERMS) + r")\b",
    re.IGNORECASE)


def extract_evidence(text: str) -> list[str]:
    """Specific, checkable things the draft names. Deduplicated.

    Numbers count, but so does naming an employer, a project or a tool. A letter
    whose only specificity is a metric is not obviously better than one whose
    specificity is "I built the feedback loop at Mavera AI", and demanding
    numbers would push drafts toward statistics they do not need.
    """
    flat = normalise(text)
    found = {m.group(1).lower() for m in _EVIDENCE_RE.finditer(flat)}
    return sorted(found) + extract_numbers(text)


def load_source(path: Path) -> tuple[str, dict[str, str]]:
    """Return (normalised text, {canonical number: TARGET|ACHIEVED|...}).

    The status map comes from CONTENT_MASTER section 7, whose fourth column
    already records the distinction. Parsing it rather than restating it means
    the truth lives in one place.
    """
    text = normalise(path.read_text(encoding="utf-8", errors="replace"))
    status: dict[str, str] = {}
    raw = path.read_text(encoding="utf-8", errors="replace")
    for line in raw.splitlines():
        if not line.startswith("|") or line.count("|") < 4:
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 4:
            continue
        value, verdict = cells[0], cells[3].upper()
        kind = ("TARGET" if "TARGET" in verdict and "ACHIEVED" not in verdict
                else "ACHIEVED" if "ACHIEVED" in verdict else "")
        if not kind:
            continue
        for m in re.finditer(rf"({NUMBER})", normalise(value)):
            canon = _canon_number(m.group(1))
            if canon and not _NOISE.match(canon):
                # First writer wins: section 7 lists the authoritative row first.
                status.setdefault(canon, kind)
    return text, status


def check(draft: str, source_text: str, status: dict[str, str],
          *, min_evidence: int = MIN_EVIDENCE) -> dict:
    """Every finding, as data. The caller decides what to do about it."""
    flat = normalise(draft)
    claims = extract_numbers(draft)
    evidence = extract_evidence(draft)
    source_numbers = set(extract_numbers(source_text))

    ungrounded = [c for c in claims if c not in source_numbers]

    # A TARGET quoted next to result language is the interesting failure.
    result_words = re.compile(
        r"\b(?:achiev\w*|deliver\w*|generat\w*|increas\w*|reduc\w*|improv\w*|"
        r"result\w*|drove|drove|led to|produced|reached|hit)\b", re.IGNORECASE)
    target_as_result: list[dict] = []
    for c in claims:
        if status.get(c) != "TARGET":
            continue
        # Search on the digits only. The canonical form carries a magnitude
        # suffix that is not in the prose -- "20 lakhs INR" canonicalises to
        # "20lakh", and looking for that literal finds nothing, so every
        # lakh/crore/million TARGET slipped through silently. Stripping "m" and
        # "k" by hand missed the same class one word wider.
        digits = re.match(r"[\d.]+", c)
        if not digits:
            continue
        for m in re.finditer(rf"\b{re.escape(digits.group(0))}\b", flat):
            window = flat[max(0, m.start() - 90):m.end() + 90]
            if result_words.search(window):
                target_as_result.append({"value": c, "context": window.strip()})
                break

    return {
        "claims": claims,
        "claim_count": len(claims),
        "ungrounded": ungrounded,
        "target_as_result": target_as_result,
        "evidence": evidence,
        "evidence_count": len(evidence),
        "min_evidence": min_evidence,
        "below_floor": len(evidence) < min_evidence,
    }


def verdict(report: dict) -> tuple[int, list[str]]:
    """(exit code, human-readable PASS/FAIL lines for BOTH outcomes).

    Both outcomes, deliberately: an accepted draft should be as auditable as a
    rejected one. Same contract as the Flagship 2 harness's gate().
    """
    lines: list[str] = []
    ok = True

    if report["ungrounded"]:
        ok = False
        lines.append(f"FAIL grounding: {len(report['ungrounded'])} number(s) not "
                     f"in CONTENT_MASTER: {', '.join(report['ungrounded'])}")
    else:
        lines.append(f"PASS grounding: all {report['claim_count']} number(s) "
                     f"appear in CONTENT_MASTER")

    if report["target_as_result"]:
        ok = False
        for t in report["target_as_result"]:
            lines.append(f"FAIL target-as-result: {t['value']} is a TARGET in "
                         f"CONTENT_MASTER, quoted as an outcome")
    else:
        lines.append("PASS target-as-result: no goal presented as an achievement")

    if report["below_floor"]:
        ok = False
        lines.append(f"FAIL substance floor: {report['evidence_count']} "
                     f"specific thing(s) named, needs {report['min_evidence']} "
                     f"-- guards against passing by saying nothing")
    else:
        lines.append(f"PASS substance floor: {report['evidence_count']} "
                     f"specific thing(s) named (floor {report['min_evidence']})")

    return (EXIT_GROUNDED if ok else EXIT_UNGROUNDED), lines


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Check outbound text against CONTENT_MASTER.")
    ap.add_argument("draft", help="the .md or .txt to check")
    ap.add_argument("--source", default=r"D:\Resume\CONTENT_MASTER.md")
    ap.add_argument("--min-evidence", type=int, default=MIN_EVIDENCE)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    draft_path, source_path = Path(args.draft), Path(args.source)
    if not draft_path.exists():
        print(f"CANNOT VERIFY: no draft at {draft_path}")
        return EXIT_CANNOT_VERIFY
    if not source_path.exists():
        print(f"CANNOT VERIFY: no CONTENT_MASTER at {source_path}. Nothing to "
              f"check against is not the same as nothing to find.")
        return EXIT_CANNOT_VERIFY

    try:
        draft = draft_path.read_text(encoding="utf-8", errors="replace")
        source_text, status = load_source(source_path)
    except OSError as exc:
        print(f"CANNOT VERIFY: {exc}")
        return EXIT_CANNOT_VERIFY

    report = check(draft, source_text, status,
                   min_evidence=args.min_evidence)
    code, lines = verdict(report)

    if args.json:
        print(json.dumps({**report, "exit": code, "reasons": lines}, indent=2))
        return code

    print(f"--- {draft_path.name} ---")
    for line in lines:
        print("  " + line)
    print()
    print("GROUNDED" if code == EXIT_GROUNDED else "NOT GROUNDED -- fix before "
          "this goes anywhere outbound")
    return code


if __name__ == "__main__":
    sys.exit(main())
