#!/usr/bin/env python3
"""Rebuild and publish the live analysis progress page when its data changes."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import time

from huggingface_hub import HfApi, CommitOperationAdd
from tools.build_analysis_dashboard import build


def digest(files: list[Path]) -> str:
    value = hashlib.sha256()
    for path in files:
        value.update(path.read_bytes())
    return value.hexdigest()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--web-root", type=Path, required=True)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--interval", type=float, default=60)
    parser.add_argument("--bundle", type=Path, help="Analysis run bundle; defaults to the web-root grandparent")
    args = parser.parse_args()
    web = args.web_root.resolve()
    files = [web / "analyses.html", web / "analysis_progress.json"]
    api = HfApi(token=args.token_file.read_text().strip())
    last = None
    while True:
        try:
            result = build(web,(args.bundle or web.parent.parent).resolve())
            current = digest(files)
            if current != last:
                operations = [CommitOperationAdd(path_in_repo=path.name, path_or_fileobj=str(path))
                              for path in files]
                commit = api.create_commit(repo_id="HuggingFriends/PixelWorld-dashboard", repo_type="space",
                                           operations=operations,
                                           commit_message="Refresh live analysis experiment progress")
                last = current
                print({"commit": commit.oid, "totals": result["totals"]}, flush=True)
        except Exception as error:
            print(type(error).__name__, str(error)[:300], flush=True)
        time.sleep(args.interval)
