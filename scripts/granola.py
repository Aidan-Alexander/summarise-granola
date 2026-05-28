#!/usr/bin/env python3
"""
Granola transcript extraction utility.

Usage:
    python granola.py check             # Check for recent call or list 5 most recent
    python granola.py list              # List all meetings with transcripts
    python granola.py get <doc_id>      # Get transcript for a specific meeting
    python granola.py recent [n]        # Get transcript for nth most recent meeting (default: 1)

Transcripts are automatically saved to the data/transcripts/ folder.
"""

import base64
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

GRANOLA_DIR = Path.home() / "Library" / "Application Support" / "Granola"
STORED_ACCOUNTS = GRANOLA_DIR / "stored-accounts.json"
SUPABASE_AUTH = GRANOLA_DIR / "supabase.json"
# Granola 7.2x+ encrypts the token store. The plaintext files above are left
# stale by newer builds; the live tokens now live in these .enc files, which are
# AES-256-GCM-encrypted with a data encryption key (DEK) wrapped in storage.dek.
STORED_ACCOUNTS_ENC = GRANOLA_DIR / "stored-accounts.json.enc"
SUPABASE_AUTH_ENC = GRANOLA_DIR / "supabase.json.enc"
DEK_FILE = GRANOLA_DIR / "storage.dek"
# macOS Keychain service holding the Electron safeStorage key that wraps the DEK.
KEYCHAIN_SERVICE = "Granola Safe Storage"
API_BASE = "https://api.granola.ai/v1"
SKILL_DIR = Path(__file__).parent.parent
TRANSCRIPTS_DIR = SKILL_DIR / "data" / "transcripts"
SUMMARIES_DIR = SKILL_DIR / "data" / "summaries"

# Granola's API rejects requests without a client-version header ("Unsupported
# client"). The desktop app sends its own version; we mirror a known-good one.
# Bump this if Granola starts rejecting the value.
GRANOLA_CLIENT_VERSION = "7.162.2"

# How recent a call must be to auto-summarise (in minutes)
RECENT_THRESHOLD_MINUTES = 30


def _parse_stored_accounts(data: dict) -> dict | None:
    """Extract tokens from a parsed stored-accounts payload (plaintext or decrypted)."""
    accounts_raw = data.get('accounts')
    if not accounts_raw:
        return None
    accounts = json.loads(accounts_raw) if isinstance(accounts_raw, str) else accounts_raw
    if not accounts:
        return None
    tokens_raw = accounts[0].get('tokens')
    if not tokens_raw:
        return None
    return json.loads(tokens_raw) if isinstance(tokens_raw, str) else tokens_raw


def _parse_supabase(data: dict) -> dict | None:
    """Extract tokens from a parsed supabase payload (plaintext or decrypted)."""
    return json.loads(data['workos_tokens'])


def _tokens_from_stored_accounts() -> dict | None:
    """Read tokens from stored-accounts.json (legacy plaintext store)."""
    if not STORED_ACCOUNTS.exists():
        return None
    try:
        return _parse_stored_accounts(json.load(open(STORED_ACCOUNTS)))
    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
        return None


def _tokens_from_supabase() -> dict | None:
    """Read tokens from supabase.json (legacy plaintext store)."""
    if not SUPABASE_AUTH.exists():
        return None
    try:
        return _parse_supabase(json.load(open(SUPABASE_AUTH)))
    except (json.JSONDecodeError, KeyError, TypeError):
        return None


def _aes_cbc_decrypt(key: bytes, iv: bytes, ct: bytes) -> bytes:
    """AES-CBC decrypt, preferring `cryptography`, falling back to pycryptodome."""
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        d = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
        return d.update(ct) + d.finalize()
    except ImportError:
        from Crypto.Cipher import AES
        return AES.new(key, AES.MODE_CBC, iv).decrypt(ct)


def _aes_gcm_decrypt(key: bytes, nonce: bytes, ct: bytes, tag: bytes) -> bytes:
    """AES-GCM decrypt+verify, preferring `cryptography`, falling back to pycryptodome."""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        return AESGCM(key).decrypt(nonce, ct + tag, None)
    except ImportError:
        from Crypto.Cipher import AES
        return AES.new(key, AES.MODE_GCM, nonce=nonce).decrypt_and_verify(ct, tag)


def _safestorage_decrypt(blob: bytes) -> bytes:
    """Decrypt an Electron safeStorage blob.

    On macOS this is `v10` + AES-128-CBC, key = PBKDF2-HMAC-SHA1(keychain_pw,
    "saltysalt", 1003, 16), IV = 16 spaces. Raises on any failure (e.g. the user
    declining the Keychain access prompt)."""
    out = subprocess.run(
        ["security", "find-generic-password", "-ws", KEYCHAIN_SERVICE],
        capture_output=True, text=True, timeout=30,
    )
    if out.returncode != 0 or not out.stdout.strip():
        raise RuntimeError(f"Keychain lookup failed for {KEYCHAIN_SERVICE!r}")
    pw = out.stdout.strip()
    key = hashlib.pbkdf2_hmac("sha1", pw.encode("utf-8"), b"saltysalt", 1003, 16)
    if blob[:3] in (b"v10", b"v11"):
        blob = blob[3:]
    dec = _aes_cbc_decrypt(key, b" " * 16, blob)
    pad = dec[-1]
    if 1 <= pad <= 16:
        dec = dec[:-pad]
    return dec


def _unwrap_dek() -> bytes | None:
    """Return the 32-byte data encryption key stored (wrapped) in storage.dek."""
    if not DEK_FILE.exists():
        return None
    dek = base64.b64decode(_safestorage_decrypt(DEK_FILE.read_bytes()).decode("utf-8"))
    return dek if len(dek) == 32 else None


def _decrypt_enc_file(path: Path, dek: bytes) -> str:
    """Decrypt a Granola *.enc payload: [iv:12][ciphertext][tag:16], AES-256-GCM."""
    data = path.read_bytes()
    iv, tag, ct = data[:12], data[-16:], data[12:-16]
    return _aes_gcm_decrypt(dek, iv, ct, tag).decode("utf-8")


def _tokens_from_encrypted() -> dict | None:
    """Decrypt the encrypted token store (Granola 7.2x+) and extract tokens.

    Returns None (rather than raising) on any failure so callers can fall back
    to the legacy plaintext stores."""
    try:
        dek = _unwrap_dek()
    except Exception:
        return None
    if not dek:
        return None
    for path, parser in ((STORED_ACCOUNTS_ENC, _parse_stored_accounts),
                         (SUPABASE_AUTH_ENC, _parse_supabase)):
        if not path.exists():
            continue
        try:
            tokens = parser(json.loads(_decrypt_enc_file(path, dek)))
            if tokens and 'access_token' in tokens:
                return tokens
        except Exception:
            continue
    return None


def get_access_token() -> str:
    """Read the Granola access token.

    Prefers the encrypted store (Granola 7.2x+ keeps the live token there and
    leaves the plaintext files stale), then falls back to the legacy plaintext
    stored-accounts.json / supabase.json for older builds."""
    tokens = (
        _tokens_from_encrypted()
        or _tokens_from_stored_accounts()
        or _tokens_from_supabase()
    )
    if not tokens or 'access_token' not in tokens:
        print(
            f"Error: Could not read Granola tokens from {GRANOLA_DIR}.\n"
            "Tried the encrypted store (stored-accounts.json.enc) and the legacy\n"
            "plaintext files. Is Granola installed and signed in? If decryption is\n"
            "failing, ensure the `cryptography` (or `pycryptodome`) package is available\n"
            "and that you allowed the Keychain access prompt.",
            file=sys.stderr,
        )
        sys.exit(1)
    return tokens['access_token']


def api_call(endpoint: str, payload: dict) -> any:
    """Make an authenticated POST request to the Granola API."""
    token = get_access_token()

    result = subprocess.run(
        [
            'curl', '-s', '--compressed', '-X', 'POST',
            f'{API_BASE}/{endpoint}',
            '-H', f'Authorization: Bearer {token}',
            '-H', 'Content-Type: application/json',
            '-H', f'X-Client-Version: {GRANOLA_CLIENT_VERSION}',
            '-d', json.dumps(payload),
        ],
        capture_output=True, text=True,
    )

    if result.returncode != 0:
        print(f"Error: API call failed: {result.stderr}", file=sys.stderr)
        sys.exit(1)

    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError:
        print(f"Error: Invalid JSON response: {result.stdout[:200]}", file=sys.stderr)
        sys.exit(1)

    # Granola returns errors as {"message": "..."} with HTTP 200; surface them.
    if isinstance(parsed, dict) and set(parsed.keys()) == {'message'}:
        msg = parsed['message']
        hint = ""
        if msg == "Unauthorized":
            hint = " (token may be expired — open Granola.app to refresh)"
        elif msg == "Unsupported client":
            hint = f" (bump GRANOLA_CLIENT_VERSION in {Path(__file__).name})"
        print(f"Error: Granola API: {msg}{hint}", file=sys.stderr)
        sys.exit(1)

    return parsed


def slugify(text: str) -> str:
    """Convert text to a filename-safe slug."""
    text = text.replace('&', 'and')
    text = re.sub(r'[^a-z0-9]+', '-', text.lower())
    text = text.strip('-')
    return text[:50]


def parse_iso_timestamp(ts: str) -> datetime:
    """Parse ISO timestamp string to datetime."""
    ts = ts.replace('Z', '+00:00')
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def _effective_timestamp(doc: dict) -> str:
    """Return the most recent activity timestamp for a doc.

    Granola pre-creates documents for recurring calendar events, so ``created_at``
    can point to when the series was first scheduled rather than when the call
    actually happened. ``updated_at`` tracks the most recent edit (including
    transcript ingestion), so it is a better proxy for "when did this meeting
    really take place".
    """
    return doc.get('updated_at') or doc.get('created_at') or ''


def get_recent_documents(limit: int = 5) -> list[dict]:
    """Fetch recent documents from the Granola API, sorted by most-recent activity."""
    # Fetch a larger window than ``limit`` so the updated_at re-sort has room to
    # surface docs that were created earlier but updated today.
    docs = api_call('get-documents', {'limit': max(limit * 4, 30)})
    if not isinstance(docs, list):
        print(f"Error: Unexpected API response format", file=sys.stderr)
        sys.exit(1)
    docs.sort(key=_effective_timestamp, reverse=True)
    return docs[:limit]


def check_recent():
    """
    Check if there's a recent call (within 30 minutes).

    If yes, output JSON indicating auto-summarise mode.
    If no, output a numbered list of the 5 most recent calls for selection.
    """
    docs = get_recent_documents(limit=5)

    if not docs:
        print("No meetings found.")
        return

    # Check if most recent meeting was active within threshold
    most_recent = docs[0]
    effective_ts = _effective_timestamp(most_recent)

    if effective_ts:
        meeting_time = parse_iso_timestamp(effective_ts)
        now = datetime.now(timezone.utc)
        minutes_ago = (now - meeting_time).total_seconds() / 60

        if minutes_ago <= RECENT_THRESHOLD_MINUTES:
            result = {
                'mode': 'auto',
                'id': most_recent['id'],
                'title': most_recent.get('title', 'Untitled'),
                'minutes_ago': round(minutes_ago, 1),
            }
            print(json.dumps(result))
            return

    # No recent call - show selection list
    result = {
        'mode': 'select',
        'meetings': [],
    }

    for i, doc in enumerate(docs, 1):
        effective = _effective_timestamp(doc)
        date_str = effective[:10] if effective else 'Unknown'
        result['meetings'].append({
            'number': i,
            'id': doc['id'],
            'title': doc.get('title', 'Untitled'),
            'date': date_str,
        })

    print(json.dumps(result))


def list_meetings():
    """List recent meetings."""
    docs = get_recent_documents(limit=20)

    print(f"Found {len(docs)} recent meeting(s):\n")
    for i, doc in enumerate(docs, 1):
        effective = _effective_timestamp(doc)
        date_str = effective[:10] if effective else 'Unknown date'
        print(f"{i}. [{date_str}] {doc.get('title', 'Untitled')}")
        print(f"   ID: {doc['id']}")
        print()


def build_transcript(doc_id: str) -> tuple[str, str, str]:
    """
    Fetch and build transcript markdown for a specific document.

    Returns:
        tuple: (markdown_content, title, date_str)
    """
    # Fetch document metadata
    docs_response = api_call('get-documents-batch', {'document_ids': [doc_id]})
    if isinstance(docs_response, dict) and 'docs' in docs_response:
        docs_list = docs_response['docs']
    elif isinstance(docs_response, list):
        docs_list = docs_response
    else:
        docs_list = []
    doc = docs_list[0] if docs_list else {}

    title = doc.get('title', 'Untitled')
    effective = _effective_timestamp(doc)
    date_str = effective[:10] if effective else 'unknown-date'

    # Fetch transcript chunks
    chunks = api_call('get-document-transcript', {'document_id': doc_id})

    if not isinstance(chunks, list) or len(chunks) == 0:
        print(f"Error: No transcript found for document ID: {doc_id}", file=sys.stderr)
        sys.exit(1)

    # Sort by timestamp
    chunks.sort(key=lambda c: c.get('start_timestamp', ''))

    lines = []
    lines.append(f"# {title}")
    lines.append(f"Date: {date_str}")
    lines.append("")

    # Group consecutive segments by speaker
    current_speaker = None
    current_text = []

    for chunk in chunks:
        source = chunk.get('source', 'unknown')
        text = chunk.get('text', '').strip()

        if not text:
            continue

        speaker = 'Me' if source == 'microphone' else 'Other'

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

    return '\n'.join(lines), title, date_str


def get_transcript(doc_id: str):
    """Get and save the full transcript for a specific document."""
    markdown, title, date_str = build_transcript(doc_id)

    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)

    filename = f"{date_str}-{slugify(title or 'untitled')}.md"
    filepath = TRANSCRIPTS_DIR / filename

    with open(filepath, 'w') as f:
        f.write(markdown)

    print(markdown)
    print(f"\n---\nTranscript saved to: {filepath}", file=sys.stderr)


def get_recent_transcript(n: int = 1):
    """Get transcript for the nth most recent meeting."""
    docs = get_recent_documents(limit=max(n, 5))

    if n < 1 or n > len(docs):
        print(f"Error: Only {len(docs)} meeting(s) available", file=sys.stderr)
        sys.exit(1)

    doc_id = docs[n - 1]['id']
    get_transcript(doc_id)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    command = sys.argv[1]

    if command == 'check':
        check_recent()
    elif command == 'list':
        list_meetings()
    elif command == 'get':
        if len(sys.argv) < 3:
            print("Error: Document ID required", file=sys.stderr)
            sys.exit(1)
        get_transcript(sys.argv[2])
    elif command == 'recent':
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 1
        get_recent_transcript(n)
    else:
        print(f"Unknown command: {command}", file=sys.stderr)
        print(__doc__)
        sys.exit(1)


if __name__ == '__main__':
    main()
