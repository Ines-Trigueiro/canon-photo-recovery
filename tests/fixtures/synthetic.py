"""Synthetic JPEG fixtures and documented ways of damaging them.

Every fixture is generated at test time from a deterministic synthetic image;
no real photo bytes ever enter the test tree. Each damage function documents
exactly what it does to the clean bytes so tests can assert offsets.
"""

import hashlib
import io
import json
import pathlib

import numpy as np
from PIL import Image

SOI = b"\xff\xd8"
EOI = b"\xff\xd9"


def make_clean_jpeg(
    *, exif: bool = True, restart_blocks: int = 0, optimize: bool = False, capture: bool = False
) -> bytes:
    """A 256x192 baseline JPEG of a gradient plus seeded noise (~50 KB).

    `capture` adds an invented capture time and lens name to the EXIF block.
    """
    rng = np.random.default_rng(750)
    y, x = np.mgrid[0:192, 0:256]
    base = np.stack([x, y, (x + y) // 2], axis=-1).astype(np.int16)
    noise = rng.integers(-40, 40, size=base.shape, dtype=np.int16)
    pixels = np.clip(base + noise, 0, 255).astype(np.uint8)
    img = Image.fromarray(pixels, "RGB")
    kwargs = {"quality": 92, "subsampling": "4:2:2", "optimize": optimize}
    if exif:
        e = Image.Exif()
        e[0x010F] = "Synthetic"  # Make
        e[0x0110] = "Fixture"  # Model
        if capture:
            e.get_ifd(0x8769)[0x9003] = "2001:02:03 04:05:06"  # DateTimeOriginal
            e.get_ifd(0x8769)[0xA434] = "Synthetic Lens"  # LensModel
        kwargs["exif"] = e.tobytes()
    if restart_blocks:
        kwargs["restart_marker_blocks"] = restart_blocks
    buf = io.BytesIO()
    img.save(buf, "JPEG", **kwargs)
    return buf.getvalue()


def segments(data: bytes) -> list[tuple[int, int, int]]:
    """(marker, offset, total_length) for every header segment up to and including SOS."""
    out = []
    pos = 2
    while pos < len(data):
        marker = data[pos + 1]
        length = int.from_bytes(data[pos + 2 : pos + 4], "big")
        out.append((marker, pos, 2 + length))
        pos += 2 + length
        if marker == 0xDA:
            break
    return out


def segment(data: bytes, marker: int) -> tuple[int, int]:
    """(offset, total_length) of the first segment with this marker."""
    for m, off, length in segments(data):
        if m == marker:
            return off, length
    raise ValueError(f"no FF{marker:02X} segment")


def entropy_range(data: bytes) -> tuple[int, int]:
    """(start, end) of the entropy-coded data: from after the SOS header to the EOI."""
    off, length = segment(data, 0xDA)
    assert data.endswith(EOI)
    return off + length, len(data) - 2


def remove_segments(data: bytes, marker: int) -> bytes:
    """Delete every header segment with this marker."""
    out = bytearray(data)
    for m, off, length in reversed(segments(data)):
        if m == marker:
            del out[off : off + length]
    return bytes(out)


def strip_eoi(data: bytes) -> bytes:
    """Drop the final two bytes (the EOI marker); the entropy data stays complete."""
    assert data.endswith(EOI)
    return data[:-2]


def truncate_mid_scan(data: bytes) -> tuple[bytes, int]:
    """Cut the file halfway through the entropy-coded data. Returns (bytes, cut offset)."""
    start, end = entropy_range(data)
    cut = _safe_cut(data, (start + end) // 2)
    return data[:cut], cut


def short_scan(data: bytes, fraction: float = 0.5) -> tuple[bytes, int]:
    """Cut the entropy data at `fraction` of its length, then append EOI:
    well-formed tail, missing MCUs. Returns (bytes, cut offset)."""
    start, end = entropy_range(data)
    cut = _safe_cut(data, start + int((end - start) * fraction))
    return data[:cut] + EOI, cut


def restart_offsets(data: bytes) -> list[int]:
    """Offsets of every FFD0-FFD7 inside the entropy-coded data."""
    start, end = entropy_range(data)
    return [i for i in range(start, end - 1) if data[i] == 0xFF and 0xD0 <= data[i + 1] <= 0xD7]


def lose_restart_marker(data: bytes, index: int) -> tuple[bytes, int]:
    """Overwrite the index-th restart marker with two zero bytes, merging two intervals."""
    off = restart_offsets(data)[index]
    return data[:off] + b"\x00\x00" + data[off + 2 :], off


def corrupt_app1_length(data: bytes) -> bytes:
    """Set the EXIF APP1 segment length to 0xFFFF so it claims to run past the end of file."""
    off, _ = segment(data, 0xE1)
    return data[: off + 2] + b"\xff\xff" + data[off + 4 :]


def zero_fill(data: bytes, length: int = 1024) -> tuple[bytes, int]:
    """Overwrite `length` bytes mid-scan with 0x00, bordered by non-zero bytes."""
    start, end = entropy_range(data)
    off = (start + end) // 3
    while data[off - 1] in (0x00, 0xFF) or data[off + length] == 0x00:
        off += 1
    return data[:off] + bytes(length) + data[off + length :], off


def _safe_cut(data: bytes, pos: int) -> int:
    """Move a cut point so it never splits an FF xx pair."""
    while data[pos - 1] == 0xFF:
        pos += 1
    return pos


def write_originals(root: pathlib.Path, files: dict[str, bytes]) -> pathlib.Path:
    """Write files into root/originals plus a baseline manifest; return the manifest path."""
    originals = root / "originals"
    originals.mkdir(parents=True)
    records = []
    for name, data in sorted(files.items()):
        (originals / name).write_bytes(data)
        records.append({"path": name, "sha256": hashlib.sha256(data).hexdigest()})
    manifest = root / "manifest.json"
    manifest.write_text(
        json.dumps({"root": "originals", "count": len(records), "files": records})
    )
    return manifest


def invalid_huffman_run(data: bytes) -> tuple[bytes, int]:
    """Overwrite 8 bytes mid-scan with FF00 x4, i.e. 32 consecutive one-bits once
    destuffed; no Huffman code is all ones, so decoding must fail here."""
    start, end = entropy_range(data)
    off = _safe_cut(data, (start + end) // 2)
    return data[:off] + b"\xff\x00" * 4 + data[off + 8 :], off


def stray_marker(data: bytes) -> tuple[bytes, int]:
    """Replace two mid-scan bytes with FF 3A, a marker that cannot occur in coded data."""
    start, end = entropy_range(data)
    off = _safe_cut(data, (start + end) // 2)
    while data[off + 1] == 0xFF or data[off + 2] == 0x00:
        off += 1
    return data[:off] + b"\xff\x3a" + data[off + 2 :], off


def flip_bits(data: bytes, seed: int, count: int = 3) -> bytes:
    """Flip `count` seeded single bits mid-scan, never creating or touching an FF byte."""
    import random

    rng = random.Random(seed)
    start, end = entropy_range(data)
    out = bytearray(data)
    for _ in range(count):
        off = rng.randrange(start + 100, end - 100)
        while out[off] == 0xFF or out[off - 1] == 0xFF:
            off += 1
        out[off] ^= 1 << rng.randrange(8)
        if out[off] == 0xFF:
            out[off] ^= 1
    return bytes(out)


def empty_scan(data: bytes) -> bytes:
    """Keep the scan header but drop every byte of entropy-coded data."""
    sos, length = segment(data, 0xDA)
    return data[: sos + length] + EOI


def jfif_revision(data: bytes, major: int) -> bytes:
    """Set the JFIF major version in APP0 (a header detail libjpeg merely warns about)."""
    app0, _ = segment(data, 0xE0)
    out = bytearray(data)
    out[app0 + 9] = major
    return bytes(out)


def padding_before_restart(data: bytes, index: int, count: int = 5) -> tuple[bytes, int]:
    """Insert `count` 0x55 bytes just before the index-th restart marker; returns the
    marker's new offset."""
    off = restart_offsets(data)[index]
    return data[:off] + b"\x55" * count + data[off:], off + count


def make_clean_jpeg_420() -> bytes:
    """Like make_clean_jpeg but 4:2:0, so one MCU spans two decoded rows at 1/8 scale."""
    import io

    from PIL import Image

    clean = Image.open(io.BytesIO(make_clean_jpeg(exif=False))).convert("RGB")
    buf = io.BytesIO()
    clean.save(buf, "JPEG", quality=92, subsampling="4:2:0")
    return buf.getvalue()


def header_marker_in_scan(data: bytes, marker: int) -> tuple[bytes, int]:
    """Replace two mid-scan bytes with FF <marker>, as corrupted coded data can,
    without the segment that a real marker of that type would introduce."""
    start, end = entropy_range(data)
    off = _safe_cut(data, (start + end) // 2)
    while data[off + 1] == 0xFF or data[off + 2] == 0x00:
        off += 1
    return data[:off] + bytes([0xFF, marker]) + data[off + 2 :], off


def make_thumbnail_jpeg() -> bytes:
    """A 64x48 baseline JPEG with no EXIF, standing in for an Embedded thumbnail."""
    clean = Image.open(io.BytesIO(make_clean_jpeg(exif=False))).convert("RGB")
    buf = io.BytesIO()
    clean.resize((64, 48)).save(buf, "JPEG", quality=85)
    return buf.getvalue()


def with_embedded_thumbnail(data: bytes, thumb: bytes) -> bytes:
    """Replace any EXIF APP1 with one whose IFD1 points at `thumb` as the
    Embedded thumbnail, inserted right after the APP0 segment.

    Big-endian TIFF laid out as: header (8) | IFD0, one entry: Make (18) |
    "Synthetic\\0" (10) | IFD1: Compression, JPEGInterchangeFormat(Length) (42) |
    thumbnail bytes at TIFF offset 78.
    """
    def entry(tag, typ, count, value):
        v = value.to_bytes(2, "big") + b"\0\0" if typ == 3 else value.to_bytes(4, "big")
        return tag.to_bytes(2, "big") + typ.to_bytes(2, "big") + count.to_bytes(4, "big") + v

    make = b"Synthetic\0"
    tiff = b"MM\x00\x2a" + (8).to_bytes(4, "big")
    tiff += (1).to_bytes(2, "big") + entry(0x010F, 2, len(make), 26) + (36).to_bytes(4, "big")
    tiff += make
    tiff += (3).to_bytes(2, "big") + entry(0x0103, 3, 1, 6) + entry(0x0201, 4, 1, 78)
    tiff += entry(0x0202, 4, 1, len(thumb)) + (0).to_bytes(4, "big")
    assert len(tiff) == 78
    payload = b"Exif\0\0" + tiff + thumb
    app1 = b"\xff\xe1" + (2 + len(payload)).to_bytes(2, "big") + payload
    data = remove_segments(data, 0xE1)
    app0, length = segment(data, 0xE0)
    return data[: app0 + length] + app1 + data[app0 + length :]


def embedded_thumbnail_offset(data: bytes) -> int:
    """Absolute file offset of the Embedded thumbnail written by with_embedded_thumbnail."""
    app1, _ = segment(data, 0xE1)
    return app1 + 4 + 6 + 78


# --- Silent corruption ------------------------------------------------------
#
# Damage applied to the pixels before encoding, so the JPEG decodes without a
# single warning yet looks wrong: what Needs eyes exists to catch. Boundaries
# are multiples of 8 rows, one MCU row for 4:2:2, one decoded row at 1/8 scale.

def make_pixels(seed: int = 750, width: int = 512, height: int = 384, flip: bool = False):
    """A gradient plus seeded noise; `flip` runs the gradients the other way."""
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:height, 0:width]
    x, y = x * 255 // (width - 1), y * 255 // (height - 1)
    if flip:
        x, y = 255 - y, 255 - x
    base = np.stack([x, y, (x + y) // 2], axis=-1).astype(np.int16)
    noise = rng.integers(-40, 40, size=base.shape, dtype=np.int16)
    return np.clip(base + noise, 0, 255).astype(np.uint8)


def encode(pixels) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(pixels, "RGB").save(buf, "JPEG", quality=92, subsampling="4:2:2")
    return buf.getvalue()


def displaced_strip(pixels, top: int = 128, bottom: int = 192, shift: int = 96):
    """Rows [top, bottom) moved `shift` pixels to the right, wrapping round."""
    out = pixels.copy()
    out[top:bottom] = np.roll(pixels[top:bottom], shift, axis=1)
    return out


def colour_discontinuity(pixels, row: int = 200, offset: int = 70):
    """Every row from `row` down has `offset` added to its red channel, as a lost
    DC predictor does."""
    out = pixels.astype(np.int16)
    out[row:, :, 0] += offset
    return np.clip(out, 0, 255).astype(np.uint8)


def spliced_band(pixels, top: int = 160, bottom: int = 224):
    """Rows [top, bottom) replaced by the same rows of a different synthetic image."""
    out = pixels.copy()
    out[top:bottom] = make_pixels(seed=97, width=pixels.shape[1], height=pixels.shape[0],
                                  flip=True)[top:bottom]
    return out


def lose_interval_data(data: bytes, index: int, keep: int = 10) -> bytes:
    """Drop all but the first `keep` coded bytes of the restart interval that follows
    the index-th restart marker; libjpeg grey-fills the rest of that interval and
    resumes at the next marker, leaving decoder grey fill mid-image."""
    r = restart_offsets(data)
    return data[: r[index] + 2 + keep] + data[r[index + 1] :]
