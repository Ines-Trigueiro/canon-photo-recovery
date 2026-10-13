# Canon EOS 750D photo recovery

Recovering authentic photographic data from damaged JPEGs copied off a failing
SD card, without ever passing off synthesised pixels as recovered ones.

## Evidence

**Original**:
A JPEG as copied off the card, recorded in the baseline manifest and never modified.
_Avoid_: source file, input, raw

**Absent from copy**:
A sequence number inside the observed `IMG_NNNN` range for which no Original exists.
_Avoid_: deleted, lost, missing file

**Main image**:
The full-resolution photograph inside an Original.
_Avoid_: picture, frame, the JPEG

**Embedded thumbnail**:
The small preview image the camera stores alongside the Main image inside an Original.
_Avoid_: preview, EXIF image

**Foreign data**:
A run of bytes inside an Original that belongs to a different photo.
_Avoid_: cross-linked (that names the file-system cause, not what is in the file)

## Triage

**Triage**:
Assigning every Original exactly one Triage class, plus its Defects, without altering it.
_Avoid_: classification, scan, health check

**Triage class**:
One of four mutually exclusive labels describing the Main image only, taken as the first that applies in the order Unreadable → Structurally damaged → Visually corrupted → Intact.

**Unreadable**:
No pixels of the Main image can be decoded, whatever else survives in the Original.
_Avoid_: dead, empty, corrupt

**Structurally damaged**:
The JPEG container of the Main image is broken — a required marker is missing or invalid, there is no EOI, the coded data contains a zero-fill run, or it ends early — even if some pixels still decode.
_Avoid_: truncated (truncation is one kind of Structural damage, not a synonym)

**Visually corrupted**:
The container is sound, but some of the Main image's decoded pixels are wrong.
_Avoid_: glitched, partially corrupt

**Intact**:
Passes every structural check and decodes without error. Says nothing about whether the pixels *look* right — see Needs eyes.
_Avoid_: good, clean, OK

**Defect**:
One specific, located fault in an Original (kind plus byte offset or pixel row), recorded as evidence for its Triage class.
_Avoid_: error, issue, problem

**Metadata salvageable**:
A flag on an Original whose EXIF metadata can be read, independent of its Triage class.

**Thumbnail salvageable**:
A flag on an Original whose Embedded thumbnail decodes without error, independent of its Triage class.

**Needs eyes**:
A flag on an Original the decoder accepted but whose pixels look suspicious; never changes the Triage class, only queues the Original for human review.
_Avoid_: suspect, false positive

**Human class**:
The Triage class a reviewer assigns after looking at an Original; recorded alongside, never over, the automatic Triage class.
_Avoid_: override, correction

## Review

**Contact sheet**:
A local-only page showing every Original's decoded Main image as a thumbnail, for human review. Neither recovered nor reconstructed output.
_Avoid_: gallery, preview
