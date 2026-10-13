"""Whether an Original's EXIF metadata can be read (Metadata salvageable)."""

from canon_photo_recovery.jpeg import Structure


def metadata_salvageable(data: bytes, structure: Structure) -> bool:
    """True when the EXIF block has a valid TIFF header and an in-bounds, non-empty IFD0."""
    if structure.exif is None:
        return False
    off, length = structure.exif
    tiff = data[off + 6 : off + length]
    if len(tiff) < 8 or tiff[:2] not in (b"II", b"MM"):
        return False
    order = "little" if tiff[:2] == b"II" else "big"
    if int.from_bytes(tiff[2:4], order) != 42:
        return False
    ifd0 = int.from_bytes(tiff[4:8], order)
    if ifd0 < 8 or ifd0 + 2 > len(tiff):
        return False
    entries = int.from_bytes(tiff[ifd0 : ifd0 + 2], order)
    return 0 < entries and ifd0 + 2 + 12 * entries <= len(tiff)
