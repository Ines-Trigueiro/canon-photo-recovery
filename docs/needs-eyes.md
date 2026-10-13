# Needs eyes: checks, threshold, and how it was derived

Needs eyes looks for corruption the decoder did not report. It never changes a
Triage class (ADR-0001); it only queues an Original for review and orders the
Contact sheet. To be revisited once `review.csv` holds Human class verdicts.

## Checks

All checks run on the 1/8-scale `djpeg` decode (one value per 8×8 block), with
any decoder grey fill left out: boundaries that touch grey fill are not
measured. At any other decode scale the checks are skipped, because the
threshold was calibrated at 1/8 only.

- **Row discontinuity.** For each boundary between two decoded rows, the mean
  absolute change across the row and all three channels, divided by the median
  change of the 4 boundaries on each side (floored at 2.0). A boundary at or
  above the threshold is a *break*. Each break gets a kind:
  - `shift`: the lower row is the upper row moved sideways (a displaced strip);
  - `offset`: the lower row is the upper row plus a uniform colour change (a lost
    DC predictor);
  - `content`: neither accounts for at least half of the change.
- **Foreign data.** A band between two consecutive `content` breaks is recorded
  as a Foreign data Defect with its pixel-row range in the Main image.
- **Flat grey.** Runs of whole decoder-grey MCUs at least one MCU row long in
  the middle of the image (libjpeg skipping to the next restart marker).

The score is the strongest check over its threshold: the largest break ratio
divided by the threshold, or the mid-image grey fill in MCU rows, whichever is
larger. Needs eyes is set when the score is at least 1 and the decoder accepted
the Original (no decoder warning), and only then is Foreign data recorded:
after a decoder error, desynchronised data also looks like a foreign band.

## Threshold: 4.5

Calibrated on the synthetic fixtures in `tests/fixtures/synthetic.py` (a
512×384 gradient-plus-noise image, encoded at quality 92, 4:2:2):

| Fixture | Largest break ratio |
|---|---|
| clean | 1.2 |
| `colour_discontinuity` (+70 red below row 200) | 5.8 |
| `displaced_strip` (rows 128–191 moved 96 px) | 10.1 |
| `spliced_band` (rows 160–223 from another image) | 17.1 |

4.5 sits between the clean image and the weakest damage, with margin on both
sides. Window sizes of 2, 4 and 8 boundaries gave the same separation; 4 was kept.

A global median (each change against the whole image's median) was tried first
and rejected. Every textured region of a real photo crossed the threshold, and
39 Intact Originals were flagged.

Measured afterwards on the 362 Originals classed Intact (largest ratio per
file, counts only): median 1.4, 90th percentile 2.3, 99th percentile 3.8,
maximum 4.0. At 4.5, none is flagged. Among the 116 Visually corrupted
Originals, 38 have at least one break. Those are not flagged, because the
decoder already reported them, but they sort first on the Contact sheet.

## Known limitations

- Only changes between rows are measured. A colour (DC) shift that starts
  partway along a row is seen only where it reaches a whole row.
- Flat grey means exact decoder grey fill only. Flat content that decodes
  close to, but not exactly, mid-grey is not detected.
- Content breaks are paired from the top. One stray content break shifts the
  pairing of every break below it. A foreign band that runs to the bottom of
  the image has only one break and is not recorded as Foreign data, although
  it still raises the score.
- Mid-image grey fill normally comes with a decoder warning, so it raises the
  score (and the sort order) but rarely sets the flag.
