"""An Original's metadata: the full exiftool read, whether the EXIF block parses
(Metadata salvageable), and where its Embedded thumbnail sits."""

import functools
import json
import shutil
import subprocess

from canon_photo_recovery.jpeg import Structure


def _tiff(data: bytes, structure: Structure) -> tuple[bytes, int, str] | None:
    """(TIFF block, its absolute offset, byte order) when the header is valid."""
    if structure.exif is None:
        return None
    off, length = structure.exif
    tiff = data[off + 6 : off + length]
    if len(tiff) < 8 or tiff[:2] not in (b"II", b"MM"):
        return None
    order = "little" if tiff[:2] == b"II" else "big"
    if int.from_bytes(tiff[2:4], order) != 42:
        return None
    return tiff, off + 6, order


def _ifd(tiff: bytes, at: int, order: str) -> dict[int, tuple[int, int, bytes]] | None:
    """{tag: (type, count, 4-byte value field)}, or None when the IFD is empty or
    its entries run past the TIFF block."""
    if at < 8 or at + 2 > len(tiff):
        return None
    n = int.from_bytes(tiff[at : at + 2], order)
    end = at + 2 + 12 * n
    if n == 0 or end > len(tiff):
        return None
    entries = {}
    for k in range(n):
        e = tiff[at + 2 + 12 * k : at + 14 + 12 * k]
        entries[int.from_bytes(e[0:2], order)] = (
            int.from_bytes(e[2:4], order), int.from_bytes(e[4:8], order), e[8:12]
        )
    return entries


def _next_ifd(tiff: bytes, at: int, order: str) -> int:
    """The pointer that follows the IFD at `at`, or 0 when it is cut off."""
    end = at + 2 + 12 * int.from_bytes(tiff[at : at + 2], order)
    return int.from_bytes(tiff[end : end + 4], order) if end + 4 <= len(tiff) else 0


def _value(entry: tuple[int, int, bytes], order: str) -> int | None:
    typ, count, raw = entry
    if count != 1 or typ not in (3, 4):
        return None
    return int.from_bytes(raw[:2] if typ == 3 else raw, order)


def metadata_salvageable(data: bytes, structure: Structure) -> bool:
    """True when the EXIF block has a valid TIFF header and an in-bounds, non-empty IFD0."""
    found = _tiff(data, structure)
    if found is None:
        return False
    tiff, _, order = found
    return _ifd(tiff, int.from_bytes(tiff[4:8], order), order) is not None


def embedded_thumbnail(data: bytes, structure: Structure) -> tuple[int, int] | str:
    """(absolute offset, length) of the Embedded thumbnail that IFD1 points at, or
    a status: "absent" when no IFD1 thumbnail is declared, "malformed" when its
    offset or length tag cannot be read or the length is zero, "out_of_bounds"
    when the declared bytes are not all inside the EXIF block."""
    found = _tiff(data, structure)
    if found is None:
        return "absent"
    tiff, base, order = found
    at = int.from_bytes(tiff[4:8], order)
    if _ifd(tiff, at, order) is None:
        return "absent"
    ifd1 = _ifd(tiff, _next_ifd(tiff, at, order), order)
    if ifd1 is None or 0x0201 not in ifd1 or 0x0202 not in ifd1:
        return "absent"
    start, length = _value(ifd1[0x0201], order), _value(ifd1[0x0202], order)
    if start is None or not length:
        return "malformed"
    if start < 8 or start + length > len(tiff):
        return "out_of_bounds"
    return base + start, length


def check_tool() -> None:
    if shutil.which("exiftool") is None:
        raise RuntimeError("exiftool is not on PATH")


@functools.cache
def tool_version() -> str:
    p = subprocess.run(["exiftool", "-ver"], capture_output=True, text=True, check=False)
    return f"exiftool {p.stdout.strip()}"


def read_metadata(data: bytes) -> dict:
    """Every tag exiftool reads from the Original's bytes, keyed `Group:Tag`.

    The bytes go in on stdin so no file-system tags (dates, permissions) leak
    in, keeping the report identical across runs. `-G1:4` keeps repeated tags
    (as `Group:CopyN:Tag`), which plain `-G1` JSON output silently drops.
    """
    p = subprocess.run(
        ["exiftool", "-j", "-G1:4", "-a", "--System:all", "--ExifTool:ExifToolVersion", "-"],
        input=data, capture_output=True, check=False,
    )
    try:
        tags = json.loads(p.stdout)[0]
    except (ValueError, IndexError):
        return {"ExifTool:Error": p.stderr.decode(errors="replace").strip() or "no output"}
    tags.pop("SourceFile", None)
    return tags
