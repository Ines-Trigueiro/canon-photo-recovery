# Canon EOS 750D photo recovery

Recover authentic photographic data from damaged JPEGs copied off a failing
4 GB SD card. Never fabricate pixels and call it recovery.

## Hard rules — these are not negotiable

1. **Originals are read-only.** `evidence/arch-copies/` is a symlink to a tree
   outside this repo, chmod'd `a-w`. Never write, move, rename, or chmod
   anything under `evidence/`. Copy into `output/` and work there.
2. **Never run anything against the physical SD card.** No `dd`, no `fsck`
   without `-n`, no mounting a device. The card is not attached and must stay
   that way.
3. **Nothing from `evidence/` or `output/` is ever committed.** If a command
   would add an image or disk image to git, stop.
4. **Authentic vs. synthesised.** Any output containing pixels not decoded from
   the original entropy-coded data is written to `output/reconstructed/` and
   named `*.synthetic.*`. Recovered-authentic output goes to
   `output/recovered/`. Never mix them in one directory.
5. **Test destructive algorithms on synthetic corruption first.** Generate
   damaged copies of a known-good file in `tests/fixtures/`, prove the
   algorithm there, then run it on real files.
6. Run `uv run pytest` before every commit. `tests/test_evidence_integrity.py`
   must pass — it fails if any original changed.

## Layout

- `evidence/arch-copies/` — the originals (read-only, outside git)
- `evidence/disk-images/` — ddrescue image of the card (read-only, outside git)
- `evidence/manifest/` — SHA-256 baselines
- `tools/` — deterministic parsers and recovery scripts
- `tests/` — pytest; fixtures are synthetic, never real photos
- `output/` — everything this project produces (gitignored)
- `docs/` — findings, ADRs, glossary

## Commands

- `uv run pytest` — tests
- `uv run ruff check .` — lint
- `uv run python tools/<script>.py` — run a tool

## Known state

Three reference files: `IMG_0497.JPG` decodes cleanly (use as the structural
reference), `IMG_0034.JPG` decodes with a displaced strip and a horizontal
discontinuity, `IMG_0015.JPG` has surviving EXIF but no usable frame header, no
EOI, a corrupt thumbnail, and ~236 KB of zero-fill. The ddrescue image of the
card shows ~8.0 bits/byte entropy and FF-00 stuffing rates near random, so
conventional carving found nothing — treat it as a low-priority source.
