# Security Policy

## Public repo rules

Do not commit real instance data:

- `config.json`
- `.env` files
- Discord bot tokens
- channel, guild, or role IDs from private servers if you consider them sensitive
- runtime logs
- local FastDL backups
- uploaded zip files

The repository should only contain `config.example.json` with placeholders.

## Operating recommendations

- Run the bot as a dedicated system user.
- Give that user write access only to the configured FastDL or `svencoop_addon` root.
- Keep `allow_overwrite` disabled unless you have a reviewed replacement/rollback policy.
- Use a private audit channel visible only to server operators.
- Rotate the Discord token immediately if it is ever pasted into chat, logs, screenshots, commits, or CI output.
- Review every whitelist change. AngelScript uploads are intentionally limited to `scripts/maps/` in the example config.
- Keep dependencies updated and review `discord.py` release notes before upgrading across major versions.

## Reporting vulnerabilities

If this is published on GitHub, enable private vulnerability reporting or provide a private contact address in this section before announcing the repository publicly.

Please include:

- affected version or commit;
- impact;
- reproduction steps;
- whether a token, host path, or private server ID was exposed.
