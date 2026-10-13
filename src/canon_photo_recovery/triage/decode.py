"""Decode an Original's Main image with libjpeg-turbo's `djpeg` (ADR-0001)."""

import functools
import io
import re
import shutil
import subprocess
from dataclasses import dataclass

import numpy as np
from PIL import Image

from canon_photo_recovery.jpeg import Defect, Structure

SCALES = ("1/1", "1/2", "1/4", "1/8")
# libjpeg fills every coefficient it could not read with zero, which decodes to
# exactly this colour.
GREY_FILL = (128, 128, 128)
# libjpeg messages about header details that do not affect decoded pixels.
NOTICE = re.compile(r"^Warning:|Adobe|ICC")


@dataclass
class Decoded:
    pixels: np.ndarray | None  # H x W x 3 uint8, None when nothing decoded
    messages: list[str]  # djpeg's warnings and errors, in order


def check_decoder(scale: str) -> None:
    """Fail before any work if djpeg is missing or the scale is not one it supports."""
    if scale not in SCALES:
        raise ValueError(f"decode_scale must be one of {SCALES}, not {scale!r}")
    if shutil.which("djpeg") is None:
        raise RuntimeError("djpeg (libjpeg-turbo) is not on PATH")


def decode(data: bytes, scale: str = "1/8") -> Decoded:
    p = subprocess.run(
        ["djpeg", "-scale", scale, "-pnm"], input=data, capture_output=True, check=False
    )
    messages = [
        line.removeprefix("djpeg: ").strip()
        for line in p.stderr.decode(errors="replace").splitlines()
        if line.strip()
    ]
    pixels = None
    if p.stdout:
        try:
            pixels = np.asarray(Image.open(io.BytesIO(p.stdout)).convert("RGB"))
        except OSError as e:
            messages.append(f"decoder output unreadable: {e}")
    return Decoded(pixels, messages)


@functools.cache
def decoder_version() -> str:
    p = subprocess.run(["djpeg", "-version"], capture_output=True, text=True, check=False)
    return (p.stderr or p.stdout).strip().splitlines()[0]


def decode_defects(decoded: Decoded, structure: Structure) -> list[Defect]:
    """Every djpeg message as a Defect, located where the message allows it."""
    if decoded.pixels is None:
        return [Defect("decode_failed", None, detail="; ".join(decoded.messages) or None)]
    return [
        Defect(
            "decoder_notice" if NOTICE.search(msg) else "decoder_warning",
            _locate(msg, structure),
            detail=msg,
        )
        for msg in decoded.messages
    ]


def _locate(msg: str, s: Structure) -> int | None:
    if len(s.scans) != 1:
        return None  # djpeg does not say which scan it was reading
    scan = s.scans[0]
    if m := re.search(r"(\d+) extraneous bytes before marker 0x([0-9a-f]{2})", msg):
        if int(m.group(2), 16) == scan.terminator:
            return scan.data_end - int(m.group(1))
        return None  # a restart or stray marker mid-scan; djpeg does not say which
    if "premature end of data segment" in msg:
        return scan.data_end
    if "Premature end of JPEG file" in msg:
        return s.size
    return None


def grey_fill(pixels: np.ndarray, mcu_w: int, mcu_h: int) -> dict | None:
    """The trailing run of MCUs that are entirely decoder grey fill, in MCU order.

    Measured in whole MCUs so it is right whatever the chroma subsampling, and
    only reported when at least one MCU is filled, so a clean image whose last
    pixel happens to be mid-grey is not mistaken for decoder fill.
    """
    h, w, _ = pixels.shape
    gh, gw = -(-h // mcu_h), -(-w // mcu_w)
    grey = np.ones((gh * mcu_h, gw * mcu_w), dtype=bool)  # padding counts as grey
    grey[:h, :w] = np.all(pixels == GREY_FILL, axis=2)
    cells = grey.reshape(gh, mcu_h, gw, mcu_w).all(axis=(1, 3)).ravel()
    not_grey = np.flatnonzero(~cells)
    first = int(not_grey[-1]) + 1 if len(not_grey) else 0
    if first == len(cells):
        return None
    fill = {
        "from_x": (first % gw) * mcu_w,
        "from_y": (first // gw) * mcu_h,
        "mcu_height": mcu_h,
    }
    count = int(grey_mask((h, w), fill).sum())
    return {**fill, "pixels": count, "fraction": count / (h * w)}


def grey_mask(shape: tuple[int, int], fill: dict) -> np.ndarray:
    """Boolean H x W mask of the region a grey_fill() result covers."""
    h, w = shape
    x, y, step = fill["from_x"], fill["from_y"], fill["mcu_height"]
    mask = np.zeros((h, w), dtype=bool)
    mask[y : y + step, x:] = True
    mask[y + step :, :] = True
    return mask
