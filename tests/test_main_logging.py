"""The per-search log line in main.py, checked against every real search.

This exists because of a bug that ran for weeks without anyone noticing, and
the reason nobody noticed is the interesting part: `logging` catches a format
error inside `emit`, prints "--- Logging error ---" to stderr, and lets the
program continue. So a broken log call is not a crash. It is a missing line,
and a missing line in a headless 09:00 task is invisible.

The specific fault: tiers 1-4 are ints, Tier 5 is the string tag "5-watch"
(`config.TIER5_TAG`), and the format used `%d` for all of them. Two searches a
day silently stopped reporting.

Rather than pin the literal format string here, these read it out of `main.py`.
A copy in the test would let the two drift apart, and a test that agrees with a
stale copy of the code proves nothing.

Run:  python tests/test_main_logging.py
"""
import logging
import os
import pathlib
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402

MAIN = pathlib.Path(__file__).resolve().parent.parent / "main.py"


def per_search_format() -> str:
    """The format string main.py uses for its per-search progress line."""
    src = MAIN.read_text(encoding="utf-8")
    m = re.search(r'logger\.info\(\s*"(T%[sd] \[%s\][^"]*)"', src)
    assert m, ("could not find the per-search log line in main.py — if it was "
               "renamed, update this test rather than deleting it")
    return m.group(1)


def test_the_per_search_line_renders_for_every_configured_search():
    """Every search in the real config, not a representative one.

    The broken case was 2 searches out of 21. A test that checked one search
    would have picked a Tier 1 and passed.
    """
    fmt = per_search_format()
    searches = config.SAVED_SEARCHES
    assert searches, "no saved searches configured"

    failures = []
    for search in searches:
        args = (search["tier"], search["category"] or "-", search["keywords"],
                1, 20, 3)
        try:
            fmt % args
        except (TypeError, ValueError) as exc:
            failures.append((search["tier"], f"{type(exc).__name__}: {exc}"))

    assert not failures, (
        f"{len(failures)} of {len(searches)} searches cannot be logged: "
        f"{failures[:3]}")


def test_a_tier_5_search_exists_to_be_broken_by():
    """Guard against the test above passing because Tier 5 was deleted.

    If the watch-only track ever goes away the test above becomes vacuous, and
    it should be removed deliberately rather than quietly stop covering
    anything.
    """
    tagged = [s for s in config.SAVED_SEARCHES
              if s["tier"] == config.TIER5_TAG]
    assert tagged, ("no Tier 5 searches configured — the log-format test above "
                    "no longer covers the string-tier case")
    assert isinstance(config.TIER5_TAG, str), config.TIER5_TAG


def test_logging_really_does_swallow_a_bad_format():
    """Proves the premise of this whole module.

    If a format error propagated, the 09:00 task would have died loudly on day
    one and been fixed the same morning. It does not, which is why a test is
    the only thing that catches this class of bug.
    """
    records = []

    class Capture(logging.Handler):
        def emit(self, record):
            try:
                records.append(record.getMessage())
            except Exception:                            # noqa: BLE001
                # Exactly what logging.Handler.emit does: swallow and report.
                records.append("<format failed>")

    logger = logging.getLogger("test_swallow")
    logger.handlers = [Capture()]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    logger.info("T%d", "5-watch")      # the original bug, verbatim
    assert records == ["<format failed>"], records

    # And the fixed form works for both tier shapes.
    records.clear()
    logger.info("T%s", "5-watch")
    logger.info("T%s", 1)
    assert records == ["T5-watch", "T1"], records


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL  {fn.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001 - report, don't mask
            failed += 1
            print(f"ERROR {fn.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
