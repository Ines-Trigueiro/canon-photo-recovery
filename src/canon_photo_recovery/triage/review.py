"""The review file: one row per Original where a reviewer records a Human class.

Created once with empty verdicts and never written again, so re-running Triage
cannot destroy a reviewer's work.
"""

import csv
import io
import pathlib

from canon_photo_recovery.triage.classify import TITLES, TriageClass

FILENAME = "review.csv"
COLUMNS = ("name", "human_class", "note")
# A Human class may be written as its value or its title, in any case.
_SPELLINGS = {
    **{c.value: c for c in TriageClass},
    **{title.lower(): c for c, title in TITLES.items()},
}


def create_if_absent(path: pathlib.Path, names: list[str]) -> bool:
    """Write the empty review file unless one exists; True when it was created."""
    try:
        with open(path, "x", newline="") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(COLUMNS)
            w.writerows([name, "", ""] for name in names)
    except FileExistsError:
        return False
    return True


def merge(path: pathlib.Path, results: list[dict]) -> dict:
    """Add `human_class` and `review_note` to every result, from the review file.

    Rows that cannot be used are reported as warnings and never abort the run.
    Returns the review summary for the report.
    """
    by_name = {r["name"]: r for r in results}
    for r in results:
        r["human_class"] = r["review_note"] = None
    rows, warnings = _read(path)
    seen = set()
    for line, row in rows or []:
        name = (row.get("name") or "").strip()
        value = (row.get("human_class") or "").strip()
        note = (row.get("note") or "").strip()
        if not (name or value or note):
            continue
        if name in seen:
            warnings.append(f"{path.name} line {line}: {name} is listed again; kept the first")
            continue
        seen.add(name)
        if not value and not note:
            continue
        if name not in by_name:
            warnings.append(f"{path.name} line {line}: no Original named {name!r}")
            continue
        human = _SPELLINGS.get(value.lower())
        if value and human is None:
            choices = ", ".join(c.value for c in TriageClass)
            warnings.append(
                f"{path.name} line {line}: {value!r} is not a Triage class ({choices})"
            )
        by_name[name]["human_class"] = human and human.value
        by_name[name]["review_note"] = note or None
    if rows is not None and (unlisted := sorted(set(by_name) - seen)):
        warnings.append(f"{path.name} has no row for {len(unlisted)} Originals: "
                        + ", ".join(unlisted))
    reviewed = [r for r in results if r["human_class"] is not None]
    return {
        "reviewed": len(reviewed),
        "disagreements": sum(r["human_class"] != r["triage_class"] for r in reviewed),
        "warnings": warnings,
    }


def _read(path: pathlib.Path) -> tuple[list[tuple[int, dict]] | None, list[str]]:
    """((line number, row) for every row, warnings), accepting what spreadsheets save:
    UTF-8 with or without BOM or Windows-1252, separated by commas, semicolons or tabs."""
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")
    try:
        dialect = csv.Sniffer().sniff(text.partition("\n")[0], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text, newline=""), dialect=dialect)
    if reader.fieldnames is None or not {"name", "human_class"} <= set(reader.fieldnames):
        return None, [f"{path.name}: needs the columns {', '.join(COLUMNS)}"]
    rows = []
    line = reader.line_num + 1
    for row in reader:
        rows.append((line, row))
        line = reader.line_num + 1
    return rows, []
