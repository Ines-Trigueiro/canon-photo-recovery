"""Read-only structural model of a JPEG's Main image.

Walks SOI -> header segments -> SOS -> entropy-coded data -> EOI, recording
every marker's byte offset and every Defect found on the way. Never modifies
its input. Offsets are absolute byte offsets into the file.
"""

import math
import re
from dataclasses import dataclass, field

# A run of zero bytes this long cannot come from Huffman-coded data and matches
# the card's 512-byte sector size, so it marks storage-level zero-fill.
ZERO_FILL_MIN = 512

SOF_MARKERS = frozenset(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}
# Markers that may legitimately follow an entropy-coded segment.
SCAN_TERMINATORS = (
    SOF_MARKERS | {0xC4, 0xCC, 0xD9, 0xDA, 0xDB, 0xDC, 0xDD, 0xDE, 0xDF, 0xFE}
    | frozenset(range(0xE0, 0xF0))
)


@dataclass(frozen=True)
class Defect:
    kind: str
    offset: int
    length: int | None = None
    detail: str | None = None

    def to_dict(self) -> dict:
        d = {"kind": self.kind, "offset": self.offset}
        if self.length is not None:
            d["length"] = self.length
        if self.detail is not None:
            d["detail"] = self.detail
        return d


@dataclass(frozen=True)
class Component:
    id: int
    h: int
    v: int
    tq: int


@dataclass(frozen=True)
class Frame:
    offset: int
    marker: int
    precision: int
    height: int
    width: int
    components: tuple[Component, ...]


@dataclass
class Scan:
    offset: int
    components: tuple[tuple[int, int, int], ...]  # (component id, DC table, AC table)
    data_start: int
    data_end: int = 0
    # Tables and restart interval in force when this scan's SOS was read.
    huffman_tables: dict[tuple[int, int], tuple[list[int], bytes]] = field(default_factory=dict)
    restart_interval: int = 0
    # (offset, interval index) of each restart marker, resynchronised by marker number
    restarts: list[tuple[int, int]] = field(default_factory=list)
    stray_offsets: list[int] = field(default_factory=list)
    mcus_expected: int | None = None
    mcus_decoded: int | None = None


@dataclass
class Structure:
    size: int
    frame: Frame | None = None
    quant_tables: set[int] = field(default_factory=set)
    huffman_tables: dict[tuple[int, int], tuple[list[int], bytes]] = field(
        default_factory=dict
    )  # (class, id) -> (code counts per length 1..16, symbols)
    restart_interval: int = 0
    scans: list[Scan] = field(default_factory=list)
    eoi_offset: int | None = None
    exif: tuple[int, int] | None = None  # (offset, length) of the APP1 payload
    defects: list[Defect] = field(default_factory=list)


def parse(data: bytes) -> Structure:
    s = Structure(size=len(data))
    n = len(data)
    if data[:2] != b"\xff\xd8":
        s.defects.append(Defect("missing_soi", 0))
        return s

    pos = 2
    stopped = None  # offset where the walk could no longer follow the structure
    while True:
        if pos >= n:
            s.defects.append(Defect("missing_eoi", n))
            break
        if data[pos] != 0xFF:
            s.defects.append(Defect("invalid_marker", pos, detail=f"byte {data[pos]:02X}"))
            stopped = pos
            break
        while pos + 1 < n and data[pos + 1] == 0xFF:
            pos += 1  # fill bytes
        if pos + 1 >= n:
            s.defects.append(Defect("missing_eoi", n))
            break
        m = data[pos + 1]
        if m == 0xD9:
            s.eoi_offset = pos
            break
        if 0xD0 <= m <= 0xD7 or m == 0x01:
            pos += 2
            continue
        if m in (0x00, 0xD8):
            s.defects.append(Defect("invalid_marker", pos, detail=f"FF{m:02X}"))
            stopped = pos
            break
        if pos + 4 > n:
            s.defects.append(Defect("missing_eoi", n))
            break
        length = int.from_bytes(data[pos + 2 : pos + 4], "big")
        if length < 2:
            s.defects.append(Defect("invalid_marker", pos, detail=f"FF{m:02X} length {length}"))
            stopped = pos
            break
        if pos + 2 + length > n:
            s.defects.append(Defect("segment_overrun", pos, length=length, detail=f"FF{m:02X}"))
            if m == 0xE1 and data[pos + 4 : pos + 10] == b"Exif\x00\x00" and s.exif is None:
                s.exif = (pos + 4, n - pos - 4)  # read what survives of the EXIF block
            s.defects.append(Defect("missing_eoi", n))
            break
        body = data[pos + 4 : pos + 2 + length]

        if m in SOF_MARKERS:
            frame = _parse_sof(pos, m, body)
            if frame is None:
                s.defects.append(Defect("invalid_sof", pos))
            elif s.frame is None:
                s.frame = frame
        elif m == 0xC4:
            if not _parse_dht(body, s.huffman_tables):
                s.defects.append(Defect("invalid_dht", pos))
        elif m == 0xDB:
            if not _parse_dqt(body, s.quant_tables):
                s.defects.append(Defect("invalid_dqt", pos))
        elif m == 0xDD:
            if length == 4:
                s.restart_interval = int.from_bytes(body, "big")
            else:
                s.defects.append(Defect("invalid_dri", pos))
        elif m == 0xE1 and body.startswith(b"Exif\x00\x00") and s.exif is None:
            s.exif = (pos + 4, length - 2)
        elif m == 0xDA:
            scan = _parse_sos(pos, body)
            if scan is None:
                s.defects.append(Defect("invalid_sos", pos))
                stopped = pos
                break
            scan.data_start = pos + 2 + length
            scan.huffman_tables = dict(s.huffman_tables)
            scan.restart_interval = s.restart_interval
            pos = _walk_entropy(data, scan, s)
            s.scans.append(scan)
            continue
        pos += 2 + length

    _check_required(s)
    _find_zero_fill(data, s, stopped)
    if s.frame is not None and s.frame.marker in (0xC0, 0xC1):
        for scan in s.scans:
            _count_mcus(data, s, scan)
    return s


def _walk_entropy(data: bytes, scan: Scan, s: Structure) -> int:
    """Advance through entropy-coded data; return the offset of the terminating marker."""
    n = len(data)
    i = scan.data_start
    while True:
        i = data.find(b"\xff", i)
        if i < 0 or i + 1 >= n:
            scan.data_end = n
            return n
        nb = data[i + 1]
        if nb == 0x00:
            i += 2
        elif 0xD0 <= nb <= 0xD7:
            if not scan.restart_interval:
                s.defects.append(Defect("stray_marker", i, detail=f"FF{nb:02X} without DRI"))
                scan.stray_offsets.append(i)
            else:
                prev = scan.restarts[-1][1] if scan.restarts else 0
                # segment k (k >= 1) is preceded by RST((k - 1) mod 8)
                skipped = ((nb - 0xD0) - prev % 8) % 8
                if skipped:
                    s.defects.append(
                        Defect("restart_mismatch", i, detail=f"FF{nb:02X}, {skipped} lost")
                    )
                scan.restarts.append((i, prev + 1 + skipped))
            i += 2
        elif nb == 0xFF:
            i += 1
        elif nb in SCAN_TERMINATORS:
            scan.data_end = i
            return i
        else:
            s.defects.append(Defect("stray_marker", i, detail=f"FF{nb:02X}"))
            scan.stray_offsets.append(i)
            i += 2


def _check_required(s: Structure) -> None:
    where = s.scans[0].offset if s.scans else s.size
    if s.frame is None:
        if not any(d.kind == "invalid_sof" for d in s.defects):
            s.defects.append(Defect("missing_sof", where))
        return
    missing_q = sorted({c.tq for c in s.frame.components} - s.quant_tables)
    if missing_q:
        s.defects.append(Defect("missing_dqt", where, detail=f"tables {missing_q}"))
    if not s.scans:
        if not any(d.kind == "invalid_sos" for d in s.defects):
            s.defects.append(Defect("missing_sos", where))
        return
    for scan in s.scans:
        needed = {(0, td) for _, td, _ in scan.components} | {
            (1, ta) for _, _, ta in scan.components
        }
        missing_h = sorted(needed - set(scan.huffman_tables))
        if missing_h:
            s.defects.append(Defect("missing_dht", scan.offset, detail=f"tables {missing_h}"))


def _find_zero_fill(data: bytes, s: Structure, stopped: int | None) -> None:
    ranges = [(sc.data_start, sc.data_end) for sc in s.scans]
    if stopped is not None:
        ranges.append((stopped, s.size))
    pattern = re.compile(rb"\x00{%d,}" % ZERO_FILL_MIN)
    for start, end in ranges:
        for m in pattern.finditer(data, start, end):
            s.defects.append(Defect("zero_fill_run", m.start(), length=m.end() - m.start()))


def _parse_sof(offset: int, marker: int, body: bytes) -> Frame | None:
    if len(body) < 6:
        return None
    precision, height, width, nc = (
        body[0], int.from_bytes(body[1:3], "big"), int.from_bytes(body[3:5], "big"), body[5]
    )
    if nc not in (1, 3, 4) or len(body) != 6 + 3 * nc or height == 0 or width == 0:
        return None
    comps = []
    for k in range(nc):
        cid, hv, tq = body[6 + 3 * k : 9 + 3 * k]
        h, v = hv >> 4, hv & 15
        if not (1 <= h <= 4 and 1 <= v <= 4) or tq > 3:
            return None
        comps.append(Component(cid, h, v, tq))
    return Frame(offset, marker, precision, height, width, tuple(comps))


def _parse_dqt(body: bytes, tables: set[int]) -> bool:
    i = 0
    found = set()
    while i < len(body):
        pq, tq = body[i] >> 4, body[i] & 15
        size = 1 + 64 * (2 if pq else 1)
        if pq > 1 or tq > 3 or i + size > len(body):
            return False
        found.add(tq)
        i += size
    tables |= found
    return bool(found)


def _parse_dht(body: bytes, tables: dict) -> bool:
    i = 0
    found = {}
    while i < len(body):
        if i + 17 > len(body):
            return False
        tc, th = body[i] >> 4, body[i] & 15
        counts = list(body[i + 1 : i + 17])
        total = sum(counts)
        if tc > 1 or th > 3 or total > 256 or i + 17 + total > len(body):
            return False
        found[(tc, th)] = (counts, bytes(body[i + 17 : i + 17 + total]))
        i += 17 + total
    tables.update(found)
    return bool(found)


def _parse_sos(offset: int, body: bytes) -> Scan | None:
    if not body:
        return None
    ns = body[0]
    if not 1 <= ns <= 4 or len(body) != 1 + 2 * ns + 3:
        return None
    comps = tuple(
        (body[1 + 2 * k], body[2 + 2 * k] >> 4, body[2 + 2 * k] & 15) for k in range(ns)
    )
    return Scan(offset, comps, data_start=0)


# --- MCU accounting -------------------------------------------------------
#
# Decodes Huffman symbols just far enough to count complete MCUs; coefficient
# values are skipped, never reconstructed. Only baseline / extended sequential
# Huffman scans are counted.

_PAD = bytes(4096)  # zero padding so a read past the data stays well-formed


def _lookup(counts: list[int], symbols: bytes) -> list[int] | None:
    """16-bit peek table: entry = symbol << 8 | code length, or -1 for no code."""
    table = [-1] * 65536
    code = 0
    k = 0
    for length in range(1, 17):
        for _ in range(counts[length - 1]):
            if k >= len(symbols) or code >= (1 << length):
                return None
            shift = 16 - length
            entry = symbols[k] << 8 | length
            table[code << shift : (code + 1) << shift] = [entry] * (1 << shift)
            code += 1
            k += 1
        code <<= 1
    return table


def _count_mcus(data: bytes, s: Structure, scan: Scan) -> None:
    frame = s.frame
    comps = {c.id: c for c in frame.components}
    if any(cid not in comps for cid, _, _ in scan.components):
        return
    luts: dict[tuple[int, int], list[int] | None] = {}
    blocks = []  # (dc table, ac table) for each block of one MCU, in order
    hmax = max(c.h for c in frame.components)
    vmax = max(c.v for c in frame.components)
    for cid, td, ta in scan.components:
        for key in ((0, td), (1, ta)):
            if key not in luts and key in scan.huffman_tables:
                luts[key] = _lookup(*scan.huffman_tables[key])
        dc, ac = luts.get((0, td)), luts.get((1, ta))
        if dc is None or ac is None:
            return
        c = comps[cid]
        n = c.h * c.v if len(scan.components) > 1 else 1
        blocks += [(dc, ac)] * n
    if len(scan.components) > 1:
        expected = math.ceil(frame.width / (8 * hmax)) * math.ceil(frame.height / (8 * vmax))
    else:
        c = comps[scan.components[0][0]]
        expected = math.ceil(math.ceil(frame.width * c.h / hmax) / 8) * math.ceil(
            math.ceil(frame.height * c.v / vmax) / 8
        )
    scan.mcus_expected = expected

    interval = scan.restart_interval or expected
    intervals = math.ceil(expected / interval)
    # (start, end, interval index) of each restart segment
    segs = [(scan.data_start, None, 0)]
    for off, idx in scan.restarts:
        segs[-1] = (segs[-1][0], off, segs[-1][2])
        segs.append((off + 2, None, idx))
    segs[-1] = (segs[-1][0], scan.data_end, segs[-1][2])

    decoded = 0
    ran_out = False
    for a, b, idx in segs:
        want = min(interval, expected - idx * interval)
        if want <= 0:
            break
        got, status, raw_off = _decode_interval(data, a, b, want, blocks)
        decoded += got
        tainted = any(a <= o < b for o in scan.stray_offsets)
        if status == "invalid":
            s.defects.append(
                Defect("huffman_error", raw_off, detail=f"MCU {idx * interval + got}")
            )
            if not scan.restart_interval:
                break
        elif status == "out_of_data" and b == scan.data_end and not tainted:
            ran_out = True
    scan.mcus_decoded = decoded
    if ran_out or segs[-1][2] < intervals - 1:
        s.defects.append(
            Defect("short_scan", scan.data_end, detail=f"{decoded}/{expected} MCUs")
        )


def _decode_interval(data: bytes, a: int, b: int, want: int, blocks) -> tuple[int, str, int]:
    """Decode up to `want` MCUs from raw entropy bytes [a, b).

    Returns (complete MCUs, status, raw offset of the failure or end).
    """
    raw = data[a:b]
    stuffed = [m.start() for m in re.finditer(rb"\xff\x00", raw)]
    buf = raw.replace(b"\xff\x00", b"\xff") + _PAD
    nbits = (b - a - len(stuffed)) * 8

    def raw_offset(bit: int) -> int:
        j = bit >> 3
        # each stuffed pair before destuffed index j shifts the raw index by one
        lo, hi = 0, len(stuffed)
        while lo < hi:
            mid = (lo + hi) // 2
            if stuffed[mid] - mid < j:
                lo = mid + 1
            else:
                hi = mid
        return a + j + lo

    p = 0
    from_bytes = int.from_bytes
    for mcu in range(want):
        for dc, ac in blocks:
            i = p >> 3
            e = dc[(from_bytes(buf[i : i + 4], "big") >> (16 - (p & 7))) & 0xFFFF]
            if e < 0:
                return _failure(mcu, p, nbits, b, raw_offset)
            p += (e & 0xFF) + (e >> 8)
            k = 1
            while k < 64:
                i = p >> 3
                e = ac[(from_bytes(buf[i : i + 4], "big") >> (16 - (p & 7))) & 0xFFFF]
                if e < 0:
                    return _failure(mcu, p, nbits, b, raw_offset)
                rs = e >> 8
                p += (e & 0xFF) + (rs & 15)
                if rs & 15:
                    k += (rs >> 4) + 1
                elif rs == 0xF0:
                    k += 16
                else:
                    break
            if k > 64:
                return _failure(mcu, p, nbits, b, raw_offset)
        if p > nbits:
            return mcu, "out_of_data", b
    return want, "complete", raw_offset(p)


def _failure(mcu: int, p: int, nbits: int, end: int, raw_offset) -> tuple[int, str, int]:
    """A failed lookup whose 16-bit window reaches the zero padding means the data
    ran out, not that it is corrupt."""
    if p + 16 > nbits:
        return mcu, "out_of_data", end
    return mcu, "invalid", raw_offset(p)
