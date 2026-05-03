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
    pending_command_name: str = "fastdl_pending"
    approve_command_name: str = "fastdl_approve"
    reject_command_name: str = "fastdl_reject"
    uploads_command_name: str = "fastdl_uploads"
    rollback_command_name: str = "fastdl_rollback"
    enable_message_uploads: bool = False
    approval_required: bool = False
    admin_role_ids: tuple[int, ...] = ()
    require_access_rules: bool = True
    attachment_download_timeout_seconds: int = 120
    rate_limit_max_requests: int = 3
    rate_limit_window_seconds: int = 60


@dataclass(frozen=True)
class StorageConfig:
    backend: str
    root_path: Path
    fastdl_root_path: Path | None = None
    allow_overwrite: bool = False
    backup_existing: bool = True
    compressed_formats: tuple[str, ...] = ()
    install_lock_timeout_seconds: int = 300


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
            "pending_command_name": ("FASTDL_DISCORD_PENDING_COMMAND_NAME", str),
            "approve_command_name": ("FASTDL_DISCORD_APPROVE_COMMAND_NAME", str),
            "reject_command_name": ("FASTDL_DISCORD_REJECT_COMMAND_NAME", str),
            "uploads_command_name": ("FASTDL_DISCORD_UPLOADS_COMMAND_NAME", str),
            "rollback_command_name": ("FASTDL_DISCORD_ROLLBACK_COMMAND_NAME", str),
            "enable_message_uploads": ("FASTDL_ENABLE_MESSAGE_UPLOADS", parse_bool),
            "approval_required": ("FASTDL_APPROVAL_REQUIRED", parse_bool),
            "admin_role_ids": ("FASTDL_ADMIN_ROLE_IDS", parse_int_tuple),
            "require_access_rules": ("FASTDL_REQUIRE_ACCESS_RULES", parse_bool),
            "attachment_download_timeout_seconds": ("FASTDL_ATTACHMENT_DOWNLOAD_TIMEOUT_SECONDS", int),
            "rate_limit_max_requests": ("FASTDL_RATE_LIMIT_MAX_REQUESTS", int),
            "rate_limit_window_seconds": ("FASTDL_RATE_LIMIT_WINDOW_SECONDS", int),
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
            "install_lock_timeout_seconds": ("FASTDL_INSTALL_LOCK_TIMEOUT_SECONDS", int),
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
        pending_command_name=str(discord_data.get("pending_command_name", "fastdl_pending")),
        approve_command_name=str(discord_data.get("approve_command_name", "fastdl_approve")),
        reject_command_name=str(discord_data.get("reject_command_name", "fastdl_reject")),
        uploads_command_name=str(discord_data.get("uploads_command_name", "fastdl_uploads")),
        rollback_command_name=str(discord_data.get("rollback_command_name", "fastdl_rollback")),
        enable_message_uploads=bool(discord_data.get("enable_message_uploads", False)),
        approval_required=bool(discord_data.get("approval_required", False)),
        admin_role_ids=tuple(int(v) for v in discord_data.get("admin_role_ids", [])),
        require_access_rules=bool(discord_data.get("require_access_rules", True)),
        attachment_download_timeout_seconds=int(
            discord_data.get("attachment_download_timeout_seconds", 120)
        ),
        rate_limit_max_requests=int(discord_data.get("rate_limit_max_requests", 3)),
        rate_limit_window_seconds=int(discord_data.get("rate_limit_window_seconds", 60)),
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
        install_lock_timeout_seconds=int(storage_data.get("install_lock_timeout_seconds", 300)),
    )

    if storage.backend != "local":
        raise ValueError("only the local storage backend is implemented in this version")

    content_types = {
        name: ContentTypeConfig.from_dict(name, value)
        for name, value in content_data.items()
    }
    if not content_types:
        raise ValueError("at least one content type must be configured")
    if discord.require_access_rules:
        _validate_access_rules(content_types)

    return AppConfig(discord=discord, storage=storage, content_types=content_types)


def _validate_access_rules(content_types: dict[str, ContentTypeConfig]) -> None:
    missing: list[str] = []
    for name, content in content_types.items():
        if not content.allowed_channel_ids:
            missing.append(f"{name}: allowed_channel_ids")
        if not content.allowed_role_ids:
            missing.append(f"{name}: allowed_role_ids")
    if missing:
        joined = ", ".join(missing)
        raise ValueError(
            "access rules are required for every content type; configure these fields "
            f"or set require_access_rules=false for local testing only: {joined}"
        )


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
