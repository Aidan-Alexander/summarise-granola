#!/usr/bin/env python3
"""Strip markdown syntax for clean Google Doc upload as text/plain.

Removes heading markers, bold/italic markers, horizontal rules, and
blockquote prefixes while preserving the document structure and readability.

Usage:
    python3 md-to-plaintext.py <input.md>           # prints to stdout
    python3 md-to-plaintext.py <input.md> -o out.txt
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


def strip_inline(text: str) -> str:
    """Remove inline markdown: bold, italic, links, code backticks."""
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    text = re.sub(r'\*\*\*(.+?)\*\*\*', r'\1', text)
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'\1', text)
    text = re.sub(r'`([^`]+)`', r'\1', text)
    return text


def md_to_plaintext(md_text: str) -> str:
    """Convert markdown to clean plain text for Google Docs."""
    lines = md_text.split("\n")
    out: list[str] = []

    for line in lines:
        stripped = line.strip()

        # Horizontal rules → blank line
        if re.match(r'^-{3,}$', stripped) or re.match(r'^\*{3,}$', stripped):
            if out and out[-1] != "":
                out.append("")
            continue

        # Headings → UPPERCASE text with blank line before
        m = re.match(r'^(#{1,6})\s+(.+)$', stripped)
        if m:
            text = strip_inline(m.group(2))
            if out and out[-1] != "":
                out.append("")
            out.append(text.upper())
            out.append("")
            continue

        # Blockquotes → indented text
        if stripped.startswith(">"):
            text = re.sub(r'^>\s?', '', stripped)
            out.append(f"    {strip_inline(text)}")
            continue

        # Lists → keep structure, strip inline
        if re.match(r'^[-*+]\s', stripped):
            text = re.sub(r'^[-*+]\s+', '', stripped)
            out.append(f"  - {strip_inline(text)}")
            continue

        if re.match(r'^\d+\.\s', stripped):
            m2 = re.match(r'^(\d+\.)\s+(.+)', stripped)
            if m2:
                out.append(f"  {m2.group(1)} {strip_inline(m2.group(2))}")
                continue

        if re.match(r'^[a-z]\.\s', stripped):
            m2 = re.match(r'^([a-z]\.)\s+(.+)', stripped)
            if m2:
                out.append(f"  {m2.group(1)} {strip_inline(m2.group(2))}")
                continue

        # Table rows → aligned with pipes (keep as-is but strip inline)
        if stripped.startswith("|"):
            if re.match(r'^\|[\s:|-]+\|$', stripped):
                # Separator row → dashes
                out.append(re.sub(r'[^|]', '-', stripped))
                continue
            cells = [strip_inline(c.strip()) for c in stripped.strip("|").split("|")]
            out.append("| " + " | ".join(cells) + " |")
            continue

        # Blank lines
        if not stripped:
            if out and out[-1] != "":
                out.append("")
            continue

        # Regular text → strip inline markdown
        out.append(strip_inline(stripped))

    # Collapse multiple blank lines
    result: list[str] = []
    for line in out:
        if line == "" and result and result[-1] == "":
            continue
        result.append(line)

    # Strip trailing blanks
    while result and result[-1] == "":
        result.pop()

    return "\n".join(result) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description="Strip markdown for Google Doc upload")
    ap.add_argument("input", help="Markdown file")
    ap.add_argument("-o", "--output", help="Output file (default: stdout)")
    args = ap.parse_args()

    md_text = Path(args.input).read_text(encoding="utf-8")
    result = md_to_plaintext(md_text)

    if args.output:
        Path(args.output).write_text(result, encoding="utf-8")
    else:
        print(result, end="")


if __name__ == "__main__":
    main()
