from __future__ import annotations

import json
from pathlib import Path

import pytest

from fastdl_upload_bot.config import SftpPublishConfig, StorageConfig
from fastdl_upload_bot.sftp_publisher import SftpPublisher
from fastdl_upload_bot.storage import LocalStorage


class _FakeSftp:
	def __init__(self) -> None:
		self.dirs = {"/", "/remote"}
		self.files: dict[str, bytes] = {}

	def stat(self, path: str) -> object:
		if path in self.dirs or path in self.files:
			return object()
		raise FileNotFoundError(path)

	def mkdir(self, path: str) -> None:
		self.dirs.add(path)

	def put(self, local_path: str, remote_path: str) -> None:
		self.files[remote_path] = Path(local_path).read_bytes()

	def rename(self, source: str, target: str) -> None:
		self.files[target] = self.files.pop(source)

	def remove(self, path: str) -> None:
		del self.files[path]


class _FakeConnection:
	def __init__(self, sftp: _FakeSftp) -> None:
		self.sftp = sftp

	def __enter__(self) -> _FakeSftp:
		return self.sftp

	def __exit__(self, *_args: object) -> None:
		return None


def test_sftp_publisher_uploads_compressed_files_and_updates_manifest(tmp_path: Path) -> None:
	storage = LocalStorage(
		StorageConfig(
			backend="local",
			root_path=tmp_path / "server",
			fastdl_root_path=tmp_path / "fastdl-cache",
			compressed_formats=("gz",),
		)
	)
	result = _install_map(storage)
	fake_sftp = _FakeSftp()
	publisher = _publisher(fake_sftp)

	publish_result = publisher.publish_install_result(storage, result)

	remote_path = "/remote/fastdl/maps/test.bsp.gz"
	manifest = json.loads(storage.manifest_path(result.upload_id).read_text(encoding="utf-8"))
	assert publish_result.published_files == ("fastdl/maps/test.bsp.gz",)
	assert publish_result.remote_files == (remote_path,)
	assert fake_sftp.files[remote_path] == (storage.fastdl_root / "maps" / "test.bsp.gz").read_bytes()
	assert manifest["sftp_publish_status"] == "published"
	assert manifest["sftp_published_files"] == ["fastdl/maps/test.bsp.gz"]


def test_sftp_publisher_rejects_remote_conflict_without_overwrite(tmp_path: Path) -> None:
	storage = LocalStorage(
		StorageConfig(
			backend="local",
			root_path=tmp_path / "server",
			fastdl_root_path=tmp_path / "fastdl-cache",
			compressed_formats=("gz",),
		)
	)
	result = _install_map(storage)
	fake_sftp = _FakeSftp()
	fake_sftp.dirs.add("/remote/fastdl")
	fake_sftp.dirs.add("/remote/fastdl/maps")
	fake_sftp.files["/remote/fastdl/maps/test.bsp.gz"] = b"old"
	publisher = _publisher(fake_sftp)

	with pytest.raises(FileExistsError, match="already exists"):
		publisher.publish_install_result(storage, result)

	assert fake_sftp.files["/remote/fastdl/maps/test.bsp.gz"] == b"old"


def _install_map(storage: LocalStorage):
	staging = storage.create_staging_dir()
	target = staging / "maps" / "test.bsp"
	target.parent.mkdir(parents=True)
	target.write_bytes(b"bsp-data")
	return storage.install(staging)


def _publisher(fake_sftp: _FakeSftp) -> SftpPublisher:
	publisher = SftpPublisher(
		SftpPublishConfig(
			enabled=True,
			host="fastdl.example.com",
			username="fastdl-bot",
			remote_fastdl_root_path="/remote/fastdl",
		)
	)
	publisher._connect = lambda: _FakeConnection(fake_sftp)  # type: ignore[method-assign]
	return publisher
