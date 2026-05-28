#!/usr/bin/env python3
"""Add a hyperlinked date to the 'Meeting recording summaries' tab of a Google Doc.

Calls a deployed Google Apps Script web app that uses the Docs API to insert
a linked date string at the top of the named tab.

Usage:
    python3 add_summary_link.py --doc-id DOC_ID --date "26 May 2026" --url "https://..."
    python3 add_summary_link.py --doc-id DOC_ID --date "26 May 2026" --url "https://..." --tab "Meeting recording summaries"
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
import urllib.error
from pathlib import Path

DEFAULT_TAB = "Meeting recording summaries"


def load_config_link_webapp_url() -> str | None:
    """Read meeting_doc_link_webapp_url from config.json.

    This is a separate Apps Script from the (optional) markdown→Doc one used
    by create_gdoc.py. See references/apps-script/README.md for setup.
    """
    config_path = Path(__file__).resolve().parent.parent / "config.json"
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
            return config.get("meeting_doc_link_webapp_url")
        except (json.JSONDecodeError, KeyError):
            pass
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--doc-id", required=True, help="Google Doc ID")
    ap.add_argument("--date", required=True, help="Date text to display (e.g. '26 May 2026')")
    ap.add_argument("--url", required=True, help="URL the date text should link to")
    ap.add_argument("--tab", default=DEFAULT_TAB, help="Tab name (default: 'Meeting recording summaries')")
    ap.add_argument("--webapp-url", default=None, help="Google Apps Script web app URL (overrides config)")
    args = ap.parse_args()

    webapp_url = args.webapp_url or load_config_link_webapp_url()
    if not webapp_url:
        print(json.dumps({"error": "No meeting_doc_link_webapp_url in config.json. See references/apps-script/README.md for setup, or paste the summary into the meeting doc manually."}), file=sys.stderr)
        sys.exit(1)
    payload = json.dumps({
        "docId": args.doc_id,
        "tabName": args.tab,
        "dateText": args.date,
        "linkUrl": args.url,
    }).encode("utf-8")

    req = urllib.request.Request(
        webapp_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = json.loads(e.read().decode("utf-8")) if e.readable() else {"error": str(e)}
    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)

    if body.get("error"):
        print(json.dumps(body), file=sys.stderr)
        sys.exit(1)

    print(json.dumps(body))


if __name__ == "__main__":
    main()
