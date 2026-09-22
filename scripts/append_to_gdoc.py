#!/usr/bin/env python3
"""Append a markdown file to the end of an existing Google Doc via Apps Script.

Used to put the tidied transcript at the bottom of the call-summary doc, so the
summary and the transcript it came from live in one shareable place.

Sends the raw markdown to the same deployed Apps Script web app that
create_gdoc.py uses; it renders the markdown with proper formatting onto the end
of the target doc, after a page break.

The Apps Script source lives at: references/apps-script/Code.gs
Setup instructions: references/apps-script/README.md

Note: if you deployed the web app before append support was added, redeploy it
from the current Code.gs or this will fail with "no doc_id handler".

Usage:
    python3 append_to_gdoc.py --doc-id <id> --md transcript.md
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


def append_to_gdoc(doc_id: str, markdown: str, webapp_url: str,
                   page_break: bool = True) -> dict:
    payload = json.dumps({
        "doc_id": doc_id,
        "markdown": markdown,
        "page_break": page_break,
    }).encode("utf-8")
    req = urllib.request.Request(
        webapp_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
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
    ap = argparse.ArgumentParser(
        description="Append markdown to an existing Google Doc via Apps Script")
    ap.add_argument("--doc-id", required=True, help="Target Google Doc ID")
    ap.add_argument("--md", type=Path, required=True, help="Markdown file to append")
    ap.add_argument("--no-page-break", action="store_true",
                    help="Append directly without starting a new page")
    ap.add_argument("--webapp-url", help="Apps Script URL (overrides config.json)")
    args = ap.parse_args()

    webapp_url = args.webapp_url or load_webapp_url()
    if not webapp_url:
        print("Error: no webapp_url in config.json and --webapp-url not provided",
              file=sys.stderr)
        print("See references/apps-script/README.md for setup instructions",
              file=sys.stderr)
        sys.exit(1)

    markdown = args.md.read_text(encoding="utf-8")
    result = append_to_gdoc(args.doc_id, markdown, webapp_url,
                            page_break=not args.no_page_break)
    print(json.dumps(result))

    if result.get("error"):
        sys.exit(1)


if __name__ == "__main__":
    main()
