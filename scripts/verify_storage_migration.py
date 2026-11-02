"""Read-only validation for the storage migration receipt and data layout."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from storage.migration import verify_migration


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--after-cleanup", action="store_true", help="also require legacy cleanup to be complete")
    args = parser.parse_args()
    passed, errors = verify_migration(args.root, require_clean=args.after_cleanup)
    print(json.dumps({"passed": passed, "errors": errors}, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
