from __future__ import annotations

import json
from pathlib import Path

import pytest

from fastdl_upload_bot.config import load_config


def _base_config(tmp_path: Path) -> dict[str, object]:
	return {
		"discord": {
			"token": "",
			"require_access_rules": True,
		},
		"storage": {
			"backend": "local",
			"root_path": str(tmp_path / "server"),
		},
		"content_types": {
			"map": {
				"allowed_extensions": [".bsp"],
				"required_extensions": [".bsp"],
				"path_rules": [
					{"prefix": "maps", "extensions": [".bsp"]},
				],
			},
		},
	}


def _clear_fastdl_env(monkeypatch: pytest.MonkeyPatch) -> None:
	for key in (
		"FASTDL_REQUIRE_ACCESS_RULES",
		"FASTDL_APPROVAL_REQUIRED",
		"FASTDL_ADMIN_ROLE_IDS",
		"FASTDL_SERVER_ROOT_PATH",
		"FASTDL_FASTDL_ROOT_PATH",
		"FASTDL_SFTP_ENABLED",
		"FASTDL_SFTP_HOST",
		"FASTDL_SFTP_PORT",
		"FASTDL_SFTP_USERNAME",
		"FASTDL_SFTP_PASSWORD",
		"FASTDL_SFTP_PRIVATE_KEY_PATH",
		"FASTDL_SFTP_PRIVATE_KEY_PASSPHRASE",
		"FASTDL_SFTP_REMOTE_FASTDL_ROOT_PATH",
		"FASTDL_SFTP_KNOWN_HOSTS_PATH",
		"FASTDL_SFTP_STRICT_HOST_KEY_CHECKING",
		"FASTDL_SFTP_CONNECT_TIMEOUT_SECONDS",
		"FASTDL_MAP_CHANNEL_IDS",
		"FASTDL_MAP_ROLE_IDS",
	):
		monkeypatch.delenv(key, raising=False)


def test_config_requires_channel_and_role_rules_by_default(
	tmp_path: Path,
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	monkeypatch.chdir(tmp_path)
	_clear_fastdl_env(monkeypatch)
	config_path = tmp_path / "config.json"
	config_path.write_text(json.dumps(_base_config(tmp_path)), encoding="utf-8")

	with pytest.raises(ValueError, match="access rules are required"):
		load_config(config_path)


def test_config_can_disable_access_rule_requirement_for_local_testing(
	tmp_path: Path,
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	monkeypatch.chdir(tmp_path)
	_clear_fastdl_env(monkeypatch)
	data = _base_config(tmp_path)
	data["discord"]["require_access_rules"] = False
	config_path = tmp_path / "config.json"
	config_path.write_text(json.dumps(data), encoding="utf-8")

	config = load_config(config_path)

	assert config.discord.require_access_rules is False


def test_config_accepts_required_channel_and_role_rules(
	tmp_path: Path,
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	monkeypatch.chdir(tmp_path)
	_clear_fastdl_env(monkeypatch)
	data = _base_config(tmp_path)
	data["content_types"]["map"]["allowed_channel_ids"] = [123]
	data["content_types"]["map"]["allowed_role_ids"] = [456]
	config_path = tmp_path / "config.json"
	config_path.write_text(json.dumps(data), encoding="utf-8")

	config = load_config(config_path)

	assert config.content_types["map"].allowed_channel_ids == (123,)
	assert config.content_types["map"].allowed_role_ids == (456,)


def test_config_loads_approval_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	monkeypatch.chdir(tmp_path)
	_clear_fastdl_env(monkeypatch)
	data = _base_config(tmp_path)
	data["discord"]["require_access_rules"] = False
	data["discord"]["approval_required"] = True
	data["discord"]["admin_role_ids"] = [777]
	config_path = tmp_path / "config.json"
	config_path.write_text(json.dumps(data), encoding="utf-8")

	config = load_config(config_path)

	assert config.discord.approval_required is True
	assert config.discord.admin_role_ids == (777,)


def test_config_loads_sftp_publish_settings(
	tmp_path: Path,
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	monkeypatch.chdir(tmp_path)
	_clear_fastdl_env(monkeypatch)
	data = _base_config(tmp_path)
	data["discord"]["require_access_rules"] = False
	data["storage"]["sftp"] = {
		"enabled": True,
		"host": "fastdl.example.com",
		"username": "fastdl-bot",
		"private_key_path": str(tmp_path / "id_ed25519"),
		"remote_fastdl_root_path": "/var/www/fastdl/svencoop",
	}
	config_path = tmp_path / "config.json"
	config_path.write_text(json.dumps(data), encoding="utf-8")

	config = load_config(config_path)

	assert config.storage.sftp.enabled is True
	assert config.storage.sftp.host == "fastdl.example.com"
	assert config.storage.sftp.username == "fastdl-bot"
	assert config.storage.sftp.remote_fastdl_root_path == "/var/www/fastdl/svencoop"


def test_config_requires_sftp_target_when_enabled(
	tmp_path: Path,
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	monkeypatch.chdir(tmp_path)
	_clear_fastdl_env(monkeypatch)
	data = _base_config(tmp_path)
	data["discord"]["require_access_rules"] = False
	data["storage"]["sftp"] = {"enabled": True}
	config_path = tmp_path / "config.json"
	config_path.write_text(json.dumps(data), encoding="utf-8")

	with pytest.raises(ValueError, match="SFTP publishing is enabled"):
		load_config(config_path)
