"""The top-level Triage run: originals + manifest in, report and Contact sheet out."""

import hashlib
import json
import pathlib

from canon_photo_recovery import jpeg, provenance
from canon_photo_recovery.triage.classify import classify, sort_key
from canon_photo_recovery.triage.contact_sheet import write_contact_sheet
from canon_photo_recovery.triage.metadata import metadata_salvageable


class IntegrityError(Exception):
    """An Original is missing or no longer matches its manifest hash."""


def run_triage(
    originals_dir: pathlib.Path, manifest_path: pathlib.Path, out_dir: pathlib.Path
) -> dict:
    """Triage every Original listed in the manifest and write the outputs.

    Every Original is verified against its manifest hash as it is read; any
    mismatch raises IntegrityError before anything is written.
    """
    originals_dir = pathlib.Path(originals_dir)
    manifest_bytes = pathlib.Path(manifest_path).read_bytes()
    manifest = json.loads(manifest_bytes)
    results = [
        _triage_one(originals_dir, rec)
        for rec in sorted(manifest["files"], key=lambda r: r["path"])
    ]
    report = {"originals": results}

    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    write_contact_sheet(report, out / "contact-sheet.html")

    record = {
        "tool": "canon_photo_recovery.triage",
        "git_commit": provenance.git_commit(),
        "source_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "sources": "every Original in the manifest; per-file SHA-256 in report.json",
        "transformations": [
            "read each Original, verify SHA-256 against the manifest",
            "structural parse and MCU accounting (read-only, no pixels decoded)",
        ],
        "contains_synthesised_pixels": False,
    }
    for name in ("report.json", "contact-sheet.html"):
        provenance.write_sidecar(out / name, record)
    return report


def _triage_one(originals_dir: pathlib.Path, rec: dict) -> dict:
    name = rec["path"]
    path = originals_dir / name
    if not path.is_file():
        raise IntegrityError(f"MISSING {name}")
    data = path.read_bytes()
    sha256 = hashlib.sha256(data).hexdigest()
    if sha256 != rec["sha256"]:
        raise IntegrityError(f"ALTERED {name}")

    structure = jpeg.parse(data)
    result = {
        "name": name,
        "size": len(data),
        "sha256": sha256,
        "triage_class": str(classify(structure)),
        "defects": [d.to_dict() for d in sorted(structure.defects, key=sort_key)],
        "flags": {"metadata_salvageable": metadata_salvageable(data, structure)},
        "frame": None,
        "mcus": None,
    }
    if structure.frame is not None:
        f = structure.frame
        result["frame"] = {
            "width": f.width,
            "height": f.height,
            "sampling": [f"{c.h}x{c.v}" for c in f.components],
        }
    counted = [sc for sc in structure.scans if sc.mcus_expected is not None]
    if counted:
        result["mcus"] = {
            "expected": sum(sc.mcus_expected for sc in counted),
            "decoded": sum(sc.mcus_decoded for sc in counted),
        }
    return result
