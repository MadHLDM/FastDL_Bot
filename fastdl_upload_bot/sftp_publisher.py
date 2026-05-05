from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import errno
import hashlib
from pathlib import Path, PurePosixPath
from typing import Any, Iterator
from uuid import uuid4

from .config import SftpPublishConfig
from .storage import InstallResult, LocalStorage


@dataclass(frozen=True)
class SftpPublishResult:
	published_files: tuple[str, ...]
	remote_files: tuple[str, ...]


class SftpPublisher:
	def __init__(self, config: SftpPublishConfig):
		self.config = config

	def publish_install_result(
		self,
		storage: LocalStorage,
		result: InstallResult,
	) -> SftpPublishResult:
		if not self.config.enabled or not result.compressed_files:
			return SftpPublishResult((), ())

		manifest = _read_manifest(storage.manifest_path(result.upload_id))
		mappings = tuple(
			self._remote_mapping(storage, path)
			for path in result.compressed_files
		)
		published_remote: list[str] = []
		published_display: list[str] = []

		try:
			with self._connect() as sftp:
				if not storage.config.allow_overwrite:
					for _local_path, _display_path, remote_path in mappings:
						if _remote_exists(sftp, remote_path):
							raise FileExistsError(
								f"{remote_path} already exists in the remote FastDL destination"
							)

				try:
					for local_path, display_path, remote_path in mappings:
						self._upload_file(sftp, local_path, remote_path, storage.config.allow_overwrite)
						published_remote.append(remote_path)
						published_display.append(display_path)
				except Exception:
					if not storage.config.allow_overwrite:
						for remote_path in reversed(published_remote):
							if _remote_exists(sftp, remote_path):
								sftp.remove(remote_path)
					raise
		except Exception as exc:
			manifest["sftp_publish_status"] = "failed"
			manifest["sftp_publish_failed_at"] = datetime.now(timezone.utc).isoformat()
			manifest["sftp_publish_error"] = str(exc)
			storage._write_manifest(result.upload_id, manifest)
			raise

		manifest["sftp_publish_status"] = "published"
		manifest["sftp_published_at"] = datetime.now(timezone.utc).isoformat()
		manifest["sftp_remote_fastdl_root"] = self.config.remote_fastdl_root_path
		manifest["sftp_published_files"] = tuple(published_display)
		manifest["sftp_remote_files"] = tuple(published_remote)
		manifest["sftp_published_hashes"] = {
			display_path: _sha256_file(local_path)
			for local_path, display_path, _remote_path in mappings
		}
		storage._write_manifest(result.upload_id, manifest)

		return SftpPublishResult(
			published_files=tuple(published_display),
			remote_files=tuple(published_remote),
		)

	def delete_manifest_files(
		self,
		storage: LocalStorage,
		upload_id: str,
	) -> tuple[str, ...]:
		if not self.config.enabled:
			return ()

		manifest = _read_manifest(storage.manifest_path(upload_id))
		display_files = tuple(str(path) for path in manifest.get("sftp_published_files", ()))
		if not display_files:
			return ()

		remote_files = tuple(
			self._remote_path_for_display_path(path.removeprefix("fastdl/"))
			for path in display_files
		)
		deleted: list[str] = []
		with self._connect() as sftp:
			for display_path, remote_path in zip(display_files, remote_files, strict=True):
				if _remote_exists(sftp, remote_path):
					sftp.remove(remote_path)
					deleted.append(display_path)

		manifest["sftp_deleted_files"] = tuple(deleted)
		manifest["sftp_deleted_at"] = datetime.now(timezone.utc).isoformat()
		storage._write_manifest(upload_id, manifest)
		return tuple(deleted)

	def _remote_mapping(
		self,
		storage: LocalStorage,
		local_path: Path,
	) -> tuple[Path, str, str]:
		display_path = storage.display_path(local_path)
		relative_path = display_path.removeprefix("fastdl/")
		return local_path, display_path, self._remote_path_for_display_path(relative_path)

	def _remote_path_for_display_path(self, display_path: str) -> str:
		remote_root = self.config.remote_fastdl_root_path
		if not remote_root:
			raise RuntimeError("SFTP remote FastDL root is not configured")
		return _safe_remote_path(remote_root, display_path)

	def _upload_file(
		self,
		sftp: Any,
		local_path: Path,
		remote_path: str,
		allow_overwrite: bool,
	) -> None:
		_ensure_remote_directory(sftp, str(PurePosixPath(remote_path).parent))
		tmp_path = f"{remote_path}.tmp-{uuid4().hex}"
		try:
			sftp.put(str(local_path), tmp_path)
			if allow_overwrite and _remote_exists(sftp, remote_path):
				sftp.remove(remote_path)
			sftp.rename(tmp_path, remote_path)
		except Exception:
			try:
				if _remote_exists(sftp, tmp_path):
					sftp.remove(tmp_path)
			finally:
				raise

	@contextmanager
	def _connect(self) -> Iterator[Any]:
		try:
			import paramiko
		except ImportError as exc:
			raise RuntimeError(
				"SFTP publishing requires paramiko; install the project dependencies again"
			) from exc

		client = paramiko.SSHClient()
		if self.config.known_hosts_path:
			client.load_host_keys(str(self.config.known_hosts_path))
		else:
			client.load_system_host_keys()
		if self.config.strict_host_key_checking:
			client.set_missing_host_key_policy(paramiko.RejectPolicy())
		else:
			client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

		key_filename = str(self.config.private_key_path) if self.config.private_key_path else None
		try:
			client.connect(
				hostname=self.config.host,
				port=self.config.port,
				username=self.config.username,
				password=self.config.password,
				key_filename=key_filename,
				passphrase=self.config.private_key_passphrase,
				timeout=self.config.connect_timeout_seconds,
				banner_timeout=self.config.connect_timeout_seconds,
				auth_timeout=self.config.connect_timeout_seconds,
			)
			with client.open_sftp() as sftp:
				yield sftp
		finally:
			client.close()


def _safe_remote_path(remote_root: str, relative_path: str) -> str:
	if "\\" in remote_root or "\\" in relative_path:
		raise RuntimeError("SFTP paths must use / as separator")
	root = PurePosixPath(remote_root)
	relative = PurePosixPath(relative_path)
	if not str(root).strip() or str(root) == ".":
		raise RuntimeError("SFTP remote root must not be empty")
	if relative.is_absolute() or ".." in relative.parts:
		raise RuntimeError(f"SFTP path escapes remote root: {relative_path}")
	return str(root / relative)


def _ensure_remote_directory(sftp: Any, directory: str) -> None:
	path = PurePosixPath(directory)
	if str(path) in {"", "."}:
		return
	parents: list[PurePosixPath] = []
	while str(path) not in {"", "."}:
		parents.append(path)
		next_path = path.parent
		if next_path == path:
			break
		path = next_path
	for parent in reversed(parents):
		if str(parent) == "/":
			continue
		if _remote_exists(sftp, str(parent)):
			continue
		sftp.mkdir(str(parent))


def _remote_exists(sftp: Any, path: str) -> bool:
	try:
		sftp.stat(path)
	except OSError as exc:
		if getattr(exc, "errno", None) in {errno.ENOENT, errno.ENOTDIR, None}:
			return False
		raise
	return True


def _read_manifest(path: Path) -> dict[str, Any]:
	import json

	with path.open("r", encoding="utf-8") as handle:
		data = json.load(handle)
	if not isinstance(data, dict):
		raise RuntimeError(f"upload manifest is invalid: {path.name}")
	return data


def _sha256_file(path: Path) -> str:
	digest = hashlib.sha256()
	with path.open("rb") as handle:
		for chunk in iter(lambda: handle.read(1024 * 1024), b""):
			digest.update(chunk)
	return digest.hexdigest()
