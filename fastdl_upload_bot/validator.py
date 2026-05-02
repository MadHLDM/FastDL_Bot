from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath
import stat
import zipfile

from .config import ContentTypeConfig


WINDOWS_RESERVED_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


class ValidationError(Exception):
    """Raised when an upload package fails validation."""


@dataclass(frozen=True)
class ZipEntry:
    source_name: str
    path: PurePosixPath
    size: int
    compressed_size: int


@dataclass(frozen=True)
class ZipValidationResult:
    entries: tuple[ZipEntry, ...]
    total_uncompressed_bytes: int


def validate_zip_file(
    zip_path: str,
    content: ContentTypeConfig,
    existing_roots: tuple[Path, ...] = (),
) -> ZipValidationResult:
    try:
        archive = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as exc:
        raise ValidationError("uploaded file is not a valid zip") from exc

    with archive:
        entries: list[ZipEntry] = []
        res_files: list[tuple[ZipEntry, zipfile.ZipInfo]] = []
        seen_paths: set[str] = set()
        package_paths: set[str] = set()
        total_uncompressed = 0
        found_extensions: set[str] = set()

        for info in archive.infolist():
            if info.is_dir():
                continue
            raw_name = getattr(info, "orig_filename", info.filename)
            if info.flag_bits & 0x1:
                raise ValidationError(f"{raw_name}: encrypted zips are not accepted")
            if _is_symlink(info):
                raise ValidationError(f"{raw_name}: symlinks are not accepted")

            normalized_path = normalize_zip_path(raw_name, content.max_depth)
            _validate_lowercase_path(normalized_path, content)
            folded = normalized_path.as_posix().casefold()
            if folded in seen_paths:
                raise ValidationError(
                    f"{normalized_path.as_posix()}: duplicate file or case collision"
                )
            seen_paths.add(folded)

            extension = normalized_path.suffix.lower()
            if not extension:
                raise ValidationError(f"{normalized_path.as_posix()}: file has no extension")
            if extension not in content.allowed_extensions:
                raise ValidationError(
                    f"{normalized_path.as_posix()}: extension {extension} is not allowed for {content.name}"
                )
            if content.path_rules and not _matches_path_rule(normalized_path, extension, content):
                without_wrapper = _path_without_wrapper_dir(normalized_path)
                if without_wrapper and _matches_path_rule(without_wrapper, extension, content):
                    raise ValidationError(
                        f"{normalized_path.as_posix()}: extra root folder detected; "
                        f"zip the folder contents, not the folder itself "
                        f"(expected: {without_wrapper.as_posix()})"
                    )
                raise ValidationError(
                    f"{normalized_path.as_posix()}: folder/extension is not allowed for {content.name}"
                )

            if info.file_size > content.max_file_bytes:
                raise ValidationError(
                    f"{normalized_path.as_posix()}: file exceeds the per-file size limit"
                )

            total_uncompressed += info.file_size
            if total_uncompressed > content.max_uncompressed_bytes:
                raise ValidationError("uncompressed content exceeds the configured limit")

            entries.append(
                entry := ZipEntry(
                    source_name=info.filename,
                    path=normalized_path,
                    size=info.file_size,
                    compressed_size=info.compress_size,
                )
            )
            package_paths.add(normalized_path.as_posix().casefold())
            found_extensions.add(extension)
            if _is_map_res_file(normalized_path):
                res_files.append((entry, info))

        if not entries:
            raise ValidationError("zip is empty")
        if len(entries) > content.max_file_count:
            raise ValidationError("zip exceeds the maximum file count")

        missing_required = content.required_extensions - found_extensions
        if missing_required:
            expected = ", ".join(sorted(missing_required))
            raise ValidationError(f"zip is missing required file type: {expected}")
        if content.required_any_extensions and not content.required_any_extensions.intersection(found_extensions):
            expected = ", ".join(sorted(content.required_any_extensions))
            raise ValidationError(f"zip must contain at least one of these file types: {expected}")

        for entry, info in res_files:
            _validate_res_file(
                archive=archive,
                info=info,
                res_entry=entry,
                content=content,
                package_paths=package_paths,
                existing_roots=existing_roots,
            )

        return ZipValidationResult(
            entries=tuple(entries),
            total_uncompressed_bytes=total_uncompressed,
        )


def normalize_zip_path(raw_name: str, max_depth: int) -> PurePosixPath:
    if not raw_name or "\x00" in raw_name:
        raise ValidationError("entry has an empty or invalid name")
    if "\\" in raw_name:
        raise ValidationError(f"{raw_name}: use / as the folder separator")
    if raw_name.startswith("/") or raw_name.startswith("//"):
        raise ValidationError(f"{raw_name}: absolute paths are not accepted")
    if ":" in raw_name.split("/", 1)[0]:
        raise ValidationError(f"{raw_name}: drive paths are not accepted")

    path = PurePosixPath(raw_name)
    parts = path.parts
    if not parts:
        raise ValidationError("entry has an empty path")
    if len(parts) > max_depth:
        raise ValidationError(f"{raw_name}: folder depth exceeds the limit")

    for part in parts:
        if part in {"", ".", ".."}:
            raise ValidationError(f"{raw_name}: path traversal is not accepted")
        if part.endswith(" ") or part.endswith("."):
            raise ValidationError(f"{raw_name}: names ending with space/dot are not accepted")
        stem = part.split(".", 1)[0].casefold()
        if stem in WINDOWS_RESERVED_NAMES:
            raise ValidationError(f"{raw_name}: Windows reserved names are not accepted")

    return path


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0xFFFF
    return stat.S_ISLNK(mode)


def _matches_path_rule(
    path: PurePosixPath,
    extension: str,
    content: ContentTypeConfig,
) -> bool:
    path_text = path.as_posix()
    for rule in content.path_rules:
        if extension not in rule.extensions:
            continue
        if not rule.prefix:
            if "/" not in path_text:
                return True
            continue
        if path_text == rule.prefix or path_text.startswith(f"{rule.prefix}/"):
            return True
    return False


def _path_without_wrapper_dir(path: PurePosixPath) -> PurePosixPath | None:
    if len(path.parts) < 2:
        return None
    return PurePosixPath(*path.parts[1:])


def _validate_lowercase_path(
    path: PurePosixPath,
    content: ContentTypeConfig,
    source: str = "",
) -> None:
    path_text = path.as_posix()
    if content.require_lowercase_paths and path_text != path_text.lower():
        raise ValidationError(f"{source}{path_text}: path must be lowercase")


def _is_map_res_file(path: PurePosixPath) -> bool:
    return (
        len(path.parts) == 2
        and path.parts[0].casefold() == "maps"
        and path.suffix.casefold() == ".res"
    )


def _validate_res_file(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    res_entry: ZipEntry,
    content: ContentTypeConfig,
    package_paths: set[str],
    existing_roots: tuple[Path, ...],
) -> None:
    references = parse_res_references(_read_text_member(archive, info))
    for reference in references:
        reference_path = normalize_zip_path(reference, content.max_depth)
        _validate_lowercase_path(reference_path, content, source=f"{res_entry.path.as_posix()}: ")
        extension = reference_path.suffix.lower()
        if not extension:
            raise ValidationError(
                f"{res_entry.path.as_posix()}: .res reference has no extension: {reference}"
            )
        if extension not in content.allowed_extensions:
            raise ValidationError(
                f"{res_entry.path.as_posix()}: .res references a disallowed extension: {reference}"
            )
        if content.path_rules and not _matches_path_rule(reference_path, extension, content):
            raise ValidationError(
                f"{res_entry.path.as_posix()}: .res references a disallowed path: {reference}"
            )
        if reference_path.as_posix().casefold() in package_paths:
            continue
        if _exists_in_any_root(reference_path, existing_roots):
            continue
        raise ValidationError(
            f"{res_entry.path.as_posix()}: resource listed in .res was not found: {reference}"
        )


def parse_res_references(text: str) -> tuple[str, ...]:
    references: list[str] = []
    for raw_line in text.splitlines():
        line = _strip_res_comment(raw_line).strip()
        if not line:
            continue
        for token in _split_res_tokens(line):
            token = token.strip()
            if token and token not in {"{", "}"}:
                references.append(token)
    return tuple(references)


def _strip_res_comment(line: str) -> str:
    quote: str | None = None
    index = 0
    while index < len(line):
        char = line[index]
        if char in {"'", '"'}:
            quote = None if quote == char else char if quote is None else quote
        if quote is None and line.startswith("//", index):
            return line[:index]
        index += 1
    return line


def _split_res_tokens(line: str) -> tuple[str, ...]:
    tokens: list[str] = []
    current: list[str] = []
    quote: str | None = None

    for char in line:
        if char in {"'", '"'}:
            if quote is None:
                quote = char
                continue
            if quote == char:
                quote = None
                continue
        if quote is None and char.isspace():
            if current:
                tokens.append("".join(current))
                current = []
            continue
        current.append(char)

    if quote is not None:
        raise ValidationError(".res line contains an unterminated quote")
    if current:
        tokens.append("".join(current))
    return tuple(tokens)


def _read_text_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> str:
    data = archive.read(info)
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def _exists_in_any_root(path: PurePosixPath, roots: tuple[Path, ...]) -> bool:
    relative = Path(*path.parts)
    for root in roots:
        candidate = (root / relative).resolve()
        if candidate.exists():
            return True
    return False
