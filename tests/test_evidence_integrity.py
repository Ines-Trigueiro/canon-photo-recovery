"""Fails if any original evidence file was modified, removed, or became writable."""
import hashlib
import json
import os
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
ARCH = ROOT / "evidence" / "arch-copies"
MANIFEST = ROOT / "evidence" / "manifest" / "baseline-sha256.json"


def _sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@pytest.fixture(scope="module")
def baseline():
    if not MANIFEST.exists():
        pytest.skip(f"no baseline manifest at {MANIFEST}")
    return json.loads(MANIFEST.read_text())


def test_every_original_is_unchanged(baseline):
    drift = []
    for rec in baseline["files"]:
        p = ARCH / rec["path"]
        if not p.exists():
            drift.append(f"MISSING {rec['path']}")
        elif _sha256(p) != rec["sha256"]:
            drift.append(f"ALTERED {rec['path']}")
    assert not drift, "Evidence has been modified:\n" + "\n".join(drift)


def test_originals_are_not_writable():
    writable = [
        str(p.relative_to(ARCH))
        for p in ARCH.rglob("*")
        if p.is_file() and os.access(p, os.W_OK)
    ]
    assert not writable, f"Originals are writable: {writable[:10]}"
