from __future__ import annotations

import argparse
import json
import logging
import sys

from .config import load_config
from .extractor import extract_validated_zip
from .pending import (
	list_pending_uploads,
	pending_summary,
	prune_pending_uploads,
	read_pending_upload,
)
from .reports import preview_install, validation_summary
from .storage import LocalStorage
from .uploads import (
	clear_install_lock,
	list_upload_manifests,
	read_install_lock,
	read_upload_manifest,
	recover_upload,
)
from .validator import ValidationError, validate_zip_file


def main() -> None:
	parser = argparse.ArgumentParser(description="Sven Co-op FastDL upload bot")
	parser.add_argument(
		"--config",
		default="config.json",
		help="Path to the instance config file. Defaults to config.json.",
	)
	subparsers = parser.add_subparsers(dest="command")

	subparsers.add_parser("run", help="Run the Discord bot.")
	validate_parser = subparsers.add_parser("validate", help="Validate a local zip without installing.")
	validate_parser.add_argument("zip_path", help="Path to the .zip package to validate.")
	validate_parser.add_argument(
		"--content-type",
		required=True,
		help="Configured content type to validate against, for example map.",
	)
	config_parser = subparsers.add_parser("config", help="Validate and inspect local configuration.")
	config_subparsers = config_parser.add_subparsers(dest="config_command", required=True)
	config_subparsers.add_parser("check", help="Validate config and print a safe summary.")
	pending_parser = subparsers.add_parser("pending", help="Inspect or prune pending uploads.")
	pending_subparsers = pending_parser.add_subparsers(dest="pending_command", required=True)
	pending_subparsers.add_parser("list", help="List pending uploads.")
	pending_show_parser = pending_subparsers.add_parser("show", help="Print one pending upload.")
	pending_show_parser.add_argument("pending_id")
	pending_prune_parser = pending_subparsers.add_parser("prune", help="Delete pending uploads older than N days.")
	pending_prune_parser.add_argument(
		"--older-than-days",
		type=int,
		required=True,
		help="Delete pending uploads created at least this many days ago.",
	)
	uploads_parser = subparsers.add_parser("uploads", help="Inspect or recover upload manifests.")
	upload_subparsers = uploads_parser.add_subparsers(dest="uploads_command", required=True)
	upload_subparsers.add_parser("list", help="List upload manifests.")
	show_parser = upload_subparsers.add_parser("show", help="Print one upload manifest as JSON.")
	show_parser.add_argument("upload_id")
	recover_parser = upload_subparsers.add_parser("recover", help="Roll back files recorded in a manifest.")
	recover_parser.add_argument("upload_id")
	recover_parser.add_argument(
		"--force",
		action="store_true",
		help="Allow rollback of an upload whose status is installed.",
	)
	upload_subparsers.add_parser("lock-status", help="Show the cross-process install lock.")
	clear_lock_parser = upload_subparsers.add_parser("clear-lock", help="Remove a stale install lock.")
	clear_lock_parser.add_argument(
		"--force",
		action="store_true",
		required=True,
		help="Required confirmation. Only use after verifying the bot is stopped.",
	)
	args = parser.parse_args()

	logging.basicConfig(
		level=logging.INFO,
		format="%(asctime)s %(levelname)s %(name)s: %(message)s",
	)
	try:
		config = load_config(args.config)
	except (OSError, ValueError, json.JSONDecodeError) as exc:
		print(f"error: {exc}", file=sys.stderr)
		raise SystemExit(1) from None
	if args.command == "config":
		if args.config_command == "check":
			print("config: ok")
			print(f"guild_ids: {len(config.discord.guild_ids)}")
			print(f"audit_channel_id: {'set' if config.discord.audit_channel_id else 'not set'}")
			print(f"message_uploads: {str(config.discord.enable_message_uploads).lower()}")
			print(f"approval_required: {str(config.discord.approval_required).lower()}")
			print(f"admin_roles: {len(config.discord.admin_role_ids)}")
			print(f"require_access_rules: {str(config.discord.require_access_rules).lower()}")
			print(f"attachment_download_timeout_seconds: {config.discord.attachment_download_timeout_seconds}")
			print(f"server_root: {config.storage.root_path}")
			print(f"fastdl_root: {config.storage.fastdl_root_path or 'not set'}")
			formats = ",".join(config.storage.compressed_formats) or "none"
			print(f"compressed_formats: {formats}")
			print(f"allow_overwrite: {str(config.storage.allow_overwrite).lower()}")
			print("content_types:")
			for name, content in sorted(config.content_types.items()):
				print(
					f"- {name}: channels={len(content.allowed_channel_ids)} "
					f"roles={len(content.allowed_role_ids)} "
					f"extensions={len(content.allowed_extensions)} "
					f"rules={len(content.path_rules)} "
					f"lowercase={str(content.require_lowercase_paths).lower()}"
				)
			return
	if args.command == "validate":
		storage = LocalStorage(config.storage)
		content_type = config.content_types.get(args.content_type)
		if content_type is None:
			print(f"error: unknown content type: {args.content_type}", file=sys.stderr)
			raise SystemExit(1)
		staging_dir = storage.create_staging_dir()
		try:
			try:
				validation = validate_zip_file(
					args.zip_path,
					content_type,
					(storage.root,),
				)
				extract_validated_zip(args.zip_path, staging_dir, validation.entries)
				preview = preview_install(storage, staging_dir)
			except (OSError, ValidationError, RuntimeError) as exc:
				print(f"error: {exc}", file=sys.stderr)
				raise SystemExit(1) from None
			print(f"zip: ok for {content_type.name}")
			print(validation_summary(validation, preview))
			return
		finally:
			storage.cleanup_staging_dir(staging_dir)
	if args.command == "pending":
		storage = LocalStorage(config.storage)
		if args.pending_command == "list":
			for pending in list_pending_uploads(storage):
				print(
					"\t".join(
						(
							pending.pending_id,
							pending.content_type,
							pending.created_at,
							str(len(pending.files)),
							pending.filename,
						)
					)
				)
			return
		if args.pending_command == "show":
			try:
				pending = read_pending_upload(storage, args.pending_id)
			except (FileNotFoundError, RuntimeError, ValueError) as exc:
				print(f"error: {exc}", file=sys.stderr)
				raise SystemExit(1) from None
			print(pending_summary(pending))
			return
		if args.pending_command == "prune":
			try:
				pruned = prune_pending_uploads(storage, args.older_than_days)
			except (RuntimeError, ValueError) as exc:
				print(f"error: {exc}", file=sys.stderr)
				raise SystemExit(1) from None
			print(f"pruned_pending_uploads: {len(pruned)}")
			for pending in pruned:
				print(f"- {pending.pending_id}\t{pending.content_type}\t{pending.filename}")
			return
	if args.command == "uploads":
		storage = LocalStorage(config.storage)
		if args.uploads_command == "list":
			for manifest in list_upload_manifests(storage):
				print(
					"\t".join(
						(
							str(manifest.get("upload_id", "")),
							str(manifest.get("status", "")),
							str(manifest.get("started_at", "")),
							str(manifest.get("completed_at", "")),
						)
					)
				)
			return
		if args.uploads_command == "show":
			try:
				manifest = read_upload_manifest(storage, args.upload_id)
			except (FileNotFoundError, RuntimeError, ValueError) as exc:
				print(f"error: {exc}", file=sys.stderr)
				raise SystemExit(1) from None
			print(json.dumps(manifest, indent=2, sort_keys=True))
			return
		if args.uploads_command == "recover":
			try:
				result = recover_upload(storage, args.upload_id, force=args.force)
			except (FileNotFoundError, RuntimeError, ValueError) as exc:
				print(f"error: {exc}", file=sys.stderr)
				raise SystemExit(1) from None
			print(f"upload_id: {result.upload_id}")
			print(f"status: {result.status}")
			print(f"deleted_files: {len(result.deleted_files)}")
			for path in result.deleted_files:
				print(f"- deleted {path}")
			print(f"restored_files: {len(result.restored_files)}")
			for path in result.restored_files:
				print(f"- restored {path}")
			return
		if args.uploads_command == "lock-status":
			lock_text = read_install_lock(storage)
			if lock_text is None:
				print("install lock: not present")
			else:
				print("install lock: present")
				print(lock_text.rstrip())
			return
		if args.uploads_command == "clear-lock":
			clear_install_lock(storage)
			print("install lock cleared")
			return

	from .discord_bot import FastDLUploadBot

	bot = FastDLUploadBot(config)
	bot.run(config.discord.token)


if __name__ == "__main__":
	main()
