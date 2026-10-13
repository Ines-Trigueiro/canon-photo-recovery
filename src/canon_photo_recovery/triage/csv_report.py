"""The spreadsheet-friendly report: curated columns, one row per Original."""

import csv
import pathlib

COLUMNS = (
    "name", "triage_class", "human_class", "width", "height", "capture_time", "shutter_count", "lens",
    "embedded_thumbnail", "metadata_salvageable", "thumbnail_salvageable", "needs_eyes",
    "needs_eyes_score", "defect_count",
)
# exiftool tags to try for each curated column, best first.
TAGS = {
    "capture_time": ("Composite:SubSecDateTimeOriginal", "ExifIFD:DateTimeOriginal"),
    "lens": ("Composite:LensID", "ExifIFD:LensModel", "Canon:LensModel", "Composite:Lens"),
}


def write_csv(report: dict, path: pathlib.Path) -> None:
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, COLUMNS, lineterminator="\n")
        w.writeheader()
        for r in report["originals"]:
            w.writerow(_row(r))


def _row(r: dict) -> dict:
    meta, frame = r["metadata"], r["frame"] or {}
    shutter = next((v for k, v in sorted(meta.items()) if k.endswith(":ShutterCount")), "")
    return {
        "name": r["name"],
        "triage_class": r["triage_class"],
        "human_class": r["human_class"] or "",
        "width": frame.get("width", ""),
        "height": frame.get("height", ""),
        "capture_time": _first(meta, TAGS["capture_time"]),
        "shutter_count": shutter,
        "lens": _first(meta, TAGS["lens"]),
        "embedded_thumbnail": r["embedded_thumbnail"]["status"],
        **{
            k: str(r["flags"][k]).lower()
            for k in ("metadata_salvageable", "thumbnail_salvageable", "needs_eyes")
        },
        "needs_eyes_score": r["needs_eyes"]["score"] if r["needs_eyes"] else "",
        "defect_count": len(r["defects"]),
    }


def _first(meta: dict, tags: tuple[str, ...]) -> str:
    return next((str(meta[t]) for t in tags if t in meta), "")
