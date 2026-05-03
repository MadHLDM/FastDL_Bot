from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import shutil
from typing import Any
from uuid import uuid4

from .reports import InstallPreview
from .storage import LocalStorage


@dataclass(frozen=True)
class PendingUpload:
	pending_id: str
	content_type: str
	filename: str
	sha256: str
	user_id: int
	user_name: str
	channel_id: int
	created_at: str
	files: tuple[str, ...]
	compressed_files: tuple[str, ...]
	conflicts: tuple[str, ...]
	hashes: dict[str, str]


def create_pending_upload(
	storage: LocalStorage,
	staging_dir: Path,
	*,
	content_type: str,
	filename: str,
	sha256: str,
	user_id: int,
	user_name: str,
	channel_id: int,
	preview: InstallPreview,
) -> PendingUpload:
	pending_id = _new_pending_id()
	pending_dir = storage.pending_root / pending_id
	content_dir = pending_dir / "content"
	pending_dir.mkdir(parents=True)
	shutil.move(str(staging_dir), str(content_dir))
	pending = PendingUpload(
		pending_id=pending_id,
		content_type=content_type,
		filename=filename,
		sha256=sha256,
		user_id=user_id,
		user_name=user_name,
		channel_id=channel_id,
		created_at=datetime.now(timezone.utc).isoformat(),
		files=preview.files,
		compressed_files=preview.compressed_files,
		conflicts=preview.conflicts,
		hashes=_hash_pending_files(content_dir, preview.files),
	)
	_write_pending(storage, pending)
	return pending


def list_pending_uploads(storage: LocalStorage) -> tuple[PendingUpload, ...]:
	pending: list[PendingUpload] = []
	for path in sorted(storage.pending_root.glob("*/pending.json")):
		pending.append(_pending_from_dict(_read_json(path)))
	return tuple(pending)


def read_pending_upload(storage: LocalStorage, pending_id: str) -> PendingUpload:
	_validate_pending_id(pending_id)
	path = _pending_manifest_path(storage, pending_id)
	if not path.exists():
		raise FileNotFoundError(f"pending upload was not found: {pending_id}")
	return _pending_from_dict(_read_json(path))


def pending_content_dir(storage: LocalStorage, pending_id: str) -> Path:
	_validate_pending_id(pending_id)
	path = (storage.pending_root / pending_id / "content").resolve()
	if not storage._is_child(path, storage.pending_root):
		raise RuntimeError("pending content path escapes configured root")
	return path


def verify_pending_integrity(storage: LocalStorage, pending: PendingUpload) -> None:
	content_dir = pending_content_dir(storage, pending.pending_id)
	expected_files = set(pending.files)
	actual_files = {
		path.relative_to(content_dir).as_posix()
		for path in content_dir.rglob("*")
		if path.is_file()
	}
	missing = sorted(expected_files - actual_files)
	if missing:
		raise RuntimeError(f"pending upload is missing staged files: {', '.join(missing[:5])}")
	extra = sorted(actual_files - expected_files)
	if extra:
		raise RuntimeError(f"pending upload contains unexpected staged files: {', '.join(extra[:5])}")
	actual_hashes = _hash_pending_files(content_dir, pending.files)
	changed = sorted(
		path
		for path, expected_hash in pending.hashes.items()
		if actual_hashes.get(path) != expected_hash
	)
	if changed:
		raise RuntimeError(f"pending upload staged files changed: {', '.join(changed[:5])}")


def delete_pending_upload(storage: LocalStorage, pending_id: str) -> None:
	_validate_pending_id(pending_id)
	path = (storage.pending_root / pending_id).resolve()
	if not storage._is_child(path, storage.pending_root):
		raise RuntimeError("pending path escapes configured root")
	if path.exists():
		shutil.rmtree(path)


def prune_pending_uploads(storage: LocalStorage, older_than_days: int) -> tuple[PendingUpload, ...]:
	if older_than_days < 0:
		raise ValueError("older-than-days must be zero or greater")
	cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
	pruned: list[PendingUpload] = []
	for pending in list_pending_uploads(storage):
		if _parse_created_at(pending.created_at) <= cutoff:
			delete_pending_upload(storage, pending.pending_id)
			pruned.append(pending)
	return tuple(pruned)


def pending_summary(pending: PendingUpload, limit: int = 20) -> str:
	lines = [
		f"Pending ID: `{pending.pending_id}`",
		f"Uploader: `{pending.user_name}` ({pending.user_id})",
		f"Type: `{pending.content_type}`",
		f"File: `{pending.filename}`",
		f"SHA-256: `{pending.sha256}`",
		f"Files: `{len(pending.files)}`",
	]
	if pending.compressed_files:
		lines.append(f"Compressed files to generate: `{len(pending.compressed_files)}`")
	if pending.conflicts:
		lines.append("Destination conflicts:")
		lines.extend(f"- `{path}`" for path in pending.conflicts[:limit])
		if len(pending.conflicts) > limit:
			lines.append(f"- ... +{len(pending.conflicts) - limit} conflicts")
	lines.append("Content:")
	lines.extend(f"- `{path}`" for path in pending.files[:limit])
	if len(pending.files) > limit:
		lines.append(f"- ... +{len(pending.files) - limit} files")
	return "\n".join(lines)


def _new_pending_id() -> str:
	now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
	return f"{now}-{uuid4().hex[:10]}"


def _pending_manifest_path(storage: LocalStorage, pending_id: str) -> Path:
	return storage.pending_root / pending_id / "pending.json"


def _write_pending(storage: LocalStorage, pending: PendingUpload) -> None:
	path = _pending_manifest_path(storage, pending.pending_id)
	tmp_path = path.with_suffix(".json.tmp")
	tmp_path.write_text(
		json.dumps(_pending_to_dict(pending), indent=2, sort_keys=True) + "\n",
		encoding="utf-8",
	)
	tmp_path.replace(path)


def _read_json(path: Path) -> dict[str, Any]:
	with path.open("r", encoding="utf-8") as handle:
		data = json.load(handle)
	if not isinstance(data, dict):
		raise RuntimeError(f"pending upload manifest is invalid: {path.name}")
	return data


def _pending_to_dict(pending: PendingUpload) -> dict[str, object]:
	return {
		"pending_id": pending.pending_id,
		"content_type": pending.content_type,
		"filename": pending.filename,
		"sha256": pending.sha256,
		"user_id": pending.user_id,
		"user_name": pending.user_name,
		"channel_id": pending.channel_id,
		"created_at": pending.created_at,
		"files": pending.files,
		"compressed_files": pending.compressed_files,
		"conflicts": pending.conflicts,
		"hashes": pending.hashes,
	}


def _pending_from_dict(data: dict[str, Any]) -> PendingUpload:
	return PendingUpload(
		pending_id=str(data["pending_id"]),
		content_type=str(data["content_type"]),
		filename=str(data["filename"]),
		sha256=str(data["sha256"]),
		user_id=int(data["user_id"]),
		user_name=str(data["user_name"]),
		channel_id=int(data["channel_id"]),
		created_at=str(data["created_at"]),
		files=tuple(str(path) for path in data.get("files", ())),
		compressed_files=tuple(str(path) for path in data.get("compressed_files", ())),
		conflicts=tuple(str(path) for path in data.get("conflicts", ())),
		hashes={str(path): str(digest) for path, digest in data.get("hashes", {}).items()},
	)


def _parse_created_at(value: str) -> datetime:
	created_at = datetime.fromisoformat(value)
	if created_at.tzinfo is None:
		return created_at.replace(tzinfo=timezone.utc)
	return created_at.astimezone(timezone.utc)


def _hash_pending_files(content_dir: Path, files: tuple[str, ...]) -> dict[str, str]:
	return {
		path: _sha256_file(content_dir / Path(*path.split("/")))
		for path in files
	}


def _sha256_file(path: Path) -> str:
	digest = hashlib.sha256()
	with path.open("rb") as handle:
		for chunk in iter(lambda: handle.read(1024 * 1024), b""):
			digest.update(chunk)
	return digest.hexdigest()


def _validate_pending_id(pending_id: str) -> None:
	if not pending_id or pending_id != Path(pending_id).name or pending_id.endswith(".json"):
		raise ValueError("invalid pending id")
