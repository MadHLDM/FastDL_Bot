# Changelog

## 1.0.0 - 2026-05-03

- Added optional admin approval queue for uploads before installation.
- Added Discord admin commands for pending uploads, approval, rejection, manifest lookup, and rollback.
- Added pending-upload integrity checks before approval.
- Tightened destructive Discord admin commands to require Administrator or explicit configured admin role.
- Added audit records for denied Discord admin command attempts.
- Added richer validation/install previews with file categories, largest files, compressed outputs, and destination conflicts.
- Added persisted pending-upload manifests under `.pending/`.
- Added CLI dry-run validation for local zip packages.
- Added CLI pending upload list/show/prune commands.
- Updated example config and environment files for approval and admin command settings.
- Added tests for pending uploads, install previews, and approval configuration.

## 0.2.0

- Hardened upload safety and recovery tooling.

## 0.1.0

- Initial Sven Co-op FastDL upload bot.
