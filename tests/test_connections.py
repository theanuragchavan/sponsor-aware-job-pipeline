"""Tests for the LinkedIn-connections cross-reference.

Two properties carry the weight.

**The company join is the whole feature, and it is fragile.** A LinkedIn
profile says "Palantir Technologies UK Ltd" where the tracker says "Palantir".
The first version's suffix list omitted "technologies", so the single most
valuable match in the first real run — his #1 target, holding the one contact
whose title is literally Forward Deployed Engineer — silently failed to join.
Nothing errored. The company just was not in the output.

**Nothing here touches LinkedIn.** The rule is no scraping, no automation, no
dummy accounts. This reads a file LinkedIn hands him through the export button
in his own settings, and a test asserts the module imports nothing that could
make a request.

Run:  python tests/test_connections.py
"""
import io
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import connections  # noqa: E402

HEADER = "First Name,Last Name,URL,Email Address,Company,Position,Connected On"


def _export(*rows: str, preamble: bool = True) -> Path:
    body = []
    if preamble:
        # LinkedIn prepends this to recent exports.
        body += ["Notes:", '"Some fields may be empty."', ""]
    body.append(HEADER)
    body += list(rows)
    d = tempfile.mkdtemp()
    p = Path(d) / "Connections.csv"
    p.write_text("\n".join(body), encoding="utf-8")
    return p


def _job(company, **over):
    row = {"id": "1", "company": company, "title": "Software Engineer",
           "sponsor_match": "yes", "status": "new", "source": "lever",
           "posted_date": "2026-08-01", "date_first_seen": "2026-08-01",
           "location": "London", "tier": "1"}
    row.update(over)
    return row


# --- reading the export ----------------------------------------------------

def test_the_notes_preamble_does_not_become_the_header():
    """Handing LinkedIn's preamble to DictReader makes every row unreadable."""
    p = _export("Ada,Lovelace,https://x,,Monzo,Engineer,01 Jan 2025")
    rows = connections.read_export(p)
    assert len(rows) == 1 and rows[0]["name"] == "Ada Lovelace", rows


def test_an_export_without_the_preamble_still_reads():
    p = _export("Ada,Lovelace,https://x,,Monzo,Engineer,01 Jan 2025",
                preamble=False)
    assert len(connections.read_export(p)) == 1


def test_a_file_that_is_not_an_export_is_refused_not_read_as_empty():
    """Zero connections and "wrong file" must not look the same."""
    d = tempfile.mkdtemp()
    p = Path(d) / "x.csv"
    p.write_text("a,b,c\n1,2,3\n", encoding="utf-8")
    try:
        connections.read_export(p)
    except ValueError as exc:
        assert "Connections export" in str(exc), exc
    else:
        raise AssertionError("a non-export must raise, not return []")


def test_a_renamed_column_is_reported_rather_than_silently_dropping_everyone():
    d = tempfile.mkdtemp()
    p = Path(d) / "c.csv"
    p.write_text("First Name,Last Name,Position\nAda,Lovelace,Engineer\n",
                 encoding="utf-8")
    try:
        connections.read_export(p)
    except ValueError as exc:
        assert "Company" in str(exc), exc
    else:
        raise AssertionError("a missing column must raise")


def test_a_row_with_no_company_or_no_name_is_skipped():
    p = _export("Ada,Lovelace,https://x,,,Engineer,01 Jan 2025",
                ",,https://y,,SomeCorp,Analyst,01 Jan 2025",
                "Grace,Hopper,https://z,,Monzo,Engineer,01 Jan 2025")
    rows = connections.read_export(p)
    assert [r["name"] for r in rows] == ["Grace Hopper"], rows


def test_a_missing_email_stays_missing():
    """LinkedIn includes an address only where that person allowed it. Absent
    is the default, and absent means there is no route."""
    p = _export("Ada,Lovelace,https://x,,Monzo,Engineer,01 Jan 2025")
    assert connections.read_export(p)[0]["email"] == ""


# --- the join --------------------------------------------------------------

def test_a_legal_suffix_does_not_break_the_match():
    """"Palantir Technologies UK Ltd" and "Palantir" are one company.

    The omission of "technologies" cost the most valuable match in the first
    real run.
    """
    assert connections._norm("Palantir Technologies UK Ltd") == \
        connections._norm("Palantir")
    assert connections._norm("Graphcore Ltd") == connections._norm("Graphcore")


def test_different_companies_still_do_not_match():
    assert connections._norm("Monzo Bank") != connections._norm("Starling Bank")


def test_a_connection_at_a_hiring_company_surfaces():
    people = connections.read_export(
        _export("Tom,Beckett,https://x,,Palantir Technologies UK Ltd,"
                "Forward Deployed Engineer,02 Nov 2024"))
    found = connections.overlap(people, [_job("Palantir")])
    assert len(found) == 1
    assert found[0]["people"][0]["name"] == "Tom Beckett"


def test_a_connection_at_a_company_with_no_open_role_is_not_a_lead():
    people = connections.read_export(
        _export("Jo,Adeyemi,https://x,,Nowhere Ltd,Founder,01 Jan 2020"))
    assert connections.overlap(people, [_job("Palantir")]) == []


def test_a_job_the_shortlist_would_drop_is_not_a_lead():
    """A connection at a company whose only opening he would never apply to is
    not a lead, and padding the list stops it being read."""
    people = connections.read_export(
        _export("Tom,Beckett,https://x,,Palantir,Engineer,02 Nov 2024"))
    dropped = _job("Palantir", title="Head of Engineering")  # seniority
    assert connections.overlap(people, [dropped]) == []


def test_an_agency_posting_is_not_a_warm_intro():
    """An agency's listing hides the real employer, so knowing someone at the
    agency does not help."""
    people = connections.read_export(
        _export("Pat,Smith,https://x,,Robert Walters,Recruiter,01 Jan 2025"))
    assert connections.overlap(people, [_job("Robert Walters")]) == []


def test_companies_are_ranked_by_how_much_is_open_there():
    people = connections.read_export(
        _export("A,One,https://a,,Palantir,Engineer,01 Jan 2025",
                "B,Two,https://b,,Graphcore,Engineer,01 Jan 2025"))
    jobs = [_job("Graphcore", id="1"), _job("Graphcore", id="2"),
            _job("Palantir", id="3")]
    found = connections.overlap(people, jobs)
    assert found[0]["company"] == "Graphcore", [h["company"] for h in found]


# --- the boundary ----------------------------------------------------------

def test_the_module_cannot_reach_linkedin():
    """No scraping, no automation, no dummy accounts. This reads a file off
    disk; it must not be able to do anything else."""
    import inspect
    src = inspect.getsource(connections)
    for bad in ("requests", "urllib", "httpx", "webbrowser", "selenium",
                "playwright", "socket"):
        assert bad not in src, f"{bad} has no business in this module"


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
