from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
from datetime import datetime, timezone
from typing import Any

from .storage import LocalStorage


@dataclass(frozen=True)
class UploadRecoveryResult:
	upload_id: str
	deleted_files: tuple[str, ...]
	restored_files: tuple[str, ...]
	status: str


def list_upload_manifests(storage: LocalStorage) -> tuple[dict[str, Any], ...]:
	manifests: list[dict[str, Any]] = []
	for path in sorted(storage.uploads_root.glob("*.json")):
		manifest = _read_manifest_path(path)
		manifest.setdefault("upload_id", path.stem)
		manifests.append(manifest)
	return tuple(manifests)


def read_upload_manifest(storage: LocalStorage, upload_id: str) -> dict[str, Any]:
	_validate_upload_id(upload_id)
	path = storage.manifest_path(upload_id)
	if not path.exists():
		raise FileNotFoundError(f"upload manifest was not found: {upload_id}")
	return _read_manifest_path(path)


def read_install_lock(storage: LocalStorage) -> str | None:
	lock_path = storage.root / ".fastdl-upload.lock"
	if not lock_path.exists():
		return None
	return lock_path.read_text(encoding="utf-8", errors="replace")


def clear_install_lock(storage: LocalStorage) -> None:
	lock_path = storage.root / ".fastdl-upload.lock"
	if not lock_path.exists():
		return
	if not storage._is_child(lock_path, storage.root):
		raise RuntimeError("lock path escapes configured root")
	lock_path.unlink()


def recover_upload(
	storage: LocalStorage,
	upload_id: str,
	*,
	force: bool = False,
) -> UploadRecoveryResult:
	manifest = read_upload_manifest(storage, upload_id)
	status = str(manifest.get("status", "unknown"))
	if status == "rolled_back":
		return UploadRecoveryResult(upload_id, (), (), status)
	if status == "installed" and not force:
		raise RuntimeError("refusing to roll back an installed upload without --force")
	if status not in {"started", "failed", "installed"}:
		raise RuntimeError(f"upload status cannot be recovered automatically: {status}")

	deleted: list[str] = []
	restored: list[str] = []
	expected_hashes = _manifest_hashes(manifest)
	for display_path in tuple(manifest.get("compressed_files", ())) + tuple(
		manifest.get("installed_files", ())
	):
		target = _resolve_display_path(storage, str(display_path))
		if target.exists():
			if target.is_dir():
				raise RuntimeError(f"refusing to delete directory during recovery: {display_path}")
			_verify_expected_hash(target, str(display_path), expected_hashes)
			target.unlink()
			deleted.append(str(display_path))

	for backup in reversed(tuple(manifest.get("backups", ()))):
		if not isinstance(backup, dict):
			raise RuntimeError("manifest contains an invalid backup entry")
		target_display = str(backup.get("target", ""))
		backup_display = str(backup.get("backup", ""))
		target = _resolve_display_path(storage, target_display)
		backup_path = _resolve_display_path(storage, backup_display)
		if not backup_path.exists():
			continue
		if target.exists():
			raise RuntimeError(f"refusing to overwrite during recovery: {target_display}")
		target.parent.mkdir(parents=True, exist_ok=True)
		shutil.move(str(backup_path), str(target))
		restored.append(target_display)

	manifest["status"] = "rolled_back"
	manifest["recovered_at"] = datetime.now(timezone.utc).isoformat()
	manifest["recovery_deleted_files"] = tuple(deleted)
	manifest["recovery_restored_files"] = tuple(restored)
	storage._write_manifest(upload_id, manifest)

	return UploadRecoveryResult(
		upload_id=upload_id,
		deleted_files=tuple(deleted),
		restored_files=tuple(restored),
		status="rolled_back",
	)


def _read_manifest_path(path: Path) -> dict[str, Any]:
	with path.open("r", encoding="utf-8") as handle:
		data = json.load(handle)
	if not isinstance(data, dict):
		raise RuntimeError(f"upload manifest is invalid: {path.name}")
	return data


def _validate_upload_id(upload_id: str) -> None:
	if not upload_id or upload_id != Path(upload_id).name or upload_id.endswith(".json"):
		raise ValueError("invalid upload id")


def _resolve_display_path(storage: LocalStorage, display_path: str) -> Path:
	if not display_path:
		raise RuntimeError("manifest contains an empty path")
	if "\\" in display_path:
		raise RuntimeError(f"manifest path must use / as separator: {display_path}")

	if display_path.startswith("fastdl/"):
		if storage.fastdl_root is None:
			raise RuntimeError(f"manifest references FastDL root but none is configured: {display_path}")
		base = storage.fastdl_root
		relative = display_path.removeprefix("fastdl/")
	else:
		base = storage.root
		relative = display_path

	pure = PurePosixPath(relative)
	if pure.is_absolute() or ".." in pure.parts:
		raise RuntimeError(f"manifest contains an unsafe path: {display_path}")
	target = (base / Path(*pure.parts)).resolve()
	if not storage._is_child(target, base):
		raise RuntimeError(f"manifest path escapes configured root: {display_path}")
	return target


def _manifest_hashes(manifest: dict[str, Any]) -> dict[str, str]:
	hashes: dict[str, str] = {}
	for key in ("installed_hashes", "compressed_hashes"):
		raw_value = manifest.get(key, {})
		if not isinstance(raw_value, dict):
			raise RuntimeError(f"manifest contains invalid {key}")
		for path, digest in raw_value.items():
			hashes[str(path)] = str(digest)
	return hashes


def _verify_expected_hash(target: Path, display_path: str, expected_hashes: dict[str, str]) -> None:
	expected_hash = expected_hashes.get(display_path)
	if not expected_hash:
		return
	actual_hash = _sha256_file(target)
	if actual_hash != expected_hash:
		raise RuntimeError(
			f"refusing to delete modified file during recovery: {display_path}"
		)


def _sha256_file(path: Path) -> str:
	digest = hashlib.sha256()
	with path.open("rb") as handle:
		for chunk in iter(lambda: handle.read(1024 * 1024), b""):
			digest.update(chunk)
	return digest.hexdigest()
