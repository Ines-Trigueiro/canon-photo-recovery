"""The Contact sheet: a local-only HTML page with one tile per Original."""

import html
import pathlib

from canon_photo_recovery.triage.classify import TriageClass

TITLES = {
    TriageClass.UNREADABLE: "Unreadable",
    TriageClass.STRUCTURALLY_DAMAGED: "Structurally damaged",
    TriageClass.VISUALLY_CORRUPTED: "Visually corrupted",
    TriageClass.INTACT: "Intact",
}

STYLE = """
:root { --bg: #fafafa; --fg: #1a1a1a; --card: #fff; --muted: #666; --line: #ddd; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #161616; --fg: #e8e8e8; --card: #222; --muted: #9a9a9a; --line: #333; }
}
body { background: var(--bg); color: var(--fg); font: 14px/1.4 system-ui, sans-serif;
       margin: 0 16px 32px; }
h2 { margin-top: 32px; border-bottom: 1px solid var(--line); padding-bottom: 4px; }
.tiles { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
         gap: 12px; }
.tile { background: var(--card); border: 1px solid var(--line); border-radius: 6px;
        padding: 8px 10px; overflow-wrap: anywhere; }
.name { font-weight: 600; }
.flags, .defects { color: var(--muted); font-size: 12px; margin: 4px 0 0; padding: 0;
                   list-style: none; }
"""


def write_contact_sheet(report: dict, path: pathlib.Path) -> None:
    by_class = {c: [] for c in TriageClass}
    for r in report["originals"]:
        by_class[TriageClass(r["triage_class"])].append(r)

    parts = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>Triage contact sheet</title>",
        f"<style>{STYLE}</style></head><body>",
        "<h1>Triage contact sheet</h1>",
    ]
    for cls in TriageClass:
        originals = by_class[cls]
        parts.append(f'<section data-class="{cls}">')
        parts.append(f"<h2>{TITLES[cls]} ({len(originals)})</h2>")
        parts.append('<div class="tiles">')
        parts += [_tile(r) for r in originals]
        parts.append("</div></section>")
    parts.append("</body></html>")
    path.write_text("\n".join(parts) + "\n")


def _tile(r: dict) -> str:
    esc = html.escape
    flags = [name.replace("_", " ") for name, on in sorted(r["flags"].items()) if on]
    defects = "".join(
        f"<li>{esc(d['kind'])} @ {d['offset']:#x}</li>" for d in r["defects"]
    )
    return (
        f'<div class="tile" data-original="{esc(r["name"])}">'
        f'<div class="name">{esc(r["name"])}</div>'
        f'<div class="class">{TITLES[TriageClass(r["triage_class"])]}</div>'
        f'<div class="flags">{esc(", ".join(flags))}</div>'
        f'<ul class="defects">{defects}</ul>'
        "</div>"
    )
