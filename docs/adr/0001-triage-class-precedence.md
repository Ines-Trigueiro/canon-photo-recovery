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

## Considered options

- **Class by decodability first** (anything that decodes is Visually corrupted):
  rejected — it hides truncation, which is the most actionable defect for
  later recovery.
- **Pixel heuristics assign Visually corrupted**: rejected — untuned
  thresholds would silently mislabel Intact photos.
- **Pillow as the decode authority**: rejected in favour of `djpeg`, whose
  warnings are explicit and versioned; Pillow is used only to resize
  thumbnails.
