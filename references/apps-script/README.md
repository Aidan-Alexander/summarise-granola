# Optional: formatted Google Docs

By default, the skill makes plain-text Google Docs. That means headings show up as ALL CAPS, bold text isn't bold, and lists are flatter than they could be. Still readable -- just not pretty.

If you want the summaries to look properly formatted (real headings, bold text, bullet lists, links) -- follow the steps below. **You can do this any time** -- before your first summary, after the tenth, whenever. It doesn't change anything about how the skill works otherwise.

**Prerequisite:** the skill must already be installed and you've run `/summarise-granola` at least once (which creates your `config.json`).

## Setup (≈ 5 min)

1. Go to [script.google.com](https://script.google.com) and click **New project** in the top left

2. In the editor that opens, delete the placeholder code, then **copy the entire contents of [`Code.gs`](Code.gs) from this folder and paste it in**

3. Click the **⚙ gear icon** ("Project Settings") in the left sidebar

4. Tick the box **"Show 'appsscript.json' manifest file in editor"**

5. Click the **`<>`** icon in the left sidebar to return to the editor

6. In the file list on the left, click **`appsscript.json`**. **Copy the contents of [`appsscript.json`](appsscript.json) from this folder and paste it in**, replacing whatever's there

7. Click **Deploy** (top right) → **New deployment**

8. Click the gear icon next to "Select type" → choose **Web app**

9. Fill in:
   - **Description:** anything (e.g. "Markdown to Doc")
   - **Execute as:** Me
   - **Who has access:** Anyone

10. Click **Deploy**. Accept any permission prompts that pop up (it needs to make Google Docs on your behalf)

11. Copy the **Web app URL** it gives you

12. Open Claude Code and paste this in (replacing `PASTE_URL_HERE` with the URL you just copied):

    ```
    Please save this as my webapp_url in the summarise-granola config: PASTE_URL_HERE
    ```

    Claude will update your `config.json` for you.

That's it. Next time you run `/summarise-granola`, the Google Doc will be properly formatted.

## If you want to turn it off later

In Claude Code, paste:

```
Please remove the webapp_url from my summarise-granola config.
```

The skill will go back to plain-text Google Docs.

## What it handles

- Headings (proper Google Docs heading styles)
- **Bold** and *italic*
- Bulleted and numbered lists
- Links
- Tables
- Inline code and code blocks
- Blockquotes
- Horizontal rules

Tables don't carry inline formatting inside cells, and some edge cases (e.g. bold inside a link) may not stack perfectly. If something looks off, you can always edit the doc manually.
