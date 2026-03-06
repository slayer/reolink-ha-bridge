#!/usr/bin/env python3
"""Download Reolink camera recordings filtered by AI detection type.

Standalone script using only Python stdlib — no external dependencies.
Talks directly to Reolink cameras via their HTTP API.

Usage:
    python3 reolink_download.py \
        --host 10.8.12.201 \
        --password SECRET \
        --type person,vehicle \
        --since 2h \
        --max 3 \
        --stream sub \
        --output /path/to/dir \
        --manifest \
        --cleanup 60
"""

from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Reolink HTTP API helpers
# ---------------------------------------------------------------------------

# Disable SSL verification — cameras use self-signed certs
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE


def api_request(host: str, cmd: str, params: list[dict], token: str = "") -> list[dict]:
    """Send a POST request to Reolink camera API."""
    url = f"http://{host}/cgi-bin/api.cgi?cmd={cmd}"
    if token:
        url += f"&token={token}"

    body = json.dumps(params).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30, context=SSL_CTX) as resp:
            data = json.loads(resp.read())
            if not isinstance(data, list):
                data = [data]
            return data
    except urllib.error.URLError as e:
        raise ConnectionError(f"Failed to connect to {host}: {e}") from e


def login(host: str, user: str, password: str) -> str:
    """Login to camera, return token."""
    result = api_request(host, "Login", [
        {"cmd": "Login", "action": 0, "param": {
            "User": {"userName": user, "password": password}
        }}
    ])
    token = result[0].get("value", {}).get("Token", {}).get("name", "")
    if not token:
        error = result[0].get("error", {}).get("detail", "unknown error")
        raise RuntimeError(f"Login failed: {error}")
    return token


def logout(host: str, token: str) -> None:
    """Logout from camera."""
    try:
        api_request(host, "Logout", [
            {"cmd": "Logout", "action": 0}
        ], token=token)
    except Exception:
        pass


def search_recordings(
    host: str,
    token: str,
    channel: int,
    start: datetime,
    end: datetime,
    stream: str = "sub",
) -> list[dict]:
    """Search for recordings in the given time range."""
    stream_type = "sub" if stream == "sub" else "main"
    result = api_request(host, "Search", [
        {"cmd": "Search", "action": 0, "param": {
            "Search": {
                "channel": channel,
                "onlyStatus": 0,
                "streamType": stream_type,
                "StartTime": {
                    "year": start.year,
                    "mon": start.month,
                    "day": start.day,
                    "hour": start.hour,
                    "min": start.minute,
                    "sec": start.second,
                },
                "EndTime": {
                    "year": end.year,
                    "mon": end.month,
                    "day": end.day,
                    "hour": end.hour,
                    "min": end.minute,
                    "sec": end.second,
                },
            }
        }}
    ], token=token)

    search_result = result[0].get("value", {}).get("SearchResult", {})
    files = search_result.get("File", [])
    return files if isinstance(files, list) else []


def download_file(host: str, token: str, filename: str, dest: Path) -> int:
    """Download a recording file. Returns file size in bytes."""
    url = f"http://{host}/cgi-bin/api.cgi?cmd=Download&source={filename}&output={filename}&token={token}"
    req = urllib.request.Request(url, method="GET")

    with urllib.request.urlopen(req, timeout=120, context=SSL_CTX) as resp:
        total = 0
        with open(dest, "wb") as f:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                total += len(chunk)
    return total


# ---------------------------------------------------------------------------
# Filename trigger parsing
# ---------------------------------------------------------------------------

# Trigger type flags (bitmask)
TRIGGER_PERSON = 1
TRIGGER_VEHICLE = 2
TRIGGER_PET = 4
TRIGGER_MOTION = 8

TRIGGER_NAMES = {
    TRIGGER_PERSON: "person",
    TRIGGER_VEHICLE: "vehicle",
    TRIGGER_PET: "pet",
    TRIGGER_MOTION: "motion",
}


def parse_triggers_from_filename(filename: str) -> int:
    """Parse trigger flags from recording filename hex field.

    Filename formats:
      Old: RecM02_20230515_071811_071835_6D28900_13CE8C7
      New: RecM07_20260220_000000_000024_0_6D28808000_E386CE

    Hex flags field is second-to-last underscore-separated part.
    Trigger nibble layout (at offset 4 in hex field):
      nibble[4]: bit 2 = Person, bit 0 = Vehicle
      nibble[5]: bit 3 = Pet
      nibble[6]: bit 3 = Motion
    """
    basename = filename.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    parts = basename.split("_")

    if len(parts) < 6:
        return 0

    hex_field = parts[-2]
    if len(hex_field) < 7:
        return 0

    try:
        nib_4 = int(hex_field[4], 16)
        nib_5 = int(hex_field[5], 16)
        nib_6 = int(hex_field[6], 16)
    except (ValueError, IndexError):
        return 0

    triggers = 0
    if nib_4 & 4:
        triggers |= TRIGGER_PERSON
    if nib_4 & 1:
        triggers |= TRIGGER_VEHICLE
    if nib_5 & 8:
        triggers |= TRIGGER_PET
    if nib_6 & 8:
        triggers |= TRIGGER_MOTION

    return triggers


def get_primary_trigger_name(triggers: int) -> str:
    """Get human-readable name for the primary trigger."""
    for val, name in TRIGGER_NAMES.items():
        if triggers & val:
            return name
    return "recording"


def parse_type_arg(type_str: str) -> Optional[int]:
    """Parse --type argument into trigger bitmask. Returns None for 'all'."""
    types = [t.strip().lower() for t in type_str.split(",")]
    if "all" in types:
        return None

    mask = 0
    valid = {"person": TRIGGER_PERSON, "vehicle": TRIGGER_VEHICLE,
             "pet": TRIGGER_PET, "motion": TRIGGER_MOTION}
    for t in types:
        if t not in valid:
            raise ValueError(f"Unknown type: {t!r}. Valid: {', '.join(valid)}")
        mask |= valid[t]
    return mask if mask else None


def filter_recordings(files: list[dict], trigger_filter: Optional[int]) -> list[dict]:
    """Filter recording files by trigger type."""
    if trigger_filter is None:
        return files
    result = []
    for f in files:
        triggers = parse_triggers_from_filename(f.get("name", ""))
        if triggers & trigger_filter:
            result.append(f)
    return result


# ---------------------------------------------------------------------------
# Time parsing
# ---------------------------------------------------------------------------

def parse_since(since_str: str) -> tuple[datetime, datetime]:
    """Parse relative time like '30m', '2h', '3d' into (start, end)."""
    match = re.fullmatch(r"(\d+)([mhd])", since_str)
    if not match:
        raise ValueError(f"Invalid --since format: {since_str!r}. Use e.g. 30m, 2h, 3d")

    amount = int(match.group(1))
    if amount <= 0:
        raise ValueError(f"Value must be > 0, got {since_str!r}")
    unit = match.group(2)
    multiplier = {"m": 60, "h": 3600, "d": 86400}[unit]

    end = datetime.now()
    start = end - timedelta(seconds=amount * multiplier)
    return start, end


def parse_recording_time(time_dict: dict) -> datetime:
    """Parse Reolink time dict {year, mon, day, hour, min, sec} to datetime."""
    return datetime(
        time_dict["year"], time_dict["mon"], time_dict["day"],
        time_dict["hour"], time_dict["min"], time_dict["sec"],
    )


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

def cleanup_old_files(directory: Path, max_age_minutes: int) -> int:
    """Remove files older than max_age_minutes. Returns count of removed files."""
    if not directory.exists():
        return 0
    cutoff = time.time() - max_age_minutes * 60
    removed = 0
    for f in directory.glob("*.mp4"):
        if f.stat().st_mtime < cutoff:
            f.unlink()
            removed += 1
    # Also clean up empty manifest
    manifest = directory / "manifest.json"
    if manifest.exists() and manifest.stat().st_mtime < cutoff:
        manifest.unlink()
        removed += 1
    return removed


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download Reolink camera recordings filtered by AI detection type.",
    )

    parser.add_argument("--host", required=True, help="Camera IP address")
    parser.add_argument("--user", default="admin", help="Username (default: admin)")
    parser.add_argument("--password", required=True, help="Camera password")
    parser.add_argument("--channel", type=int, default=0, help="Camera channel (default: 0)")
    parser.add_argument(
        "--type", dest="detection_type", default="all",
        help="Detection type filter: person,vehicle,pet,motion,all (comma-separated, default: all)",
    )
    parser.add_argument("--since", default="2h", help="Time period: 30m, 2h, 1d (default: 2h)")
    parser.add_argument("--max", dest="max_files", type=int, default=3,
                        help="Max files to download (default: 3)")
    parser.add_argument("--stream", choices=["main", "sub"], default="sub",
                        help="Stream quality (default: sub)")
    parser.add_argument("--output", default=".", help="Output directory (default: .)")
    parser.add_argument("--manifest", action="store_true",
                        help="Write manifest.json with file list")
    parser.add_argument("--cleanup", type=int, default=0, metavar="MINUTES",
                        help="Remove files older than N minutes before downloading")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Cleanup old files first
    if args.cleanup > 0:
        removed = cleanup_old_files(output_dir, args.cleanup)
        if removed:
            print(f"Cleaned up {removed} old files", file=sys.stderr)

    # Parse trigger filter
    try:
        trigger_filter = parse_type_arg(args.detection_type)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # Parse time range
    try:
        start, end = parse_since(args.since)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    trigger_desc = args.detection_type if trigger_filter else "all"

    token = ""
    try:
        # Login
        print(f"Connecting to {args.host}...", file=sys.stderr)
        token = login(args.host, args.user, args.password)
        print(f"Searching recordings ({trigger_desc}) from {start:%H:%M} to {end:%H:%M}...",
              file=sys.stderr)

        # Search
        files = search_recordings(
            args.host, token, args.channel, start, end, args.stream,
        )
        print(f"Found {len(files)} total recordings", file=sys.stderr)

        # Filter by trigger type
        filtered = filter_recordings(files, trigger_filter)
        print(f"Matched {len(filtered)} recordings for: {trigger_desc}", file=sys.stderr)

        if not filtered:
            print("No recordings found", file=sys.stderr)
            if args.manifest:
                manifest = {
                    "camera": args.host,
                    "type": args.detection_type,
                    "files": [],
                    "count": 0,
                }
                (output_dir / "manifest.json").write_text(json.dumps(manifest))
            return 0

        # Sort by start time (newest first) and limit
        filtered.sort(
            key=lambda f: f.get("StartTime", {}).get("hour", 0) * 3600
            + f.get("StartTime", {}).get("min", 0) * 60
            + f.get("StartTime", {}).get("sec", 0),
            reverse=True,
        )
        filtered = filtered[:args.max_files]

        # Download
        downloaded_files: list[str] = []
        for i, rec in enumerate(filtered, 1):
            name = rec.get("name", "")
            start_t = rec.get("StartTime", {})
            end_t = rec.get("EndTime", {})

            triggers = parse_triggers_from_filename(name)
            trigger_name = get_primary_trigger_name(triggers)

            # Build output filename
            st = f"{start_t.get('hour', 0):02d}{start_t.get('min', 0):02d}{start_t.get('sec', 0):02d}"
            et = f"{end_t.get('hour', 0):02d}{end_t.get('min', 0):02d}{end_t.get('sec', 0):02d}"
            out_name = f"{trigger_name}_{st}_{et}.mp4"
            dest = output_dir / out_name

            print(f"  [{i}/{len(filtered)}] Downloading {out_name}...", file=sys.stderr)

            try:
                size = download_file(args.host, token, name, dest)
                size_mb = size / (1024 * 1024)
                print(f"           Saved: {dest} ({size_mb:.1f} MB)", file=sys.stderr)
                downloaded_files.append(str(dest))
            except Exception as e:
                print(f"           FAILED: {e}", file=sys.stderr)
                if dest.exists():
                    dest.unlink()

        # Write manifest
        if args.manifest:
            manifest = {
                "camera": args.host,
                "type": args.detection_type,
                "files": downloaded_files,
                "count": len(downloaded_files),
            }
            manifest_path = output_dir / "manifest.json"
            manifest_path.write_text(json.dumps(manifest))
            print(f"Manifest: {manifest_path}", file=sys.stderr)

        print(f"Done. Downloaded {len(downloaded_files)} files.", file=sys.stderr)

        # Print file list to stdout (for HA shell_command consumption)
        for f in downloaded_files:
            print(f)

        return 0

    except ConnectionError as e:
        print(f"Connection error: {e}", file=sys.stderr)
        return 1
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        return 1
    finally:
        if token:
            logout(args.host, token)


if __name__ == "__main__":
    sys.exit(main())
