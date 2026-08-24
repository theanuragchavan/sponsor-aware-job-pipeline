"""Build the portable folder that gets handed to Cowork.

Reads `data_contract.py` rather than a copy list someone maintains by hand.
That is the whole reason the contract exists: a packager that knows what to
copy because a human remembered is a packager that ships a stale register or
forgets the decision log the one time it matters.

What travels, and why
---------------------
Everything the system needs to run standalone: the tool, the tracker, the
sponsor register, the decision log, the CVs, the master history. Anurag chose
"everything it needs to run standalone" over the leaner options deliberately —
Cowork has to answer unexpected form questions from ground truth rather than
guessing, and a guess on a job application is the failure this whole pipeline
exists to prevent.

What never travels
------------------
**Secrets.** `.env` is in neither layer of the contract, and this refuses to
copy it even if someone adds it. The folder is handed to an agent driving a
browser already logged into a personal Gmail; putting API keys in the same
bundle turns one compromise into two. `.env.example` ships instead, and the
real values are injected at runtime.

**Debris.** Backups, `__pycache__`, `node_modules`, the frozen 2026-07-01 CSV's
backups, `bash.exe.stackdump`, and `job-search-kit/` — whose bundled
`check_sponsor.py` uses the difflib fuzzy matching the main pipeline rejected on
measured evidence. Exporting both would ship two contradictory answers to "is
this company a sponsor".

One thing that does travel, knowingly
-------------------------------------
`jobs_tracker_beautified.xlsx` carries the Adzuna **APP_ID** inside 3,479
`redirect_url` values, as `utm_source=`. That is the public half of the pair —
Adzuna stamps it into every redirect it serves, so it is already visible to
anyone who has ever clicked one — and the 32-character APP_KEY is not in the
export at all. Checked, not assumed: `build()` was verified against the real
`.env` values and no secret string appears in any exported text file.

Recorded here because the June audit found the same thing and it read as
alarming until the two halves were told apart. It is not a reason to strip the
URLs: they are how you get back to the posting, and rewriting 3,479 of them
would break the one column a human actually clicks.

The manifest
------------
Every export writes `MANIFEST.json`: a sha256 per file, the layer it came from,
the source revision, and the timestamp. Two uses. It tells you whether the
folder Cowork is holding is the one you built, and it makes a later re-export
diffable — so "did the register change?" is answerable without opening a 10 MB
CSV.

This never deletes. A target that already exists is refused unless --force,
because the target is a folder someone may have put things in.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import data_contract

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_CANNOT_BUILD = 2

def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _revision(root: Path) -> str:
    """The source commit, so an export can be traced back to a tree."""
    try:
        out = subprocess.run(  # nosec B603,B607 - fixed argv, no shell
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=15)
        rev = out.stdout.strip()
        dirty = subprocess.run(  # nosec B603,B607
            ["git", "-C", str(root), "status", "--porcelain"],
            capture_output=True, text=True, timeout=15).stdout.strip()
        return f"{rev}{'-dirty' if dirty else ''}" if rev else "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def build(source: Path, target: Path, *, include_user: bool = True,
          force: bool = False, dry_run: bool = False) -> tuple[int, dict]:
    """Copy the contract's classified files into `target`. Never deletes."""
    source = Path(source).resolve()
    target = Path(target).resolve()

    if target == source or source in target.parents:
        return EXIT_CANNOT_BUILD, {
            "error": "target is the source, or inside it — that would copy the "
                     "export into itself"}
    if target.exists() and any(target.iterdir()) and not force:
        return EXIT_REFUSED, {
            "error": f"{target} exists and is not empty. Use --force if you "
                     f"mean to write into it (nothing is deleted either way)."}

    wanted = {"system"} | ({"user"} if include_user else set())
    files: list[tuple[str, str]] = [
        (rel, layer) for rel, layer in data_contract.walk(source)
        if layer in wanted]

    if not files:
        return EXIT_CANNOT_BUILD, {"error": "the contract matched no files"}

    manifest = {
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": str(source),
        "source_revision": _revision(source),
        "includes_user_layer": include_user,
        "files": {},
    }
    counts = {"system": 0, "user": 0}
    total_bytes = 0

    for rel, layer in sorted(files):
        src = source / rel
        try:
            digest = sha256_of(src)
            size = src.stat().st_size
        except OSError as exc:
            return EXIT_CANNOT_BUILD, {"error": f"cannot read {rel}: {exc}"}

        if not dry_run:
            dst = target / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

        manifest["files"][rel] = {"sha256": digest, "bytes": size,
                                  "layer": layer}
        counts[layer] += 1
        total_bytes += size

    manifest["counts"] = counts
    manifest["total_bytes"] = total_bytes

    if not dry_run:
        # Directories the system writes into but which are empty on a fresh
        # export. Creating them here means the first run does not have to.
        for d in ("packages", "runs", "drafts", "logs"):
            (target / d).mkdir(parents=True, exist_ok=True)

        # COWORK.md is NOT written here. It is a real file in SYSTEM_PATHS and
        # the ordinary copy carries it. This used to overwrite it with a
        # summary held in this module, which meant the agent read a contract
        # nobody was maintaining: on 2026-08-24 the repo's copy gained the
        # field-shape reporting the gate now depends on and the rule against
        # upgrading a student visa, and the exported copy had neither. Two
        # sources of truth, and the stale one won every time.
        (target / ".env.example").write_text(
            "# Injected at runtime. The real .env is never exported.\n"
            "ADZUNA_APP_ID=\nADZUNA_APP_KEY=\nFIRECRAWL_API_KEY_1=\n",
            encoding="utf-8")
        (target / "MANIFEST.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    return EXIT_OK, manifest


def verify(target: Path) -> tuple[int, dict]:
    """Does the folder still match the manifest it was built with?"""
    target = Path(target).resolve()
    mpath = target / "MANIFEST.json"
    if not mpath.exists():
        return EXIT_CANNOT_BUILD, {"error": f"no MANIFEST.json in {target}"}

    try:
        manifest = json.loads(mpath.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return EXIT_CANNOT_BUILD, {"error": f"manifest unreadable: {exc}"}

    missing, changed = [], []
    for rel, rec in manifest.get("files", {}).items():
        path = target / rel
        if not path.exists():
            missing.append(rel)
        elif sha256_of(path) != rec["sha256"]:
            changed.append(rel)

    return (EXIT_OK if not (missing or changed) else EXIT_REFUSED), {
        "files": len(manifest.get("files", {})),
        "missing": missing, "changed": changed,
        "built_at": manifest.get("built_at"),
        "source_revision": manifest.get("source_revision"),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Build the portable folder Cowork is handed.")
    ap.add_argument("target", nargs="?", default=r"D:\GetThatJob",
                    help="where to write it (default: D:\\GetThatJob)")
    ap.add_argument("--system-only", action="store_true",
                    help="ship the tool without any personal data")
    ap.add_argument("--force", action="store_true",
                    help="write into a non-empty target (never deletes)")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be copied, write nothing")
    ap.add_argument("--verify", action="store_true",
                    help="check an existing export against its manifest")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    root = Path(__file__).resolve().parent

    if args.verify:
        code, report = verify(Path(args.target))
        if args.json:
            print(json.dumps(report, indent=2))
        elif "error" in report:
            print(f"CANNOT VERIFY: {report['error']}")
        elif code == EXIT_OK:
            print(f"INTACT: {report['files']} files match the manifest "
                  f"(built {report['built_at']}, rev {report['source_revision']})")
        else:
            print(f"DRIFT: {len(report['missing'])} missing, "
                  f"{len(report['changed'])} changed")
            for rel in (report["missing"] + report["changed"])[:20]:
                print("  " + rel)
        return code

    code, report = build(root, Path(args.target),
                         include_user=not args.system_only,
                         force=args.force, dry_run=args.dry_run)

    if args.json:
        print(json.dumps(report if "error" in report
                         else {k: v for k, v in report.items() if k != "files"},
                         indent=2))
        return code

    if "error" in report:
        print(f"REFUSED: {report['error']}")
        return code

    mb = report["total_bytes"] / (1024 * 1024)
    verb = "would copy" if args.dry_run else "copied"
    print(f"{verb} {report['counts']['system']} system + "
          f"{report['counts']['user']} user files ({mb:.1f} MB)")
    print(f"source revision: {report['source_revision']}")
    if not args.dry_run:
        print(f"wrote {args.target}")
        print("  + .env.example, MANIFEST.json")
        print("  + packages/ runs/ drafts/ logs/")
        print("\nSecrets were NOT copied. Inject .env at runtime.")
    return code


if __name__ == "__main__":
    sys.exit(main())
