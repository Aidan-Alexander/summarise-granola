#!/usr/bin/env python3
"""Create a formatted Google Doc from a markdown summary via Apps Script.

Sends the raw markdown to a deployed Google Apps Script web app which parses
the markdown and creates a Google Doc with proper formatting (headings,
bold/italic, lists, links, tables, etc).

The Apps Script source lives at: references/apps-script/Code.gs
Setup instructions: references/apps-script/README.md

Usage:
    python3 create_gdoc.py --title "Call summary: ..." --md summary.md
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
import urllib.error
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parent.parent


def load_webapp_url() -> str | None:
    config_path = SKILL_DIR / "config.json"
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
            return config.get("webapp_url")
        except (json.JSONDecodeError, KeyError):
            pass
    return None


def create_gdoc(title: str, markdown: str, webapp_url: str) -> dict:
    payload = json.dumps({"title": title, "markdown": markdown}).encode("utf-8")
    req = urllib.request.Request(
        webapp_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode("utf-8")
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                return {"error": f"Non-JSON response: {body[:500]}"}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8") if e.readable() else str(e)
        return {"error": f"HTTP {e.code}: {body[:500]}"}
    except Exception as e:
        return {"error": str(e)}


def main() -> None:
    ap = argparse.ArgumentParser(description="Create a formatted Google Doc from markdown via Apps Script")
    ap.add_argument("--title", required=True, help="Google Doc title")
    ap.add_argument("--md", type=Path, required=True, help="Markdown file to upload")
    ap.add_argument("--webapp-url", help="Apps Script URL (overrides config.json)")
    args = ap.parse_args()

    webapp_url = args.webapp_url or load_webapp_url()
    if not webapp_url:
        print("Error: no webapp_url in config.json and --webapp-url not provided", file=sys.stderr)
        print("See references/apps-script/README.md for setup instructions", file=sys.stderr)
        sys.exit(1)

    markdown = args.md.read_text(encoding="utf-8")
    result = create_gdoc(args.title, markdown, webapp_url)
    print(json.dumps(result))

    if result.get("error"):
        sys.exit(1)


if __name__ == "__main__":
    main()
