from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import unicodedata


@dataclass(frozen=True)
class AuditRecord:
    status: str
    user_id: int
    user_name: str
    channel_id: int
    content_type: str
    filename: str
    sha256: str
    message: str
    files: tuple[str, ...] = ()
    upload_id: str | None = None

    def to_discord_message(self) -> str:
        lines = [
            f"FastDL upload {self.status}",
            f"user: {_clean_discord_text(self.user_name)} ({self.user_id})",
            f"channel: {self.channel_id}",
            f"type: {_clean_discord_text(self.content_type)}",
            f"file: {_clean_discord_text(self.filename)}",
            f"sha256: {self.sha256}",
        ]
        if self.upload_id:
            lines.append(f"upload_id: {_clean_discord_text(self.upload_id)}")
        lines.append(f"result: {_clean_discord_text(self.message)}")
        if self.files:
            shown = list(self.files[:20])
            lines.append("installed:")
            lines.extend(f"- {_clean_discord_text(path)}" for path in shown)
            if len(self.files) > len(shown):
                lines.append(f"- ... +{len(self.files) - len(shown)} files")
        return "```text\n" + "\n".join(lines) + "\n```"

    def to_log_line(self) -> str:
        return json.dumps(self.to_log_record(), sort_keys=True, ensure_ascii=False) + "\n"

    def to_log_record(self) -> dict[str, object]:
        timestamp = datetime.now(timezone.utc).isoformat()
        return {
            "timestamp": timestamp,
            "status": _clean_log_text(self.status),
            "user_id": self.user_id,
            "user_name": _clean_log_text(self.user_name),
            "channel_id": self.channel_id,
            "content_type": _clean_log_text(self.content_type),
            "filename": _clean_log_text(self.filename),
            "sha256": _clean_log_text(self.sha256),
            "upload_id": _clean_log_text(self.upload_id or ""),
            "message": _clean_log_text(self.message),
            "files": tuple(_clean_log_text(path) for path in self.files),
        }


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def append_audit_log(record: AuditRecord, log_path: Path = Path("logs/audit.jsonl")) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8", newline="") as handle:
        handle.write(record.to_log_line())


def _clean_discord_text(value: str) -> str:
    return _clean_log_text(value).replace("`", "'")


def _clean_log_text(value: str) -> str:
    return "".join(
        char if unicodedata.category(char)[0] != "C" else " "
        for char in str(value)
    )
