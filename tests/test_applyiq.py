"""Tests for parsing Adzuna's ApplyIQ daily summary emails.

The parser exists to fix a denominator, so the way it fails is by getting a
count wrong, and there are exactly two ways to do that.

**Counting a queued job as an application.** Each email has two lists: what
ApplyIQ sent, and what it stopped on and wants input for. They look identical
in the body -- same heading, same fields -- and differ only by a `tab=` value
buried in a tracking URL. Merging them would inflate the applied count in the
flattering direction, which is the error the module was written to undo.

**Counting one job twice.** Every block's link appears more than once in the
mail (the heading, then a "complete all" or "view sent" repeat), so a naive
scan double-counts.

The fixture below is synthetic. The real bodies name companies that applied to
on his behalf and carry salary bands; this repo is public.

Run:  python tests/test_applyiq.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline import applyiq  # noqa: E402

BASE = "https://www.adzuna.co.uk/jobs/apply-iq/dashboard?utm_medium=email"

EMAIL = f"""| |
| Hi Anurag, Great news - we submitted 2 applications on your behalf today. |

| |
| Complete info for 1 application |

| ## Staff Platform Engineer[]({BASE}&tab=info_required&application_id 111) |

| |
| |

Info required

*Company:* Northwind Systems
*Location:* Manchester
*Salary:* 90000-130000 per year
| |
| Why this job? Some generated prose about the candidate. |

| |
| Complete all applications []({BASE}&tab=info_required&application_id 111) |

| |
| Applications sent |

| ## Data Engineer[]({BASE}&tab=applied&application_id 222) |

| |
| |

Applied

*Company:* Contoso Data Ltd
*Location:* London
| |
| Why this job? More generated prose. |

| ## Freelance Developer - asap start[]({BASE}&tab=applied&application_id 333) |

| |
| |

Applied

*Company:* Acme Freelancing
*Location:* Remote
*Salary:* Up to £450 per day
| |
| Why this job? More generated prose. |

| |
| View sent applications []({BASE}&tab=all) |
"""


def parsed():
    return applyiq.parse(EMAIL, "2026-08-04")


# --- the two counts must not blur together --------------------------------

def test_a_queued_job_is_not_an_application():
    """`info_required` means ApplyIQ stopped. Nothing was sent."""
    states = [e["state"] for e in parsed()]
    assert states.count("applied") == 2, states
    assert states.count("info_required") == 1, states


def test_only_the_sent_ones_reach_the_tracker():
    rows = applyiq.for_log_manual(parsed())
    assert len(rows) == 2
    assert "Northwind Systems" not in {r["company"] for r in rows}


def test_the_count_matches_what_the_email_itself_claims():
    """The body says "we submitted 2 applications"; the parser must agree."""
    sent = [e for e in parsed() if e["state"] == "applied"]
    assert len(sent) == 2


# --- a job appearing twice is still one job -------------------------------

def test_a_repeated_link_does_not_double_count():
    """The queued job is linked twice: its heading and "complete all"."""
    queued = [e for e in parsed() if e["state"] == "info_required"]
    assert len(queued) == 1, queued


def test_the_footer_view_all_link_is_not_a_job():
    assert all(e["company"] for e in parsed())


# --- fields ---------------------------------------------------------------

def test_it_reads_company_title_and_location():
    sent = [e for e in parsed() if e["state"] == "applied"]
    first = sent[0]
    assert first["company"] == "Contoso Data Ltd"
    assert first["title"] == "Data Engineer"
    assert first["location"] == "London"


def test_the_application_date_is_the_day_the_mail_arrived():
    """The body says "today" and never names a date."""
    assert all(e["date_applied"] == "2026-08-04" for e in parsed())


def test_salary_is_kept_when_given_and_absent_when_not():
    by_company = {e["company"]: e for e in parsed()}
    assert "salary" in by_company["Acme Freelancing"]
    assert "salary" not in by_company["Contoso Data Ltd"]


def test_the_id_survives_the_mangled_separator():
    """`application_id=222` arrives as `application_id 222`, or with `!`."""
    ids = {e["applyiq_id"] for e in parsed()}
    assert ids == {"111", "222", "333"}, ids
    mangled = applyiq.parse(EMAIL.replace("application_id 222",
                                          "application_id!222"), "2026-08-04")
    assert "222" in {e["applyiq_id"] for e in mangled}


# --- what a parsed row may claim ------------------------------------------

def test_the_channel_is_distinct_from_a_manual_adzuna_application():
    """"He found it on Adzuna and applied" and "Adzuna applied for him" are
    different facts, and the channel funnel divides by exactly this field."""
    assert applyiq.APPLIED_VIA != "adzuna"
    assert all(r["applied_via"] == applyiq.APPLIED_VIA
               for r in applyiq.for_log_manual(parsed()))


def test_every_row_says_the_answers_were_not_looked_up():
    """These did not pass condition 8, and the record has to say so."""
    for row in applyiq.for_log_manual(parsed()):
        assert "screening.yml" in row["notes"]


def test_it_never_asserts_a_sponsor_verdict():
    """Sponsorship is the register's call. Nothing parsed from an email may
    pre-empt it -- `log_manual.build_row` asks `match_company` for every row,
    and that only works if nothing upstream supplies the field."""
    for row in applyiq.for_log_manual(parsed()):
        assert "sponsor_match" not in row
        assert "sponsor_rating" not in row


def test_a_body_that_is_not_an_applyiq_summary_yields_nothing():
    assert applyiq.parse("Dear Anurag, thanks for applying.", "2026-08-04") == []


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
