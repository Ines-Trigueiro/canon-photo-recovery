# Salvaged Embedded thumbnails count as recovered, not reconstructed

An Embedded thumbnail is its own JPEG stream inside the Original, so pixels
decoded from it come from the original entropy-coded data, even when the Main
image is Unreadable. We therefore treat a decoded Embedded thumbnail as
authentic: it goes to `output/recovered/`, never `output/reconstructed/`, and is
flagged Thumbnail salvageable. Upscaling, inpainting, or otherwise using it to
stand in for the Main image produces synthetic pixels and falls under
`output/reconstructed/` with a `*.synthetic.*` name, as any other
reconstruction does. The Triage class always describes the Main image only, so
a salvageable thumbnail never lifts a file out of Unreadable.

The Triage run is read-only and produces no recovered output, so it does not
write salvaged thumbnails to `output/recovered/`. It places a byte-exact copy
beside the Contact sheet as a review inset only; extracting them as recovered
output is a recovery step.
