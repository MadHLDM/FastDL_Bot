# Sven Co-op FastDL Upload Bot

Discord bot that accepts `.zip` uploads, validates Sven Co-op content packages, installs the normal files into a server root, and optionally generates compressed `.gz`/`.bz2` files into a separate FastDL root.

The bot runs outside the game. AngelScript support is handled through package rules: map scripts are allowed under `scripts/maps/`, while scripts outside that folder are rejected by default.

## Features

- Slash command `/upload_fastdl content_type zip_file`.
- Slash command `/validate_fastdl content_type zip_file` to validate without installing.
- Optional admin approval queue before installation.
- Admin slash commands to list pending uploads, approve/reject them, inspect manifests, and roll back uploads.
- Optional message attachment uploads in channels mapped to one content type.
- Per-content role and channel validation.
- `.zip` uploads only.
- Compressed size, uncompressed size, per-file size, depth, and file-count limits.
- Protection against path traversal, absolute paths, Windows drive paths, Windows reserved names, case collisions, and symlinks.
- Strict folder/extension whitelist.
- `maps/*.res` validation: listed resources must exist in the zip or in the server root.
- Lowercase paths required by default to avoid Linux/FastDL case-sensitivity issues.
- Isolated staging extraction before install.
- Configurable overwrite policy with backup/rollback safety for local installs.
- Per-upload manifests in `.uploads/` for install auditing and crash recovery.
- Audit log in `logs/audit.tsv` and optional Discord audit channel.
- Per-user rate limits for upload and validate commands.
- Serialized install step to avoid concurrent writes racing on the same destination.

## Install

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

## Configure

Copy the examples:

```powershell
Copy-Item config.example.json config.json
Copy-Item .env.example .env
```

Use `config.json` for public rules such as whitelists, limits, and content types.

Use `.env` for instance-specific values:

- `FASTDL_DISCORD_TOKEN`: bot token.
- `FASTDL_DISCORD_GUILD_IDS`: comma-separated guild IDs.
- `FASTDL_DISCORD_AUDIT_CHANNEL_ID`: audit channel ID.
- `FASTDL_APPROVAL_REQUIRED`: validate uploads and hold them for admin approval instead of installing immediately.
- `FASTDL_ADMIN_ROLE_IDS`: comma-separated role IDs allowed to use admin review and rollback commands. Discord administrators also qualify. Users with Manage Server can inspect pending uploads and manifests, but cannot approve, reject, or roll back without one of these roles.
- `FASTDL_REQUIRE_ACCESS_RULES`: require every content type to define channel and role rules. Keep this `true` for public servers.
- `FASTDL_ATTACHMENT_DOWNLOAD_TIMEOUT_SECONDS`: timeout for downloading a Discord attachment.
- `FASTDL_RATE_LIMIT_MAX_REQUESTS`: max upload/validate requests per user per window. Set `0` to disable.
- `FASTDL_RATE_LIMIT_WINDOW_SECONDS`: rate-limit window in seconds.
- `FASTDL_SERVER_ROOT_PATH`: local root for normal server files.
- `FASTDL_FASTDL_ROOT_PATH`: optional local FastDL root. When set, compressed files are generated there.
- `FASTDL_COMPRESSED_FORMATS`: comma-separated compressed formats, for example `gz`.
- `FASTDL_INSTALL_LOCK_TIMEOUT_SECONDS`: timeout for the cross-process install lock.
- `FASTDL_SFTP_ENABLED`: publish generated FastDL files to a remote SFTP server after local install.
- `FASTDL_SFTP_HOST`, `FASTDL_SFTP_PORT`, and `FASTDL_SFTP_USERNAME`: remote SSH/SFTP login target.
- `FASTDL_SFTP_PRIVATE_KEY_PATH` or `FASTDL_SFTP_PASSWORD`: SFTP authentication. Prefer a private key on production.
- `FASTDL_SFTP_REMOTE_FASTDL_ROOT_PATH`: remote web root that serves FastDL files.
- `FASTDL_MAP_CHANNEL_IDS` and `FASTDL_MAP_ROLE_IDS`: content-specific channel/role IDs. Equivalent variables exist for `PLAYERMODEL`, `SOUNDS`, `SPRITES`, and `ANGELSCRIPT_MAP`.

Values from `.env` override equivalent fields from `config.json`.

`config.json` and `.env` are ignored by Git and must not be committed.

By default, `FASTDL_REQUIRE_ACCESS_RULES=true`. With that setting, every configured content type must have at least one allowed channel ID and one allowed role ID. For temporary local-only testing, set it to `false` explicitly.

Paths can be Windows or Linux. Examples:

```dotenv
FASTDL_SERVER_ROOT_PATH=C:/servers/svencoop/svencoop_addon
FASTDL_FASTDL_ROOT_PATH=C:/servers/svencoop-fastdl
```

```dotenv
FASTDL_SERVER_ROOT_PATH=/opt/svencoop/svencoop_addon
FASTDL_FASTDL_ROOT_PATH=/var/www/fastdl/svencoop
```

Inside zip files, always use `/` as the separator, even on Windows: `maps/test.bsp`, never `maps\test.bsp`.

By default, all content types require lowercase paths. This prevents packages that work on Windows but break on Linux:

```text
maps/test.bsp
sound/mymap/ambience.wav
```

Avoid:

```text
Maps/Test.bsp
Sound/MyMap/Ambience.wav
```

Before publishing a fork or mirror, verify:

- no `config.json`, `.env`, real logs, pending uploads, manifests, or local backups are tracked;
- `config.example.json` contains placeholders only;
- the bot token never appears in issues, screenshots, public logs, stack traces, or commits;
- `storage.root_path` points to a dedicated folder with minimal write permissions;
- no more than one bot instance should usually run, but installs are protected by a cross-process lock file;
- the system user running the bot cannot write outside the configured roots;
- `FASTDL_REQUIRE_ACCESS_RULES` is `true` in public deployments;
- `allow_overwrite` remains `false` until you define a clear replacement policy.

## Run

Validate configuration without starting Discord:

```powershell
python -m fastdl_upload_bot.main --config config.json config check
```

Dry-run a local package without installing:

```powershell
python -m fastdl_upload_bot.main --config config.json validate path\to\package.zip --content-type map
```

```powershell
fastdl-upload-bot --config config.json
```

Or:

```powershell
python -m fastdl_upload_bot.main --config config.json
```

The example config uses slash commands only and sets `"enable_message_uploads": false`. This avoids privileged intents in the Discord Developer Portal.

If you want message attachment uploads in mapped channels, set `"enable_message_uploads": true` in `config.json` or `.env`, and enable `Message Content Intent` in the Discord Developer Portal.

The bot does not request `Server Members Intent`.

If your FastDL is a second mirrored root with files like `maps/test.bsp.gz`, configure this in `.env`:

```dotenv
FASTDL_SERVER_ROOT_PATH=C:/server/svencoop_addon
FASTDL_FASTDL_ROOT_PATH=C:/server-fastdl
FASTDL_COMPRESSED_FORMATS=gz
```

With that setup, the bot installs `maps/test.bsp` in the normal root and generates `maps/test.bsp.gz` only in the FastDL root. If `fastdl_root_path` is empty or `null`, compressed files are generated next to the original files.

## Remote FastDL over SFTP

If the bot runs on one server, for example an Oracle VPS, while the FastDL web server lives elsewhere, keep local roots on the bot machine and enable SFTP publishing:

```dotenv
FASTDL_SERVER_ROOT_PATH=/srv/svencoop/svencoop_addon
FASTDL_FASTDL_ROOT_PATH=/var/lib/fastdl-upload-bot/fastdl-cache
FASTDL_COMPRESSED_FORMATS=gz

FASTDL_SFTP_ENABLED=true
FASTDL_SFTP_HOST=fastdl.example.com
FASTDL_SFTP_PORT=22
FASTDL_SFTP_USERNAME=fastdl-bot
FASTDL_SFTP_PRIVATE_KEY_PATH=/home/fastdl-bot/.ssh/fastdl_upload
FASTDL_SFTP_REMOTE_FASTDL_ROOT_PATH=/var/www/fastdl/svencoop
FASTDL_SFTP_STRICT_HOST_KEY_CHECKING=true
```

The SFTP user only needs write access to the configured remote FastDL root. With `allow_overwrite=false`, the bot refuses to publish over an existing remote file. Keep strict host-key checking enabled and add the FastDL server key to the Oracle VPS user's `known_hosts`.

To validate a package without installing:

```text
/validate_fastdl content_type: map zip_file: test.zip
```

The validation response includes the file count, uncompressed size, top folders, extensions, largest files, compressed files that would be generated, and destination conflicts.

## Admin Approval and Discord Operations

Set `approval_required` to `true` in `config.json`, or set this in `.env`:

```dotenv
FASTDL_APPROVAL_REQUIRED=true
FASTDL_ADMIN_ROLE_IDS=111111111111111111,222222222222222222
```

With approval enabled, `/upload_fastdl` validates and stages the package under `.pending/` instead of installing it. The pending manifest records SHA-256 hashes for every staged file; approval refuses to install if staged content changed, disappeared, or gained extra files. Admins can then use:

```text
/fastdl_pending
/fastdl_approve pending_id: 20260503T210000Z-abc123def0
/fastdl_reject pending_id: 20260503T210000Z-abc123def0 reason: wrong package
```

Admin commands also expose manifest history and rollback:

```text
/fastdl_uploads
/fastdl_uploads upload_id: 20260501T232531Z-1fdcb5d14b
/fastdl_rollback upload_id: 20260501T232531Z-1fdcb5d14b force: true
```

Rollback uses the same manifest hash checks as the CLI recovery path and refuses to delete modified installed files.

Pending uploads can also be inspected and cleaned up from the CLI:

```powershell
python -m fastdl_upload_bot.main --config config.json pending list
python -m fastdl_upload_bot.main --config config.json pending show 20260503T210000Z-abc123def0
python -m fastdl_upload_bot.main --config config.json pending prune --older-than-days 7
```

## Upload Manifests and Recovery

Every install writes a JSON manifest under `.uploads/` inside `FASTDL_SERVER_ROOT_PATH`. Manifests include installed paths and SHA-256 hashes. Recovery refuses to delete a file if it changed after the upload.

List recent manifests:

```powershell
python -m fastdl_upload_bot.main --config config.json uploads list
```

Inspect one upload:

```powershell
python -m fastdl_upload_bot.main --config config.json uploads show 20260501T232531Z-1fdcb5d14b
```

Recover an interrupted upload whose manifest is still `started` or `failed`:

```powershell
python -m fastdl_upload_bot.main --config config.json uploads recover 20260501T232531Z-1fdcb5d14b
```

Rollback of an already `installed` upload is refused by default. If you intentionally want to remove the uploaded files and restore backups recorded in the manifest:

```powershell
python -m fastdl_upload_bot.main --config config.json uploads recover 20260501T232531Z-1fdcb5d14b --force
```

Check or clear a stale install lock:

```powershell
python -m fastdl_upload_bot.main --config config.json uploads lock-status
python -m fastdl_upload_bot.main --config config.json uploads clear-lock --force
```

Only clear the lock after confirming the bot process is stopped.

## Included Content Types

- `map`: requires `.bsp` under `maps/`. Allows common resources under `models/`, `sound/`, `sprites/`, `gfx/`, `overviews/`, `materials/`, `resource/`, `particles/`, root `.wad`, and AngelScript only under `scripts/maps/`.
- `playermodel`: requires `.mdl`.
- `sounds`: requires at least one `.wav`, `.mp3`, or `.ogg` under `sound/`.
- `sprites`: requires `.spr` under `sprites/`.
- `angelscript_map`: requires `.as` and allows only `scripts/maps/*.as` or `scripts/maps/*.inc`.

## Sven Co-op / AngelScript Notes

For maps, keep the game folder structure inside the zip:

```text
maps/mymap.bsp
maps/mymap.res
scripts/maps/mymap/main.as
sound/mymap/ambience.wav
sprites/mymap/hud.spr
```

The bot does not guess destinations. It copies the validated structure into the configured root.

Plugin scripts under `scripts/plugins/` are not accepted by the example config because they are operationally more sensitive than FastDL content. If you want a separate admin-plugin workflow, create another content type with exclusive channel/role IDs and smaller limits.

When a map package includes `maps/*.res`, the bot validates every reference. A resource listed in the `.res` must be inside the zip or already exist in `FASTDL_SERVER_ROOT_PATH`. References outside the whitelist, such as `scripts/plugins/admin.as`, reject the upload.

## Security

The bot is designed to fail closed: strange paths, extensions outside the whitelist, oversized content, case duplicates, symlinks, or AngelScript outside `scripts/maps/` reject the whole upload.

Validation errors are shown to the uploader. Internal errors return a generic Discord message to avoid leaking host paths or stack traces; check the process stdout/stderr for details.

Local audit logs are written as sanitized JSONL in `logs/audit.jsonl`.

For security issues, see `SECURITY.md`.

## Tests

```powershell
pytest
```

The project includes GitHub Actions to run the suite on Windows and Linux.

## License

MIT. See `LICENSE`.
