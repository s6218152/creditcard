import os
from pathlib import Path
import time


def private_directory(path: Path) -> Path:
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)
    return path


def private_file(path: Path) -> None:
    os.chmod(path, 0o600)


def prune_old_files(directory: Path, patterns: tuple[str, ...], retention_days: int,
                    *, now: float | None = None) -> list[Path]:
    """Delete only matching regular files older than the configured retention window."""
    if retention_days <= 0:
        return []
    directory = Path(directory).resolve()
    cutoff = (time.time() if now is None else now) - retention_days * 86400
    removed = []
    candidates = {path for pattern in patterns for path in directory.glob(pattern)}
    for path in sorted(candidates):
        resolved = path.resolve()
        if (not resolved.is_relative_to(directory) or not resolved.is_file()
                or resolved.stat().st_mtime >= cutoff):
            continue
        resolved.unlink()
        removed.append(resolved)
    return removed
