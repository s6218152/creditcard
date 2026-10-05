import os
from pathlib import Path


def private_directory(path: Path) -> Path:
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)
    return path


def private_file(path: Path) -> None:
    os.chmod(path, 0o600)
