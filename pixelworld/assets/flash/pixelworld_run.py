#!/usr/bin/env python3
"""Run generate.py with a bounded, compact log, then validate every output.

One command replaces a run followed by hand-written shape/identity checks.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pixelworld_check

ROOT = Path(__file__).resolve().parent
LOG = ROOT / ".tmp" / "generate_run.log"
TAIL_LINES = 40
TIMEOUT_SECONDS = 480


def main() -> int:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    try:
        with LOG.open("wb") as handle:
            process = subprocess.run(
                [sys.executable, "generate.py"], cwd=ROOT,
                stdout=handle, stderr=subprocess.STDOUT, timeout=TIMEOUT_SECONDS)
        status = process.returncode
    except subprocess.TimeoutExpired:
        status = "timeout"
    lines = LOG.read_text(errors="replace").splitlines()
    print(f"generate.py status={status} log_lines={len(lines)} log=.tmp/generate_run.log")
    for line in lines[-TAIL_LINES:]:
        print(line[:300])
    if status != 0:
        print("generate.py did not finish successfully; fix it before validating.")
        return 1
    return pixelworld_check.main()


if __name__ == "__main__":
    sys.exit(main())
