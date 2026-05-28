#!/usr/bin/env python3
"""Resolve the meeting doc ID for a person.

Lookup order:
  1. people.json registry — if `people[<key>].meeting_doc.url` is set, extract doc_id.
  2. Not found — return {"source": "not_found"} and exit 0 (caller can fall back to
     Google Drive MCP search or ask the user).

Output: single-line JSON on stdout:
  {"doc_id": "<id>|null", "source": "registry|not_found", "person_key": "<key>"}
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


REGISTRY_PATH = Path(__file__).resolve().parent.parent / "data" / "people.json"
DOC_ID_RE = re.compile(r"/document/d/([A-Za-z0-9_-]+)")


def slugify(name: str) -> str:
    return re.sub(r"\s+", "-", name.strip().lower())


def extract_doc_id(url: str) -> str | None:
    m = DOC_ID_RE.search(url)
    return m.group(1) if m else None


def load_registry() -> dict:
    if not REGISTRY_PATH.exists():
        return {"people": {}}
    try:
        return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"people": {}}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True, help="Full name (e.g., 'Jane Smith')")
    ap.add_argument("--initials", help="Lowercase initials (reserved for future use)")
    args = ap.parse_args()

    key = slugify(args.name)
    data = load_registry()
    people = data.get("people", {})
    entry = people.get(key)

    if entry and entry.get("meeting_doc") and entry["meeting_doc"].get("url"):
        doc_id = extract_doc_id(entry["meeting_doc"]["url"])
        if doc_id:
            print(json.dumps({"doc_id": doc_id, "source": "registry", "person_key": key}))
            return

    print(json.dumps({"doc_id": None, "source": "not_found", "person_key": key}))


if __name__ == "__main__":
    main()
