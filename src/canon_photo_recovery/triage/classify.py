"""Map an Original's Defects to its Triage class (precedence per ADR-0001)."""

from enum import StrEnum

from canon_photo_recovery.jpeg import Defect


class TriageClass(StrEnum):
    UNREADABLE = "unreadable"
    STRUCTURALLY_DAMAGED = "structurally_damaged"
    VISUALLY_CORRUPTED = "visually_corrupted"
    INTACT = "intact"


# Faults in the entropy-coded data rather than the container.
ENTROPY = {"stray_marker", "huffman_error", "restart_mismatch", "decoder_warning"}
# Recorded for the reviewer, but say nothing about the pixels.
INFORMATIONAL = {"decoder_notice"}


def classify(defects: list[Defect], *, nothing_decoded: bool) -> TriageClass:
    """`defects` is every Defect found in the Original, the parser's and the decoder's.

    Only the decoder decides Unreadable (ADR-0001): nothing decoded, or nothing but
    decoder grey fill.
    """
    kinds = {d.kind for d in defects} - INFORMATIONAL
    if nothing_decoded:
        return TriageClass.UNREADABLE
    if kinds - ENTROPY:
        return TriageClass.STRUCTURALLY_DAMAGED
    if kinds:
        return TriageClass.VISUALLY_CORRUPTED
    return TriageClass.INTACT


def sort_key(defect: Defect) -> tuple:
    return (defect.offset is None, defect.offset or 0, defect.kind, defect.detail or "")
