"""The top-level Triage run: originals + manifest in, report and Contact sheet out."""

import fractions
import hashlib
import json
import pathlib
import shutil

import numpy as np
from PIL import Image

from canon_photo_recovery import jpeg, provenance
from canon_photo_recovery.triage import decode
from canon_photo_recovery.triage.classify import classify, sort_key
from canon_photo_recovery.triage.contact_sheet import THUMB_DIR, write_contact_sheet
from canon_photo_recovery.triage.metadata import metadata_salvageable

TOOL = "canon_photo_recovery.triage"
THUMB_MAX = 320  # longest thumbnail edge, in pixels


class IntegrityError(Exception):
    """An Original is missing or no longer matches its manifest hash."""


def run_triage(
    originals_dir: pathlib.Path,
    manifest_path: pathlib.Path,
    out_dir: pathlib.Path,
    *,
    decode_scale: str = "1/8",
) -> dict:
    """Triage every Original listed in the manifest and write the outputs.

    Every Original is verified against its manifest hash as it is read; any
    mismatch raises IntegrityError before anything is written.
    """
    decode.check_decoder(decode_scale)
    originals_dir = pathlib.Path(originals_dir)
    manifest_bytes = pathlib.Path(manifest_path).read_bytes()
    manifest = json.loads(manifest_bytes)
    triaged = [
        _triage_one(originals_dir, rec, decode_scale)
        for rec in sorted(manifest["files"], key=lambda r: r["path"])
    ]
    results = [result for result, _ in triaged]
    report = {
        "decoder": {"version": decode.decoder_version(), "scale": decode_scale},
        "originals": results,
    }

    out = pathlib.Path(out_dir)
    shutil.rmtree(out / THUMB_DIR, ignore_errors=True)  # only this run's thumbnails
    (out / THUMB_DIR).mkdir(parents=True)
    commit = provenance.git_commit()
    for result, thumb in triaged:
        if thumb is not None:
            _write_thumbnail(out, result, thumb, decode_scale, commit)
    (out / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    write_contact_sheet(report, out / "contact-sheet.html")

    record = {
        "tool": TOOL,
        "git_commit": commit,
        "source_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "sources": "every Original in the manifest; per-file SHA-256 in report.json",
        "transformations": [
            "read each Original, verify SHA-256 against the manifest",
            "structural parse and MCU accounting",
            f"djpeg -scale {decode_scale} decode; messages recorded as Defects",
        ],
        "contains_synthesised_pixels": False,
    }
    for name in ("report.json", "contact-sheet.html"):
        provenance.write_sidecar(out / name, record)
    return report


def _triage_one(
    originals_dir: pathlib.Path, rec: dict, decode_scale: str
) -> tuple[dict, Image.Image | None]:
    name = rec["path"]
    path = originals_dir / name
    if not path.is_file():
        raise IntegrityError(f"MISSING {name}")
    data = path.read_bytes()
    sha256 = hashlib.sha256(data).hexdigest()
    if sha256 != rec["sha256"]:
        raise IntegrityError(f"ALTERED {name}")

    structure = jpeg.parse(data)
    decoded = decode.decode(data, decode_scale)
    defects = structure.defects + decode.decode_defects(decoded, structure)
    fill = None
    if decoded.pixels is not None:
        fill = decode.grey_fill(decoded.pixels, *_mcu_size(structure, decode_scale))
    nothing_decoded = decoded.pixels is None or (fill is not None and fill["fraction"] == 1)
    result = {
        "name": name,
        "size": len(data),
        "sha256": sha256,
        "triage_class": str(classify(defects, nothing_decoded=nothing_decoded)),
        "defects": [d.to_dict() for d in sorted(defects, key=sort_key)],
        "flags": {"metadata_salvageable": metadata_salvageable(data, structure)},
        "frame": None,
        "mcus": None,
        "decoded": None,
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
        expected = sum(sc.mcus_expected for sc in counted)
        got = sum(sc.mcus_decoded for sc in counted)
        result["mcus"] = {"expected": expected, "decoded": got, "fraction": got / expected}

    if nothing_decoded:
        return result, None
    h, w, _ = decoded.pixels.shape
    result["decoded"] = {
        "width": w,
        "height": h,
        "grey_fill": fill,
        "thumbnail": f"{THUMB_DIR}/{_thumb_name(name)}",
    }
    # Decoder grey fill is not decoded from the Original, so it is made
    # transparent rather than shown: a thumbnail holds only decoded pixels.
    alpha = np.full((h, w), 255, dtype=np.uint8)
    if fill is not None:
        alpha[decode.grey_mask((h, w), fill)] = 0
    thumb = Image.fromarray(np.dstack([decoded.pixels, alpha]), "RGBA")
    thumb.thumbnail((THUMB_MAX, THUMB_MAX), Image.Resampling.LANCZOS)
    return result, thumb


def _thumb_name(name: str) -> str:
    """A flat file name for an Original's thumbnail, even if its path has folders."""
    return name.replace("/", "__") + ".png"


def _mcu_size(structure: jpeg.Structure, decode_scale: str) -> tuple[int, int]:
    """(width, height) in decoded pixels of one MCU at this scale."""
    if structure.frame is None:
        return 1, 1
    f = fractions.Fraction(decode_scale)
    hmax = max(c.h for c in structure.frame.components)
    vmax = max(c.v for c in structure.frame.components)
    return max(1, int(8 * hmax * f)), max(1, int(8 * vmax * f))


def _write_thumbnail(
    out: pathlib.Path, result: dict, thumb: Image.Image, decode_scale: str, commit: str | None
) -> None:
    path = out / result["decoded"]["thumbnail"]
    thumb.save(path, "PNG")
    grey = result["decoded"]["grey_fill"] is not None
    provenance.write_sidecar(
        path,
        {
            "tool": TOOL,
            "git_commit": commit,
            "source_sha256": result["sha256"],
            "transformations": [
                f"djpeg -scale {decode_scale} decode of the Main image",
                *(["decoder grey fill set fully transparent"] if grey else []),
                f"Pillow LANCZOS resize to fit {THUMB_MAX}x{THUMB_MAX}",
                "PNG encode (lossless)",
            ],
            "contains_synthesised_pixels": False,
            "note": "review thumbnail only, not recovered output",
        },
    )
