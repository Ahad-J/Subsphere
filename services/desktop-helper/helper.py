"""SubSphere Desktop Activity Helper (Opt-In Allowlist Observer).

Privacy guarantees:
- Disabled by default (`--enable` or `SUBSPHERE_HELPER_ENABLED=true` required).
- Records ONLY allowlisted executable names + active observation seconds.
- NEVER captures window titles, keystrokes, prompts, file contents, URLs, or tokens.
"""
from __future__ import annotations

import argparse
import os
import time
from typing import Iterable

import psutil
import requests


DEFAULT_ALLOWLIST = ("cursor", "code", "claude", "chatgpt", "figma", "notion")


def normalize_process_name(proc_name: str) -> str:
    cleaned = (proc_name or "").strip().lower()
    if cleaned.endswith(".exe"):
        cleaned = cleaned[:-4]
    return cleaned


def sample_allowlisted_processes(allowlist: Iterable[str]) -> list[str]:
    """Return sorted unique allowlisted process names currently running."""
    allowed = {normalize_process_name(item) for item in allowlist if item.strip()}
    matched: set[str] = set()
    for proc in psutil.process_iter(["name"]):
        try:
            name = normalize_process_name(proc.info.get("name") or "")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if name and name in allowed:
            matched.add(name)
    return sorted(matched)


def build_activity_payload(app_name: str, observed_seconds: int) -> dict[str, object]:
    """Construct a privacy-safe payload conforming to POST /api/v1/personal/activity."""
    if observed_seconds <= 0:
        raise ValueError("observed_seconds must be positive")
    return {
        "domain_or_app": normalize_process_name(app_name),
        "source": "ALLOWED_DESKTOP_ACTIVITY",
        "observed_active_seconds": int(observed_seconds),
        "visit_count": 1,
    }


def send_activity(api_base: str, access_token: str, payload: dict[str, object]) -> int:
    resp = requests.post(
        f"{api_base.rstrip('/')}/api/v1/personal/activity/aggregate",
        headers={"Authorization": f"Bearer {access_token}"},
        json=payload,
        timeout=10,
    )
    return resp.status_code


def main() -> None:
    parser = argparse.ArgumentParser(description="SubSphere opt-in desktop activity helper")
    parser.add_argument("--api-base", default=os.getenv("SUBSPHERE_API_URL", "http://localhost:8000"))
    parser.add_argument("--token", default=os.getenv("SUBSPHERE_ACCESS_TOKEN", ""))
    parser.add_argument("--allowlist", default=",".join(DEFAULT_ALLOWLIST))
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--once", action="store_true", help="Sample once and exit")
    parser.add_argument("--enable", action="store_true", help="Explicit opt-in flag")
    args = parser.parse_args()

    enabled = args.enable or os.getenv("SUBSPHERE_HELPER_ENABLED", "false").lower() == "true"
    if not enabled:
        print("Desktop helper is disabled (opt-in required via --enable).")
        return

    allowlist = [item.strip() for item in args.allowlist.split(",") if item.strip()]
    while True:
        apps = sample_allowlisted_processes(allowlist)
        for app_name in apps:
            payload = build_activity_payload(app_name, args.interval)
            if args.token:
                status_code = send_activity(args.api_base, args.token, payload)
                print(f"Sent {payload['domain_or_app']} ({args.interval}s) -> HTTP {status_code}")
            else:
                print(f"Dry-run sampled: {payload}")
        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
