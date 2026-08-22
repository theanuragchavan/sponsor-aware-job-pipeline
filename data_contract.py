"""Which files are the tool, which files are yours, and which are neither.

Borrowed wholesale from career-ops' DATA_CONTRACT, including the shape of the
decision: **a manifest over a flat root, not a `system/` and `user/` folder
move.** That distinction is the whole point and it is easy to get wrong.

Moving 200 files into two new directories would break `run_tracker.bat`, the
09:00 Task Scheduler action, `tools/Resolve.vbs`, `render.yaml`, the Dockerfile's
COPY list, every absolute path in `CLAUDE.md`, and the muscle memory of every
command already learned. career-ops keeps ~70 scripts at its root deliberately
for exactly that reason -- path stability is a feature, not an accident -- and
the vault says the same thing in plainer words: *"Don't reorganize the structure
because a new system looks appealing; the agent works because paths are stable."*

So the layers are declared here and the tree stays where it is.

THE RULE
--------
**If a path is in the USER layer, no update or packaging process may overwrite
it.** The tool can be replaced wholesale; the data underneath it cannot.

Three consumers:

1. `tests/test_data_contract.py` -- every file on disk lands in exactly one
   layer. A file in neither is invisible to the packager and will be missing
   from the export at the worst possible moment; a file in both means an
   update can eat data.
2. `package.py` -- builds the portable folder Cowork is handed, by reading
   these lists rather than by someone remembering what to copy.
3. Any future updater -- checks out SYSTEM paths and never touches USER ones.

A trailing slash means "this directory and everything under it".
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent

# --- the tool ---------------------------------------------------------------
# Replaceable. Carries no personal data, by definition: if something here would
# embarrass you in a screenshot, it is in the wrong list.
SYSTEM_PATHS: tuple[str, ...] = (
    # ingest
    "adzuna_client.py",
    "reed_client.py",
    "hn_hiring.py",
    "ats_main.py",
    "main.py",
    "ats/",
    # store + gates
    "tracker.py",
    "sponsor_check.py",
    "ats_prober.py",
    "reconcile_audit.py",
    "config.py",
    "pipeline/shortlist.py",
    "pipeline/explain.py",
    "pipeline/ghost.py",
    "pipeline/cv_gate.py",
    "pipeline/verify_claims.py",
    "pipeline/outcomes.py",
    "pipeline/liveness.py",
    "scripts/",
    "package/",
    "pipeline/sponsor_review.py",
    "pipeline/__init__.py",
    # the prompt layer
    ".claude/",
    # the app
    "web/api/",
    "web/demo/generate_demo.py",
    "web/demo/data/",
    "web/ui/src/",
    "web/ui/public/",
    "web/ui/icon-src/",
    "web/ui/package.json",
    "web/ui/package-lock.json",
    "web/ui/vite.config.ts",
    "web/ui/tsconfig.json",
    "web/ui/tsconfig.node.json",
    "web/ui/tsconfig.app.json",
    "web/ui/index.html",
    "web/ui/.oxlintrc.json",
    "web/ui/.gitignore",
    "web/__init__.py",
    # ops
    "tools/",
    "tests/",
    "deploy/",
    "Dockerfile",
    "render.yaml",
    "requirements-web.txt",
    "run_tracker.bat.example",
    "README.md",
    # Research, not personal data: public API endpoints, measured status codes
    # and two verified false-positive traps. It sits in the system layer because
    # it describes the world rather than him -- and because this repo is public,
    # so "who wrote it" is the wrong test for whether it can ship. The right one
    # is whether it would embarrass in a screenshot, and a list of job-board
    # endpoints would not.
    "JOB_SOURCES.md",
    "data_contract.py",
    "export.py",
    ".gitignore",
    ".github/",
)

# --- yours ------------------------------------------------------------------
# Never overwritten. Some of it is expensive to rebuild (the register cache),
# some is impossible to rebuild (the decision log, the aliases, the rulings).
USER_PATHS: tuple[str, ...] = (
    # the constitution and the research behind it
    "CLAUDE.md",
    "adzuna_tracker_brief.md",
    "STATUS_adzuna_resume_2026-06-29.md",
    # the store, and the frozen snapshot that is not the store
    "jobs_tracker_beautified.xlsx",
    "jobs_tracker.csv",
    # decisions a machine must never author
    "pipeline/decisions.json",
    "pipeline/profile.md",
    "pipeline/sponsor_review.md",
    "pipeline/shortlists/",
    "pipeline/liveness_cache.json",
    "data/sponsor_aliases.json",
    "data/decision_log.jsonl",
    "data/ats_boards.csv",
    # Which employers were already probed. A cache, but a slow one to
    # rebuild -- 150 companies is ~10 minutes of throttled requests against
    # other people's servers, so it travels rather than being re-earned.
    "data/ats_probe_cache.json",
    "data/contacts.json",
    # His answers to form questions. Personal by definition -- salary
    # floor, visa status, location -- so it lives with the rest of the
    # personal data and is gitignored. It travels in a full export
    # because condition 8 of the autosubmit gate cannot work without it.
    "data/screening.yml",
    # caches and counters: regenerable, but not by the packager
    "data/sponsors_register.csv",
    "data/sponsors_register_meta.json",
    "data/call_counter.json",
    "data/ats_call_counter.json",
    "data/reed_call_counter.json",
    "data/ats_closed.json",
    # output
    "drafts/",
    "logs/",
    # Per-job artifacts. A record of what was prepared and what came
    # back, so an update must never regenerate over them.
    "packages/",
    "runs/",
    # machine-specific
    "run_tracker.bat",
)

#: The subset of USER_PATHS that must never reach a remote.
#:
#: User-layer and personal are two different properties and conflating them was
#: a modelling error that a test caught on 2026-08-21. `data/ats_boards.csv` is
#: hand-curated, so an update must not overwrite it -- that is what makes it
#: user-layer. It is also just a company-to-ATS-slug mapping, so tracking it in
#: a public repo costs nothing. Meanwhile `data/screening.yml` is both.
#:
#: The distinction matters because the first version of the guard asserted that
#: every user path is gitignored, which would have forced `ats_boards.csv` out
#: of git to satisfy a rule it never needed to obey.
PERSONAL_PATHS: tuple[str, ...] = (
    "CLAUDE.md",
    "jobs_tracker_beautified.xlsx",
    "jobs_tracker.csv",
    "data/screening.yml",
    "data/decision_log.jsonl",
    "data/sponsor_aliases.json",
    "data/contacts.json",
    "pipeline/decisions.json",
    "pipeline/profile.md",
    "pipeline/sponsor_review.md",
    "pipeline/shortlists/",
    "drafts/",
    "packages/",
    "runs/",
    "logs/",
    "adzuna_tracker_brief.md",
    "STATUS_adzuna_resume_2026-06-29.md",
)

# --- secrets ----------------------------------------------------------------
# In neither layer on purpose. Never copied anywhere, never exported. The
# portable folder is handed to an agent driving a logged-in browser; shipping
# API keys in the same bundle makes one compromise into two.
SECRET_PATHS: tuple[str, ...] = (
    ".env",
    ".env.local",
)

# --- neither ----------------------------------------------------------------
# Derived, vendored, or debris. Excluded from the export and from the coverage
# test, because a coverage test that has to enumerate every .pyc is a test
# nobody keeps green.
EXCLUDED_PATHS: tuple[str, ...] = (
    ".git/",
    "__pycache__/",
    ".pytest_cache/",
    "node_modules/",
    "web/ui/dist/",
    "web/ui/node_modules/",
    ".venv/",
    "job-search-kit/",          # the distributable source of .claude/; not runtime
    "compass_artifact_wf-90ce2437-6637-5247-a560-fab7af5810dc_text_markdown.md",
)

# Filename patterns that are debris wherever they appear.
EXCLUDED_GLOBS: tuple[str, ...] = (
    "*.pyc",
    "*.pyo",
    "bash.exe.stackdump",
    "jobs_tracker_backup_*.xlsx",
    "*.LOCKED-*.xlsx",
    "~$*",
)


def _matches(rel: str, spec: str) -> bool:
    """A spec ending in / matches a directory prefix; otherwise exact."""
    rel = rel.replace("\\", "/")
    if spec.endswith("/"):
        return rel == spec[:-1] or rel.startswith(spec)
    return rel == spec


def classify(rel_path: str) -> str:
    """'system' | 'user' | 'secret' | 'excluded' | 'unclassified'.

    Order matters: excluded first, so a `__pycache__` under a system directory
    is debris rather than part of the tool.
    """
    from fnmatch import fnmatch

    rel = str(rel_path).replace("\\", "/")
    name = rel.rsplit("/", 1)[-1]

    if any(fnmatch(name, g) for g in EXCLUDED_GLOBS):
        return "excluded"
    for spec in EXCLUDED_PATHS:
        if _matches(rel, spec):
            return "excluded"
    for spec in SECRET_PATHS:
        if _matches(rel, spec):
            return "secret"
    for spec in SYSTEM_PATHS:
        if _matches(rel, spec):
            return "system"
    for spec in USER_PATHS:
        if _matches(rel, spec):
            return "user"
    return "unclassified"


def walk(root: Path | None = None):
    """Yield (relative_path, layer) for every file under root."""
    root = Path(root or ROOT)
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        layer = classify(rel)
        if layer == "excluded":
            continue
        yield rel, layer


def overlaps() -> list[str]:
    """Specs claimed by more than one layer. Must always be empty."""
    seen: dict[str, str] = {}
    clashes: list[str] = []
    for layer, specs in (("system", SYSTEM_PATHS), ("user", USER_PATHS),
                         ("secret", SECRET_PATHS)):
        for spec in specs:
            if spec in seen:
                clashes.append(f"{spec} claimed by both {seen[spec]} and {layer}")
            seen[spec] = layer
    return clashes


if __name__ == "__main__":
    import collections

    counts: collections.Counter = collections.Counter()
    unclassified: list[str] = []
    for rel, layer in walk():
        counts[layer] += 1
        if layer == "unclassified":
            unclassified.append(rel)

    for layer in ("system", "user", "secret", "unclassified"):
        print(f"  {layer:<14} {counts[layer]}")
    if overlaps():
        print("\nOVERLAPS (must be none):")
        for o in overlaps():
            print("  " + o)
    if unclassified:
        print(f"\nUNCLASSIFIED ({len(unclassified)}) — each is invisible to the "
              f"packager:")
        for u in unclassified[:40]:
            print("  " + u)
