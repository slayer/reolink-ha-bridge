# reolink-ha-bridge

Standalone Python script to download Reolink camera recordings filtered by AI detection type (person, vehicle, pet, motion). Designed for Home Assistant integration via `shell_command`.

**No dependencies** — uses only Python 3.9+ stdlib.

## Why?

The native Reolink HA integration doesn't support downloading recordings filtered by AI detection type. This script talks directly to cameras via their HTTP API.

## Usage

```bash
python3 reolink_download.py \
  --host 10.8.12.201 \
  --password SECRET \
  --type person \
  --since 2h \
  --max 3 \
  --stream sub \
  --output /tmp/downloads \
  --manifest
```

### Arguments

| Argument | Default | Description |
|----------|---------|-------------|
| `--host` | required | Camera IP address |
| `--password` | required | Camera password |
| `--user` | `admin` | Camera username |
| `--channel` | `0` | Camera channel |
| `--type` | `all` | Comma-separated: `person`, `vehicle`, `pet`, `motion`, `all` |
| `--since` | `2h` | Time period: `30m`, `2h`, `1d`, etc. |
| `--max` | `3` | Max files to download |
| `--stream` | `sub` | Stream quality: `main` (full HD) or `sub` (smaller, good for Telegram) |
| `--output` | `.` | Output directory |
| `--manifest` | off | Write `manifest.json` with file list |
| `--cleanup` | `0` | Remove files older than N minutes before downloading |

### Examples

```bash
# Person detections from last 2 hours
python3 reolink_download.py --host 10.8.12.201 --password PASS --type person --since 2h

# Person + vehicle from last 4 hours, max 5 files
python3 reolink_download.py --host 10.8.12.201 --password PASS --type person,vehicle --since 4h --max 5

# All recordings from last day, sub stream for Telegram
python3 reolink_download.py --host 10.8.12.201 --password PASS --type all --since 1d --stream sub

# With manifest and cleanup (for HA integration)
python3 reolink_download.py --host 10.8.12.201 --password PASS --type person --since 2h \
  --output /config/www/reolink_downloads --manifest --cleanup 60
```

### Output

- Downloaded `.mp4` files saved to output directory with descriptive names: `person_143000_143500.mp4`
- File paths printed to stdout (one per line) for script consumption
- Status messages printed to stderr
- Exit code 0 on success, 1 on error
- With `--manifest`: writes `manifest.json`:

```json
{
  "camera": "10.8.12.201",
  "type": "person",
  "files": ["/path/to/person_143000_143500.mp4"],
  "count": 1
}
```

## How it works

1. Login to camera via `POST /cgi-bin/api.cgi?cmd=Login`
2. Search recordings via `POST /cgi-bin/api.cgi?cmd=Search`
3. Filter by AI type using hex flags in filenames (bit 4:2=person, 4:0=vehicle, 5:3=pet, 6:3=motion)
4. Download files via `GET /cgi-bin/api.cgi?cmd=Download`
5. Logout via `POST /cgi-bin/api.cgi?cmd=Logout`

### Filename hex flag parsing

Reolink encodes detection types in recording filenames:

```
RecS07_20250219_111146_111238_0_A714C0A000_21E67C.mp4
                                 ^^^^---
                                 hex flags field (second-to-last underscore part)

Nibble at offset 4: bit 2 = Person, bit 0 = Vehicle
Nibble at offset 5: bit 3 = Pet
Nibble at offset 6: bit 3 = Motion
```

## Home Assistant Integration

See the HA package in [`ha_config/packages/reolink_download.yaml`](https://github.com/kadykov/ha_config) for:
- `shell_command` that calls this script
- Telegram `/download` command
- Automatic cleanup

### Setup

1. Copy `reolink_download.py` to `/config/scripts/` on your HA instance
2. Add `reolink_password` to `secrets.yaml`
3. Add the package from `packages/reolink_download.yaml`
4. Restart HA
5. Send `/download person` via Telegram
