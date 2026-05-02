from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import hashlib


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
            f"user: {self.user_name} ({self.user_id})",
            f"channel: {self.channel_id}",
            f"type: {self.content_type}",
            f"file: {self.filename}",
            f"sha256: {self.sha256}",
        ]
        if self.upload_id:
            lines.append(f"upload_id: {self.upload_id}")
        lines.append(f"result: {self.message}")
        if self.files:
            shown = list(self.files[:20])
            lines.append("installed:")
            lines.extend(f"- {path}" for path in shown)
            if len(self.files) > len(shown):
                lines.append(f"- ... +{len(self.files) - len(shown)} files")
        return "```text\n" + "\n".join(lines) + "\n```"

    def to_log_line(self) -> str:
        timestamp = datetime.now(timezone.utc).isoformat()
        files = "|".join(self.files)
        return (
            f"{timestamp}\t{self.status}\t{self.user_id}\t{self.channel_id}\t"
            f"{self.content_type}\t{self.filename}\t{self.sha256}\t"
            f"{self.upload_id or ''}\t{self.message}\t{files}\n"
        )


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def append_audit_log(record: AuditRecord, log_path: Path = Path("logs/audit.tsv")) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8", newline="") as handle:
        handle.write(record.to_log_line())
