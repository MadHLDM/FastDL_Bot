from __future__ import annotations

from pathlib import Path
import gzip

import pytest

from fastdl_upload_bot.config import StorageConfig
from fastdl_upload_bot.storage import LocalStorage


def test_storage_rejects_internal_roots(tmp_path: Path) -> None:
    storage = LocalStorage(
        StorageConfig(
            backend="local",
            root_path=tmp_path / "fastdl",
        )
    )
    staging = storage.create_staging_dir()
    target = staging / ".incoming" / "nested.wad"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"wad")

    with pytest.raises(RuntimeError, match="internal storage"):
        storage.install(staging)


def test_storage_can_generate_gzip_fastdl_files(tmp_path: Path) -> None:
    storage = LocalStorage(
        StorageConfig(
            backend="local",
            root_path=tmp_path / "fastdl",
            compressed_formats=("gz",),
        )
    )
    staging = storage.create_staging_dir()
    target = staging / "maps" / "test.bsp"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"bsp-data")

    result = storage.install(staging)

    assert (storage.root / "maps" / "test.bsp").read_bytes() == b"bsp-data"
    gz_path = storage.root / "maps" / "test.bsp.gz"
    assert result.compressed_files == (gz_path,)
    with gzip.open(gz_path, "rb") as handle:
        assert handle.read() == b"bsp-data"


def test_storage_can_generate_gzip_files_in_separate_fastdl_root(tmp_path: Path) -> None:
    storage = LocalStorage(
        StorageConfig(
            backend="local",
            root_path=tmp_path / "server",
            fastdl_root_path=tmp_path / "fastdl",
            compressed_formats=("gz",),
        )
    )
    staging = storage.create_staging_dir()
    target = staging / "maps" / "test.bsp"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"bsp-data")

    result = storage.install(staging)

    assert (tmp_path / "server" / "maps" / "test.bsp").read_bytes() == b"bsp-data"
    assert not (tmp_path / "server" / "maps" / "test.bsp.gz").exists()
    gz_path = tmp_path / "fastdl" / "maps" / "test.bsp.gz"
    assert result.compressed_files == (gz_path,)
    assert storage.display_path(gz_path) == "fastdl/maps/test.bsp.gz"
    with gzip.open(gz_path, "rb") as handle:
        assert handle.read() == b"bsp-data"
