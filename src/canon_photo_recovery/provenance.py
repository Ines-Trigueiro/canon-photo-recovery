"""Provenance sidecars: every output file gets `<name>.provenance.json`."""

import json
import pathlib
import subprocess

REPO = pathlib.Path(__file__).resolve().parents[2]


def git_commit() -> str | None:
    """HEAD of this repo, suffixed `-dirty` when tracked files have uncommitted changes."""
    try:
        head = subprocess.run(
            ["git", "-C", str(REPO), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", str(REPO), "status", "--porcelain", "--untracked-files=no"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    return f"{head}-dirty" if dirty else head


def write_sidecar(output: pathlib.Path, record: dict) -> None:
    sidecar = output.with_name(output.name + ".provenance.json")
    sidecar.write_text(json.dumps({"output": output.name, **record}, indent=2) + "\n")
