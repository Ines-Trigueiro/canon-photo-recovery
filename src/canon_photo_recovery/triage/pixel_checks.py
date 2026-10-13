"""Needs eyes: pixel checks for corruption the decoder did not notice (ADR-0001).

They run on the 1/8-scale decode, one value per 8x8 block, and never change a
Triage class. Threshold and derivation: docs/needs-eyes.md.
"""

import warnings

import numpy as np

from canon_photo_recovery.jpeg import Defect

SCALE = "1/8"  # the only decode scale the threshold was calibrated at
# A row boundary whose change is this many times the median change of the
# boundaries around it is a break (docs/needs-eyes.md).
BREAK_RATIO = 4.5
# How many boundaries on each side make up "around it".
WINDOW = 4
# The local median is floored so a near-flat area does not turn noise into breaks.
MIN_ROW_CHANGE = 2.0
# A break that a sideways shift or a uniform colour offset accounts for to
# within this fraction of its change is a displacement, not new content.
MAX_UNEXPLAINED = 0.5


def assess(
    pixels: np.ndarray, grey: np.ndarray, grey_mcu_rows: float,
    rows_per_decoded_row: int, image_height: int,
) -> tuple[dict, list[Defect]]:
    """(Needs eyes record, Foreign data Defects) for decoded pixels with any
    trailing decoder grey fill already cut off. `grey` masks mid-image decoder
    grey fill, which covers `grey_mcu_rows` MCU rows' worth of MCUs. Boundaries
    touching grey fill are not measured.

    Score is the strongest check result over its threshold, so >= 1 means suspicious.
    """
    px = pixels.astype(np.float32)
    breaks = []
    max_ratio = 0.0
    if len(px) >= 3:
        change = np.abs(px[1:] - px[:-1]).mean(axis=(1, 2))
        touched = grey.any(axis=1)
        change[touched[1:] | touched[:-1]] = np.nan
        with np.errstate(invalid="ignore"):
            ratio = change / np.maximum(_local_median(change), MIN_ROW_CHANGE)
        if not np.isnan(ratio).all():
            max_ratio = float(np.nanmax(ratio))
        for i in np.flatnonzero(ratio >= BREAK_RATIO):  # NaN never compares true
            breaks.append({
                "row": (int(i) + 1) * rows_per_decoded_row,
                "ratio": round(float(ratio[i]), 2),
                "kind": _kind(px[i], px[i + 1], float(change[i])),
            })

    # Each band between two consecutive content breaks, taken in pairs from the top.
    content = [b["row"] for b in breaks if b["kind"] == "content"]
    foreign = [
        Defect("foreign_data", None, rows=(top, min(bottom, image_height) - 1),
               detail="band between two content breaks")
        for top, bottom in zip(content[::2], content[1::2], strict=False)
    ]

    # any mid-image grey run is at least one MCU row long, so it alone scores >= 1
    score = max(max_ratio / BREAK_RATIO, grey_mcu_rows)
    return {
        "score": round(score, 3),
        "row_discontinuity": round(max_ratio, 2),
        "mid_image_grey_mcu_rows": round(grey_mcu_rows, 3),
        "breaks": breaks,
    }, foreign


def _local_median(change: np.ndarray) -> np.ndarray:
    """For each boundary, the median change of up to WINDOW boundaries either side of it."""
    padded = np.pad(change, WINDOW, constant_values=np.nan)
    windows = np.lib.stride_tricks.sliding_window_view(padded, 2 * WINDOW + 1).copy()
    windows[:, WINDOW] = np.nan  # leave the boundary itself out
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN windows give NaN
        return np.nanmedian(windows, axis=1)


def _kind(above: np.ndarray, below: np.ndarray, change: float) -> str:
    """"shift" when the row below is the row above moved sideways, "offset" when it
    is the row above plus a uniform colour change, otherwise "content"."""
    w = len(above)
    if w >= 4:
        best = min(
            min(np.abs(below[s:] - above[:-s]).mean(), np.abs(below[:-s] - above[s:]).mean())
            for s in range(1, w // 2 + 1)
        )
        if best <= MAX_UNEXPLAINED * change:
            return "shift"
    residual = np.abs(below - above - (below - above).mean(axis=0)).mean()
    if residual <= MAX_UNEXPLAINED * change:
        return "offset"
    return "content"
