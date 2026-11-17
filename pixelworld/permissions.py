"""Native Codex folder permissions for isolated benchmark execution."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import sysconfig


CASE_ONLY_POLICY = "case-only-v1"
WORKSPACE_WRITE_POLICY = "workspace-write"
PROFILE_NAME = "pixelworld_case"


def case_permissions(workspace: Path) -> dict:
    """Allow one case plus runtime libraries, without exposing the runtime's secrets."""
    workspace = workspace.resolve()
    entries = {":minimal": "read", str(workspace): "write"}
    # :minimal preserves Linux's system mounts/symlinks; explicit library roots
    # additionally cover the cloud-packaged Python and native executables.
    roots = {Path(sys.executable).resolve().parent}
    packaged_libraries = Path(sys.executable).resolve().parent.parent / 'lib'
    if packaged_libraries.is_dir():
        roots.add(packaged_libraries.resolve())
    for name in ("stdlib", "platstdlib", "purelib", "platlib"):
        value = sysconfig.get_path(name)
        if value:
            roots.add(Path(value).resolve())
    for name in ("ffmpeg", "bwrap"):
        value = shutil.which(name)
        if value:
            roots.add(Path(value).resolve().parent)
    for root in sorted(roots):
        if root != workspace and root not in workspace.parents:
            entries[str(root)] = "read"
    # :minimal supplies the sandbox's device mounts. Explicit /dev overrides
    # interfere with the pinned CLI's read-only image helper/protected subpaths.
    return {"name": PROFILE_NAME, "filesystem": entries, "network": {"enabled": False}}


def permission_args(profile: dict) -> list[str]:
    # Supply the whole inline table: dotted CLI keys keep quote characters in
    # literal paths on the pinned CLI, whereas TOML table keys parse correctly.
    table = "{" + ", ".join(json.dumps(path) + "=" + json.dumps(mode)
                              for path, mode in profile["filesystem"].items()) + "}"
    return ["-c", f'default_permissions="{profile["name"]}"',
            "-c", f'permissions.{profile["name"]}.filesystem={table}',
            "-c", f'permissions.{profile["name"]}.network.enabled=false']
