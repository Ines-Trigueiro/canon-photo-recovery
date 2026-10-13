---
name: jpeg-recovery
description: Rules for writing JPEG repair and recovery code in this project. Use whenever implementing or modifying anything that parses, repairs, or reconstructs a damaged JPEG.
---

## Non-negotiable

- Read the original, write a new file. Never open an evidence file for writing.
- Every output file gets a sidecar `.provenance.json`: source SHA-256, tool and
  git commit, every transformation applied in order, and a boolean
  `contains_synthesised_pixels`.
- An algorithm is validated against synthetic corruption in `tests/fixtures/`
  before it runs on a real file. Build fixtures by damaging a copy of a
  known-good file in a specific, documented way.

## Technique order, cheapest first

1. **Structural repair** — append a missing EOI, repair a declared segment
   length, drop a corrupt APP segment. Try this before anything clever.
2. **Table transplant** — graft DQT/DHT from the known-good reference when the
   damaged file's own tables are missing. Only valid between files from the same
   camera at the same quality setting; record it in provenance.
3. **Restart-marker resync** — on an entropy-coded stream with DRI set, each
   FFD0-FFD7 is an independent resume point. Decode each interval
   independently; a corrupt interval costs you that interval, not the rest of
   the image.
4. **DC-offset correction** — horizontal displacement and colour shifts after a
   damage point usually mean a lost DC predictor, not lost pixels. Re-deriving
   the DC offset per component often restores a whole region.
5. **Partial decode** — decode until the stream becomes invalid, emit what
   decoded, and record the exact byte offset where you stopped.

## Forbidden

Inpainting, generative fill, upscaling, or any model-produced pixels presented
as recovered. If a region cannot be decoded, leave it flagged as undecodable.
Reconstructions, if ever produced, go to `output/reconstructed/` with
`.synthetic.` in the filename and are never described as recovered.
