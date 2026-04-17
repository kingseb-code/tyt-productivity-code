#!/usr/bin/env python3
"""
scripts/wp_setup.py — Standalone WordPress setup checker

Run this from the terminal after filling in your .env to verify the connection
and see what needs attention before the bot takes over maintenance.

Usage:
    python scripts/wp_setup.py
    python scripts/wp_setup.py --json    # raw JSON output
"""

import argparse
import json
import os
import sys

# Allow running from the repo root or the scripts/ directory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import wordpress as wp


def strip_markdown(text: str) -> str:
    """Remove Telegram markdown so terminal output is readable."""
    return (
        text
        .replace("*", "")
        .replace("_", "")
        .replace("`", "")
        .replace("\\", "")
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="WordPress setup checker for OpenClaw")
    parser.add_argument("--json", action="store_true", help="Output raw data as JSON instead of report")
    args = parser.parse_args()

    config = wp.load_wp_config()

    if args.json:
        print("Testing connections...")
        conns = wp.test_connections(config)

        print("Fetching plugins...")
        plugins = wp.get_all_plugins(config) if config.url else {}

        print("Checking security...")
        security = wp.check_security(config) if config.url else {}

        print("Checking backups...")
        backup = wp.check_last_backup(config) if config.url else {}

        output = {
            "connections": conns,
            "plugins": plugins,
            "security": security,
            "backup": backup,
        }
        print(json.dumps(output, indent=2, default=str))
    else:
        print("Running setup check…\n")
        report = wp.run_setup_check()
        print(strip_markdown(report))
        print()


if __name__ == "__main__":
    main()
