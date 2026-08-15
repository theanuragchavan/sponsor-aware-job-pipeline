"""Run every test module in this folder.

The suites use a hand-rolled runner rather than unittest discovery (each file
has its own __main__ block), so `unittest discover` reports "Ran 0 tests" — a
silent pass that makes an untested project look tested. This runs them for real
and fails if any one of them does.

Usage:  python tests/run_all.py
"""
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent

def main() -> int:
    modules = sorted(p for p in HERE.glob("test_*.py"))
    if not modules:
        print("no test modules found")
        return 1

    results, failed = [], 0
    for path in modules:
        proc = subprocess.run([sys.executable, str(path)],
                              capture_output=True, text=True)
        tail = [ln for ln in proc.stdout.splitlines() if "passed" in ln]
        summary = tail[-1].strip() if tail else "no summary"
        ok = proc.returncode == 0
        failed += 0 if ok else 1
        results.append((ok, path.name, summary))
        if not ok:
            for line in proc.stdout.splitlines():
                if line.startswith(("FAIL", "ERROR")):
                    print(f"  {path.name}: {line}")
            if proc.stderr.strip():
                print(f"  {path.name}: {proc.stderr.strip()[:400]}")

    print()
    for ok, name, summary in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name:<34} {summary}")
    print(f"\n{len(results) - failed}/{len(results)} modules green")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
