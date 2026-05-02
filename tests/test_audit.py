from __future__ import annotations

import json
from pathlib import Path

from fastdl_upload_bot.audit import AuditRecord, append_audit_log


def test_audit_log_is_jsonl_and_sanitizes_control_characters(tmp_path: Path) -> None:
	log_path = tmp_path / "audit.jsonl"
	record = AuditRecord(
		status="rejected",
		user_id=1,
		user_name="name\nwith\ttabs",
		channel_id=2,
		content_type="map",
		filename="bad\nfile.zip",
		sha256="abc",
		message="reason\twith\ncontrols",
		files=("maps/test.bsp\n",),
	)

	append_audit_log(record, log_path=log_path)
	data = json.loads(log_path.read_text(encoding="utf-8"))

	assert data["user_name"] == "name with tabs"
	assert data["filename"] == "bad file.zip"
	assert data["message"] == "reason with controls"
	assert data["files"] == ["maps/test.bsp "]


def test_discord_audit_message_cannot_break_code_block() -> None:
	record = AuditRecord(
		status="rejected",
		user_id=1,
		user_name="user```name",
		channel_id=2,
		content_type="map",
		filename="bad```file.zip",
		sha256="abc",
		message="bad```reason",
	)

	message = record.to_discord_message()

	assert "```file" not in message
	assert "```reason" not in message
