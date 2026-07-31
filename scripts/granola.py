#!/usr/bin/env python3
"""
Granola transcript extraction utility (MCP-backed).

Usage:
    python granola.py check             # Check for recent call or list 5 most recent
    python granola.py list              # List all meetings with transcripts
    python granola.py get <doc_id>      # Get transcript for a specific meeting
    python granola.py recent [n]        # Get transcript for nth most recent meeting (default: 1)

Transcripts are automatically saved to the data/transcripts/ folder.

--- How this works (changed 2026-07-17) ---
Granola v7.427+ moved its local data-encryption key into the macOS
data-protection keychain (access group QZ7DHHLN25.granola), readable only by
Granola's own code-signed binary. That permanently broke the previous approach
of decrypting Granola's local token store and calling the private api.granola.ai
endpoints directly.

Instead we now talk to Granola's official hosted MCP server
(https://mcp.granola.ai/mcp), reusing the OAuth token that Claude Code stores
after the user authorises the `granola` connector (`claude mcp add ... granola`
then `/mcp` to authenticate). The token lives in the macOS keychain item
"Claude Code-credentials" under mcpOAuth. Claude Code refreshes it on session
start, so reading it fresh per invocation is normally valid.

MCP tools used:
    list_meetings          -> recent meeting list (titles, ids, dates)
    get_meeting_transcript -> verbatim transcript for one meeting
    get_meetings           -> meeting metadata (used here only for the date)
"""

import json
import re
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

SKILL_DIR = Path(__file__).parent.parent
TRANSCRIPTS_DIR = SKILL_DIR / "data" / "transcripts"

MCP_URL = "https://mcp.granola.ai/mcp"
MCP_PROTOCOL_VERSION = "2025-06-18"
# macOS keychain item where Claude Code stores its credentials (incl. MCP OAuth).
KEYCHAIN_CRED_SERVICE = "Claude Code-credentials"

# How recent a call must be (since it started) to auto-summarise, in minutes.
RECENT_THRESHOLD_MINUTES = 30
# Window used when listing "recent" meetings via the MCP.
RECENT_WINDOW_DAYS = 30


# --------------------------------------------------------------------------- #
# MCP transport
# --------------------------------------------------------------------------- #

def _granola_token() -> str:
    """Read the granola MCP OAuth access token from Claude Code's keychain item.

    Claude Code stores per-server OAuth tokens under mcpOAuth, keyed by
    "<serverName>|<hash>". We match the entry whose name is "granola"."""
    out = subprocess.run(
        ["security", "find-generic-password", "-s", KEYCHAIN_CRED_SERVICE, "-w"],
        capture_output=True, text=True,
    )
    if out.returncode != 0 or not out.stdout.strip():
        print(
            "Error: Could not read Claude Code credentials from the macOS keychain.\n"
            "Is the Granola MCP connector set up? Run:\n"
            "  claude mcp add --transport http --scope user granola https://mcp.granola.ai/mcp\n"
            "then start `claude` and run /mcp to authenticate.",
            file=sys.stderr,
        )
        sys.exit(1)
    try:
        creds = json.loads(out.stdout)
    except json.JSONDecodeError:
        print("Error: Claude Code credential blob was not valid JSON.", file=sys.stderr)
        sys.exit(1)

    mcp_oauth = creds.get("mcpOAuth") or {}
    for key, info in mcp_oauth.items():
        # Keys look like "granola|<16-hex-char hash>".
        if key.split("|", 1)[0].lower() == "granola" and isinstance(info, dict):
            token = info.get("accessToken") or info.get("access_token")
            if token:
                return token
    print(
        "Error: No authenticated `granola` MCP connector found in Claude Code.\n"
        "Start `claude`, run /mcp, select granola and authenticate, then retry.",
        file=sys.stderr,
    )
    sys.exit(1)


def _mcp_request(method: str, params: dict | None, _id: int) -> list[dict]:
    """POST a single JSON-RPC message to the MCP server and parse the reply.

    The server is stateless and answers over either application/json or an SSE
    (text/event-stream) body; both are handled here."""
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {_granola_token()}",
        "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
    }
    body = {"jsonrpc": "2.0", "id": _id, "method": method}
    if params is not None:
        body["params"] = params
    req = urllib.request.Request(
        MCP_URL, data=json.dumps(body).encode(), headers=headers, method="POST"
    )
    try:
        resp = urllib.request.urlopen(req, timeout=90)
        raw = resp.read().decode()
    except urllib.error.HTTPError as e:
        if e.code == 401:
            print(
                "Error: Granola MCP returned 401 (token expired or revoked).\n"
                "The token refreshes when Claude Code restarts; start a new `claude`\n"
                "session, or run /mcp to re-authenticate the granola connector.",
                file=sys.stderr,
            )
        else:
            print(f"Error: Granola MCP HTTP {e.code}: {e.read().decode()[:200]}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:  # noqa: BLE001 - surface transport failures plainly
        print(f"Error: Granola MCP request failed: {e}", file=sys.stderr)
        sys.exit(1)

    messages: list[dict] = []
    if "data:" in raw:
        for line in raw.splitlines():
            if line.startswith("data:"):
                try:
                    messages.append(json.loads(line[5:].strip()))
                except json.JSONDecodeError:
                    pass
    elif raw.strip():
        messages.append(json.loads(raw))
    return messages


def mcp_tool(name: str, arguments: dict) -> str:
    """Call an MCP tool and return its concatenated text content."""
    # The server is stateless, but still expects an initialize before use.
    _mcp_request(
        "initialize",
        {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "summarise-granola", "version": "2"},
        },
        0,
    )
    messages = _mcp_request("tools/call", {"name": name, "arguments": arguments}, 1)
    for m in messages:
        if "error" in m:
            print(f"Error: Granola MCP tool '{name}': {json.dumps(m['error'])}", file=sys.stderr)
            sys.exit(1)
        if "result" in m:
            parts = [
                c.get("text", "")
                for c in m["result"].get("content", [])
                if c.get("type") == "text"
            ]
            return "\n".join(parts)
    print(f"Error: Granola MCP tool '{name}' returned no result.", file=sys.stderr)
    sys.exit(1)


# --------------------------------------------------------------------------- #
# Parsing helpers
# --------------------------------------------------------------------------- #

# A <meeting ...> opening tag: a run of attr="value" pairs. Matching quoted
# values as whole units keeps titles containing < or > (e.g. 'Aaron <> Aidan
# 1:1') from ending the tag early, and tolerates extra or reordered attributes
# (mid-2026 list_meetings added captured_by_me/listed_as_participant/... after
# date, which broke matching on a fixed id/title/date sequence).
_MEETING_TAG_RE = re.compile(r'<meeting\s+((?:[\w-]+="[^"]*"\s*)+)/?>')
_ATTR_RE = re.compile(r'([\w-]+)="([^"]*)"')
_DATE_RE = re.compile(r"^(.*?)\s*(?:GMT([+-]\d+))?$")


def _parse_meetings(text: str) -> list[dict]:
    """Extract meetings from a list_meetings/get_meetings response.

    Returns dicts with id, title, date, preserving server order (most recent
    first)."""
    meetings = []
    for m in _MEETING_TAG_RE.finditer(text):
        attrs = dict(_ATTR_RE.findall(m.group(1)))
        if attrs.get("id"):
            meetings.append({
                "id": attrs["id"],
                "title": attrs.get("title", ""),
                "date": attrs.get("date", ""),
            })
    return meetings


def slugify(text: str) -> str:
    """Convert text to a filename-safe slug."""
    text = text.replace("&", "and")
    text = re.sub(r"[^a-z0-9]+", "-", text.lower())
    text = text.strip("-")
    return text[:50]


def parse_granola_date(date_str: str) -> datetime | None:
    """Parse a Granola display date like 'Jul 17, 2026 2:16 PM GMT+1'."""
    m = _DATE_RE.match(date_str.strip())
    if not m:
        return None
    front, offset = m.group(1).strip(), m.group(2)
    try:
        dt = datetime.strptime(front, "%b %d, %Y %I:%M %p")
    except ValueError:
        try:
            dt = datetime.strptime(front, "%b %d, %Y")
        except ValueError:
            return None
    tz = timezone(timedelta(hours=int(offset))) if offset else timezone.utc
    return dt.replace(tzinfo=tz)


def iso_date(date_str: str) -> str:
    """Return a YYYY-MM-DD string from a Granola display date."""
    dt = parse_granola_date(date_str)
    return dt.date().isoformat() if dt else "unknown-date"


def list_recent_meetings(limit: int) -> list[dict]:
    """Return recent meetings (most recent first) as dicts: id, title, date."""
    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=RECENT_WINDOW_DAYS)).date().isoformat()
    end = (now + timedelta(days=1)).date().isoformat()
    text = mcp_tool(
        "list_meetings",
        {"time_range": "custom", "custom_start": start, "custom_end": end},
    )
    return _parse_meetings(text)[:limit]


def meeting_date_for(doc_id: str) -> str:
    """Fetch a single meeting's display date via get_meetings (for the filename)."""
    text = mcp_tool("get_meetings", {"meeting_ids": [doc_id]})
    meetings = _parse_meetings(text)
    return meetings[0]["date"] if meetings else ""


def _extract_transcript_json(text: str) -> dict:
    """Pull the JSON object out of a get_meeting_transcript response.

    The response is prefixed with a plain-text guard line, then a JSON object."""
    brace = text.find("{")
    if brace == -1:
        print("Error: transcript response contained no JSON.", file=sys.stderr)
        sys.exit(1)
    try:
        return json.loads(text[brace:])
    except json.JSONDecodeError:
        print("Error: could not parse transcript JSON.", file=sys.stderr)
        sys.exit(1)


# Turn boundaries: the two-sided labels Granola uses ('Me'/'Them' as of
# mid-2026, 'Microphone'/'Speaker' before that) or a capitalised name label,
# preceded by 2+ spaces (Granola separates turns with a double space).
_TURN_RE = re.compile(
    r"(?:^\s*|\s{2,})(Me|Them|Microphone|Speaker|[A-Z][\w.'’-]*(?:\s[A-Z][\w.'’-]*){0,3})\s*:\s+"
)


def format_transcript(transcript: str) -> str:
    """Turn Granola's inline-labelled transcript string into speaker turns.

    'Me' or 'Microphone' (the note-taker's own audio) maps to **Me**; every
    other label ('Them', 'Speaker', or a named participant) maps to **Other**,
    matching the format the rest of the skill expects."""
    boundaries = list(_TURN_RE.finditer(transcript))
    turns: list[tuple[str, str]] = []
    for i, match in enumerate(boundaries):
        label = match.group(1)
        speaker = "Me" if label.lower() in ("me", "microphone") else "Other"
        text_start = match.end()
        text_end = boundaries[i + 1].start() if i + 1 < len(boundaries) else len(transcript)
        text = transcript[text_start:text_end].strip()
        if text:
            turns.append((speaker, text))

    # Group consecutive turns by the same speaker.
    lines: list[str] = []
    current_speaker = None
    current_text: list[str] = []
    for speaker, text in turns:
        if speaker != current_speaker:
            if current_text:
                lines.append(f"**{current_speaker}**: {' '.join(current_text)}")
                lines.append("")
            current_speaker = speaker
            current_text = [text]
        else:
            current_text.append(text)
    if current_text:
        lines.append(f"**{current_speaker}**: {' '.join(current_text)}")
    return "\n".join(lines)


def build_transcript(doc_id: str) -> tuple[str, str, str]:
    """Fetch and build transcript markdown for a specific meeting.

    Returns (markdown_content, title, date_str)."""
    text = mcp_tool("get_meeting_transcript", {"meeting_id": doc_id})
    data = _extract_transcript_json(text)

    title = data.get("title", "Untitled")
    transcript = (data.get("transcript") or "").strip()
    if not transcript:
        print(f"Error: No transcript found for meeting ID: {doc_id}", file=sys.stderr)
        sys.exit(1)

    date_str = iso_date(meeting_date_for(doc_id))

    body = format_transcript(transcript)
    markdown = f"# {title}\nDate: {date_str}\n\n{body}"
    return markdown, title, date_str


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #

def check_recent():
    """Check for a recent call (auto mode) or list the 5 most recent (select mode)."""
    meetings = list_recent_meetings(limit=5)
    if not meetings:
        print("No meetings found.")
        return

    most_recent = meetings[0]
    dt = parse_granola_date(most_recent["date"])
    if dt:
        minutes_ago = (datetime.now(timezone.utc) - dt).total_seconds() / 60
        if 0 <= minutes_ago <= RECENT_THRESHOLD_MINUTES:
            print(json.dumps({
                "mode": "auto",
                "id": most_recent["id"],
                "title": most_recent["title"] or "Untitled",
                "minutes_ago": round(minutes_ago, 1),
            }))
            return

    result = {"mode": "select", "meetings": []}
    for i, mtg in enumerate(meetings, 1):
        result["meetings"].append({
            "number": i,
            "id": mtg["id"],
            "title": mtg["title"] or "Untitled",
            "date": iso_date(mtg["date"]),
        })
    print(json.dumps(result))


def list_meetings_cmd():
    """List recent meetings in a human-readable form."""
    meetings = list_recent_meetings(limit=20)
    print(f"Found {len(meetings)} recent meeting(s):\n")
    for i, mtg in enumerate(meetings, 1):
        print(f"{i}. [{iso_date(mtg['date'])}] {mtg['title'] or 'Untitled'}")
        print(f"   ID: {mtg['id']}")
        print()


def get_transcript(doc_id: str):
    """Get and save the full transcript for a specific meeting."""
    markdown, title, date_str = build_transcript(doc_id)

    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{date_str}-{slugify(title or 'untitled')}.md"
    filepath = TRANSCRIPTS_DIR / filename
    with open(filepath, "w") as f:
        f.write(markdown)

    # Print only the path + metadata, never the transcript body: the body is
    # large and is read directly from `saved_to` by the summary/tidied agents,
    # so echoing it to stdout would needlessly load it into the caller's context.
    print(json.dumps({
        "saved_to": str(filepath),
        "title": title,
        "date": date_str,
        "chars": len(markdown),
    }))


def get_recent_transcript(n: int = 1):
    """Get transcript for the nth most recent meeting."""
    meetings = list_recent_meetings(limit=max(n, 5))
    if n < 1 or n > len(meetings):
        print(f"Error: Only {len(meetings)} meeting(s) available", file=sys.stderr)
        sys.exit(1)
    get_transcript(meetings[n - 1]["id"])


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    command = sys.argv[1]
    if command == "check":
        check_recent()
    elif command == "list":
        list_meetings_cmd()
    elif command == "get":
        if len(sys.argv) < 3:
            print("Error: Document ID required", file=sys.stderr)
            sys.exit(1)
        get_transcript(sys.argv[2])
    elif command == "recent":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 1
        get_recent_transcript(n)
    else:
        print(f"Unknown command: {command}", file=sys.stderr)
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
