from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DiscordConfig:
    token: str
    guild_ids: tuple[int, ...] = ()
    audit_channel_id: int | None = None
    command_name: str = "upload_fastdl"
    validate_command_name: str = "validate_fastdl"
    enable_message_uploads: bool = False


@dataclass(frozen=True)
class StorageConfig:
    backend: str
    root_path: Path
    fastdl_root_path: Path | None = None
    allow_overwrite: bool = False
    backup_existing: bool = True
    compressed_formats: tuple[str, ...] = ()


@dataclass(frozen=True)
class PathRule:
    prefix: str
    extensions: frozenset[str]

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "PathRule":
        prefix = str(data.get("prefix", "")).strip().replace("\\", "/").strip("/")
        extensions = frozenset(normalize_extension(ext) for ext in data.get("extensions", []))
        if not extensions:
            raise ValueError(f"path rule {prefix!r} must define at least one extension")
        return PathRule(prefix=prefix, extensions=extensions)


@dataclass(frozen=True)
class ContentTypeConfig:
    name: str
    allowed_channel_ids: tuple[int, ...] = ()
    allowed_role_ids: tuple[int, ...] = ()
    max_compressed_bytes: int = 25 * 1024 * 1024
    max_uncompressed_bytes: int = 100 * 1024 * 1024
    max_file_bytes: int = 50 * 1024 * 1024
    max_file_count: int = 500
    max_depth: int = 8
    require_lowercase_paths: bool = True
    required_extensions: frozenset[str] = field(default_factory=frozenset)
    required_any_extensions: frozenset[str] = field(default_factory=frozenset)
    allowed_extensions: frozenset[str] = field(default_factory=frozenset)
    path_rules: tuple[PathRule, ...] = ()

    @staticmethod
    def from_dict(name: str, data: dict[str, Any]) -> "ContentTypeConfig":
        path_rules = tuple(PathRule.from_dict(rule) for rule in data.get("path_rules", []))
        allowed_extensions = frozenset(
            normalize_extension(ext) for ext in data.get("allowed_extensions", [])
        )
        if path_rules:
            allowed_extensions = allowed_extensions | frozenset(
                ext for rule in path_rules for ext in rule.extensions
            )
        if not allowed_extensions:
            raise ValueError(f"content type {name!r} must allow at least one extension")

        return ContentTypeConfig(
            name=name,
            allowed_channel_ids=tuple(int(v) for v in data.get("allowed_channel_ids", [])),
            allowed_role_ids=tuple(int(v) for v in data.get("allowed_role_ids", [])),
            max_compressed_bytes=int(data.get("max_compressed_bytes", 25 * 1024 * 1024)),
            max_uncompressed_bytes=int(data.get("max_uncompressed_bytes", 100 * 1024 * 1024)),
            max_file_bytes=int(data.get("max_file_bytes", 50 * 1024 * 1024)),
            max_file_count=int(data.get("max_file_count", 500)),
            max_depth=int(data.get("max_depth", 8)),
            require_lowercase_paths=bool(data.get("require_lowercase_paths", True)),
            required_extensions=frozenset(
                normalize_extension(ext) for ext in data.get("required_extensions", [])
            ),
            required_any_extensions=frozenset(
                normalize_extension(ext) for ext in data.get("required_any_extensions", [])
            ),
            allowed_extensions=allowed_extensions,
            path_rules=path_rules,
        )


@dataclass(frozen=True)
class AppConfig:
    discord: DiscordConfig
    storage: StorageConfig
    content_types: dict[str, ContentTypeConfig]


def normalize_extension(extension: str) -> str:
    extension = extension.strip().lower()
    if not extension.startswith("."):
        extension = f".{extension}"
    return extension


def load_config(path: str | Path) -> AppConfig:
    load_env_file()
    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)

    raw_discord_data = data.get("discord", {})
    raw_storage_data = data.get("storage", {})
    discord_data = _apply_env_overrides(
        raw_discord_data,
        {
            "token": ("FASTDL_DISCORD_TOKEN", str),
            "guild_ids": ("FASTDL_DISCORD_GUILD_IDS", parse_int_tuple),
            "audit_channel_id": ("FASTDL_DISCORD_AUDIT_CHANNEL_ID", parse_optional_int),
            "command_name": ("FASTDL_DISCORD_COMMAND_NAME", str),
            "validate_command_name": ("FASTDL_DISCORD_VALIDATE_COMMAND_NAME", str),
            "enable_message_uploads": ("FASTDL_ENABLE_MESSAGE_UPLOADS", parse_bool),
        },
    )
    storage_data = _apply_env_overrides(
        raw_storage_data,
        {
            "root_path": ("FASTDL_SERVER_ROOT_PATH", str),
            "fastdl_root_path": ("FASTDL_FASTDL_ROOT_PATH", parse_optional_str),
            "allow_overwrite": ("FASTDL_ALLOW_OVERWRITE", parse_bool),
            "backup_existing": ("FASTDL_BACKUP_EXISTING", parse_bool),
            "compressed_formats": ("FASTDL_COMPRESSED_FORMATS", parse_str_tuple),
        },
    )
    content_data = _content_types_with_env_overrides(data.get("content_types", {}))

    discord = DiscordConfig(
        token=str(discord_data.get("token") or ""),
        guild_ids=tuple(int(v) for v in discord_data.get("guild_ids", [])),
        audit_channel_id=(
            int(discord_data["audit_channel_id"])
            if discord_data.get("audit_channel_id")
            else None
        ),
        command_name=str(discord_data.get("command_name", "upload_fastdl")),
        validate_command_name=str(discord_data.get("validate_command_name", "validate_fastdl")),
        enable_message_uploads=bool(discord_data.get("enable_message_uploads", False)),
    )

    storage = StorageConfig(
        backend=str(storage_data.get("backend", "local")),
        root_path=Path(str(storage_data["root_path"])).expanduser().resolve(),
        fastdl_root_path=(
            Path(str(storage_data["fastdl_root_path"])).expanduser().resolve()
            if storage_data.get("fastdl_root_path")
            else None
        ),
        allow_overwrite=bool(storage_data.get("allow_overwrite", False)),
        backup_existing=bool(storage_data.get("backup_existing", True)),
        compressed_formats=tuple(
            normalize_compressed_format(value)
            for value in storage_data.get("compressed_formats", [])
        ),
    )

    if storage.backend != "local":
        raise ValueError("only the local storage backend is implemented in this version")

    content_types = {
        name: ContentTypeConfig.from_dict(name, value)
        for name, value in content_data.items()
    }
    if not content_types:
        raise ValueError("at least one content type must be configured")

    return AppConfig(discord=discord, storage=storage, content_types=content_types)


def normalize_compressed_format(value: str) -> str:
    normalized = value.strip().lower().lstrip(".")
    if normalized not in {"gz", "bz2"}:
        raise ValueError(f"unsupported compressed format: {value}")
    return normalized


def _apply_env_overrides(
    data: dict[str, Any],
    mapping: dict[str, tuple[str, Any]],
) -> dict[str, Any]:
    result = dict(data)
    for key, (env_name, parser) in mapping.items():
        raw_value = os.getenv(env_name)
        if raw_value is not None:
            result[key] = parser(raw_value)
    return result


def _content_types_with_env_overrides(content_data: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, raw_value in content_data.items():
        value = dict(raw_value)
        prefix = f"FASTDL_{_env_key(name)}"
        if os.getenv(f"{prefix}_CHANNEL_IDS") is not None:
            value["allowed_channel_ids"] = parse_int_tuple(os.environ[f"{prefix}_CHANNEL_IDS"])
        if os.getenv(f"{prefix}_ROLE_IDS") is not None:
            value["allowed_role_ids"] = parse_int_tuple(os.environ[f"{prefix}_ROLE_IDS"])
        result[name] = value
    return result


def _env_key(value: str) -> str:
    return "".join(char if char.isalnum() else "_" for char in value.upper())


def parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"invalid boolean value: {value}")


def parse_optional_str(value: str) -> str | None:
    stripped = value.strip()
    if not stripped or stripped.lower() in {"null", "none"}:
        return None
    return stripped


def parse_optional_int(value: str) -> int | None:
    stripped = value.strip()
    if not stripped or stripped.lower() in {"null", "none"}:
        return None
    return int(stripped)


def parse_int_tuple(value: str) -> tuple[int, ...]:
    return tuple(int(part.strip()) for part in value.split(",") if part.strip())


def parse_str_tuple(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())


def load_env_file(path: str | Path = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return

    with env_path.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line.removeprefix("export ").strip()
            if "=" not in line:
                raise ValueError(f"{env_path}:{line_number}: invalid .env line")

            key, value = line.split("=", 1)
            key = key.strip()
            value = _strip_env_value(value.strip())
            if not key:
                raise ValueError(f"{env_path}:{line_number}: empty .env key")
            os.environ.setdefault(key, value)


def _strip_env_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value
