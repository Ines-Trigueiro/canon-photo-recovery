"""Triage every Original into one Triage class and write the review outputs.

    uv run python tools/triage.py [--originals DIR] [--manifest FILE] [--out DIR]
"""

import argparse
import collections
import pathlib
import sys

from canon_photo_recovery.triage import IntegrityError, run_triage
from canon_photo_recovery.triage.classify import TriageClass

ROOT = pathlib.Path(__file__).resolve().parent.parent


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--originals", type=pathlib.Path, default=ROOT / "evidence/arch-copies")
    ap.add_argument(
        "--manifest", type=pathlib.Path, default=ROOT / "evidence/manifest/baseline-sha256.json"
    )
    ap.add_argument("--out", type=pathlib.Path, default=ROOT / "output/triage")
    args = ap.parse_args()

    try:
        report = run_triage(args.originals, args.manifest, args.out)
    except IntegrityError as e:
        print(f"integrity check failed, nothing written: {e}", file=sys.stderr)
        return 2
    except (ValueError, RuntimeError) as e:
        print(f"cannot run triage, nothing written: {e}", file=sys.stderr)
        return 2
    counts = collections.Counter(r["triage_class"] for r in report["originals"])
    for cls in TriageClass:
        print(f"{cls:22} {counts[cls]}")
    print(f"report and contact sheet in {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
