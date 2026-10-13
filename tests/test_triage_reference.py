"""Reference acceptance: the Triage run on three real Originals, read in place.

Skipped when the evidence tree is absent. Only classes and flags are asserted;
no image bytes, EXIF values, or photo content appear in this file or its output.
"""

import json
import pathlib

import pytest

from canon_photo_recovery.triage import run_triage

ROOT = pathlib.Path(__file__).resolve().parent.parent
ARCH = ROOT / "evidence" / "arch-copies"
MANIFEST = ROOT / "evidence" / "manifest" / "baseline-sha256.json"

EXPECTED = {
    "IMG_0497.JPG": ("intact", {"metadata_salvageable": True}),
    "IMG_0034.JPG": ("visually_corrupted", {"metadata_salvageable": True}),
    "IMG_0015.JPG": ("unreadable", {"metadata_salvageable": True}),
}


@pytest.mark.skipif(not MANIFEST.exists(), reason="evidence tree not present")
def test_reference_originals_get_their_known_classes(tmp_path):
    baseline = json.loads(MANIFEST.read_text())
    subset = [rec for rec in baseline["files"] if rec["path"] in EXPECTED]
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"files": subset}))

    report = run_triage(ARCH, manifest, tmp_path / "out")

    got = {
        o["name"]: (o["triage_class"], {k: o["flags"][k] for k in EXPECTED[o["name"]][1]})
        for o in report["originals"]
    }
    assert got == EXPECTED
