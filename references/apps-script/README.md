# Apps Script: formatted Google Doc output (advanced, optional)

By default, the skill creates plain-text Google Docs via the Drive MCP. This works well but loses formatting (bold, headings, lists render as plain text with structure hints).

If you want **fully formatted Google Docs** — proper headings, bold/italic, lists, links, tables — you can deploy this Apps Script web app and add its URL to your `config.json`.

## Why this is separate

Google blocks the Drive REST API for "Anyone" web apps (insufficient OAuth scopes). This script uses Google's `DocumentApp` instead, which is handled internally by Apps Script and doesn't have the scope problem. It receives the markdown directly and renders it using `DocumentApp.appendParagraph`, `setHeading`, `setBold`, etc.

## Setup (≈ 5 min)

1. Go to [script.google.com](https://script.google.com) → **New project**
2. Replace `Code.gs` contents with the contents of `Code.gs` in this folder
3. Click the **⚙ gear icon** (Project Settings) in the left sidebar
4. Check **"Show 'appsscript.json' manifest file in editor"**
5. Click the **`<>`** icon to return to the editor, then click `appsscript.json` in the file list
6. Replace its contents with `appsscript.json` from this folder
7. Click **Deploy → New deployment**
8. Gear icon next to "Select type" → **Web app**
9. Set:
   - Description: anything (e.g. "Markdown to Doc")
   - Execute as: **Me**
   - Who has access: **Anyone**
10. Click **Deploy** — accept any authorization prompts (Drive/Docs access)
11. Copy the **Web app URL**
12. Add it to your `config.json`:
    ```json
    {
      "user_name": "...",
      "webapp_url": "https://script.google.com/macros/s/.../exec",
      "meeting_docs": { ... }
    }
    ```

## What it supports

- Headings (`# H1` through `###### H6`)
- Bold (`**text**`), italic (`*text*` or `_text_`)
- Bulleted and numbered lists (with nesting via indentation)
- Links (`[text](url)`)
- Inline code (`` `code` ``) and fenced code blocks (```` ``` ````)
- Blockquotes (`> text`)
- Horizontal rules (`---`)
- Tables (basic — `| col | col |` syntax with `|---|---|` separator)

## Known limitations

- Tables don't render inline formatting inside cells
- Italics use a simple `*`/`_` toggle, which can misfire on text like `5 * 6` (rare in summaries)
- Nested formatting (e.g. bold inside a link) may not stack correctly
- Multi-line bold (`**` spanning lines) isn't supported

If something looks wrong, the skill will still have created a Google Doc — just edit the formatting manually, or fall back to the plain-text path by removing `webapp_url` from your `config.json`.
