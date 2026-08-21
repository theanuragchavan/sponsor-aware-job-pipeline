"""Tests for the system/user split.

The contract only means something if it is complete. A file in neither layer is
invisible to the packager and goes missing from the export at the worst possible
moment — you find out when Cowork cannot find the sponsor register, halfway
through an application. A file in *both* layers is worse: an update would
overwrite data it was supposed to leave alone.

So the property worth pinning is not "the lists look sensible", it is **every
file on disk lands in exactly one layer, and the dangerous ones land in the
right one**. career-ops enforces the same thing with
`validate-system-paths-coverage.mjs`, for the same reason.

The load-bearing assertions below are the specific-file ones. `decision_log.jsonl`
classified as system rather than user is not a lint failure; it is a hash-chained
record of every decision ever made, silently replaced on the next update.

Run:  python tests/test_data_contract.py
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import data_contract  # noqa: E402


# --- completeness ---------------------------------------------------------

def test_every_file_is_classified():
    """Zero unclassified. A file in neither layer cannot be exported."""
    stray = [rel for rel, layer in data_contract.walk()
             if layer == "unclassified"]
    assert not stray, (
        f"{len(stray)} file(s) in neither layer — the packager cannot see "
        f"them: {stray[:20]}")


def test_no_path_is_claimed_by_two_layers():
    """An overlap means an update can overwrite data it must not touch."""
    assert data_contract.overlaps() == [], data_contract.overlaps()


def test_both_layers_are_non_empty():
    """A contract that classifies everything as one thing is not a contract."""
    layers = {layer for _, layer in data_contract.walk()}
    assert "system" in layers and "user" in layers, layers


# --- the files that must never move ---------------------------------------

def test_the_irreplaceable_files_are_user():
    """These cannot be regenerated from anything. Losing them loses the work.

    The decision log is hash-chained evidence, the aliases are human rulings a
    matcher is forbidden to author, and decisions.json holds standing CUT/FLAG
    verdicts with verification dates.
    """
    for rel in ("data/decision_log.jsonl",
                "data/sponsor_aliases.json",
                "data/ats_boards.csv",
                "pipeline/decisions.json",
                "jobs_tracker_beautified.xlsx",
                "CLAUDE.md"):
        assert data_contract.classify(rel) == "user", \
            f"{rel} must be user-layer, got {data_contract.classify(rel)!r}"


def test_the_tool_is_system():
    for rel in ("tracker.py", "sponsor_check.py", "reconcile_audit.py",
                "pipeline/shortlist.py", "pipeline/cv_gate.py",
                "web/api/actions.py", "ats/clients.py"):
        assert data_contract.classify(rel) == "system", \
            f"{rel} must be system-layer, got {data_contract.classify(rel)!r}"


def test_every_personal_path_that_exists_is_gitignored():
    """The check that was missing on 2026-08-21, and it cost a public push.

    `data/screening.yml` and `packages/` were classified user-layer correctly,
    every test passed, and both went to a public GitHub repo carrying work
    authorisation, a disclosure field, a CV and a cover-letter draft.

    Nothing was wrong with the contract. The contract answers "does this travel
    into an export"; `.gitignore` answers "does this go to a remote". Two
    mechanisms, two different questions, and adding a new personal-data path
    updated only one of them. `data/` is not blanket-ignored -- it lists
    specific files -- so a new one lands tracked by default.

    This makes the two check each other. A user-layer path that git would
    track is now a red test rather than a discovery after the fact.
    """
    import subprocess

    root = Path(data_contract.ROOT)
    if not (root / ".git").exists():
        return                                  # not a checkout; nothing to check

    existing = [spec for spec in data_contract.PERSONAL_PATHS
                if (root / spec.rstrip("/")).exists()]
    assert existing, "no personal paths on disk -- the test proves nothing"

    tracked = subprocess.run(  # nosec B603,B607 - fixed argv, no shell
        ["git", "ls-files", "--"] + [s.rstrip("/") for s in existing],
        capture_output=True, text=True, cwd=root).stdout.split()

    assert not tracked, (
        f"{len(tracked)} personal file(s) are tracked by git and would go to "
        f"the remote: {tracked[:10]}")


def test_personal_paths_are_a_subset_of_user_paths():
    """Personal implies user-layer. Something an update may overwrite cannot
    also be something too sensitive to publish."""
    stray = [p for p in data_contract.PERSONAL_PATHS
             if p not in data_contract.USER_PATHS]
    assert not stray, f"personal but not user-layer: {stray}"


def test_secrets_are_never_tracked():
    """Stronger than the layer check: git must not know about them at all."""
    import subprocess

    root = Path(data_contract.ROOT)
    if not (root / ".git").exists():
        return
    tracked = subprocess.run(  # nosec B603,B607
        ["git", "ls-files", "--"] + list(data_contract.SECRET_PATHS),
        capture_output=True, text=True, cwd=root).stdout.split()
    assert not tracked, f"secrets tracked by git: {tracked}"


def test_secrets_are_in_neither_layer():
    """.env is never exported.

    The portable folder is handed to an agent driving a browser logged into a
    personal account. Shipping API keys in the same bundle turns one compromise
    into two, so they are not in the tool and not in the data — they are
    injected at runtime.
    """
    assert data_contract.classify(".env") == "secret"
    for spec in data_contract.SECRET_PATHS:
        assert spec not in data_contract.SYSTEM_PATHS, spec
        assert spec not in data_contract.USER_PATHS, spec


def test_the_frozen_csv_is_user_not_system():
    """`jobs_tracker.csv` froze on 2026-07-01 and is not the store.

    It is still user data — an update must not delete it — but nothing reads it
    for current rows. Classifying it as system would let an updater bin it.
    """
    assert data_contract.classify("jobs_tracker.csv") == "user"


# --- exclusion ------------------------------------------------------------

def test_debris_under_a_system_directory_is_excluded_not_system():
    """Order matters: excluded is checked before system.

    Without that, `web/api/__pycache__/actions.cpython-313.pyc` classifies as
    system and gets copied into the export.
    """
    assert data_contract.classify("web/api/__pycache__/x.cpython-313.pyc") == "excluded"
    assert data_contract.classify("pipeline/__pycache__/y.pyc") == "excluded"


def test_backups_and_lock_files_are_excluded():
    for rel in ("data/jobs_tracker_backup_pre_alias_20260815.xlsx",
                "jobs_tracker_beautified.LOCKED-20260815.xlsx",
                "~$jobs_tracker_beautified.xlsx",
                "bash.exe.stackdump"):
        assert data_contract.classify(rel) == "excluded", rel


def test_the_kit_is_excluded_because_the_installed_copy_is_authoritative():
    """`job-search-kit/` is the distributable source of `.claude/`, not runtime.

    Its `application-pack/SKILL.md` predates the CV gate and its
    `check_sponsor.py` uses the difflib fuzzy matching the main pipeline
    rejected on measured evidence. Exporting both would ship two contradictory
    answers to "is this company a sponsor".
    """
    assert data_contract.classify("job-search-kit/README.md") == "excluded"
    assert data_contract.classify(".claude/skills/job-triage/SKILL.md") == "system"


# --- the matcher itself ---------------------------------------------------

def test_a_directory_spec_matches_the_directory_and_its_contents():
    assert data_contract._matches("ats", "ats/")
    assert data_contract._matches("ats/clients.py", "ats/")
    assert data_contract._matches("ats/deep/nested.py", "ats/")


def test_a_directory_spec_does_not_match_a_sibling_with_the_same_prefix():
    """`ats/` must not swallow `ats_main.py`, which is a different file."""
    assert not data_contract._matches("ats_main.py", "ats/")
    assert not data_contract._matches("atsx/thing.py", "ats/")


def test_a_file_spec_is_exact():
    assert data_contract._matches("tracker.py", "tracker.py")
    assert not data_contract._matches("web/tracker.py", "tracker.py")


def test_walk_reports_nothing_for_an_empty_tree():
    with tempfile.TemporaryDirectory() as tmp:
        assert list(data_contract.walk(Path(tmp))) == []


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
