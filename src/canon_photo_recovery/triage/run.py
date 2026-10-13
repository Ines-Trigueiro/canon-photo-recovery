"""The top-level Triage run: originals + manifest in, report and Contact sheet out."""

import concurrent.futures
import fractions
import functools
import hashlib
import json
import os
import pathlib
import re
import shutil

import numpy as np
from PIL import Image

from canon_photo_recovery import jpeg, provenance
from canon_photo_recovery.triage import decode, metadata, review
from canon_photo_recovery.triage.classify import classify, sort_key
from canon_photo_recovery.triage.contact_sheet import THUMB_DIR, write_contact_sheet
from canon_photo_recovery.triage.csv_report import write_csv

TOOL = "canon_photo_recovery.triage"
THUMB_MAX = 320  # longest thumbnail edge, in pixels
SEQUENCE = re.compile(r"IMG_(\d{4})\.JPE?G", re.IGNORECASE)


class IntegrityError(Exception):
    """An Original is missing or no longer matches its manifest hash."""


def run_triage(
    originals_dir: pathlib.Path,
    manifest_path: pathlib.Path,
    out_dir: pathlib.Path,
    *,
    decode_scale: str = "1/8",
    workers: int | None = None,
) -> dict:
    """Triage every Original listed in the manifest and write the outputs.

    Every Original is verified against its manifest hash as it is read; any
    mismatch raises IntegrityError before anything is written. Originals are
    triaged in `workers` processes (default: one per CPU); the outputs do not
    depend on how many.
    """
    if workers is not None and workers < 1:
        raise ValueError(f"workers must be at least 1, not {workers}")
    decode.check_decoder(decode_scale)
    metadata.check_tool()
    originals_dir = pathlib.Path(originals_dir)
    manifest_bytes = pathlib.Path(manifest_path).read_bytes()
    manifest = json.loads(manifest_bytes)
    records = sorted(manifest["files"], key=lambda r: r["path"])
    triage_one = functools.partial(_triage_one, originals_dir, decode_scale=decode_scale)
    workers = workers or os.process_cpu_count() or 1
    if workers == 1:
        triaged = [triage_one(rec) for rec in records]
    else:
        with concurrent.futures.ProcessPoolExecutor(workers) as pool:
            try:
                triaged = list(pool.map(triage_one, records))
            except BaseException:
                pool.shutdown(cancel_futures=True)
                raise
    results = [result for result, _, _ in triaged]
    report = {
        "decoder": {"version": decode.decoder_version(), "scale": decode_scale},
        "metadata_tool": metadata.tool_version(),
        "absent_from_copy": _absent_from_copy(r["name"] for r in results),
        "originals": results,
    }

    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    commit = provenance.git_commit()
    review_file = out / review.FILENAME
    if review.create_if_absent(review_file, [r["name"] for r in results]):
        provenance.write_sidecar(review_file, {
            "tool": TOOL,
            "git_commit": commit,
            "source_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "transformations": ["one empty row per Original in the manifest"],
            "contains_synthesised_pixels": False,
            "note": "for the reviewer to fill in; never overwritten by Triage",
        })
    report["review"] = review.merge(review_file, results)

    shutil.rmtree(out / THUMB_DIR, ignore_errors=True)  # only this run's thumbnails
    (out / THUMB_DIR).mkdir(parents=True)
    for result, thumb, embedded in triaged:
        if thumb is not None:
            _write_thumbnail(out, result, thumb, decode_scale, commit)
        if embedded is not None:
            _write_embedded_thumbnail(out, result, embedded, commit)
    (out / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    write_csv(report, out / "report.csv")
    write_contact_sheet(report, out / "contact-sheet.html")

    record = {
        "tool": TOOL,
        "git_commit": commit,
        "source_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "sources": "every Original in the manifest; per-file SHA-256 in report.json",
        "review_file_sha256": hashlib.sha256(review_file.read_bytes()).hexdigest(),
        "transformations": [
            "read each Original, verify SHA-256 against the manifest",
            "structural parse and MCU accounting",
            f"djpeg -scale {decode_scale} decode; messages recorded as Defects",
            "exiftool -j -G1:4 -a on the Original's bytes; Embedded thumbnail decoded by djpeg",
            f"Human class and note merged from {review.FILENAME}",
        ],
        "contains_synthesised_pixels": False,
    }
    for name in ("report.json", "report.csv", "contact-sheet.html"):
        provenance.write_sidecar(out / name, record)
    return report


def _triage_one(
    originals_dir: pathlib.Path, rec: dict, decode_scale: str
) -> tuple[dict, Image.Image | None, bytes | None]:
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
        "flags": {"metadata_salvageable": metadata.metadata_salvageable(data, structure)},
        "embedded_thumbnail": _embedded_thumbnail(data, structure),
        "metadata": metadata.read_metadata(data),
        "frame": None,
        "mcus": None,
        "decoded": None,
    }
    result["flags"]["thumbnail_salvageable"] = result["embedded_thumbnail"]["status"] == "decodes"
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

    embedded = None
    if result["flags"]["thumbnail_salvageable"]:
        e = result["embedded_thumbnail"]
        embedded = data[e["offset"] : e["offset"] + e["length"]]
        e["file"] = f"{THUMB_DIR}/{_thumb_name(name, '.embedded.jpg')}"
    if nothing_decoded:
        return result, None, embedded
    h, w, _ = decoded.pixels.shape
    result["decoded"] = {
        "width": w,
        "height": h,
        "grey_fill": fill,
        "thumbnail": f"{THUMB_DIR}/{_thumb_name(name, '.png')}",
    }
    # Decoder grey fill is not decoded from the Original, so it is made
    # transparent rather than shown: a thumbnail holds only decoded pixels.
    alpha = np.full((h, w), 255, dtype=np.uint8)
    if fill is not None:
        alpha[decode.grey_mask((h, w), fill)] = 0
    thumb = Image.fromarray(np.dstack([decoded.pixels, alpha]), "RGBA")
    thumb.thumbnail((THUMB_MAX, THUMB_MAX), Image.Resampling.LANCZOS)
    return result, thumb, embedded


def _absent_from_copy(names) -> list[int]:
    """Sequence numbers inside the observed IMG_NNNN range with no Original."""
    seen = {
        int(m.group(1))
        for name in names
        if (m := SEQUENCE.fullmatch(pathlib.PurePath(name).name))
    }
    return sorted(set(range(min(seen), max(seen) + 1)) - seen) if seen else []


def _embedded_thumbnail(data: bytes, structure: jpeg.Structure) -> dict:
    """Where the Embedded thumbnail is and whether it decodes without error (ADR-0002)."""
    where = metadata.embedded_thumbnail(data, structure)
    if isinstance(where, str):
        return {"status": where}
    offset, length = where
    decoded = decode.decode(data[offset : offset + length], "1/1")
    record = {"offset": offset, "length": length, "messages": decoded.messages}
    if decoded.pixels is None:
        return {**record, "status": "undecodable"}
    h, w, _ = decoded.pixels.shape
    damaged = any(not decode.NOTICE.search(m) for m in decoded.messages)
    return {**record, "status": "damaged" if damaged else "decodes", "width": w, "height": h}


def _thumb_name(name: str, suffix: str) -> str:
    """A flat file name for an Original's thumbnail, even if its path has folders."""
    return name.replace("/", "__") + suffix


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


def _write_embedded_thumbnail(
    out: pathlib.Path, result: dict, embedded: bytes, commit: str | None
) -> None:
    e = result["embedded_thumbnail"]
    path = out / e["file"]
    path.write_bytes(embedded)
    provenance.write_sidecar(
        path,
        {
            "tool": TOOL,
            "git_commit": commit,
            "source_sha256": result["sha256"],
            "transformations": [
                f"byte-exact copy of the Embedded thumbnail, bytes {e['offset']}.."
                + f"{e['offset'] + e['length'] - 1} of the Original (ADR-0002)",
            ],
            "contains_synthesised_pixels": False,
            "note": "review inset only, not recovered output",
        },
    )
