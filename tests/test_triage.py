"""Triage behaviour, driven only through the top-level Triage run (the agreed seam)."""

import csv
import hashlib
import json
import urllib.parse
from html.parser import HTMLParser

import numpy as np
import pytest
from fixtures import synthetic
from PIL import Image

from canon_photo_recovery.triage import IntegrityError, run_triage


def triage(tmp_path, files):
    manifest = synthetic.write_originals(tmp_path, files)
    out = tmp_path / "out"
    run_triage(tmp_path / "originals", manifest, out)
    report = json.loads((out / "report.json").read_text())
    return {o["name"]: o for o in report["originals"]}, out


def test_clean_original_is_intact_with_no_defects(tmp_path):
    results, _ = triage(tmp_path, {"IMG_0001.JPG": synthetic.make_clean_jpeg()})

    r = results["IMG_0001.JPG"]
    assert r["triage_class"] == "intact"
    assert r["defects"] == []


def defect_kinds(result):
    return {d["kind"]: d for d in result["defects"]}


def test_missing_eoi_is_structurally_damaged(tmp_path):
    damaged = synthetic.strip_eoi(synthetic.make_clean_jpeg())

    results, _ = triage(tmp_path, {"IMG_0001.JPG": damaged})

    r = results["IMG_0001.JPG"]
    assert r["triage_class"] == "structurally_damaged"
    assert set(defect_kinds(r)) == {"missing_eoi", "decoder_warning"}
    assert defect_kinds(r)["missing_eoi"]["offset"] == len(damaged)
    assert defect_kinds(r)["decoder_warning"]["detail"] == "Premature end of JPEG file"


def sos_offset(data):
    return synthetic.segment(data, 0xDA)[0]


def test_missing_frame_header_is_unreadable(tmp_path):
    damaged = synthetic.remove_segments(synthetic.make_clean_jpeg(), 0xC0)

    results, _ = triage(tmp_path, {"IMG_0001.JPG": damaged})

    r = results["IMG_0001.JPG"]
    assert r["triage_class"] == "unreadable"
    assert defect_kinds(r)["missing_sof"]["offset"] == sos_offset(damaged)


def test_invalid_frame_header_is_unreadable(tmp_path):
    clean = synthetic.make_clean_jpeg()
    sof, _ = synthetic.segment(clean, 0xC0)
    damaged = bytearray(clean)
    damaged[sof + 9] = 7  # component count no longer matches the segment length

    results, _ = triage(tmp_path, {"IMG_0001.JPG": bytes(damaged)})

    r = results["IMG_0001.JPG"]
    assert r["triage_class"] == "unreadable"
    assert defect_kinds(r)["invalid_sof"]["offset"] == sof


def test_missing_scan_header_is_unreadable(tmp_path):
    clean = synthetic.make_clean_jpeg()
    sos, _ = synthetic.segment(clean, 0xDA)
    damaged = clean[:sos] + synthetic.EOI

    results, _ = triage(tmp_path, {"IMG_0001.JPG": damaged})

    r = results["IMG_0001.JPG"]
    assert r["triage_class"] == "unreadable"
    assert defect_kinds(r)["missing_sos"]["offset"] == len(damaged)


def test_missing_quantisation_tables_is_unreadable_because_nothing_decodes(tmp_path):
    damaged = synthetic.remove_segments(synthetic.make_clean_jpeg(), 0xDB)

    results, _ = triage(tmp_path, {"IMG_0001.JPG": damaged})

    r = results["IMG_0001.JPG"]
    assert r["triage_class"] == "unreadable"
    assert "decode_failed" in defect_kinds(r)
    assert defect_kinds(r)["missing_dqt"]["offset"] == sos_offset(damaged)


def test_missing_huffman_tables_is_structurally_damaged(tmp_path):
    damaged = synthetic.remove_segments(synthetic.make_clean_jpeg(), 0xC4)

    results, _ = triage(tmp_path, {"IMG_0001.JPG": damaged})

    r = results["IMG_0001.JPG"]
    assert r["triage_class"] == "structurally_damaged"
    assert defect_kinds(r)["missing_dht"]["offset"] == sos_offset(damaged)


def test_invalid_huffman_table_is_unreadable_because_nothing_decodes(tmp_path):
    clean = synthetic.make_clean_jpeg()
    dht, _ = synthetic.segment(clean, 0xC4)
    damaged = bytearray(clean)
    damaged[dht + 5 : dht + 21] = bytes([255] * 16)  # code counts far exceed the segment

    results, _ = triage(tmp_path, {"IMG_0001.JPG": bytes(damaged)})

    r = results["IMG_0001.JPG"]
    assert r["triage_class"] == "unreadable"
    assert "decode_failed" in defect_kinds(r)
    assert defect_kinds(r)["invalid_dht"]["offset"] == dht


def test_zero_fill_run_is_located(tmp_path):
    damaged, off = synthetic.zero_fill(synthetic.make_clean_jpeg(), length=1024)

    results, _ = triage(tmp_path, {"IMG_0001.JPG": damaged})

    r = results["IMG_0001.JPG"]
    assert r["triage_class"] == "structurally_damaged"
    run = defect_kinds(r)["zero_fill_run"]
    assert (run["offset"], run["length"]) == (off, 1024)


def test_truncated_scan_is_structurally_damaged(tmp_path):
    damaged, _ = synthetic.truncate_mid_scan(synthetic.make_clean_jpeg())

    results, _ = triage(tmp_path, {"IMG_0001.JPG": damaged})

    r = results["IMG_0001.JPG"]
    assert r["triage_class"] == "structurally_damaged"
    assert defect_kinds(r)["missing_eoi"]["offset"] == len(damaged)


def test_scan_ending_before_last_mcu_is_structurally_damaged(tmp_path):
    damaged, cut = synthetic.short_scan(synthetic.make_clean_jpeg())

    results, _ = triage(tmp_path, {"IMG_0001.JPG": damaged})

    r = results["IMG_0001.JPG"]
    assert r["triage_class"] == "structurally_damaged"
    assert set(defect_kinds(r)) == {"short_scan", "decoder_warning"}
    assert defect_kinds(r)["short_scan"]["offset"] == cut
    assert defect_kinds(r)["decoder_warning"]["offset"] == cut


def test_clean_original_with_restart_markers_is_intact(tmp_path):
    results, _ = triage(
        tmp_path, {"IMG_0001.JPG": synthetic.make_clean_jpeg(restart_blocks=48)}
    )

    r = results["IMG_0001.JPG"]
    assert r["triage_class"] == "intact"
    assert r["defects"] == []


def test_invalid_huffman_code_is_located_and_not_structural(tmp_path):
    damaged, off = synthetic.invalid_huffman_run(synthetic.make_clean_jpeg())

    results, _ = triage(tmp_path, {"IMG_0001.JPG": damaged})

    r = results["IMG_0001.JPG"]
    assert r["triage_class"] == "visually_corrupted"
    assert set(defect_kinds(r)) == {"huffman_error", "decoder_warning"}
    assert off - 2 <= defect_kinds(r)["huffman_error"]["offset"] <= off + 8


def test_stray_marker_in_coded_data_is_located_and_not_structural(tmp_path):
    damaged, off = synthetic.stray_marker(synthetic.make_clean_jpeg())

    results, _ = triage(tmp_path, {"IMG_0001.JPG": damaged})

    r = results["IMG_0001.JPG"]
    assert r["triage_class"] == "visually_corrupted"
    assert defect_kinds(r)["stray_marker"]["offset"] == off


def test_metadata_salvageable_when_exif_parses(tmp_path):
    with_exif = synthetic.make_clean_jpeg(exif=True)
    without_exif = synthetic.make_clean_jpeg(exif=False)
    app1, _ = synthetic.segment(with_exif, 0xE1)
    broken = bytearray(with_exif)
    broken[app1 + 10 : app1 + 14] = b"\x00\x00\x00\x00"  # TIFF byte-order mark gone

    results, _ = triage(
        tmp_path,
        {"IMG_0001.JPG": with_exif, "IMG_0002.JPG": without_exif, "IMG_0003.JPG": bytes(broken)},
    )

    assert results["IMG_0001.JPG"]["flags"]["metadata_salvageable"] is True
    assert results["IMG_0002.JPG"]["flags"]["metadata_salvageable"] is False
    assert results["IMG_0003.JPG"]["flags"]["metadata_salvageable"] is False


def test_metadata_salvageable_does_not_depend_on_triage_class(tmp_path):
    damaged = synthetic.remove_segments(synthetic.make_clean_jpeg(exif=True), 0xC0)

    results, _ = triage(tmp_path, {"IMG_0001.JPG": damaged})

    r = results["IMG_0001.JPG"]
    assert r["triage_class"] == "unreadable"
    assert r["flags"]["metadata_salvageable"] is True


def test_altered_original_aborts_the_run_and_writes_nothing(tmp_path):
    clean = synthetic.make_clean_jpeg()
    manifest = synthetic.write_originals(
        tmp_path, {"IMG_0001.JPG": clean, "IMG_0002.JPG": clean}
    )
    altered = tmp_path / "originals" / "IMG_0002.JPG"
    altered.write_bytes(clean[:-1] + b"\x00")
    out = tmp_path / "out"

    with pytest.raises(IntegrityError, match="IMG_0002.JPG"):
        run_triage(tmp_path / "originals", manifest, out)

    assert not out.exists() or not any(out.iterdir())


def test_missing_original_aborts_the_run_and_writes_nothing(tmp_path):
    manifest = synthetic.write_originals(tmp_path, {"IMG_0001.JPG": synthetic.make_clean_jpeg()})
    (tmp_path / "originals" / "IMG_0001.JPG").unlink()
    out = tmp_path / "out"

    with pytest.raises(IntegrityError, match="IMG_0001.JPG"):
        run_triage(tmp_path / "originals", manifest, out)

    assert not out.exists() or not any(out.iterdir())


def test_contact_sheet_has_one_tile_per_original_grouped_by_class(tmp_path):
    clean = synthetic.make_clean_jpeg()
    files = {
        "IMG_0001.JPG": clean,
        "IMG_0002.JPG": synthetic.strip_eoi(clean),
        "IMG_0003.JPG": synthetic.remove_segments(clean, 0xC0),
        "IMG_0004.JPG": synthetic.invalid_huffman_run(clean)[0],
        "IMG_0005.JPG": synthetic.make_clean_jpeg(exif=False),
    }

    _, out = triage(tmp_path, files)

    sheet = ContactSheet.read(out / "contact-sheet.html")
    assert sheet.groups == {
        "unreadable": ["IMG_0003.JPG"],
        "structurally_damaged": ["IMG_0002.JPG"],
        "visually_corrupted": ["IMG_0004.JPG"],
        "intact": ["IMG_0001.JPG", "IMG_0005.JPG"],
    }
    assert sheet.order == ["unreadable", "structurally_damaged", "visually_corrupted", "intact"]
    assert "missing_eoi" in sheet.tile_text["IMG_0002.JPG"]
    assert "metadata salvageable" in sheet.tile_text["IMG_0001.JPG"]


class ContactSheet(HTMLParser):
    """Reads the Contact sheet's sections and tiles by their data attributes."""

    def __init__(self):
        super().__init__()
        self.order, self.groups, self.tile_text = [], {}, {}
        self.images, self.grey_outlines, self.placeholders = {}, set(), set()
        self.insets, self.disagreements = {}, set()
        self._section = self._tile = None
        self._depth = 0

    @classmethod
    def read(cls, path):
        sheet = cls()
        sheet.feed(path.read_text())
        return sheet

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if "data-class" in a:
            self._section = a["data-class"]
            self.order.append(self._section)
            self.groups[self._section] = []
        if "data-original" in a:
            self._tile = a["data-original"]
            self.groups[self._section].append(self._tile)
            self.tile_text[self._tile] = ""
            if "data-disagree" in a:
                self.disagreements.add(self._tile)
            self._depth = 0
        if self._tile:
            self._depth += 1
            if tag == "img" and "data-embedded-thumbnail" in a:
                self.insets[self._tile] = a.get("src")
            elif tag == "img":
                self.images[self._tile] = a.get("src")
            if "data-grey-fill" in a:
                self.grey_outlines.add(self._tile)
            if "placeholder" in a.get("class", "").split():
                self.placeholders.add(self._tile)

    def handle_endtag(self, tag):
        if self._tile:
            self._depth -= 1
            if self._depth == 0:
                self._tile = None

    def handle_data(self, data):
        if self._tile:
            self.tile_text[self._tile] += data


def test_run_writes_only_inside_the_output_directory(tmp_path):
    manifest = synthetic.write_originals(tmp_path, {"IMG_0001.JPG": synthetic.make_clean_jpeg()})
    before = {p: p.stat().st_mtime_ns for p in tmp_path.rglob("*")}

    run_triage(tmp_path / "originals", manifest, tmp_path / "out")

    after = {p: p.stat().st_mtime_ns for p in tmp_path.rglob("*")}
    changed = {p for p in after if before.get(p) != after[p]}
    assert all(p == tmp_path / "out" or (tmp_path / "out") in p.parents for p in changed)


def test_short_scan_is_structural_wherever_the_data_ends(tmp_path):
    clean = synthetic.make_clean_jpeg(optimize=True)
    cuts = {f"IMG_{k:04d}.JPG": synthetic.short_scan(clean, k / 400) for k in range(20, 380)}

    results, _ = triage(tmp_path, {name: data for name, (data, _) in cuts.items()})

    wrong = {
        name: sorted(defect_kinds(r))
        for name, r in results.items()
        if r["triage_class"] != "structurally_damaged" or "huffman_error" in defect_kinds(r)
    }
    assert wrong == {}


def test_truncation_after_a_corrupt_restart_interval_is_still_reported(tmp_path):
    clean = synthetic.make_clean_jpeg(restart_blocks=48)
    corrupt, _ = synthetic.invalid_huffman_run(clean)
    damaged, cut = synthetic.short_scan(corrupt, 0.75)

    results, _ = triage(tmp_path, {"IMG_0001.JPG": damaged})

    r = results["IMG_0001.JPG"]
    assert r["triage_class"] == "structurally_damaged"
    assert {"huffman_error", "short_scan"} <= set(defect_kinds(r))
    assert defect_kinds(r)["short_scan"]["offset"] == cut


def test_lost_restart_marker_is_an_entropy_defect_not_truncation(tmp_path):
    clean = synthetic.make_clean_jpeg(restart_blocks=48)
    damaged, off = synthetic.lose_restart_marker(clean, 2)

    results, _ = triage(tmp_path, {"IMG_0001.JPG": damaged})

    r = results["IMG_0001.JPG"]
    assert r["triage_class"] == "visually_corrupted"
    assert "short_scan" not in defect_kinds(r)
    assert defect_kinds(r)["restart_mismatch"]["offset"] > off


def test_restart_marker_without_restart_interval_is_a_stray_marker(tmp_path):
    clean = synthetic.make_clean_jpeg()
    start, end = synthetic.entropy_range(clean)
    off = (start + end) // 2
    while clean[off - 1] == 0xFF or clean[off + 2] == 0x00:
        off += 1
    damaged = clean[:off] + b"\xff\xd3" + clean[off + 2 :]

    results, _ = triage(tmp_path, {"IMG_0001.JPG": damaged})

    r = results["IMG_0001.JPG"]
    assert r["triage_class"] == "visually_corrupted"
    assert defect_kinds(r)["stray_marker"]["offset"] == off
    assert "short_scan" not in defect_kinds(r)


def test_exif_survives_an_overrunning_app1_length(tmp_path):
    damaged = synthetic.corrupt_app1_length(synthetic.make_clean_jpeg(exif=True))

    results, _ = triage(tmp_path, {"IMG_0001.JPG": damaged})

    r = results["IMG_0001.JPG"]
    assert "segment_overrun" in defect_kinds(r)
    assert r["flags"]["metadata_salvageable"] is True


def test_every_output_has_a_provenance_sidecar(tmp_path):
    original = synthetic.make_clean_jpeg()
    manifest = synthetic.write_originals(tmp_path, {"IMG_0001.JPG": original})
    out = tmp_path / "out"

    run_triage(tmp_path / "originals", manifest, out)

    outputs = [
        p for p in out.rglob("*") if p.is_file() and not p.name.endswith(".provenance.json")
    ]
    assert {str(p.relative_to(out)) for p in outputs} == {
        "report.json", "report.csv", "contact-sheet.html", "review.csv",
        "thumbs/IMG_0001.JPG.png",
    }
    for p in outputs:
        prov = json.loads(p.with_name(f"{p.name}.provenance.json").read_text())
        if p.parent.name == "thumbs":
            assert prov["source_sha256"] == hashlib.sha256(original).hexdigest()
        else:
            manifest_sha = hashlib.sha256(manifest.read_bytes()).hexdigest()
            assert prov["source_manifest_sha256"] == manifest_sha
        assert prov["contains_synthesised_pixels"] is False
        assert prov["tool"] == "canon_photo_recovery.triage"
        assert "git_commit" in prov and prov["transformations"]


# Seeds whose flips libjpeg-turbo reports but the parser's MCU walk does not.
DECODER_ONLY_FLIP_SEEDS = (0, 11)


def test_bit_flips_the_decoder_reports_are_visually_corrupted(tmp_path):
    clean = synthetic.make_clean_jpeg()
    files = {f"IMG_{s:04d}.JPG": synthetic.flip_bits(clean, s) for s in DECODER_ONLY_FLIP_SEEDS}

    results, _ = triage(tmp_path, files)

    for name in files:
        r = results[name]
        assert r["triage_class"] == "visually_corrupted", name
        assert "decoder_warning" in defect_kinds(r), name


def test_eighth_scale_decode_reports_the_same_warnings_as_full_scale(tmp_path):
    clean = synthetic.make_clean_jpeg()
    files = {
        "IMG_0001.JPG": clean,
        "IMG_0002.JPG": synthetic.strip_eoi(clean),
        "IMG_0003.JPG": synthetic.short_scan(clean)[0],
        "IMG_0004.JPG": synthetic.zero_fill(clean)[0],
        "IMG_0005.JPG": synthetic.invalid_huffman_run(clean)[0],
        "IMG_0006.JPG": synthetic.stray_marker(clean)[0],
        "IMG_0007.JPG": synthetic.remove_segments(clean, 0xDB),
        **{f"IMG_01{s:02d}.JPG": synthetic.flip_bits(clean, s) for s in range(12)},
    }
    manifest = synthetic.write_originals(tmp_path, files)

    def decoder_findings(scale):
        report = run_triage(tmp_path / "originals", manifest, tmp_path / scale.replace("/", "_"),
                            decode_scale=scale)
        return {
            o["name"]: (o["triage_class"], sorted(
                (d["kind"], d.get("detail")) for d in o["defects"]
                if d["kind"] in ("decoder_warning", "decode_failed")
            ))
            for o in report["originals"]
        }

    assert decoder_findings("1/8") == decoder_findings("1/1")


def test_grey_fill_starts_where_the_coded_data_ran_out(tmp_path):
    clean = synthetic.make_clean_jpeg()
    results, _ = triage(
        tmp_path,
        {"IMG_0001.JPG": clean, "IMG_0002.JPG": synthetic.truncate_mid_scan(clean)[0]},
    )

    assert results["IMG_0001.JPG"]["decoded"]["grey_fill"] is None

    r = results["IMG_0002.JPG"]
    fill, decoded = r["decoded"]["grey_fill"], r["decoded"]
    first = fill["from_y"] * decoded["width"] + fill["from_x"]
    # 4:2:2 at 1/8 scale: one MCU is two decoded pixels wide, one high
    assert 2 * r["mcus"]["decoded"] <= first <= 2 * (r["mcus"]["decoded"] + 1)
    assert fill["pixels"] == decoded["width"] * decoded["height"] - first


def test_contact_sheet_shows_decoded_thumbnails_with_grey_fill_outlined(tmp_path):
    clean = synthetic.make_clean_jpeg()
    files = {
        "IMG_0001.JPG": clean,
        "IMG_0002.JPG": synthetic.truncate_mid_scan(clean)[0],
        "IMG_0003.JPG": synthetic.remove_segments(clean, 0xC0),
    }

    _, out = triage(tmp_path, files)

    sheet = ContactSheet.read(out / "contact-sheet.html")
    assert set(sheet.images) == {"IMG_0001.JPG", "IMG_0002.JPG"}
    for src in sheet.images.values():
        assert (out / src).is_file()
    assert sheet.grey_outlines == {"IMG_0002.JPG"}
    assert sheet.placeholders == {"IMG_0003.JPG"}


def thumbnail(out, result):
    sheet = ContactSheet.read(out / "contact-sheet.html")
    return Image.open(out / urllib.parse.unquote(sheet.images[result["name"]]))


def test_thumbnails_never_contain_decoder_grey_fill(tmp_path):
    damaged, _ = synthetic.truncate_mid_scan(synthetic.make_clean_jpeg())

    results, out = triage(tmp_path, {"IMG_0001.JPG": damaged})

    r = results["IMG_0001.JPG"]
    thumb = thumbnail(out, r).convert("RGBA")
    alpha = np.asarray(thumb)[..., 3]
    fill_rows = r["decoded"]["grey_fill"]["from_y"] + 1  # first fully filled row
    first_full = int(np.ceil(fill_rows * thumb.height / r["decoded"]["height"])) + 1
    assert (alpha[first_full:] == 0).all()
    assert (alpha[0] == 255).all()
    sidecars = list((out / "thumbs").glob("*.provenance.json"))
    assert len(sidecars) == 1
    assert json.loads(sidecars[0].read_text())["contains_synthesised_pixels"] is False


def test_grey_fill_is_measured_in_whole_mcus_for_420(tmp_path):
    damaged, _ = synthetic.truncate_mid_scan(synthetic.make_clean_jpeg_420())

    results, _ = triage(tmp_path, {"IMG_0001.JPG": damaged})

    r = results["IMG_0001.JPG"]
    fill = r["decoded"]["grey_fill"]
    # 4:2:0 at 1/8: an MCU is 2x2 decoded pixels, 16 MCUs per MCU row
    mcu = r["mcus"]["decoded"]
    assert (fill["from_x"], fill["from_y"]) in {
        ((mcu % 16) * 2, (mcu // 16) * 2), (((mcu + 1) % 16) * 2, ((mcu + 1) // 16) * 2)
    }
    assert fill["mcu_height"] == 2


def test_all_grey_decode_is_unreadable(tmp_path):
    damaged = synthetic.empty_scan(synthetic.make_clean_jpeg())

    results, _ = triage(tmp_path, {"IMG_0001.JPG": damaged})

    assert results["IMG_0001.JPG"]["triage_class"] == "unreadable"


def test_header_notice_from_the_decoder_does_not_downgrade_an_intact_original(tmp_path):
    noticed = synthetic.jfif_revision(synthetic.make_clean_jpeg(), 2)

    results, _ = triage(tmp_path, {"IMG_0001.JPG": noticed})

    r = results["IMG_0001.JPG"]
    assert r["triage_class"] == "intact"
    assert set(defect_kinds(r)) == {"decoder_notice"}


def test_extraneous_bytes_before_a_restart_marker_are_not_placed_at_the_eoi(tmp_path):
    clean = synthetic.make_clean_jpeg(restart_blocks=48)
    damaged, _ = synthetic.padding_before_restart(clean, 2)

    results, _ = triage(tmp_path, {"IMG_0001.JPG": damaged})

    warnings = [
        d for d in results["IMG_0001.JPG"]["defects"]
        if d["kind"] == "decoder_warning" and "extraneous" in d["detail"]
    ]
    assert warnings and all(d["offset"] is None for d in warnings)


@pytest.mark.parametrize(("option", "value"), [("decode_scale", "0.125"), ("workers", 0)])
def test_invalid_options_are_rejected_before_anything_is_written(tmp_path, option, value):
    manifest = synthetic.write_originals(tmp_path, {"IMG_0001.JPG": synthetic.make_clean_jpeg()})

    with pytest.raises(ValueError, match=option):
        run_triage(tmp_path / "originals", manifest, tmp_path / "out", **{option: value})

    assert not (tmp_path / "out").exists()


def test_rerun_leaves_no_stale_thumbnails(tmp_path):
    clean = synthetic.make_clean_jpeg()
    manifest = synthetic.write_originals(tmp_path, {"IMG_0001.JPG": clean, "IMG_0002.JPG": clean})
    out = tmp_path / "out"
    run_triage(tmp_path / "originals", manifest, out)
    records = json.loads(manifest.read_text())
    records["files"] = records["files"][:1]
    manifest.write_text(json.dumps(records))

    run_triage(tmp_path / "originals", manifest, out)

    assert {p.name for p in (out / "thumbs").iterdir()} == {
        "IMG_0001.JPG.png", "IMG_0001.JPG.png.provenance.json"
    }


def test_original_names_with_folders_and_url_characters_get_working_thumbnails(tmp_path):
    clean = synthetic.make_clean_jpeg()
    manifest = synthetic.write_originals(tmp_path, {"IMG #1%.JPG": clean})
    out = tmp_path / "out"

    run_triage(tmp_path / "originals", manifest, out)

    src = ContactSheet.read(out / "contact-sheet.html").images["IMG #1%.JPG"]
    assert "#" not in src and " " not in src
    assert (out / urllib.parse.unquote(src)).is_file()


@pytest.mark.parametrize("marker", [0xC0, 0xC4, 0xDB, 0xE1])
def test_header_marker_bytes_inside_coded_data_are_entropy_damage(tmp_path, marker):
    damaged, off = synthetic.header_marker_in_scan(synthetic.make_clean_jpeg(), marker)

    results, _ = triage(tmp_path, {"IMG_0001.JPG": damaged})

    r = results["IMG_0001.JPG"]
    assert r["triage_class"] == "visually_corrupted"
    assert defect_kinds(r)["stray_marker"]["offset"] == off


def test_thumbnail_salvageable_when_the_embedded_thumbnail_decodes(tmp_path):
    clean = synthetic.make_clean_jpeg()
    thumb = synthetic.make_thumbnail_jpeg()
    corrupt_thumb, _ = synthetic.invalid_huffman_run(thumb)
    with_thumb = synthetic.with_embedded_thumbnail(clean, thumb)

    results, _ = triage(tmp_path, {
        "IMG_0001.JPG": with_thumb,
        "IMG_0002.JPG": synthetic.with_embedded_thumbnail(clean, corrupt_thumb),
        "IMG_0003.JPG": clean,
        "IMG_0004.JPG": synthetic.remove_segments(with_thumb, 0xC0),
    })

    flags = {name: r["flags"]["thumbnail_salvageable"] for name, r in results.items()}
    assert flags == {
        "IMG_0001.JPG": True, "IMG_0002.JPG": False, "IMG_0003.JPG": False, "IMG_0004.JPG": True
    }
    assert results["IMG_0004.JPG"]["triage_class"] == "unreadable"
    embedded = results["IMG_0001.JPG"]["embedded_thumbnail"]
    assert embedded["offset"] == synthetic.embedded_thumbnail_offset(with_thumb)
    assert embedded["length"] == len(thumb)
    assert (embedded["width"], embedded["height"]) == (64, 48)
    assert [results[n]["embedded_thumbnail"]["status"] for n in sorted(results)] == [
        "decodes", "damaged", "absent", "decodes"
    ]


def test_contact_sheet_shows_a_salvageable_embedded_thumbnail_as_a_labelled_inset(tmp_path):
    clean = synthetic.make_clean_jpeg()
    thumb = synthetic.make_thumbnail_jpeg()
    with_thumb = synthetic.with_embedded_thumbnail(clean, thumb)
    files = {
        "IMG_0001.JPG": with_thumb,
        "IMG_0002.JPG": synthetic.with_embedded_thumbnail(
            clean, synthetic.invalid_huffman_run(thumb)[0]
        ),
        "IMG_0003.JPG": synthetic.remove_segments(with_thumb, 0xC0),
    }

    _, out = triage(tmp_path, files)

    sheet = ContactSheet.read(out / "contact-sheet.html")
    assert set(sheet.insets) == {"IMG_0001.JPG", "IMG_0003.JPG"}
    assert sheet.placeholders == {"IMG_0003.JPG"}
    for name, src in sheet.insets.items():
        inset = out / urllib.parse.unquote(src)
        assert inset.read_bytes() == thumb
        prov = json.loads(inset.with_name(f"{inset.name}.provenance.json").read_text())
        assert prov["source_sha256"] == hashlib.sha256(files[name]).hexdigest()
        assert prov["contains_synthesised_pixels"] is False
    assert "Embedded thumbnail" in sheet.tile_text["IMG_0001.JPG"]


def test_csv_report_has_one_row_per_original_with_curated_columns(tmp_path):
    clean = synthetic.make_clean_jpeg(capture=True)
    damaged = synthetic.strip_eoi(clean)

    results, out = triage(tmp_path, {"IMG_0001.JPG": clean, "IMG_0002.JPG": damaged})

    with open(out / "report.csv", newline="") as f:
        rows = {row["name"]: row for row in csv.DictReader(f)}
    assert rows["IMG_0001.JPG"] == {
        "name": "IMG_0001.JPG",
        "triage_class": "intact",
        "human_class": "",
        "width": "256",
        "height": "192",
        "capture_time": "2001:02:03 04:05:06",
        "shutter_count": "",
        "lens": "Synthetic Lens",
        "embedded_thumbnail": "absent",
        "metadata_salvageable": "true",
        "thumbnail_salvageable": "false",
        "needs_eyes": "false",
        "needs_eyes_score": str(results["IMG_0001.JPG"]["needs_eyes"]["score"]),
        "defect_count": "0",
    }
    assert rows["IMG_0002.JPG"]["triage_class"] == "structurally_damaged"
    assert rows["IMG_0002.JPG"]["defect_count"] == str(len(results["IMG_0002.JPG"]["defects"]))


def test_report_holds_the_full_exif_and_the_metadata_tool_version(tmp_path):
    manifest = synthetic.write_originals(tmp_path, {"IMG_0001.JPG": synthetic.make_clean_jpeg()})

    report = run_triage(tmp_path / "originals", manifest, tmp_path / "out")

    assert report["metadata_tool"].startswith("exiftool ")
    metadata = report["originals"][0]["metadata"]
    assert metadata["IFD0:Make"] == "Synthetic"
    assert not any(k.startswith("System:") for k in metadata)


def test_gaps_in_the_sequence_numbers_are_listed_as_absent_from_copy(tmp_path):
    clean = synthetic.make_clean_jpeg()
    names = ["IMG_0003.JPG", "IMG_0004.JPG", "IMG_0007.JPG", "IMG_0010.JPG", "notes.JPG"]
    manifest = synthetic.write_originals(tmp_path, dict.fromkeys(names, clean))
    out = tmp_path / "out"

    report = run_triage(tmp_path / "originals", manifest, out)

    assert report["absent_from_copy"] == [5, 6, 8, 9]
    page = (out / "contact-sheet.html").read_text()
    assert "Absent from copy" in page
    assert all(f"IMG_{n:04d}" in page for n in (5, 6, 8, 9))


def test_reports_are_byte_identical_across_runs_serial_or_parallel(tmp_path):
    clean = synthetic.make_clean_jpeg(capture=True)
    files = {
        "IMG_0001.JPG": clean,
        "IMG_0002.JPG": synthetic.strip_eoi(clean),
        "IMG_0003.JPG": synthetic.invalid_huffman_run(clean)[0],
        "IMG_0005.JPG": synthetic.truncate_mid_scan(clean)[0],
        "IMG_0006.JPG": synthetic.with_embedded_thumbnail(clean, synthetic.make_thumbnail_jpeg()),
    }
    manifest = synthetic.write_originals(tmp_path, files)

    run_triage(tmp_path / "originals", manifest, tmp_path / "a", workers=1)
    run_triage(tmp_path / "originals", manifest, tmp_path / "b", workers=4)

    for name in ("report.json", "report.csv", "contact-sheet.html"):
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes(), name


def read_review(out):
    with open(out / "review.csv", newline="") as f:
        return list(csv.DictReader(f))


def test_first_run_creates_an_empty_review_file_with_one_row_per_original(tmp_path):
    clean = synthetic.make_clean_jpeg()

    _, out = triage(tmp_path, {"IMG_0002.JPG": clean, "IMG_0001.JPG": clean})

    assert read_review(out) == [
        {"name": "IMG_0001.JPG", "human_class": "", "note": ""},
        {"name": "IMG_0002.JPG", "human_class": "", "note": ""},
    ]


def write_review(out, rows, delimiter=","):
    with open(out / "review.csv", "w", newline="") as f:
        w = csv.writer(f, delimiter=delimiter)
        w.writerow(["name", "human_class", "note"])
        w.writerows(rows)


def test_review_edits_survive_a_rerun_and_appear_as_human_class(tmp_path):
    clean = synthetic.make_clean_jpeg()
    files = {"IMG_0001.JPG": clean, "IMG_0002.JPG": synthetic.strip_eoi(clean)}
    manifest = synthetic.write_originals(tmp_path, files)
    out = tmp_path / "out"
    run_triage(tmp_path / "originals", manifest, out)
    write_review(out, [
        ["IMG_0001.JPG", "Visually corrupted", "colour cast on the left"],
        ["IMG_0002.JPG", "", ""],
    ])
    edited = (out / "review.csv").read_bytes()

    report = run_triage(tmp_path / "originals", manifest, out)

    assert (out / "review.csv").read_bytes() == edited
    results = {o["name"]: o for o in report["originals"]}
    assert (results["IMG_0001.JPG"]["triage_class"], results["IMG_0001.JPG"]["human_class"]) == (
        "intact", "visually_corrupted"
    )
    assert results["IMG_0001.JPG"]["review_note"] == "colour cast on the left"
    assert results["IMG_0002.JPG"]["human_class"] is None
    assert report["review"] == {"reviewed": 1, "disagreements": 1, "warnings": []}
    with open(out / "report.csv", newline="") as f:
        rows = {row["name"]: row for row in csv.DictReader(f)}
    assert rows["IMG_0001.JPG"]["human_class"] == "visually_corrupted"


def test_review_file_saved_by_a_spreadsheet_with_semicolons_is_read(tmp_path):
    manifest = synthetic.write_originals(tmp_path, {"IMG_0001.JPG": synthetic.make_clean_jpeg()})
    out = tmp_path / "out"
    out.mkdir()
    write_review(out, [["IMG_0001.JPG", "intact", "ok; checked"]], delimiter=";")

    report = run_triage(tmp_path / "originals", manifest, out)

    assert report["originals"][0]["human_class"] == "intact"
    assert report["originals"][0]["review_note"] == "ok; checked"


def test_invalid_human_class_is_reported_without_aborting_the_run(tmp_path):
    clean = synthetic.make_clean_jpeg()
    manifest = synthetic.write_originals(tmp_path, {"IMG_0001.JPG": clean, "IMG_0002.JPG": clean})
    out = tmp_path / "out"
    out.mkdir()
    write_review(out, [
        ["IMG_0001.JPG", "looks fine", ""],
        ["IMG_0002.JPG", "intact", ""],
        ["IMG_0099.JPG", "intact", ""],
        ["IMG_0002.JPG", "unreadable", ""],
    ])

    report = run_triage(tmp_path / "originals", manifest, out)

    results = {o["name"]: o for o in report["originals"]}
    assert results["IMG_0001.JPG"]["human_class"] is None
    assert results["IMG_0002.JPG"]["human_class"] == "intact"
    warnings = report["review"]["warnings"]
    assert len(warnings) == 3
    assert "line 2" in warnings[0] and "'looks fine'" in warnings[0]
    assert "IMG_0099.JPG" in warnings[1]
    assert "line 5" in warnings[2] and "again" in warnings[2]


def test_tiles_show_the_human_class_and_highlight_disagreements(tmp_path):
    clean = synthetic.make_clean_jpeg()
    files = {"IMG_0001.JPG": clean, "IMG_0002.JPG": clean, "IMG_0003.JPG": clean}
    manifest = synthetic.write_originals(tmp_path, files)
    out = tmp_path / "out"
    out.mkdir()
    write_review(out, [["IMG_0001.JPG", "intact", ""], ["IMG_0002.JPG", "unreadable", ""]])

    run_triage(tmp_path / "originals", manifest, out)

    sheet = ContactSheet.read(out / "contact-sheet.html")
    assert "Human class: Intact" in sheet.tile_text["IMG_0001.JPG"]
    assert "Human class: Unreadable" in sheet.tile_text["IMG_0002.JPG"]
    assert "Human class" not in sheet.tile_text["IMG_0003.JPG"]
    assert sheet.disagreements == {"IMG_0002.JPG"}


def test_review_file_from_a_spreadsheet_keeps_multiline_notes_and_line_numbers(tmp_path):
    clean = synthetic.make_clean_jpeg()
    manifest = synthetic.write_originals(tmp_path, {"IMG_0001.JPG": clean, "IMG_0002.JPG": clean})
    out = tmp_path / "out"
    out.mkdir()
    (out / "review.csv").write_bytes(
        'name,human_class,note\r\nIMG_0001.JPG,intact,"first\r\nsecond é"\r\n'
        "IMG_0002.JPG,bad,\r\n".encode("cp1252")
    )

    report = run_triage(tmp_path / "originals", manifest, out)

    results = {o["name"]: o for o in report["originals"]}
    assert results["IMG_0001.JPG"]["review_note"] == "first\r\nsecond é"
    assert report["review"]["warnings"] == [
        "review.csv line 4: 'bad' is not a Triage class"
        + " (unreadable, structurally_damaged, visually_corrupted, intact)"
    ]


def test_invalid_human_class_keeps_the_note_and_unlisted_originals_are_reported(tmp_path):
    clean = synthetic.make_clean_jpeg()
    manifest = synthetic.write_originals(tmp_path, {"IMG_0001.JPG": clean, "IMG_0002.JPG": clean})
    out = tmp_path / "out"
    out.mkdir()
    write_review(out, [["IMG_0001.JPG", "maybe", "strip near the top"]])

    report = run_triage(tmp_path / "originals", manifest, out)

    r = report["originals"][0]
    assert (r["human_class"], r["review_note"]) == (None, "strip near the top")
    assert any("no row for 1 Originals: IMG_0002.JPG" in w for w in report["review"]["warnings"])


def test_review_file_without_the_expected_columns_is_reported_not_fatal(tmp_path):
    manifest = synthetic.write_originals(tmp_path, {"IMG_0001.JPG": synthetic.make_clean_jpeg()})
    out = tmp_path / "out"
    out.mkdir()
    (out / "review.csv").write_text("file,verdict\nIMG_0001.JPG,intact\n")

    report = run_triage(tmp_path / "originals", manifest, out)

    assert report["originals"][0]["human_class"] is None
    assert report["review"]["warnings"] == [
        "review.csv: needs the columns name, human_class, note"
    ]


def silent_corruption_files():
    base = synthetic.make_pixels()
    return {
        "IMG_0001.JPG": synthetic.encode(base),
        "IMG_0002.JPG": synthetic.encode(synthetic.displaced_strip(base)),
        "IMG_0003.JPG": synthetic.encode(synthetic.colour_discontinuity(base)),
        "IMG_0004.JPG": synthetic.encode(synthetic.spliced_band(base)),
    }


def test_silent_corruption_scores_above_the_needs_eyes_threshold(tmp_path):
    results, _ = triage(tmp_path, silent_corruption_files())

    flagged = {name: r["flags"]["needs_eyes"] for name, r in results.items()}
    assert flagged == {
        "IMG_0001.JPG": False, "IMG_0002.JPG": True, "IMG_0003.JPG": True, "IMG_0004.JPG": True
    }
    assert results["IMG_0001.JPG"]["needs_eyes"]["score"] < 1
    assert all(results[n]["needs_eyes"]["score"] >= 1 for n in flagged if flagged[n])
    # the damage is invisible to the decoder: every file is Intact
    assert {r["triage_class"] for r in results.values()} == {"intact"}


def test_spliced_band_is_recorded_as_foreign_data_with_its_rows(tmp_path):
    results, _ = triage(tmp_path, silent_corruption_files())

    foreign = {
        name: [d["rows"] for d in r["defects"] if d["kind"] == "foreign_data"]
        for name, r in results.items()
    }
    # spliced_band replaces rows 160..223 of the Main image
    assert foreign == {
        "IMG_0001.JPG": [], "IMG_0002.JPG": [], "IMG_0003.JPG": [], "IMG_0004.JPG": [[160, 223]]
    }


def test_needs_eyes_checks_never_change_the_triage_class(tmp_path):
    clean = synthetic.make_clean_jpeg()
    files = {
        **silent_corruption_files(),
        "IMG_0005.JPG": synthetic.truncate_mid_scan(clean)[0],
        "IMG_0006.JPG": synthetic.invalid_huffman_run(clean)[0],
        "IMG_0007.JPG": synthetic.remove_segments(clean, 0xC0),
        "IMG_0008.JPG": synthetic.strip_eoi(clean),
    }
    manifest = synthetic.write_originals(tmp_path, files)

    def classes(needs_eyes):
        report = run_triage(tmp_path / "originals", manifest, tmp_path / str(needs_eyes),
                            needs_eyes=needs_eyes)
        return {o["name"]: o["triage_class"] for o in report["originals"]}

    assert classes(True) == classes(False)


def test_contact_sheet_puts_flagged_and_high_scoring_originals_first(tmp_path):
    files = silent_corruption_files()

    results, out = triage(tmp_path, files)

    order = ContactSheet.read(out / "contact-sheet.html").groups["intact"]
    scores = [results[n]["needs_eyes"]["score"] for n in order]
    assert order[-1] == "IMG_0001.JPG"
    assert scores == sorted(scores, reverse=True)
    assert "needs eyes" in ContactSheet.read(out / "contact-sheet.html").tile_text["IMG_0004.JPG"]


def test_mid_image_grey_fill_is_hidden_in_thumbnails_and_raises_needs_eyes_score(tmp_path):
    damaged = synthetic.lose_interval_data(synthetic.make_clean_jpeg(restart_blocks=48), 2)

    results, out = triage(tmp_path, {"IMG_0001.JPG": damaged})

    r = results["IMG_0001.JPG"]
    assert r["decoded"]["grey_fill"] is None  # the image ends with decoded pixels
    (run,) = r["decoded"]["mid_image_grey"]
    # 4:2:2 at 1/8: an MCU is 2x1 decoded pixels, 16 MCUs per row; the lost interval
    # is MCUs 144..191, all but the first few of which are grey
    assert 144 <= run["first_mcu"] and run["first_mcu"] + run["mcus"] == 192
    assert r["needs_eyes"]["score"] >= 1
    # rows on either side of the grey fill are never compared with each other
    assert not [b for b in r["needs_eyes"]["breaks"] if 72 <= b["row"] <= 96]
    thumb = np.asarray(thumbnail(out, r).convert("RGBA"))
    assert thumb.shape[:2] == (r["decoded"]["height"], r["decoded"]["width"])
    alpha = thumb[..., 3]
    assert (alpha[10:12] == 0).all() and (alpha[:9] == 255).all() and (alpha[12:] == 255).all()


def test_breaks_are_told_apart_by_kind(tmp_path):
    results, _ = triage(tmp_path, silent_corruption_files())

    kinds = {
        name: [(b["row"], b["kind"]) for b in r["needs_eyes"]["breaks"]]
        for name, r in results.items()
    }
    assert kinds == {
        "IMG_0001.JPG": [],
        "IMG_0002.JPG": [(128, "shift"), (192, "shift")],
        "IMG_0003.JPG": [(200, "offset")],
        "IMG_0004.JPG": [(160, "content"), (224, "content")],
    }


def test_pixel_checks_run_only_at_the_scale_they_were_calibrated_at(tmp_path):
    manifest = synthetic.write_originals(tmp_path, silent_corruption_files())

    report = run_triage(tmp_path / "originals", manifest, tmp_path / "out", decode_scale="1/4")

    assert all(o["needs_eyes"] is None for o in report["originals"])
    assert not any(o["flags"]["needs_eyes"] for o in report["originals"])
