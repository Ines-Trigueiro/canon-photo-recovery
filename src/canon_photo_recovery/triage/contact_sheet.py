"""The Contact sheet: a local-only HTML page with one tile per Original."""

import html
import pathlib
import urllib.parse

from canon_photo_recovery.triage.classify import TriageClass

THUMB_DIR = "thumbs"

TITLES = {
    TriageClass.UNREADABLE: "Unreadable",
    TriageClass.STRUCTURALLY_DAMAGED: "Structurally damaged",
    TriageClass.VISUALLY_CORRUPTED: "Visually corrupted",
    TriageClass.INTACT: "Intact",
}

STYLE = """
:root { --bg: #fafafa; --fg: #1a1a1a; --card: #fff; --muted: #666; --line: #ddd;
        --fill: #d0021b; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #161616; --fg: #e8e8e8; --card: #222; --muted: #9a9a9a; --line: #333;
          --fill: #ff5a6e; }
}
body { background: var(--bg); color: var(--fg); font: 14px/1.4 system-ui, sans-serif;
       margin: 0 16px 32px; }
h2 { margin-top: 32px; border-bottom: 1px solid var(--line); padding-bottom: 4px; }
.tiles { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
         gap: 12px; }
.tile { background: var(--card); border: 1px solid var(--line); border-radius: 6px;
        padding: 8px 10px; overflow-wrap: anywhere; }
.name { font-weight: 600; }
.thumb { position: relative; margin: 0 0 6px;
         background: repeating-conic-gradient(var(--line) 0 25%, transparent 0 50%) 0 0 / 12px 12px; }
.thumb img { display: block; width: 100%; height: auto; }
.thumb svg { position: absolute; inset: 0; width: 100%; height: 100%; }
.thumb polygon { fill: color-mix(in srgb, var(--fill) 15%, transparent); stroke: var(--fill);
                 stroke-width: 2; vector-effect: non-scaling-stroke; }
.placeholder { aspect-ratio: 3 / 2; display: grid; place-items: center; margin: 0 0 6px;
               border: 1px dashed var(--line); color: var(--muted); font-size: 12px; }
.fill-note { color: var(--fill); font-size: 12px; }
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
    defects = "".join(f"<li>{esc(_defect(d))}</li>" for d in r["defects"])
    return (
        f'<div class="tile" data-original="{esc(r["name"])}">'
        f"{_picture(r)}"
        f'<div class="name">{esc(r["name"])}</div>'
        f'<div class="class">{TITLES[TriageClass(r["triage_class"])]}</div>'
        f'<div class="flags">{esc(", ".join(flags))}</div>'
        f'<ul class="defects">{defects}</ul>'
        "</div>"
    )


def _defect(d: dict) -> str:
    where = f" @ {d['offset']:#x}" if d.get("offset") is not None else ""
    return f"{d['kind']}{where}"


def _picture(r: dict) -> str:
    """The decoded Main image, with any decoder grey fill outlined, or a placeholder."""
    decoded = r.get("decoded")
    if decoded is None:
        return '<div class="placeholder">No decodable pixels</div>'
    name = html.escape(r["name"])
    src = html.escape(urllib.parse.quote(decoded["thumbnail"]))
    img = f'<img src="{src}" loading="lazy" alt="Decoded Main image of {name}">'
    fill = decoded["grey_fill"]
    if fill is None:
        return f'<figure class="thumb">{img}</figure>'
    w, h, x, y = decoded["width"], decoded["height"], fill["from_x"], fill["from_y"]
    step = y + fill["mcu_height"]
    points = (
        f"0,{y} {w},{y} {w},{h} 0,{h}" if x == 0
        else f"{x},{y} {w},{y} {w},{h} 0,{h} 0,{step} {x},{step}"
    )
    return (
        f'<figure class="thumb">{img}'
        f'<svg data-grey-fill viewBox="0 0 {w} {h}" preserveAspectRatio="none" '
        f'aria-label="decoder grey fill"><polygon points="{points}"/></svg></figure>'
        f'<div class="fill-note">not decoded (decoder fill, hidden): '
        f'{fill["fraction"]:.0%} of image</div>'
    )
