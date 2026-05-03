from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .storage import LocalStorage
from .validator import ZipValidationResult


@dataclass(frozen=True)
class InstallPreview:
	files: tuple[str, ...]
	compressed_files: tuple[str, ...]
	conflicts: tuple[str, ...]


def preview_install(storage: LocalStorage, staging_dir: Path) -> InstallPreview:
	files = tuple(
		path.relative_to(staging_dir).as_posix()
		for path in sorted(staging_dir.rglob("*"))
		if path.is_file()
	)
	compressed_base = storage.fastdl_root or storage.root
	compressed_files: list[str] = []
	conflicts: list[str] = []

	for relative_text in files:
		relative = Path(*relative_text.split("/"))
		target = (storage.root / relative).resolve()
		if target.exists():
			conflicts.append(storage.display_path(target))
		for compressed_format in storage.config.compressed_formats:
			compressed_relative = Path(f"{relative_text}.{compressed_format}")
			compressed_target = (compressed_base / compressed_relative).resolve()
			compressed_files.append(storage.display_path(compressed_target))
			if compressed_target.exists():
				conflicts.append(storage.display_path(compressed_target))

	return InstallPreview(
		files=files,
		compressed_files=tuple(compressed_files),
		conflicts=tuple(conflicts),
	)


def validation_summary(
	validation: ZipValidationResult,
	preview: InstallPreview | None = None,
	limit: int = 20,
) -> str:
	files = tuple(entry.path.as_posix() for entry in validation.entries)
	by_folder = Counter(path.split("/", 1)[0] for path in files)
	by_extension = Counter(Path(path).suffix.lower() or "(none)" for path in files)
	largest = sorted(validation.entries, key=lambda entry: entry.size, reverse=True)[:5]

	lines = [
		f"Files: `{len(files)}`",
		f"Uncompressed size: `{validation.total_uncompressed_bytes}` bytes",
		"Folders: "
		+ ", ".join(f"{name}={count}" for name, count in sorted(by_folder.items())),
		"Extensions: "
		+ ", ".join(f"{name}={count}" for name, count in sorted(by_extension.items())),
	]
	if largest:
		lines.append("Largest files:")
		lines.extend(f"- `{entry.path.as_posix()}` ({entry.size} bytes)" for entry in largest)
	if preview is not None and preview.compressed_files:
		lines.append(f"Compressed files to generate: `{len(preview.compressed_files)}`")
	if preview is not None and preview.conflicts:
		lines.append("Destination conflicts:")
		lines.extend(f"- `{path}`" for path in preview.conflicts[:limit])
		if len(preview.conflicts) > limit:
			lines.append(f"- ... +{len(preview.conflicts) - limit} conflicts")
	shown = files[:limit]
	lines.append("Validated content:")
	lines.extend(f"- `{path}`" for path in shown)
	if len(files) > limit:
		lines.append(f"- ... +{len(files) - limit} files")
	return "\n".join(lines)
