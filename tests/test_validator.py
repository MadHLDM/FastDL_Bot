from __future__ import annotations

from pathlib import Path
import zipfile

import pytest

from fastdl_upload_bot.config import ContentTypeConfig
from fastdl_upload_bot.validator import ValidationError, parse_res_references, validate_zip_file


def _content_type() -> ContentTypeConfig:
    return ContentTypeConfig.from_dict(
        "map",
        {
            "max_uncompressed_bytes": 1024,
            "max_file_bytes": 512,
            "max_file_count": 10,
            "max_depth": 5,
            "required_extensions": [".bsp"],
            "path_rules": [
                {"prefix": "maps", "extensions": [".bsp", ".res"]},
                {"prefix": "scripts/maps", "extensions": [".as", ".inc"]},
            ],
        },
    )


def _write_zip(path: Path, files: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in files.items():
            archive.writestr(name, payload)


def test_accepts_valid_map_package(tmp_path: Path) -> None:
    zip_path = tmp_path / "valid.zip"
    _write_zip(
        zip_path,
        {
            "maps/test.bsp": b"bsp",
            "maps/test.res": b"scripts/maps/test.as\n",
            "scripts/maps/test.as": b"void MapInit() {}",
        },
    )

    result = validate_zip_file(str(zip_path), _content_type())

    assert len(result.entries) == 3
    assert result.total_uncompressed_bytes > 0


def test_rejects_path_traversal(tmp_path: Path) -> None:
    zip_path = tmp_path / "traversal.zip"
    _write_zip(zip_path, {"maps/test.bsp": b"bsp", "../server.cfg": b"oops"})

    with pytest.raises(ValidationError, match="path traversal"):
        validate_zip_file(str(zip_path), _content_type())


def test_rejects_windows_drive_path(tmp_path: Path) -> None:
    zip_path = tmp_path / "drive.zip"
    _write_zip(zip_path, {"maps/test.bsp": b"bsp", "C:/server.cfg": b"oops"})

    with pytest.raises(ValidationError, match="drive"):
        validate_zip_file(str(zip_path), _content_type())


def test_rejects_backslash_paths(tmp_path: Path) -> None:
    zip_path = tmp_path / "backslash.zip"
    _write_zip(zip_path, {"maps/test.bsp": b"bsp", "maps/bad.res": b"oops"})
    zip_path.write_bytes(zip_path.read_bytes().replace(b"maps/bad.res", b"maps\\bad.res"))

    with pytest.raises(ValidationError, match="/ as the folder separator"):
        validate_zip_file(str(zip_path), _content_type())


def test_rejects_script_outside_scripts_maps(tmp_path: Path) -> None:
    zip_path = tmp_path / "script.zip"
    _write_zip(zip_path, {"maps/test.bsp": b"bsp", "scripts/plugins/admin.as": b"oops"})

    with pytest.raises(ValidationError, match="folder/extension"):
        validate_zip_file(str(zip_path), _content_type())


def test_rejects_extra_wrapper_directory_with_clear_message(tmp_path: Path) -> None:
    zip_path = tmp_path / "wrapped.zip"
    _write_zip(zip_path, {"test/maps/test.bsp": b"bsp"})

    with pytest.raises(ValidationError, match="extra root folder"):
        validate_zip_file(str(zip_path), _content_type())


def test_rejects_zip_bomb_by_uncompressed_size(tmp_path: Path) -> None:
    content = ContentTypeConfig.from_dict(
        "map",
        {
            "max_uncompressed_bytes": 1024,
            "max_file_bytes": 4096,
            "max_file_count": 10,
            "max_depth": 5,
            "required_extensions": [".bsp"],
            "path_rules": [
                {"prefix": "maps", "extensions": [".bsp", ".res"]},
            ],
        },
    )
    zip_path = tmp_path / "large.zip"
    _write_zip(zip_path, {"maps/test.bsp": b"x" * 2048})

    with pytest.raises(ValidationError, match="uncompressed"):
        validate_zip_file(str(zip_path), content)


def test_rejects_case_collision(tmp_path: Path) -> None:
    zip_path = tmp_path / "collision.zip"
    _write_zip(zip_path, {"maps/test.bsp": b"one", "MAPS/TEST.BSP": b"two"})

    with pytest.raises(ValidationError, match="lowercase"):
        validate_zip_file(str(zip_path), _content_type())


def test_rejects_uppercase_paths_for_linux_fastdl(tmp_path: Path) -> None:
    zip_path = tmp_path / "uppercase.zip"
    _write_zip(zip_path, {"maps/Test.bsp": b"bsp"})

    with pytest.raises(ValidationError, match="lowercase"):
        validate_zip_file(str(zip_path), _content_type())


def test_can_allow_uppercase_paths_when_configured(tmp_path: Path) -> None:
    content = ContentTypeConfig.from_dict(
        "map",
        {
            "require_lowercase_paths": False,
            "required_extensions": [".bsp"],
            "path_rules": [{"prefix": "maps", "extensions": [".bsp"]}],
        },
    )
    zip_path = tmp_path / "uppercase-allowed.zip"
    _write_zip(zip_path, {"maps/Test.bsp": b"bsp"})

    result = validate_zip_file(str(zip_path), content)

    assert len(result.entries) == 1


def test_required_any_extension_accepts_any_audio_format(tmp_path: Path) -> None:
    content = ContentTypeConfig.from_dict(
        "sounds",
        {
            "required_any_extensions": [".wav", ".mp3", ".ogg"],
            "path_rules": [
                {"prefix": "sound", "extensions": [".wav", ".mp3", ".ogg", ".txt"]},
            ],
        },
    )
    zip_path = tmp_path / "sounds.zip"
    _write_zip(zip_path, {"sound/ambience/loop.ogg": b"audio"})

    result = validate_zip_file(str(zip_path), content)

    assert len(result.entries) == 1


def test_accepts_res_references_existing_package_files(tmp_path: Path) -> None:
    zip_path = tmp_path / "with-res.zip"
    _write_zip(
        zip_path,
        {
            "maps/test.bsp": b"bsp",
            "maps/test.res": b"scripts/maps/test.as\n",
            "scripts/maps/test.as": b"void MapInit() {}",
        },
    )

    result = validate_zip_file(str(zip_path), _content_type())

    assert len(result.entries) == 3


def test_accepts_res_references_existing_installed_files(tmp_path: Path) -> None:
    existing_root = tmp_path / "server"
    installed = existing_root / "scripts" / "maps" / "shared.inc"
    installed.parent.mkdir(parents=True)
    installed.write_text("// shared", encoding="utf-8")
    zip_path = tmp_path / "with-existing-res.zip"
    _write_zip(
        zip_path,
        {
            "maps/test.bsp": b"bsp",
            "maps/test.res": b"scripts/maps/shared.inc\n",
        },
    )

    result = validate_zip_file(str(zip_path), _content_type(), existing_roots=(existing_root,))

    assert len(result.entries) == 2


def test_rejects_missing_res_reference(tmp_path: Path) -> None:
    zip_path = tmp_path / "missing-res.zip"
    _write_zip(
        zip_path,
        {
            "maps/test.bsp": b"bsp",
            "maps/test.res": b"scripts/maps/missing.as\n",
        },
    )

    with pytest.raises(ValidationError, match="not found"):
        validate_zip_file(str(zip_path), _content_type())


def test_rejects_disallowed_res_reference(tmp_path: Path) -> None:
    zip_path = tmp_path / "bad-res.zip"
    _write_zip(
        zip_path,
        {
            "maps/test.bsp": b"bsp",
            "maps/test.res": b"scripts/plugins/admin.as\n",
        },
    )

    with pytest.raises(ValidationError, match="disallowed path"):
        validate_zip_file(str(zip_path), _content_type())


def test_parse_res_references_ignores_comments_and_splits_whitespace() -> None:
    references = parse_res_references(
        """
        // full-line comment
        sound/test/a.wav sprites/test/a.spr // inline comment
        "scripts/maps/test.as"
        """
    )

    assert references == (
        "sound/test/a.wav",
        "sprites/test/a.spr",
        "scripts/maps/test.as",
    )


def test_rejects_backslash_inside_res_reference(tmp_path: Path) -> None:
    zip_path = tmp_path / "bad-res-backslash.zip"
    _write_zip(
        zip_path,
        {
            "maps/test.bsp": b"bsp",
            "maps/test.res": b"sound\\bad.wav\n",
        },
    )

    with pytest.raises(ValidationError, match="/ as the folder separator"):
        validate_zip_file(str(zip_path), _content_type())


def test_rejects_uppercase_res_reference_for_linux_fastdl(tmp_path: Path) -> None:
    content = ContentTypeConfig.from_dict(
        "map",
        {
            "required_extensions": [".bsp"],
            "path_rules": [
                {"prefix": "maps", "extensions": [".bsp", ".res"]},
                {"prefix": "sound", "extensions": [".wav"]},
            ],
        },
    )
    zip_path = tmp_path / "bad-res-uppercase.zip"
    _write_zip(
        zip_path,
        {
            "maps/test.bsp": b"bsp",
            "maps/test.res": b"sound/bad.wav\nsound/Bad.wav\n",
            "sound/bad.wav": b"wav",
        },
    )

    with pytest.raises(ValidationError, match="lowercase"):
        validate_zip_file(str(zip_path), content)
