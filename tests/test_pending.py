from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from fastdl_upload_bot.config import StorageConfig
from fastdl_upload_bot.pending import (
	create_pending_upload,
	delete_pending_upload,
	list_pending_uploads,
	pending_content_dir,
	prune_pending_uploads,
	read_pending_upload,
	verify_pending_integrity,
)
from fastdl_upload_bot.reports import preview_install
from fastdl_upload_bot.storage import LocalStorage


def test_pending_uploads_are_persisted_with_staged_content(tmp_path: Path) -> None:
	storage = LocalStorage(
		StorageConfig(
			backend="local",
			root_path=tmp_path / "server",
		)
	)
	staging = storage.create_staging_dir()
	target = staging / "maps" / "test.bsp"
	target.parent.mkdir(parents=True)
	target.write_bytes(b"bsp")
	preview = preview_install(storage, staging)

	pending = create_pending_upload(
		storage,
		staging,
		content_type="map",
		filename="test.zip",
		sha256="abc",
		user_id=1,
		user_name="uploader",
		channel_id=2,
		preview=preview,
	)

	loaded = read_pending_upload(storage, pending.pending_id)
	assert loaded.files == ("maps/test.bsp",)
	assert list_pending_uploads(storage) == (loaded,)
	assert (pending_content_dir(storage, pending.pending_id) / "maps" / "test.bsp").read_bytes() == b"bsp"
	assert not staging.exists()


def test_delete_pending_upload_removes_manifest_and_content(tmp_path: Path) -> None:
	storage = LocalStorage(
		StorageConfig(
			backend="local",
			root_path=tmp_path / "server",
		)
	)
	staging = storage.create_staging_dir()
	target = staging / "maps" / "test.bsp"
	target.parent.mkdir(parents=True)
	target.write_bytes(b"bsp")
	pending = create_pending_upload(
		storage,
		staging,
		content_type="map",
		filename="test.zip",
		sha256="abc",
		user_id=1,
		user_name="uploader",
		channel_id=2,
		preview=preview_install(storage, staging),
	)

	delete_pending_upload(storage, pending.pending_id)

	assert list_pending_uploads(storage) == ()
	assert not (storage.pending_root / pending.pending_id).exists()


def test_prune_pending_uploads_removes_only_old_entries(tmp_path: Path) -> None:
	storage = LocalStorage(
		StorageConfig(
			backend="local",
			root_path=tmp_path / "server",
		)
	)
	old_pending = _create_pending(storage, "old.zip")
	new_pending = _create_pending(storage, "new.zip")
	_set_pending_created_at(
		storage,
		old_pending.pending_id,
		datetime.now(timezone.utc) - timedelta(days=8),
	)

	pruned = prune_pending_uploads(storage, older_than_days=7)

	assert tuple(pending.pending_id for pending in pruned) == (old_pending.pending_id,)
	assert read_pending_upload(storage, new_pending.pending_id).filename == "new.zip"
	assert not (storage.pending_root / old_pending.pending_id).exists()


def test_pending_integrity_rejects_modified_staged_file(tmp_path: Path) -> None:
	storage = LocalStorage(
		StorageConfig(
			backend="local",
			root_path=tmp_path / "server",
		)
	)
	pending = _create_pending(storage, "test.zip")
	staged = pending_content_dir(storage, pending.pending_id) / "maps" / "test.bsp"
	staged.write_bytes(b"changed")

	try:
		verify_pending_integrity(storage, read_pending_upload(storage, pending.pending_id))
	except RuntimeError as exc:
		assert "changed" in str(exc)
	else:
		raise AssertionError("expected modified pending file to be rejected")


def test_pending_integrity_rejects_unexpected_extra_file(tmp_path: Path) -> None:
	storage = LocalStorage(
		StorageConfig(
			backend="local",
			root_path=tmp_path / "server",
		)
	)
	pending = _create_pending(storage, "test.zip")
	extra = pending_content_dir(storage, pending.pending_id) / "maps" / "extra.bsp"
	extra.write_bytes(b"extra")

	try:
		verify_pending_integrity(storage, read_pending_upload(storage, pending.pending_id))
	except RuntimeError as exc:
		assert "unexpected" in str(exc)
	else:
		raise AssertionError("expected extra pending file to be rejected")


def _create_pending(storage: LocalStorage, filename: str):
	staging = storage.create_staging_dir()
	target = staging / "maps" / filename.replace(".zip", ".bsp")
	target.parent.mkdir(parents=True)
	target.write_bytes(b"bsp")
	return create_pending_upload(
		storage,
		staging,
		content_type="map",
		filename=filename,
		sha256="abc",
		user_id=1,
		user_name="uploader",
		channel_id=2,
		preview=preview_install(storage, staging),
	)


def _set_pending_created_at(storage: LocalStorage, pending_id: str, created_at: datetime) -> None:
	path = storage.pending_root / pending_id / "pending.json"
	data = json.loads(path.read_text(encoding="utf-8"))
	data["created_at"] = created_at.isoformat()
	path.write_text(json.dumps(data), encoding="utf-8")
