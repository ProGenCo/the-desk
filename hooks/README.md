# Git hooks

Source-controlled git hooks for The Desk.

## Install

```bash
./hooks/install.sh
```

Idempotent — symlinks `.git/hooks/<name>` to the file in this directory. Edits propagate without reinstalling.

## What's here

### `pre-commit`

If `portfolio_data.json` is in the staged files, validates that:
- The file is valid JSON
- It matches the shape contract from `desk_io.validate_data_shape()` (required keys: `accounts`, `daily_log`, `momentum`)
- Every account has a known `status` (`funded`/`shelved`/`breached`/`evaluation`)
- Every `daily_log` entry has a `date`

Exit non-zero → commit is blocked. To bypass (rarely needed): `git commit --no-verify`.

This catches the kind of stale-data drift that lived for ~2 weeks before being noticed (extraction_triggers showing TS-204 -$29 when actual was -$902).
