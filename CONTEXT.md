# Canon EOS 750D photo recovery

Recovering authentic photographic data from damaged JPEGs copied off a failing
SD card, without ever passing off synthesised pixels as recovered ones.

## Evidence

**Original**:
A JPEG as copied off the card into `evidence/arch-copies/`, pinned by its SHA-256 in the baseline manifest. Never modified.
_Avoid_: source file, input, raw

**Absent from copy**:
A sequence number inside the observed `IMG_NNNN` range for which no Original exists.
_Avoid_: deleted, lost, missing file

## Triage

**Triage**:
Assigning every Original exactly one Triage class, plus its Defects, without altering it.
_Avoid_: classification, scan, health check

**Triage class**:
One of four mutually exclusive labels, assigned by taking the first that applies in the order Unreadable → Structurally damaged → Visually corrupted → Intact.

**Unreadable**:
No pixels of the main image can be decoded, whatever else survives in the file.
_Avoid_: dead, empty, corrupt

**Structurally damaged**:
The JPEG container itself is broken — a required marker is missing or invalid, there is no EOI, the entropy-coded data contains a zero-fill run, or it ends before the last MCU — even if some pixels still decode.
_Avoid_: truncated (truncation is one kind of Structural damage, not a synonym)

**Visually corrupted**:
The container is sound, but the decoder reports errors in the entropy-coded data, so the decoded image contains damaged regions.
_Avoid_: glitched, partially corrupt

**Intact**:
Passes every structural check and decodes with no decoder warnings. Says nothing about whether the pixels *look* right — see Needs eyes.
_Avoid_: good, clean, OK

**Defect**:
One specific, located fault found during Triage (kind plus byte offset or pixel row), recorded as evidence for the Triage class.
_Avoid_: error, issue, problem

**Metadata salvageable**:
A flag on an Original whose EXIF block parses, independent of its Triage class.

**Needs eyes**:
A flag raised by pixel-level heuristics on a file the decoder accepted; never changes the Triage class, only queues the file for human review.
_Avoid_: suspect, false positive

**Human class**:
The Triage class a reviewer assigns after looking at the file; recorded alongside, never over, the automatic Triage class.
_Avoid_: override, correction

## Review

**Contact sheet**:
A local-only page of thumbnails, rendered from the decoded main image with decoder grey-fill outlined, for human review. Neither recovered nor reconstructed output.
_Avoid_: gallery, preview
