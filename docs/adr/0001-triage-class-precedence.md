# Triage classes are ordered by which layer is broken, and pixel heuristics never assign a class

A truncated JPEG still partially decodes (libjpeg grey-fills the rest), so
"decodable" alone cannot separate Visually corrupted from Structurally damaged.
We classify by the outermost broken layer — pixels decodable at all, then the
container (our own marker parser), then entropy-coded data (`djpeg` warnings) —
taking the first class that applies. Decoder warnings are how the automatic
run detects Visually corrupted; a reviewer may also assign it as Human class
on sight, e.g. after confirming Foreign data. Pixel-level heuristics (row discontinuity,
DC shifts, flat grey areas) only raise the Needs eyes flag, because they produce
false positives on real photos and a misclassified file is worse than an
unflagged one when the reviewer trusts the class.

## Two detectors in the entropy-coded data

Our own parser walks the Huffman-coded data to count MCUs, and in doing so it
locates Huffman errors, stray markers and out-of-sequence restart markers with
exact byte offsets. `djpeg` reports a different, overlapping set of faults
(e.g. extraneous bytes before EOI) but rarely says where. On synthetic bit
flips each catches cases the other misses. Both are kept as Defects, and either
one is sufficient evidence for Visually corrupted: neither can show the pixels
are right, so a disagreement resolves towards the more damaged class. `djpeg`
alone decides Unreadable: no pixels decoded means Unreadable, whatever the
parser thinks of the container.

`djpeg` runs at 1/8 scale. It still entropy-decodes the whole scan, and a test
asserts that its messages match a full-scale decode on every damage fixture.

## Considered options

- **Class by decodability first** (anything that decodes is Visually corrupted):
  rejected — it hides truncation, which is the most actionable defect for
  later recovery.
- **Pixel heuristics assign Visually corrupted**: rejected — untuned
  thresholds would silently mislabel Intact photos.
- **Pillow as the decode authority**: rejected in favour of `djpeg`, whose
  warnings are explicit and versioned; Pillow is used only to resize
  thumbnails.

## Needs eyes findings

The pixel checks (docs/needs-eyes.md) may also record Foreign data as a
Defect with a pixel-row range. Like the Needs eyes flag, it is evidence for a
reviewer, never for a class: the classifier ignores it, and a test asserts
that classes are identical with the checks on and off.
