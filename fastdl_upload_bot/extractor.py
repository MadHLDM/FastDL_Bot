from __future__ import annotations

from pathlib import Path
import shutil
import zipfile

from .validator import ZipEntry


def extract_validated_zip(zip_path: str, staging_dir: Path, entries: tuple[ZipEntry, ...]) -> None:
    staging_dir.mkdir(parents=True, exist_ok=True)
    by_source_name = {entry.source_name: entry for entry in entries}

    with zipfile.ZipFile(zip_path) as archive:
        for info in archive.infolist():
            entry = by_source_name.get(info.filename)
            if entry is None:
                continue

            destination = staging_dir / Path(*entry.path.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info, "r") as source, destination.open("wb") as target:
                shutil.copyfileobj(source, target)
