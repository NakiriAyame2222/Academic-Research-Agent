from __future__ import annotations

from pathlib import Path


def read_text(file_path: str) -> str:
    return Path(file_path).read_text(encoding="utf-8")


def write_text(file_path: str, content: str) -> None:
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def save_report(output_path: str, report: str) -> None:
    write_text(output_path, report)
