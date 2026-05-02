from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import discord
from discord import app_commands

from .audit import AuditRecord, append_audit_log, sha256_file
from .config import AppConfig, ContentTypeConfig
from .extractor import extract_validated_zip
from .rate_limit import RateLimiter
from .storage import LocalStorage
from .validator import ValidationError, validate_zip_file

LOGGER = logging.getLogger(__name__)


class FastDLUploadBot(discord.Client):
    def __init__(self, config: AppConfig):
        intents = discord.Intents.default()
        intents.message_content = config.discord.enable_message_uploads
        intents.guilds = True
        intents.members = False
        super().__init__(intents=intents)
        self.config = config
        self.tree = app_commands.CommandTree(self)
        self.storage = LocalStorage(config.storage)
        self._install_lock = asyncio.Lock()
        self._rate_limiter = RateLimiter(
            max_requests=config.discord.rate_limit_max_requests,
            window_seconds=config.discord.rate_limit_window_seconds,
        )

    async def setup_hook(self) -> None:
        self._register_commands()
        if self.config.discord.guild_ids:
            for guild_id in self.config.discord.guild_ids:
                guild = discord.Object(id=guild_id)
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

    async def on_ready(self) -> None:
        print(f"Logged in as {self.user} ({self.user.id if self.user else 'unknown'})")

    async def on_message(self, message: discord.Message) -> None:
        if not self.config.discord.enable_message_uploads:
            return
        if message.author.bot or not message.guild or not message.attachments:
            return

        content_type = self._content_type_for_channel(message.channel.id)
        if content_type is None:
            return

        zip_attachments = [
            attachment for attachment in message.attachments
            if attachment.filename.lower().endswith(".zip")
        ]
        if len(zip_attachments) != 1:
            await message.reply("Send exactly one .zip file in this channel.", mention_author=False)
            return

        async def reply_text(content: str, ephemeral: bool = False) -> None:
            await message.reply(content, mention_author=False)

        await self._handle_upload(
            actor=message.author,
            channel_id=message.channel.id,
            content_type_name=content_type.name,
            attachment=zip_attachments[0],
            reply=reply_text,
        )

    def _register_commands(self) -> None:
        command_name = self.config.discord.command_name
        validate_command_name = self.config.discord.validate_command_name

        @self.tree.command(name=command_name, description="Install a validated zip into FastDL")
        @app_commands.describe(
            content_type="Configured type: map, playermodel, sounds, sprites, angelscript_map...",
            zip_file=".zip file with the correct Sven Co-op folder structure",
        )
        async def upload_fastdl(
            interaction: discord.Interaction,
            content_type: str,
            zip_file: discord.Attachment,
        ) -> None:
            await interaction.response.defer(ephemeral=True, thinking=True)

            async def reply_text(content: str, ephemeral: bool = True) -> None:
                await interaction.followup.send(content, ephemeral=ephemeral)

            await self._handle_upload(
                actor=interaction.user,
                channel_id=interaction.channel_id or 0,
                content_type_name=content_type,
                attachment=zip_file,
                reply=reply_text,
            )

        @self.tree.command(name=validate_command_name, description="Validate a FastDL zip without installing")
        @app_commands.describe(
            content_type="Configured type: map, playermodel, sounds, sprites, angelscript_map...",
            zip_file=".zip file with the correct Sven Co-op folder structure",
        )
        async def validate_fastdl(
            interaction: discord.Interaction,
            content_type: str,
            zip_file: discord.Attachment,
        ) -> None:
            await interaction.response.defer(ephemeral=True, thinking=True)

            async def reply_text(content: str, ephemeral: bool = True) -> None:
                await interaction.followup.send(content, ephemeral=ephemeral)

            await self._handle_validate(
                actor=interaction.user,
                channel_id=interaction.channel_id or 0,
                content_type_name=content_type,
                attachment=zip_file,
                reply=reply_text,
            )

    async def _handle_upload(
        self,
        actor: discord.abc.User,
        channel_id: int,
        content_type_name: str,
        attachment: discord.Attachment,
        reply,
    ) -> None:
        content_type = self.config.content_types.get(content_type_name)
        if content_type is None:
            await self._reject_unknown_content_type(
                actor,
                channel_id,
                content_type_name,
                attachment,
                reply,
            )
            await reply(f"Unknown content type: `{content_type_name}`.", ephemeral=True)
            return

        failure_reason = self._permission_error(actor, channel_id, content_type)
        if failure_reason:
            await self._reject(actor, channel_id, content_type, attachment, failure_reason, reply)
            return
        if await self._rate_limit_reject(actor, content_type, attachment, "upload", channel_id, reply):
            return

        if not attachment.filename.lower().endswith(".zip"):
            await self._reject(actor, channel_id, content_type, attachment, "only .zip files are accepted", reply)
            return
        if attachment.size > content_type.max_compressed_bytes:
            await self._reject(actor, channel_id, content_type, attachment, "zip exceeds the compressed size limit", reply)
            return

        download_dir = self.storage.create_download_dir()
        try:
            zip_path = download_dir / "upload.zip"
            try:
                await asyncio.wait_for(
                    attachment.save(zip_path),
                    timeout=self.config.discord.attachment_download_timeout_seconds,
                )
            except TimeoutError:
                await self._reject(
                    actor,
                    channel_id,
                    content_type,
                    attachment,
                    "attachment download timed out",
                    reply,
                )
                return
            digest = sha256_file(zip_path)
            staging_dir = self.storage.create_staging_dir()
            try:
                validation = await asyncio.to_thread(
                    validate_zip_file,
                    str(zip_path),
                    content_type,
                    (self.storage.root,),
                )
                await asyncio.to_thread(
                    extract_validated_zip,
                    str(zip_path),
                    staging_dir,
                    validation.entries,
                )
                async with self._install_lock:
                    install_result = await asyncio.to_thread(self.storage.install, staging_dir)
                installed_relative = tuple(
                    self.storage.display_path(path)
                    for path in install_result.installed_files
                )
                compressed_relative = tuple(
                    self.storage.display_path(path)
                    for path in install_result.compressed_files
                )
            except (ValidationError, FileExistsError, RuntimeError) as exc:
                self.storage.cleanup_staging_dir(staging_dir)
                await self._reject(
                    actor,
                    channel_id,
                    content_type,
                    attachment,
                    str(exc),
                    reply,
                    sha256=digest,
                )
                return
            except Exception as exc:
                LOGGER.exception(
                    "internal upload failure for user_id=%s channel_id=%s content_type=%s filename=%s",
                    actor.id,
                    channel_id,
                    content_type.name,
                    attachment.filename,
                )
                self.storage.cleanup_staging_dir(staging_dir)
                await self._reject(
                    actor,
                    channel_id,
                    content_type,
                    attachment,
                    "internal failure during install; an admin should check the bot logs",
                    reply,
                    sha256=digest,
                )
                return
            finally:
                self.storage.cleanup_staging_dir(staging_dir)
        finally:
            self.storage.cleanup_staging_dir(download_dir)

        record = AuditRecord(
            status="accepted",
            user_id=actor.id,
            user_name=str(actor),
            channel_id=channel_id,
            content_type=content_type.name,
            filename=attachment.filename,
            sha256=digest,
            upload_id=install_result.upload_id,
            message=f"{len(installed_relative)} file(s) installed",
            files=(*installed_relative, *compressed_relative),
        )
        append_audit_log(record)
        await self._send_audit(record)

        shown = "\n".join(f"- `{path}`" for path in installed_relative[:20])
        extra = ""
        if len(installed_relative) > 20:
            extra = f"\n- ... +{len(installed_relative) - 20} files"
        compressed_summary = ""
        if compressed_relative:
            compressed_shown = "\n".join(f"- `{path}`" for path in compressed_relative[:20])
            compressed_extra = ""
            if len(compressed_relative) > 20:
                compressed_extra = f"\n- ... +{len(compressed_relative) - 20} files"
            compressed_summary = f"\nGenerated compressed files:\n{compressed_shown}{compressed_extra}"
        await reply(
            f"Upload accepted for `{content_type.name}`.\n"
            f"Upload ID: `{install_result.upload_id}`\n"
            f"Installed files:\n{shown}{extra}{compressed_summary}",
            ephemeral=True,
        )

    async def _handle_validate(
        self,
        actor: discord.abc.User,
        channel_id: int,
        content_type_name: str,
        attachment: discord.Attachment,
        reply,
    ) -> None:
        content_type = self.config.content_types.get(content_type_name)
        if content_type is None:
            await self._reject_unknown_content_type(
                actor,
                channel_id,
                content_type_name,
                attachment,
                reply,
            )
            await reply(f"Unknown content type: `{content_type_name}`.", ephemeral=True)
            return

        failure_reason = self._permission_error(actor, channel_id, content_type)
        if failure_reason:
            await self._reject(actor, channel_id, content_type, attachment, failure_reason, reply)
            return
        if await self._rate_limit_reject(actor, content_type, attachment, "validate", channel_id, reply):
            return

        if not attachment.filename.lower().endswith(".zip"):
            await self._reject(actor, channel_id, content_type, attachment, "only .zip files are accepted", reply)
            return
        if attachment.size > content_type.max_compressed_bytes:
            await self._reject(actor, channel_id, content_type, attachment, "zip exceeds the compressed size limit", reply)
            return

        download_dir = self.storage.create_download_dir()
        try:
            zip_path = download_dir / "upload.zip"
            try:
                await asyncio.wait_for(
                    attachment.save(zip_path),
                    timeout=self.config.discord.attachment_download_timeout_seconds,
                )
            except TimeoutError:
                await self._reject(
                    actor,
                    channel_id,
                    content_type,
                    attachment,
                    "attachment download timed out",
                    reply,
                )
                return
            digest = sha256_file(zip_path)
            try:
                validation = await asyncio.to_thread(
                    validate_zip_file,
                    str(zip_path),
                    content_type,
                    (self.storage.root,),
                )
            except ValidationError as exc:
                await self._reject(
                    actor,
                    channel_id,
                    content_type,
                    attachment,
                    str(exc),
                    reply,
                    sha256=digest,
                )
                return
        finally:
            self.storage.cleanup_staging_dir(download_dir)

        validated_files = tuple(entry.path.as_posix() for entry in validation.entries)
        record = AuditRecord(
            status="validated",
            user_id=actor.id,
            user_name=str(actor),
            channel_id=channel_id,
            content_type=content_type.name,
            filename=attachment.filename,
            sha256=digest,
            message=(
                f"{len(validated_files)} valid file(s), "
                f"{validation.total_uncompressed_bytes} uncompressed bytes"
            ),
            files=validated_files,
        )
        append_audit_log(record)
        await self._send_audit(record)

        shown = "\n".join(f"- `{path}`" for path in validated_files[:20])
        extra = ""
        if len(validated_files) > 20:
            extra = f"\n- ... +{len(validated_files) - 20} files"
        await reply(
            f"Zip is valid for `{content_type.name}`. Nothing was installed.\n"
            f"Files: `{len(validated_files)}`\n"
            f"Uncompressed size: `{validation.total_uncompressed_bytes}` bytes\n"
            f"Validated content:\n{shown}{extra}",
            ephemeral=True,
        )

    def _permission_error(
        self,
        actor: discord.abc.User,
        channel_id: int,
        content_type: ContentTypeConfig,
    ) -> str | None:
        if content_type.allowed_channel_ids and channel_id not in content_type.allowed_channel_ids:
            return "upload was sent outside the allowed channel"

        if not content_type.allowed_role_ids:
            return None
        role_ids = {role.id for role in getattr(actor, "roles", [])}
        if not role_ids.intersection(content_type.allowed_role_ids):
            return "user does not have an authorized role"
        return None

    async def _rate_limit_reject(
        self,
        actor: discord.abc.User,
        content_type: ContentTypeConfig,
        attachment: discord.Attachment,
        action: str,
        channel_id: int,
        reply,
    ) -> bool:
        result = self._rate_limiter.check(actor.id, f"{action}:{content_type.name}")
        if result.allowed:
            return False
        await self._reject(
            actor,
            channel_id,
            content_type,
            attachment,
            f"rate limit exceeded; try again in {result.retry_after_seconds}s",
            reply,
        )
        return True

    def _content_type_for_channel(self, channel_id: int) -> ContentTypeConfig | None:
        matches = [
            content
            for content in self.config.content_types.values()
            if channel_id in content.allowed_channel_ids
        ]
        if len(matches) == 1:
            return matches[0]
        return None

    async def _reject(
        self,
        actor: discord.abc.User,
        channel_id: int,
        content_type: ContentTypeConfig,
        attachment: discord.Attachment,
        reason: str,
        reply,
        sha256: str = "",
    ) -> None:
        record = AuditRecord(
            status="rejected",
            user_id=actor.id,
            user_name=str(actor),
            channel_id=channel_id,
            content_type=content_type.name,
            filename=attachment.filename,
            sha256=sha256 or "not-downloaded",
            message=reason,
        )
        append_audit_log(record)
        await self._send_audit(record)
        await reply(f"Upload rejected: {reason}", ephemeral=True)

    async def _reject_unknown_content_type(
        self,
        actor: discord.abc.User,
        channel_id: int,
        content_type_name: str,
        attachment: discord.Attachment,
        reply,
    ) -> None:
        record = AuditRecord(
            status="rejected",
            user_id=actor.id,
            user_name=str(actor),
            channel_id=channel_id,
            content_type=content_type_name,
            filename=attachment.filename,
            sha256="not-downloaded",
            message="unknown content type",
        )
        append_audit_log(record)
        await self._send_audit(record)

    async def _send_audit(self, record: AuditRecord) -> None:
        channel_id = self.config.discord.audit_channel_id
        if not channel_id:
            return
        channel = self.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.fetch_channel(channel_id)
            except discord.DiscordException:
                return
        if hasattr(channel, "send"):
            await channel.send(record.to_discord_message())
