# CLAUDE.md

## Overview

Standalone Python script to download Reolink camera recordings filtered by AI detection type. Uses only Python stdlib (no dependencies). Designed for Home Assistant `shell_command` integration.

## Commands

```bash
# Basic usage
python3 reolink_download.py --host 10.8.12.201 --password PASS --type person --since 2h

# Full options for HA integration
python3 reolink_download.py \
  --host 10.8.12.201 --password PASS \
  --type person,vehicle --since 2h --max 3 \
  --stream sub --output /config/www/reolink_downloads \
  --manifest --cleanup 60

# All recordings, main stream
python3 reolink_download.py --host 10.8.12.201 --password PASS --type all --since 1d --stream main
```

No tests yet. To test against a real camera:
```bash
python3 reolink_download.py --host 10.8.12.201 --password SECRET --type person --since 2h --max 1 --output /tmp/test --manifest
```

## Architecture

Single-file script (`reolink_download.py`), ~300 lines. No classes, just functions.

### Key sections:
- **API helpers** (`api_request`, `login`, `logout`, `search_recordings`, `download_file`) — HTTP calls to Reolink camera API via `urllib`
- **Trigger parsing** (`parse_triggers_from_filename`) — Extracts AI detection types from hex flags in recording filenames
- **CLI** (`build_parser`, `main`) — argparse CLI with `--type`, `--since`, `--max`, `--stream`, `--manifest`, `--cleanup`

### Reolink HTTP API flow:
1. `POST /cgi-bin/api.cgi?cmd=Login` → get token
2. `POST /cgi-bin/api.cgi?cmd=Search&token=TOKEN` → list recordings
3. `GET /cgi-bin/api.cgi?cmd=Download&source=NAME&token=TOKEN` → download file
4. `POST /cgi-bin/api.cgi?cmd=Logout&token=TOKEN` → cleanup

### Filename hex flag format:
```
RecS07_20250219_111146_111238_0_A714C0A000_21E67C.mp4
                                 ^^^^---
                                 Nibble[4]: bit 2=Person, bit 0=Vehicle
                                 Nibble[5]: bit 3=Pet
                                 Nibble[6]: bit 3=Motion
```

### Output:
- Files: `{trigger}_{HHMMSS}_{HHMMSS}.mp4` (e.g., `person_143000_143500.mp4`)
- Manifest: `manifest.json` with `{camera, type, files[], count}`
- Stdout: file paths (one per line) for script consumption
- Stderr: status messages

### Exit codes:
- 0: success (even if 0 files found)
- 1: error (connection, auth, etc.)

## HA Integration

The companion HA package lives at `~/Shared/ha_config/packages/reolink_download.yaml`.
Script is copied to `/config/scripts/reolink_download.py` on the HA instance.
Telegram command: `/download <type> [camera] [since]`

## Code Style

- Stdlib only — no pip dependencies
- Functions, no classes
- Print status to stderr, file paths to stdout
- Consistent error handling with early returns
