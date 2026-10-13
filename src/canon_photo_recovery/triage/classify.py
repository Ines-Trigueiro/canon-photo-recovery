"""Map an Original's Defects to its Triage class (precedence per ADR-0001)."""

from enum import StrEnum

from canon_photo_recovery.jpeg import Defect, Structure


class TriageClass(StrEnum):
    UNREADABLE = "unreadable"
    STRUCTURALLY_DAMAGED = "structurally_damaged"
    VISUALLY_CORRUPTED = "visually_corrupted"
    INTACT = "intact"


# Without any of these, no pixel of the Main image can be decoded.
FATAL = {"missing_soi", "missing_sof", "invalid_sof", "missing_sos", "invalid_sos"}
# Faults in the entropy-coded data rather than the container.
ENTROPY = {"stray_marker", "huffman_error", "restart_mismatch"}


def classify(structure: Structure) -> TriageClass:
    kinds = {d.kind for d in structure.defects}
    if kinds & FATAL or not _has_coded_data(structure):
        return TriageClass.UNREADABLE
    if kinds - ENTROPY:
        return TriageClass.STRUCTURALLY_DAMAGED
    if kinds:
        return TriageClass.VISUALLY_CORRUPTED
    return TriageClass.INTACT


def _has_coded_data(structure: Structure) -> bool:
    return any(sc.data_end > sc.data_start for sc in structure.scans)


def sort_key(defect: Defect) -> tuple:
    return (defect.offset, defect.kind)
