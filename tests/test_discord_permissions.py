from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from fastdl_upload_bot.config import AppConfig, DiscordConfig, StorageConfig
from fastdl_upload_bot.discord_bot import FastDLUploadBot


@dataclass(frozen=True)
class _Role:
	id: int


@dataclass(frozen=True)
class _Permissions:
	administrator: bool = False
	manage_guild: bool = False


@dataclass(frozen=True)
class _Actor:
	roles: tuple[_Role, ...] = ()
	guild_permissions: _Permissions = field(default_factory=_Permissions)


def test_manage_guild_can_inspect_but_not_run_destructive_admin_commands(tmp_path: Path) -> None:
	bot = _bot(tmp_path)
	actor = _Actor(guild_permissions=_Permissions(manage_guild=True))

	assert bot._is_admin(actor)
	assert not bot._is_destructive_admin(actor)


def test_explicit_admin_role_can_run_destructive_admin_commands(tmp_path: Path) -> None:
	bot = _bot(tmp_path)
	actor = _Actor(roles=(_Role(777),))

	assert bot._is_destructive_admin(actor)


def _bot(tmp_path: Path) -> FastDLUploadBot:
	return FastDLUploadBot(
		AppConfig(
			discord=DiscordConfig(
				token="",
				admin_role_ids=(777,),
			),
			storage=StorageConfig(
				backend="local",
				root_path=tmp_path / "server",
			),
			content_types={},
		)
	)
