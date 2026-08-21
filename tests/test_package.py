"""Tests for the exporter that builds the folder Cowork is handed.

The interesting assertions are the refusals. An exporter that copies the right
files on a good day is easy; one that refuses to copy `.env` into a bundle
destined for an agent driving a logged-in browser is the point.

Everything below builds its own tiny source tree in a temp dir. Running against
the real repo would make the tests slow, non-hermetic, and dependent on a
15 MB register file.

Run:  python tests/test_package.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import data_contract  # noqa: E402
import package  # noqa: E402


def _tree(root: Path):
    """A miniature repo with one file of each classification."""
    files = {
        "tracker.py": "# system\n",
        "pipeline/shortlist.py": "# system\n",
        "CLAUDE.md": "# user, the constitution\n",
        "data/decision_log.jsonl": '{"seq": 1}\n',
        ".env": "ADZUNA_APP_KEY=super-secret-value\n",
        "__pycache__/x.pyc": "debris",
        "data/jobs_tracker_backup_pre_alias_20260815.xlsx": "old",
        "job-search-kit/README.md": "not runtime",
    }
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return root


# --- what travels ---------------------------------------------------------

def test_a_full_export_carries_both_layers():
    with tempfile.TemporaryDirectory() as tmp:
        src, dst = _tree(Path(tmp) / "src"), Path(tmp) / "out"
        code, m = package.build(src, dst)
        assert code == package.EXIT_OK, m
        assert (dst / "tracker.py").exists()
        assert (dst / "CLAUDE.md").exists()
        assert (dst / "data/decision_log.jsonl").exists()
        assert m["counts"] == {"system": 2, "user": 2}, m["counts"]


def test_system_only_leaves_personal_data_behind():
    """For sharing the tool without shipping his employment history."""
    with tempfile.TemporaryDirectory() as tmp:
        src, dst = _tree(Path(tmp) / "src"), Path(tmp) / "out"
        code, m = package.build(src, dst, include_user=False)
        assert code == package.EXIT_OK, m
        assert (dst / "tracker.py").exists()
        assert not (dst / "CLAUDE.md").exists()
        assert not (dst / "data/decision_log.jsonl").exists()


def test_the_runtime_directories_exist_on_a_fresh_export():
    with tempfile.TemporaryDirectory() as tmp:
        src, dst = _tree(Path(tmp) / "src"), Path(tmp) / "out"
        package.build(src, dst)
        for d in ("packages", "runs", "drafts", "logs"):
            assert (dst / d).is_dir(), d


def test_the_cowork_contract_ships_with_it():
    """A fresh agent must find the rules without being told where they are."""
    with tempfile.TemporaryDirectory() as tmp:
        src, dst = _tree(Path(tmp) / "src"), Path(tmp) / "out"
        package.build(src, dst)
        text = (dst / "COWORK.md").read_text(encoding="utf-8")
        assert "Never invent an answer to a form question" in text
        assert "sponsorship" in text.lower()
        assert "result.json" in text


# --- what must never travel -----------------------------------------------

def test_the_env_file_is_never_copied():
    """The single most important assertion in this file.

    The export is handed to an agent driving a browser already logged into a
    personal account. Shipping API keys alongside turns one compromise into two.
    """
    with tempfile.TemporaryDirectory() as tmp:
        src, dst = _tree(Path(tmp) / "src"), Path(tmp) / "out"
        package.build(src, dst)
        assert not (dst / ".env").exists()
        assert (dst / ".env.example").exists()


def test_no_secret_value_appears_anywhere_in_the_export():
    """Not just the file — the value. A copy under another name still leaks."""
    with tempfile.TemporaryDirectory() as tmp:
        src, dst = _tree(Path(tmp) / "src"), Path(tmp) / "out"
        (src / "notes.md").write_text(
            "reminder ADZUNA_APP_KEY=super-secret-value\n", encoding="utf-8")
        package.build(src, dst)
        for p in dst.rglob("*"):
            if p.is_file():
                body = p.read_text(encoding="utf-8", errors="ignore")
                assert "super-secret-value" not in body, f"leaked via {p.name}"


def test_debris_is_not_copied():
    with tempfile.TemporaryDirectory() as tmp:
        src, dst = _tree(Path(tmp) / "src"), Path(tmp) / "out"
        package.build(src, dst)
        assert not (dst / "__pycache__").exists()
        assert not (dst / "data/jobs_tracker_backup_pre_alias_20260815.xlsx").exists()
        assert not (dst / "job-search-kit").exists()


# --- refusals -------------------------------------------------------------

def test_it_refuses_a_non_empty_target_without_force():
    """The target is a folder someone may have put things in."""
    with tempfile.TemporaryDirectory() as tmp:
        src, dst = _tree(Path(tmp) / "src"), Path(tmp) / "out"
        dst.mkdir()
        (dst / "someones-notes.txt").write_text("mine", encoding="utf-8")
        code, report = package.build(src, dst)
        assert code == package.EXIT_REFUSED, (code, report)
        assert (dst / "someones-notes.txt").exists(), "it deleted something"


def test_force_writes_into_a_non_empty_target_without_deleting():
    with tempfile.TemporaryDirectory() as tmp:
        src, dst = _tree(Path(tmp) / "src"), Path(tmp) / "out"
        dst.mkdir()
        (dst / "someones-notes.txt").write_text("mine", encoding="utf-8")
        code, _ = package.build(src, dst, force=True)
        assert code == package.EXIT_OK
        assert (dst / "someones-notes.txt").read_text(encoding="utf-8") == "mine"
        assert (dst / "tracker.py").exists()


def test_it_refuses_to_export_into_itself():
    with tempfile.TemporaryDirectory() as tmp:
        src = _tree(Path(tmp) / "src")
        code, report = package.build(src, src / "nested")
        assert code == package.EXIT_CANNOT_BUILD, (code, report)


def test_dry_run_writes_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        src, dst = _tree(Path(tmp) / "src"), Path(tmp) / "out"
        code, m = package.build(src, dst, dry_run=True)
        assert code == package.EXIT_OK
        assert m["counts"]["system"] == 2
        assert not dst.exists(), "dry run created the target"


# --- the manifest ---------------------------------------------------------

def test_the_manifest_records_a_digest_per_file():
    with tempfile.TemporaryDirectory() as tmp:
        src, dst = _tree(Path(tmp) / "src"), Path(tmp) / "out"
        package.build(src, dst)
        manifest = json.loads((dst / "MANIFEST.json").read_text(encoding="utf-8"))
        rec = manifest["files"]["tracker.py"]
        assert len(rec["sha256"]) == 64
        assert rec["layer"] == "system"
        assert manifest["includes_user_layer"] is True


def test_verify_notices_a_changed_file():
    """Answers 'is the folder Cowork holds the one I built?'"""
    with tempfile.TemporaryDirectory() as tmp:
        src, dst = _tree(Path(tmp) / "src"), Path(tmp) / "out"
        package.build(src, dst)
        assert package.verify(dst)[0] == package.EXIT_OK

        (dst / "tracker.py").write_text("# tampered\n", encoding="utf-8")
        code, report = package.verify(dst)
        assert code == package.EXIT_REFUSED
        assert report["changed"] == ["tracker.py"], report


def test_verify_notices_a_missing_file():
    with tempfile.TemporaryDirectory() as tmp:
        src, dst = _tree(Path(tmp) / "src"), Path(tmp) / "out"
        package.build(src, dst)
        (dst / "CLAUDE.md").unlink()
        code, report = package.verify(dst)
        assert code == package.EXIT_REFUSED
        assert report["missing"] == ["CLAUDE.md"], report


def test_verify_cannot_verify_without_a_manifest():
    with tempfile.TemporaryDirectory() as tmp:
        code, report = package.verify(Path(tmp))
        assert code == package.EXIT_CANNOT_BUILD, (code, report)
        assert "error" in report


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
