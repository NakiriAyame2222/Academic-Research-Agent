"""Migrate legacy storage and archive old benchmark/evaluation artifacts."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from storage.migration import run_migration


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help="project root (defaults to this project)")
    parser.add_argument("--execute", action="store_true", required=True, help="confirm the one-shot migration")
    args = parser.parse_args()
    result = run_migration(args.root, execute=args.execute)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("chroma", {}).get("status") != "complete":
        print("迁移已完成，但 Chroma 未完成；验证失败前禁止执行 cleanup。", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
