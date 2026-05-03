from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import bz2
from contextlib import contextmanager
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
from time import monotonic, sleep
from uuid import uuid4

from .config import StorageConfig

INTERNAL_ROOTS = {".incoming", ".backups", ".pending", ".uploads"}
LOCK_POLL_SECONDS = 0.1


@dataclass(frozen=True)
class InstallResult:
    upload_id: str
    installed_files: tuple[Path, ...]
    compressed_files: tuple[Path, ...] = ()
    backup_dir: Path | None = None


class LocalStorage:
    def __init__(self, config: StorageConfig):
        self.config = config
        self.root = config.root_path.resolve()
        self.fastdl_root = config.fastdl_root_path.resolve() if config.fastdl_root_path else None
        self.root.mkdir(parents=True, exist_ok=True)
        if self.fastdl_root is not None:
            self.fastdl_root.mkdir(parents=True, exist_ok=True)
        self.incoming_root = self.root / ".incoming"
        self.backup_root = self.root / ".backups"
        self.pending_root = self.root / ".pending"
        self.uploads_root = self.root / ".uploads"
        self.incoming_root.mkdir(exist_ok=True)
        self.pending_root.mkdir(exist_ok=True)
        self.uploads_root.mkdir(exist_ok=True)
        if self.config.backup_existing:
            self.backup_root.mkdir(exist_ok=True)

    def create_staging_dir(self) -> Path:
        return self.create_temp_dir("upload")

    def create_download_dir(self) -> Path:
        return self.create_temp_dir("download")

    def create_temp_dir(self, prefix: str) -> Path:
        for _ in range(10):
            staging_dir = self.incoming_root / f"{prefix}-{uuid4().hex}"
            try:
                staging_dir.mkdir()
                return staging_dir
            except FileExistsError:
                continue
        raise RuntimeError("could not create a unique temporary directory")

    def cleanup_staging_dir(self, staging_dir: Path) -> None:
        if staging_dir.exists() and self._is_child(staging_dir, self.incoming_root):
            shutil.rmtree(staging_dir)

    def install(self, staging_dir: Path) -> InstallResult:
        with self._install_lock():
            return self._install_unlocked(staging_dir)

    def _install_unlocked(self, staging_dir: Path) -> InstallResult:
        upload_id = self._new_upload_id()
        files = sorted(path for path in staging_dir.rglob("*") if path.is_file())
        relative_files = tuple(path.relative_to(staging_dir) for path in files)
        compressed_base = self.fastdl_root or self.root
        relative_compressed_files = {
            relative: tuple(
                Path(f"{relative.as_posix()}.{compressed_format}")
                for compressed_format in self.config.compressed_formats
            )
            for relative in relative_files
        }
        installed: list[Path] = []
        compressed: list[Path] = []
        backed_up: list[tuple[Path, Path]] = []
        backup_dir = self.backup_root / upload_id if self.config.backup_existing else None

        for relative in relative_files:
            if relative.parts and relative.parts[0].casefold() in INTERNAL_ROOTS:
                raise RuntimeError(f"refusing to install into internal storage directory: {relative}")
            target = (self.root / relative).resolve()
            if not self._is_child(target, self.root):
                raise RuntimeError(f"refusing to install outside storage root: {relative}")
            if target.exists() and not self.config.allow_overwrite:
                raise FileExistsError(f"{relative.as_posix()} already exists in the destination")

            for compressed_relative in relative_compressed_files[relative]:
                compressed_target = (compressed_base / compressed_relative).resolve()
                if not self._is_child(compressed_target, compressed_base):
                    raise RuntimeError(
                        f"refusing to install outside FastDL storage root: {compressed_relative}"
                    )
                if compressed_target.exists() and not self.config.allow_overwrite:
                    raise FileExistsError(
                        f"{self.display_path(compressed_target)} already exists in the destination"
                    )

        manifest: dict[str, object] = {
            "upload_id": upload_id,
            "status": "started",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "server_root": str(self.root),
            "fastdl_root": str(self.fastdl_root) if self.fastdl_root else None,
            "planned_files": tuple(relative.as_posix() for relative in relative_files),
            "planned_compressed_files": tuple(
                f"fastdl/{relative.as_posix()}"
                if self.fastdl_root is not None
                else relative.as_posix()
                for relatives in relative_compressed_files.values()
                for relative in relatives
            ),
            "installed_files": (),
            "installed_hashes": {},
            "compressed_files": (),
            "compressed_hashes": {},
            "backups": (),
        }
        self._write_manifest(upload_id, manifest)

        try:
            for source, relative in zip(files, relative_files, strict=True):
                target = (self.root / relative).resolve()
                target.parent.mkdir(parents=True, exist_ok=True)

                if target.exists():
                    if backup_dir is None:
                        target.unlink()
                    else:
                        backup_path = backup_dir / relative
                        backup_path.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(target), str(backup_path))
                        backed_up.append((target, backup_path))
                        manifest["backups"] = self._manifest_backups(backed_up)
                        self._write_manifest(upload_id, manifest)

                shutil.move(str(source), str(target))
                installed.append(target)
                manifest["installed_files"] = tuple(self.display_path(path) for path in installed)
                manifest["installed_hashes"] = {
                    self.display_path(path): _sha256_file(path)
                    for path in installed
                    if path.exists()
                }
                self._write_manifest(upload_id, manifest)

            for target, relative in zip(tuple(installed), relative_files, strict=True):
                for compressed_format in self.config.compressed_formats:
                    compressed_relative = Path(f"{relative.as_posix()}.{compressed_format}")
                    compressed_target = (compressed_base / compressed_relative).resolve()
                    if compressed_target.exists():
                        if backup_dir is None:
                            compressed_target.unlink()
                        else:
                            backup_path = (
                                backup_dir
                                / "fastdl"
                                / compressed_target.relative_to(compressed_base)
                            )
                            backup_path.parent.mkdir(parents=True, exist_ok=True)
                            shutil.move(str(compressed_target), str(backup_path))
                            backed_up.append((compressed_target, backup_path))
                            manifest["backups"] = self._manifest_backups(backed_up)
                            self._write_manifest(upload_id, manifest)
                    _write_compressed_copy(target, compressed_target, compressed_format)
                    compressed.append(compressed_target)
                    manifest["compressed_files"] = tuple(
                        self.display_path(path) for path in compressed
                    )
                    manifest["compressed_hashes"] = {
                        self.display_path(path): _sha256_file(path)
                        for path in compressed
                        if path.exists()
                    }
                    self._write_manifest(upload_id, manifest)
        except Exception:
            manifest["status"] = "failed"
            manifest["failed_at"] = datetime.now(timezone.utc).isoformat()
            self._write_manifest(upload_id, manifest)
            for target in (*compressed, *installed):
                if target.exists():
                    target.unlink()
            for target, backup_path in reversed(backed_up):
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(backup_path), str(target))
            manifest["status"] = "rolled_back"
            manifest["rolled_back_at"] = datetime.now(timezone.utc).isoformat()
            self._write_manifest(upload_id, manifest)
            raise

        manifest["status"] = "installed"
        manifest["completed_at"] = datetime.now(timezone.utc).isoformat()
        self._write_manifest(upload_id, manifest)

        return InstallResult(
            upload_id=upload_id,
            installed_files=tuple(installed),
            compressed_files=tuple(compressed),
            backup_dir=backup_dir if backup_dir and backup_dir.exists() else None,
        )

    def _new_upload_id(self) -> str:
        now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return f"{now}-{uuid4().hex[:10]}"

    def display_path(self, path: Path) -> str:
        path = path.resolve()
        if self._is_child(path, self.root):
            return path.relative_to(self.root).as_posix()
        if self.fastdl_root is not None and self._is_child(path, self.fastdl_root):
            return f"fastdl/{path.relative_to(self.fastdl_root).as_posix()}"
        return str(path)

    def manifest_path(self, upload_id: str) -> Path:
        return self.uploads_root / f"{upload_id}.json"

    def _write_manifest(self, upload_id: str, manifest: dict[str, object]) -> None:
        path = self.manifest_path(upload_id)
        tmp_path = path.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp_path, path)

    def _manifest_backups(self, backups: list[tuple[Path, Path]]) -> tuple[dict[str, str], ...]:
        return tuple(
            {
                "target": self.display_path(target),
                "backup": self.display_path(backup),
            }
            for target, backup in backups
        )

    @staticmethod
    def _is_child(path: Path, parent: Path) -> bool:
        path = path.resolve()
        parent = parent.resolve()
        return path == parent or parent in path.parents

    @contextmanager
    def _install_lock(self):
        lock_path = self.root / ".fastdl-upload.lock"
        deadline = monotonic() + self.config.install_lock_timeout_seconds
        handle: int | None = None

        while handle is None:
            try:
                handle = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                if monotonic() >= deadline:
                    raise TimeoutError(
                        f"timed out waiting for install lock: {lock_path.name}"
                    )
                sleep(LOCK_POLL_SECONDS)

        try:
            with os.fdopen(handle, "w", encoding="utf-8") as lock_file:
                handle = None
                lock_file.write(f"pid={os.getpid()}\n")
                lock_file.write(f"created_at={datetime.now(timezone.utc).isoformat()}\n")
            yield
        finally:
            if handle is not None:
                os.close(handle)
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass


def _write_compressed_copy(source: Path, target: Path, compressed_format: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_target = target.with_name(f"{target.name}.tmp-{uuid4().hex}")
    if compressed_format == "gz":
        try:
            with source.open("rb") as source_file, gzip.open(tmp_target, "wb", compresslevel=9) as target_file:
                shutil.copyfileobj(source_file, target_file)
            os.replace(tmp_target, target)
        finally:
            tmp_target.unlink(missing_ok=True)
        return
    if compressed_format == "bz2":
        try:
            with source.open("rb") as source_file, bz2.open(tmp_target, "wb", compresslevel=9) as target_file:
                shutil.copyfileobj(source_file, target_file)
            os.replace(tmp_target, target)
        finally:
            tmp_target.unlink(missing_ok=True)
        return
    raise ValueError(f"unsupported compressed format: {compressed_format}")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
