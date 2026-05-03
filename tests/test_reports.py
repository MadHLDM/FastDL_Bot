from __future__ import annotations

from pathlib import Path

from fastdl_upload_bot.config import StorageConfig
from fastdl_upload_bot.reports import preview_install
from fastdl_upload_bot.storage import LocalStorage


def test_preview_install_reports_conflicts_and_compressed_outputs(tmp_path: Path) -> None:
	storage = LocalStorage(
		StorageConfig(
			backend="local",
			root_path=tmp_path / "server",
			fastdl_root_path=tmp_path / "fastdl",
			compressed_formats=("gz",),
		)
	)
	existing = storage.root / "maps" / "test.bsp"
	existing.parent.mkdir(parents=True)
	existing.write_bytes(b"old")
	staging = storage.create_staging_dir()
	target = staging / "maps" / "test.bsp"
	target.parent.mkdir(parents=True)
	target.write_bytes(b"new")

	preview = preview_install(storage, staging)

	assert preview.files == ("maps/test.bsp",)
	assert preview.compressed_files == ("fastdl/maps/test.bsp.gz",)
	assert preview.conflicts == ("maps/test.bsp",)
