"""Deploy Resolve to a Hugging Face Space.

    HF_TOKEN=hf_xxx python deploy/huggingface/deploy.py --space anurag/resolve

Why a script rather than a `git push`: the Space needs a README.md carrying YAML
frontmatter (`sdk: docker`, `app_port`), and the repository README is the
portfolio piece. Putting Space config at the top of it would leave a block of
raw YAML above the first line on GitHub. The Space is its own git repository, so
the two READMEs can legitimately differ — this script assembles a tree with the
Space README swapped in and pushes that, leaving GitHub untouched.

It pushes the working tree, not a GitHub clone, so what deploys is what you
tested locally. Files git ignores are excluded, which is what keeps the real
tracker, the alias overlay, the decision log and .env off a public Space.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
HF_README = Path(__file__).resolve().parent / "README.md"

# Anything matching these must never reach a public Space, whatever .gitignore
# happens to say on the day. Belt and braces: git ls-files already excludes
# them, and this refuses to proceed if one slips through.
FORBIDDEN = (
    "jobs_tracker_beautified.xlsx",
    "jobs_tracker.csv",
    "data/sponsor_aliases.json",
    "data/decision_log.jsonl",
    "data/sponsor_rejections.json",
    "data/sponsors_register.csv",
    "pipeline/profile.md",
    "pipeline/decisions.json",
    ".env",
    "run_tracker.bat",
    "CLAUDE.md",
)


def run(cmd: list[str], cwd: Path | None = None) -> str:
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit(f"$ {' '.join(cmd)}\n{result.stdout}{result.stderr}")
    return result.stdout


def tracked_files() -> list[str]:
    """Everything git tracks — i.e. everything .gitignore did not exclude."""
    return [line for line in run(["git", "ls-files"], ROOT).splitlines() if line]


def check(paths: list[str]) -> None:
    leaked = [p for p in paths
              if any(p == f or p.endswith("/" + f) for f in FORBIDDEN)]
    if leaked:
        sys.exit("REFUSING TO DEPLOY — private files are tracked:\n  "
                 + "\n  ".join(leaked))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--space", required=True,
                    help="owner/name, e.g. theanuragchavan/resolve")
    ap.add_argument("--dry-run", action="store_true",
                    help="assemble and report, push nothing")
    args = ap.parse_args()

    token = os.getenv("HF_TOKEN")
    if not token and not args.dry_run:
        sys.exit("HF_TOKEN is not set. Create one with WRITE scope at "
                 "https://huggingface.co/settings/tokens")

    files = tracked_files()
    check(files)

    staging = Path(tempfile.mkdtemp(prefix="hf-space-"))
    for rel in files:
        src, dst = ROOT / rel, staging / rel
        if not src.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    # The one substitution: Space config replaces the portfolio README.
    shutil.copy2(HF_README, staging / "README.md")

    total = sum(f.stat().st_size for f in staging.rglob("*") if f.is_file())
    print(f"assembled {len(files)} files ({total / 1_000_000:.1f} MB) in {staging}")
    print(f"  README.md  <- deploy/huggingface/README.md (Space config)")
    for f in FORBIDDEN[:4]:
        print(f"  excluded   -- {f}")
    print(f"  excluded   -- ... and {len(FORBIDDEN) - 4} more")

    if args.dry_run:
        print("\ndry run: nothing pushed")
        return 0

    owner, _, name = args.space.partition("/")
    if not owner or not name:
        sys.exit("--space must be owner/name")

    try:
        from huggingface_hub import HfApi
    except ImportError:
        sys.exit("pip install huggingface_hub")

    api = HfApi(token=token)
    api.create_repo(repo_id=args.space, repo_type="space", space_sdk="docker",
                    exist_ok=True, private=False)
    print(f"\nspace ready: https://huggingface.co/spaces/{args.space}")

    api.upload_folder(
        folder_path=str(staging), repo_id=args.space, repo_type="space",
        commit_message="Deploy Resolve (synthetic demo data)")

    print(f"\ndeployed: https://huggingface.co/spaces/{args.space}")
    print("first build takes a few minutes — npm ci, vite build, pip install")
    shutil.rmtree(staging, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
