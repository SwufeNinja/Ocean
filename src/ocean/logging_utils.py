from __future__ import annotations

from datetime import datetime
from pathlib import Path

_log_file: Path | None = None


def set_log_file(path: str | Path | None) -> None:
    global _log_file
    _log_file = Path(path) if path else None
    if _log_file:
        _log_file.parent.mkdir(parents=True, exist_ok=True)
        _log_file.write_text("", encoding="utf-8")


def log(message: str) -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}"
    print(line, flush=True)
    if _log_file:
        with _log_file.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
