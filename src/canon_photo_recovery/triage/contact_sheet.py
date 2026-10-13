"""The Contact sheet: a local-only HTML page with one tile per Original."""

import html
import pathlib
import urllib.parse

from canon_photo_recovery.triage.classify import TITLES, TriageClass

THUMB_DIR = "thumbs"

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
.inset { position: absolute; right: 4px; bottom: 4px; width: 30%; margin: 0;
         background: var(--card); border: 1px solid var(--line); border-radius: 3px; }
.inset img { display: block; width: 100%; height: auto; }
.inset figcaption { font-size: 10px; color: var(--muted); text-align: center; }
.placeholder { position: relative; aspect-ratio: 3 / 2; display: grid; place-items: center;
               margin: 0 0 6px;
               border: 1px dashed var(--line); color: var(--muted); font-size: 12px; }
.fill-note { color: var(--fill); font-size: 12px; }
.tile.disagree { border: 2px solid var(--fill); }
.human { font-weight: 600; }
.disagree .human { color: var(--fill); }
.note { font-size: 12px; font-style: italic; }
.review-warnings { color: var(--fill); }
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
    if report["absent_from_copy"]:
        absent = ", ".join(f"IMG_{n:04d}" for n in report["absent_from_copy"])
        parts.append(f'<p class="absent">Absent from copy: {absent}</p>')
    rv = report["review"]
    parts.append(
        f'<p class="review-summary">Human class set on {rv["reviewed"]}; '
        f'{rv["disagreements"]} disagree with the Triage class.</p>'
    )
    if rv["warnings"]:
        items = "".join(f"<li>{html.escape(w)}</li>" for w in rv["warnings"])
        parts.append(f'<div class="review-warnings">Review file warnings:<ul>{items}</ul></div>')
    for cls in TriageClass:
        originals = by_class[cls]
        parts.append(f'<section data-class="{cls}">')
        parts.append(f"<h2>{TITLES[cls]} ({len(originals)})</h2>")
        parts.append('<div class="tiles">')
        parts += [_tile(r) for r in sorted(originals, key=_attention)]
        parts.append("</div></section>")
    parts.append("</body></html>")
    path.write_text("\n".join(parts) + "\n")


def _attention(r: dict) -> tuple:
    """Needs eyes first, then the highest scores, then by name."""
    score = r["needs_eyes"]["score"] if r["needs_eyes"] else 0
    return (not r["flags"]["needs_eyes"], -score, r["name"])


def _tile(r: dict) -> str:
    esc = html.escape
    flags = [name.replace("_", " ") for name, on in sorted(r["flags"].items()) if on]
    defects = "".join(f"<li>{esc(_defect(d))}</li>" for d in r["defects"])
    human = r["human_class"]
    disagree = human is not None and human != r["triage_class"]
    review = ""
    if human is not None:
        review += f'<div class="human">Human class: {TITLES[TriageClass(human)]}</div>'
    if r["review_note"]:
        review += f'<div class="note">{esc(r["review_note"])}</div>'
    return (
        f'<div class="tile{" disagree" if disagree else ""}" data-original="{esc(r["name"])}"'
        f'{" data-disagree" if disagree else ""}>'
        f"{_picture(r)}"
        f'<div class="name">{esc(r["name"])}</div>'
        f'<div class="class">{TITLES[TriageClass(r["triage_class"])]}</div>'
        f"{review}"
        f'<div class="flags">{esc(", ".join(flags))}</div>'
        f'<ul class="defects">{defects}</ul>'
        "</div>"
    )


def _defect(d: dict) -> str:
    where = f" @ {d['offset']:#x}" if d.get("offset") is not None else ""
    if d.get("rows"):
        where = f" rows {d['rows'][0]}-{d['rows'][1]}"
    return f"{d['kind']}{where}"


def _picture(r: dict) -> str:
    """The decoded Main image, with any decoder grey fill outlined, or a placeholder."""
    decoded = r.get("decoded")
    name = html.escape(r["name"])
    inset = _inset(r)
    if decoded is None:
        return f'<div class="placeholder">No decodable pixels{inset}</div>'
    src = html.escape(urllib.parse.quote(decoded["thumbnail"]))
    img = f'<img src="{src}" loading="lazy" alt="Decoded Main image of {name}">'
    fill = decoded["grey_fill"]
    mid = sum(run["mcus"] for run in decoded["mid_image_grey"])
    mid_note = (
        f'<div class="fill-note">not decoded (decoder fill, hidden): {mid} MCUs mid-image</div>'
        if mid else ""
    )
    if fill is None:
        return f'<figure class="thumb">{img}{inset}</figure>{mid_note}'
    w, h, x, y = decoded["width"], decoded["height"], fill["from_x"], fill["from_y"]
    step = y + fill["mcu_height"]
    points = (
        f"0,{y} {w},{y} {w},{h} 0,{h}" if x == 0
        else f"{x},{y} {w},{y} {w},{h} 0,{h} 0,{step} {x},{step}"
    )
    return (
        f'<figure class="thumb">{img}'
        f'<svg data-grey-fill viewBox="0 0 {w} {h}" preserveAspectRatio="none" '
        f'aria-label="decoder grey fill"><polygon points="{points}"/></svg>{inset}</figure>'
        f'<div class="fill-note">not decoded (decoder fill, hidden): '
        f'{fill["fraction"]:.0%} of image</div>{mid_note}'
    )


def _inset(r: dict) -> str:
    """The Embedded thumbnail, labelled, when it decodes without error."""
    file = r["embedded_thumbnail"].get("file")
    if file is None:
        return ""
    src = html.escape(urllib.parse.quote(file))
    return (
        f'<figure class="inset"><img data-embedded-thumbnail src="{src}" loading="lazy" '
        f'alt="Embedded thumbnail of {html.escape(r["name"])}">'
        "<figcaption>Embedded thumbnail</figcaption></figure>"
    )
