"""Delete only validated legacy/test artifacts after migration."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from storage.migration import cleanup_legacy_data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--execute", action="store_true", required=True, help="confirm destructive cleanup")
    args = parser.parse_args()
    try:
        result = cleanup_legacy_data(args.root, execute=args.execute)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps({"cleanup_completed": result.get("cleanup_completed"), "deleted": result.get("deleted_legacy_artifacts", [])}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
