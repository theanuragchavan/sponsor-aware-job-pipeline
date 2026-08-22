"""Regression tests for the HN "Who is hiring" harvester.

Every fixture below is a real posting copied from the August 2026 thread on
2026-08-21, so no test here touches the network. They exist because each one
broke the parser during the first dry run.

Run:  python tests/test_hn_hiring.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import hn_hiring as hn  # noqa: E402
import tracker  # noqa: E402

# Field order differs between all four of these. That is the whole problem.
SNOUT = ("Snout https://snout.com/ | Multiple Engineering + Product Roles | "
         "Remote US or Ontario, Canada | Full Time Join us at Snout.")
POSTHOG = ("PostHog | Full-Time | Technical CSMs, AI Research Engineer | "
           "REMOTE (all remote) | PostHog makes your product self-driving.")
OPENRENT = ("OpenRent | London, UK | Full-Time | ONSITE+PART REMOTE | "
            "https://www.openrent.co.uk What sucked the last time you rented "
            "a house or flat? Come and fix it with us.")
AMODO = ("Amodo Design (amododesign.com) | Sheffield/London, UK | "
         "Software Engineer | Full-time | Onsite or Hybrid")
HAYSTACK = ("Haystack | Value Engineer, Product Engineering Tech Lead, Growth "
            "Marketer, Account Director (EU Institutions) | Berlin, "
            "Barcelona, London (remote-first)")
NO_PIPES = ("SpendAi I ship procurement-grade AI that actually closes the "
            "loop on spend, and we are looking for people in London.")


def _posting(text, pid="1"):
    return {"id": pid, "text": text, "created_at": "2026-08-03T12:00:00.000Z"}


# --- company ---------------------------------------------------------------

def test_company_is_the_first_field():
    assert hn.parse_company(POSTHOG) == "PostHog"
    assert hn.parse_company(HAYSTACK) == "Haystack"


def test_a_trailing_url_is_stripped_from_the_company():
    assert hn.parse_company(SNOUT) == "Snout"


def test_a_parenthesised_domain_is_stripped_from_the_company():
    assert hn.parse_company(AMODO) == "Amodo Design"


def test_a_short_pipeless_posting_is_still_cut():
    """The cut was first keyed on length, so a 48-character sentence slipped
    through untouched and put prose in the company column."""
    text = "SpendAi — I ship procurement-grade AI in London."
    assert hn.parse_company(text) == "SpendAi"


def test_a_posting_with_no_pipes_does_not_put_prose_in_the_company():
    """Without pipes the whole comment is field one. Writing that into the
    company column poisons sponsor matching, which reads that column."""
    company = hn.parse_company(NO_PIPES)
    assert len(company) <= 60, company
    assert "procurement-grade" not in company


# --- title -----------------------------------------------------------------

def test_title_is_found_regardless_of_field_position():
    """Snout puts the role second, PostHog puts it third."""
    assert "Engineering" in hn.parse_title(SNOUT)
    assert "Research Engineer" in hn.parse_title(POSTHOG)


def test_a_url_bearing_field_is_never_used_as_a_title():
    """OpenRent's last field is a link plus body copy. It matched the role
    hint and became the title on the first run."""
    title = hn.parse_title(OPENRENT)
    assert "http" not in title
    assert "What sucked" not in title


def test_a_long_multi_role_field_is_still_kept():
    """Capping the title at 90 chars silently hid Haystack's seniority from
    the filter, so it went from correctly dropped to wrongly included."""
    title = hn.parse_title(HAYSTACK)
    assert "Tech Lead" in title
    assert tracker.unsuitable_reason(title), "seniority filter must still see it"


def test_a_missing_role_gives_a_blank_title_not_a_guess():
    """A wrong title is worse than none: it feeds the seniority filter."""
    assert hn.parse_title("Flywheel Motion | REMOTE (worldwide) | Contract") == ""


# --- UK detection ----------------------------------------------------------

def test_uk_locations_are_detected():
    assert hn.looks_uk(OPENRENT)
    assert hn.looks_uk(AMODO)


def test_a_us_city_of_the_same_name_is_not_uk():
    """Cambridge MA and Manchester NH are not the ones we mean."""
    assert not hn.looks_uk("Acme | Senior Engineer | Cambridge, MA | ONSITE")
    assert not hn.looks_uk("Acme | Engineer | Manchester, NH | ONSITE")


def test_ukraine_does_not_match_uk():
    """An unanchored "UK" substring matches Ukraine."""
    assert not hn.looks_uk("Acme | Engineer | Kyiv, Ukraine | REMOTE")


def test_a_us_only_posting_is_not_uk():
    assert not hn.looks_uk(SNOUT)


# --- row contract ----------------------------------------------------------

def test_row_has_exactly_the_tracker_fieldnames():
    row = hn.to_row(_posting(AMODO))
    assert set(row) == set(tracker.FIELDNAMES), set(row) ^ set(tracker.FIELDNAMES)


def test_id_is_namespaced_so_source_of_can_route_it():
    row = hn.to_row(_posting(AMODO, pid="49156683"))
    assert row["id"] == "hn:49156683"
    assert tracker.source_of(row["id"]) == "hn"


def test_redirect_url_is_the_permalink_not_a_scraped_apply_link():
    """The links inside postings are inconsistent and often dead; the HN
    permalink always resolves and carries the full text."""
    row = hn.to_row(_posting(SNOUT, pid="123"))
    assert row["redirect_url"] == "https://news.ycombinator.com/item?id=123"
    assert "snout.com" not in row["redirect_url"]


def test_a_visa_mention_is_flagged_in_notes():
    text = "Flow Traders | Research Engineer | London | Full Time | Visa"
    assert hn.to_row(_posting(text))["notes"] == "visa mentioned"
    assert hn.to_row(_posting(AMODO))["notes"] == ""


def test_html_entities_and_tags_are_stripped():
    raw = "<p>Acme &amp; Co | Engineer | London</p>"
    assert hn.clean_text(raw) == "Acme & Co | Engineer | London"


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS ", name)
            except AssertionError as exc:
                fails += 1
                print("FAIL ", name, "->", exc)
            except Exception as exc:  # noqa: BLE001
                fails += 1
                print("ERROR", name, "->", type(exc).__name__, exc)
    total = sum(1 for n in globals() if n.startswith("test_"))
    print(f"\n{total - fails}/{total} passed")
    sys.exit(1 if fails else 0)
