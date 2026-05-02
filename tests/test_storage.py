from __future__ import annotations

from pathlib import Path
import gzip
import json
import os

import pytest

from fastdl_upload_bot.config import StorageConfig
from fastdl_upload_bot.storage import LocalStorage
from fastdl_upload_bot.uploads import recover_upload


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


def test_storage_writes_manifest_for_successful_install(tmp_path: Path) -> None:
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
    manifest = json.loads(storage.manifest_path(result.upload_id).read_text(encoding="utf-8"))

    assert manifest["status"] == "installed"
    assert manifest["planned_files"] == ["maps/test.bsp"]
    assert manifest["installed_files"] == ["maps/test.bsp"]
    assert manifest["compressed_files"] == ["fastdl/maps/test.bsp.gz"]
    assert manifest["installed_hashes"]["maps/test.bsp"]
    assert manifest["compressed_hashes"]["fastdl/maps/test.bsp.gz"]


def test_storage_rejects_uploads_internal_root(tmp_path: Path) -> None:
    storage = LocalStorage(
        StorageConfig(
            backend="local",
            root_path=tmp_path / "server",
        )
    )
    staging = storage.create_staging_dir()
    target = staging / ".uploads" / "fake.json"
    target.parent.mkdir(parents=True)
    target.write_text("{}", encoding="utf-8")

    with pytest.raises(RuntimeError, match="internal storage"):
        storage.install(staging)


def test_storage_rejects_install_when_cross_process_lock_is_held(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("fastdl_upload_bot.storage.LOCK_POLL_SECONDS", 0)
    storage = LocalStorage(
        StorageConfig(
            backend="local",
            root_path=tmp_path / "server",
            install_lock_timeout_seconds=0,
        )
    )
    lock_path = storage.root / ".fastdl-upload.lock"
    lock_handle = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.close(lock_handle)
    staging = storage.create_staging_dir()
    target = staging / "maps" / "test.bsp"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"bsp-data")

    with pytest.raises(TimeoutError, match="install lock"):
        storage.install(staging)


def test_recovery_refuses_installed_upload_without_force(tmp_path: Path) -> None:
    storage = LocalStorage(
        StorageConfig(
            backend="local",
            root_path=tmp_path / "server",
        )
    )
    staging = storage.create_staging_dir()
    target = staging / "maps" / "test.bsp"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"bsp-data")
    result = storage.install(staging)

    with pytest.raises(RuntimeError, match="without --force"):
        recover_upload(storage, result.upload_id)


def test_recovery_can_roll_back_installed_upload_with_force(tmp_path: Path) -> None:
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

    recovery = recover_upload(storage, result.upload_id, force=True)
    manifest = json.loads(storage.manifest_path(result.upload_id).read_text(encoding="utf-8"))

    assert recovery.status == "rolled_back"
    assert not (storage.root / "maps" / "test.bsp").exists()
    assert not (storage.fastdl_root / "maps" / "test.bsp.gz").exists()
    assert manifest["status"] == "rolled_back"


def test_recovery_restores_backed_up_file(tmp_path: Path) -> None:
    storage = LocalStorage(
        StorageConfig(
            backend="local",
            root_path=tmp_path / "server",
            allow_overwrite=True,
            backup_existing=True,
        )
    )
    existing = storage.root / "maps" / "test.bsp"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"old")
    staging = storage.create_staging_dir()
    target = staging / "maps" / "test.bsp"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"new")
    result = storage.install(staging)

    recovery = recover_upload(storage, result.upload_id, force=True)

    assert recovery.restored_files == ("maps/test.bsp",)
    assert existing.read_bytes() == b"old"


def test_recovery_refuses_to_delete_modified_installed_file(tmp_path: Path) -> None:
    storage = LocalStorage(
        StorageConfig(
            backend="local",
            root_path=tmp_path / "server",
        )
    )
    staging = storage.create_staging_dir()
    target = staging / "maps" / "test.bsp"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"original")
    result = storage.install(staging)
    installed = storage.root / "maps" / "test.bsp"
    installed.write_bytes(b"modified")

    with pytest.raises(RuntimeError, match="modified file"):
        recover_upload(storage, result.upload_id, force=True)

    assert installed.read_bytes() == b"modified"
