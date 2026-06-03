---
name: summarise-granola
description: For Granola call or meeting summaries, including "summarise my call", "summarise my meeting", "summarise my last call", "get Granola transcript", "call summary", and similar requests. Requires Claude Code (CLI) — will not work in claude.ai web/mobile.
---

# Granola transcript summarisation

This skill extracts raw meeting transcripts from the Granola app, creates a structured summary, saves it as a standalone Google Doc, and optionally links it from the person's meeting doc. For 1:1 calls where a meeting doc is configured, a link is added to the "Meeting recording summaries" tab (using the meeting date as the link text). For calls with no configured meeting doc, a standalone Google Doc is created for easy sharing. Optionally sends call notes via email or Slack DM. A tidied transcript is opt-in and runs in the background after the main workflow.

## Configuration

The skill reads per-user settings from `config.json` in the skill directory (`~/.claude/skills/summarise-granola/config.json`).

**On first run** (if `config.json` doesn't exist), run setup before proceeding to Step 1:

1. Ask: "What's your first name? (Used in summaries and email sign-offs)"

2. Ask: "Do you have 1:1 meeting docs in Google Docs where you'd like call summaries automatically linked? If so, I can set that up now — otherwise you can add them later or skip entirely." Provide options:
   - **"Yes, I'll add some now"** — for each person, ask their name and the Google Doc URL, one at a time, until the user says they're done
   - **"Skip for now"** — the skill will create standalone Google Doc summaries instead of linking into meeting docs

3. Write `config.json`:
   ```json
   {
     "user_name": "<name>",
     "meeting_docs": {
       "<Person Name>": {
         "url": "https://docs.google.com/document/d/..."
       }
     }
   }
   ```
   If the user skipped meeting docs, write `"meeting_docs": {}`.

4. Confirm setup is complete. If the user also triggered `/summarise-granola` (i.e., they want to summarise a call now), continue to Step 1. Otherwise, stop.

**To add meeting docs later:** the user can edit `config.json` directly, or the skill will offer to save newly discovered meeting docs during Step 4b.

**Config fields:**
- `user_name` (required): first name, used in summaries, doc titles, and email sign-offs
- `meeting_docs` (optional): map of person display names to `{"url": "..."}`, matched (case-insensitive) against the other participant's name. If empty or missing, Step 4b's meeting-doc auto-linking is skipped (a standalone Google Doc is still created in Step 4a).
- `webapp_url` (optional): Apps Script web app for formatted Google Docs in Step 4a — see `references/apps-script/README.md`. If unset, docs are plain text.
- `meeting_doc_link_webapp_url` (optional): Apps Script web app for linking the summary into the meeting doc — see `references/apps-script/README.md`.

## Workflow

**Ordering principle (read this first):** Everything that always happens — generating the summary, creating the standalone Google Doc, and (for 1:1s) linking it into the meeting doc — runs first and needs **no user input**. The only interactive decision, sharing the notes with the attendee (email / Slack DM), is saved for the **very end** (Step 7). This lets the user trigger the skill, walk away, and come back to find only the quick sharing question waiting. **Never ask the sharing question before the summary, the Google Doc, and the meeting-doc link are all done.**

### Step 1: Check for recent calls

Always start by running the check command:

```bash
python3 ~/.claude/skills/summarise-granola/scripts/granola.py check
```

This returns JSON with one of two modes:

**Auto mode** (call ended within 30 minutes):
```json
{"mode": "auto", "id": "...", "title": "Meeting Title", "minutes_ago": 15.2}
```
Proceed directly to extract and summarise this meeting.

**Select mode** (no recent call):
```json
{"mode": "select", "meetings": [
  {"number": 1, "id": "...", "title": "Meeting A", "date": "2026-01-05"},
  {"number": 2, "id": "...", "title": "Meeting B", "date": "2026-01-04"},
  ...
]}
```
Present the numbered list to the user and ask which one to summarise. User can reply with just "1", "2", etc.

### Step 2: Extract the transcript

Once you have the meeting ID (from auto mode or user selection), extract the transcript:

```bash
python3 ~/.claude/skills/summarise-granola/scripts/granola.py get <document_id>
```

Or by number if user selected from the list:

```bash
python3 ~/.claude/skills/summarise-granola/scripts/granola.py recent <n>
```

The transcript is saved to `data/transcripts/`. The command prints a small JSON object — `{"saved_to": "...", "title": "...", "date": "...", "chars": N}` — **not** the transcript body. Note the `saved_to` path: pass that path (never the contents) to the summary and tidied-transcript agents, so the large transcript never enters the orchestrator's context.

### Step 2.3: Apply STT corrections (optional)

**Skip this step** if `~/.agents/scripts/utils/apply-stt-corrections.py` does not exist.

If it exists, run the context-aware STT corrections script on the saved transcript:

```bash
python3 ~/.agents/scripts/utils/apply-stt-corrections.py <transcript-path>
```

This applies corrections from `~/.agents/references/stt-corrections.json` — fixing known STT errors for org names, person names, and acronyms. Scoped corrections (entries with a `context` field) only apply when at least one context keyword appears in the transcript. Changes are printed to stderr; if any were made, note them briefly in the Step 6 report.

If the corrections file is empty or has no entries, this step completes silently.

### Step 2.5: Confirm participant names

Before creating the tidied transcript and summary, confirm the names of all participants.

**Check the meeting title first:**

If the meeting title contains clear participant names (e.g., "Jane Smith & Your Name", "Call with Sarah Chen"), extract those names.

**If names are unclear:**

If the meeting title is generic (e.g., "Weekly sync", "Project check-in", "Team meeting") or doesn't contain recognisable names, ask the user using AskUserQuestion:

```
"Who were the participants in this call? (I'll use these names in the transcript and summary)"
```

Provide a free-text input option since participant names can't be predicted.

**Important:** Never guess participant names. The Granola transcript only shows "Me" and "Other" as speaker labels, which doesn't identify the other person. If in doubt, ask.

**Store the confirmed names** and use them consistently throughout the tidied transcript and summary.

### Step 3: Generate the summary (+ pre-fetch in parallel)

Launch the summary agent **in the background** (`run_in_background: true`) alongside the people-registry pre-fetch in a single message, then **wait for the summary to finish** before moving to Step 4. Backgrounding it lets the quick pre-fetch run alongside the slow opus summary; do not poll while you wait — you'll get a completion notification.

**Do NOT ask the sharing question here, and do NOT launch the tidied transcript agent here.** Both happen at the end (Steps 7 and 8), after the summary, Google Doc, and meeting-doc link are done. Keep them off the critical path so the user can walk away during the slow part.

**Summary agent** (`model: "opus"`, `run_in_background: true`)

Before launching, `Read` `references/summary-format.md` in an earlier message — paste its full contents verbatim into the agent prompt. The agent has no access to skill files, so the guidelines must travel with the prompt. (Read it before you launch so its contents are ready to paste; do not bundle the `Read` with the `Agent` call, or you won't have the contents yet.)

Prompt the agent with: the **absolute path to the raw transcript** (the `saved_to` value from Step 2) plus an explicit instruction to `Read` that file itself, the confirmed participant names, the summary-format contents (pasted verbatim — the agent can't see skill files), and an instruction to write the result to `data/summaries/` using the same filename as the transcript (with `--summary.md` suffix). **Do not paste the transcript body into the prompt** — passing the path keeps the transcript (often 10k–25k tokens) out of the orchestrator's context; the agent reads it directly from disk.

**Pre-fetch: People registry** (in parallel with the agent)

In the **same message as the agent launch**, find the matching meeting doc:

1. Check `config.json` for a matching entry in `meeting_docs` (case-insensitive name match). If found, note the doc URL for use in Step 4.
2. If not in config, read the local people registry: `cat ~/.claude/skills/summarise-granola/data/people.json` (may not exist — that's fine, skip if missing).
3. If the person is registered in people.json and has a `meeting_doc`, note the doc ID for use in Step 4.

Store the results (person registry data, project association, meeting doc reference) for use in Steps 4, 5, and 7.

**Progression:** Once the summary agent reports done, run straight through Steps 4–6 (Google Doc → meeting-doc link → project filing → report) with no user input. Only after all of that is complete do you ask the sharing question (Step 7). The pre-fetch runs concurrently with the summary and is quick.

### Step 4: Save summary to Google Drive

This step creates a standalone Google Doc with the summary and, for 1:1 calls, links it from the person's meeting doc. **Run it as soon as the summary is ready** — it always happens for every 1:1 with a configured meeting doc and needs no user input. The sharing question hasn't been asked yet (it's Step 7) and is irrelevant here.

**Step 4a: Create standalone summary Google Doc**

Always create a new Google Doc with the full summary content.

Title format: `Call summary: [Person Name] & [config.user_name] [topic] — YYYY-MM-DD`

**If `webapp_url` is set in `config.json`** (advanced setup — see `references/apps-script/README.md`), use the formatted Apps Script path:
```bash
python3 ~/.claude/skills/summarise-granola/scripts/create_gdoc.py \
  --title "<doc title>" --md <summary-file.md>
```
Returns JSON with `doc_id` and `url`. This creates a Google Doc with proper headings, bold/italic, lists, and links.

**Otherwise (default)** — use the plain-text path:
1. Convert the summary markdown to clean plain text:
   ```bash
   python3 ~/.claude/skills/summarise-granola/scripts/md-to-plaintext.py <summary-file.md>
   ```
   This strips markdown syntax while preserving structure — section headers become UPPERCASE, lists keep their format, blockquotes are indented.
2. Use the Google Drive MCP `create_file` tool with `textContent` set to the plaintext output and `contentMimeType: "text/plain"` (auto-converts to a Google Doc).

Either way: note the returned doc ID and URL for subsequent steps. The doc is immediately shareable.

**Step 4b: For 1:1 calls — link from the meeting doc's "Meeting recording summaries" tab**

Runs automatically for every 1:1 call where a meeting doc is configured (matched in Step 3 by participant name). This is part of the always-happens work — no user input required. If no meeting doc matched, skip 4b (the standalone doc from 4a is the deliverable).

**Look up the meeting doc** (if not already resolved in Step 3):

1. **Check config.json first:** look for a matching key in `meeting_docs` (case-insensitive). If found, extract the doc ID from the URL and use it.

2. **If not in config**, run the lookup script:
   ```bash
   python3 ~/.claude/skills/summarise-granola/scripts/find_meeting_doc.py --name "<Full Name>"
   ```
   Returns JSON: `{"doc_id": "...", "source": "registry|not_found", "person_key": "..."}`.

3. **If still not found**, try searching with the Google Drive MCP `search_files` tool — search for a doc title containing the participant's name (e.g., "Morgan", "Morgan [user_name]", or "1:1 Morgan"). If found, offer to save the URL to `config.json` under `meeting_docs` for future use. If still not found, tell the user the meeting doc wasn't found — the standalone doc from 4a is still available.

**Add the link via script** (requires `meeting_doc_link_webapp_url` in `config.json` — see `references/apps-script/README.md` for setup):

```bash
python3 ~/.claude/skills/summarise-granola/scripts/add_summary_link.py \
  --doc-id "<meeting_doc_id>" \
  --date "<DD Mon YYYY>" \
  --url "<standalone_summary_doc_url>"
```

Inserts a hyperlinked date at the top of the "Meeting recording summaries" tab. Returns `{"success": true}` or `{"error": "..."}`.

- If `meeting_doc_link_webapp_url` isn't configured (script exits with that error), tell the user the standalone doc URL from 4a is available to paste manually, and skip the rest of 4b.
- If the tab doesn't exist yet, the script returns an error — tell the user to add the link manually or create the "Meeting recording summaries" tab first.

**Step 4c: Record the Google Doc URL**

Cache the standalone summary doc URL for Step 6 (reporting) and Step 7 (email/Slack).

### Step 5: Associate with project

Now that the meeting doc is updated, determine if this call should also be filed under a project folder. **Use the people registry data pre-fetched in Step 3** — do not re-read `people.json` here.

**Step 5a: Check the people registry**

1. Extract the other participant's name from the meeting title (the person who isn't the user)
2. Convert to registry key format: lowercase, hyphenated (e.g., "Jane Smith" → "jane-smith")
3. Use the registry data already fetched in Step 3 (if not pre-fetched, read it now: `cat ~/.claude/skills/summarise-granola/data/people.json`)
4. Check if the key exists in `people` and has a `default_project` value

**Step 5b: If person is registered → auto-associate**

If found in the registry:
1. Get the `default_project` value
2. Silently associate with that project (no confirmation needed)
3. Note the auto-association for the final output (Step 6)

**Step 5c: If person is NOT registered → show project list**

If not found in the registry, list available project directories:

```bash
ls -d ~/Documents/Projects/*/  2>/dev/null | xargs -I {} basename {}
```

If project directories exist, present options using AskUserQuestion:
- List each project folder as an option
- Include a "None" option for calls not associated with any project

If no project directories exist, skip project association.

**Step 5d: Copy files to project**

For both auto-associated and manually selected projects:

1. Get the project's folder name (from registry's `default_project` or user selection)
2. The project directory is: `~/Documents/Projects/{folder}/`
3. Create the subdirectory if it doesn't exist: `{project_dir}/calls/summaries/`
4. Copy the summary: `{project_dir}/calls/summaries/{slug}--summary.md`

Example:
```bash
mkdir -p ~/Documents/Projects/acme-consulting/calls/summaries
cp ~/.claude/skills/summarise-granola/data/summaries/2026-01-06-meeting--summary.md ~/Documents/Projects/acme-consulting/calls/summaries/
```

**Store `{project_dir}` in memory for Step 8** (the tidied transcript agent will handle its own copy into `{project_dir}/calls/transcripts/` when it finishes, to avoid blocking the main workflow).

**Step 5e: Offer to register unregistered people**

If the user selected a project for someone NOT in the registry, offer to add them:

> "Would you like me to register [Name] with [Project] for future calls?"

If yes, update `data/people.json` to add or update the entry with the `default_project` value. If Step 4 cached a `meeting_doc` for this person but the rest of the entry wasn't created yet, include that too.

**If user selects "None":** No additional action needed, and don't offer registration.

### Step 6: Report saved files

When reporting the files saved, always use **full expanded paths** (not relative paths or paths with `~`). This allows the user to control-click/command-click on the path in their terminal to open the file.

**Include, in this order:**
1. **Google Doc link** (if Step 4 ran) — the Google Doc URL from Step 4c. For 1:1s where the meeting doc was opened, note that the summary content is ready to paste. For standalone docs, note the doc was created and is shareable.
2. **Files saved** — full absolute path of the summary in the project folder (if project association happened) and in the skill's data directory.
3. **Auto-association note** (when applicable) — e.g. `Auto-associated with **Acme Consulting** (Jane Smith is registered to this project)`

(The tidied-transcript note isn't reported here — that option is chosen in Step 7 and reported when the agent launches in Step 8.)

**Do NOT run `open` on any markdown files.** The user's system opens them in VS Code, which is unwanted. The full paths in the report are already clickable in the terminal.

### Step 7: Ask about sharing, then send (the only interactive step)

This is the fast, final decision — by now the summary, the Google Doc, and the meeting-doc link are all done. **Skip this step entirely** for group meetings (more than 2 participants), meetings without a clear person name, or internal/solo sessions; you've already reported in Step 6, so just stop.

**Step 7a: Ask sharing options**

Use AskUserQuestion with `multiSelect: true`:

- **"Send call notes email to [Person Name]"** — always show for 1:1 calls (creates a Gmail draft)
- **"Send call notes Slack DM to [Person Name]"** — always show for 1:1 calls
- **"Create tidied transcript"** — always show (off by default). Fire-and-forget background agent launched in Step 8; does not block anything.
- **"Skip"** — always show; it's the default when the user is shown the options and submits an empty response.

**Codex fallback:** If `AskUserQuestion` or multi-select questions are unavailable, ask this as a normal chat message and STOP until the user answers. For 1:1 calls, do not treat silence or lack of tool support as "Skip".

If the user selects only "Skip" (no email, Slack, or tidied transcript), you're done.

**Step 7b: If email or Slack was selected, ask about comment and auto-send**

Ask two follow-up questions in a single AskUserQuestion call so the send can run unattended:

Question 1 — **Comment** (single-select):
- **"No comment"**
- **"Add a comment"** — the user types the comment via the free-text option

Question 2 — **Approval** (single-select):
- **"Auto-send when ready"** — skip the preview/confirmation and send immediately
- **"Show preview first"** — show a preview and wait for explicit confirmation before sending

**If the user picks "Auto-send when ready", do NOT ask for confirmation again in Steps 7c or 7d — just send.** The user has pre-authorised the send.

**Step 7c: Send call notes email** (if selected)

**a) Find the attendee's email address:**

1. Check `data/people.json` for an `email` field on the person's entry. If found, use it.
2. If not found, search Gmail using the Gmail MCP `search_threads` tool with query `"from:<person name> OR to:<person name>"` (limit 3 results). Then read the most recent thread to extract the attendee's email from the headers.
3. If Gmail search doesn't find a match, ask the user for the email address.
4. Once an email is obtained (from Gmail or the user), save it to the person's `email` field in `data/people.json` for future use.

**b) Comment:** use the comment answer collected in Step 7b. Do NOT ask again.

**c) Create the email using Gmail MCP:**

Use the Gmail MCP `create_draft` tool to create a draft email:

- **To:** the attendee's email address
- **Subject:** `Call notes`
- **Body (without comment):**
  ```
  Hi <first name>,

  Summary of our call here:
  <google_doc_url>

  All the best,
  [config.user_name]
  ```
- **Body (with comment):**
  ```
  Hi <first name>,

  Summary of our call here:
  <google_doc_url>

  <user's comment>

  All the best,
  [config.user_name]
  ```

Use the Google Doc URL from Step 4c (either the existing meeting doc or the newly created standalone doc). If no Google Doc was created, paste the summary content inline in the email body.

**Approval behaviour:**
- If the user selected **"Auto-send when ready"** in Step 7b, create the draft and tell the user it's ready to send in Gmail.
- Otherwise, show the user a preview of the email and ask for confirmation before creating the draft.

**Step 7d: Send call notes via Slack DM** (if selected)

1. **Find the person's Slack DM channel:**
   - Check `data/people.json` for a `slack_dm_channel` field on the person's entry
   - The field is an object: `{"channel_id": "D...", "workspace": "...", "slack_connect": true}`
   - If found, use it directly (skip to step 2)
   - If not found, use the Slack MCP `slack_search_users` tool to search for the person
   - **Full name verification (CRITICAL):** Match against full name, NOT just first name. The person's full name from the meeting title must exactly match a workspace member's name. If no exact full-name match is found, do NOT proceed — ask the user to identify the correct person. This prevents sending messages to the wrong person when multiple users share a first name.
   - Cache the channel details in `data/people.json` under the person's `slack_dm_channel` field for future use

2. **Comment:** use the comment answer collected in Step 7b. Do NOT ask again.

3. **Compose message** using Slack mrkdwn formatting — keep it brief (no greeting or sign-off):
   - **Without comment:**
     ```
     Summary of our call here:
     <google_doc_url>
     ```
   - **With comment:**
     ```
     Summary of our call here:
     <google_doc_url>

     <user's comment>
     ```

4. **Approval behaviour:**
   - If the user selected **"Auto-send when ready"** in Step 7b, skip the preview/confirmation and send immediately.
   - Otherwise, **show preview and ask for confirmation** before sending. The preview MUST show the recipient's full Slack profile name (e.g., "Send to **Jane Smith**?"). If the Slack profile name differs from the expected name from the meeting title, flag this explicitly as a potential mismatch.

5. **Send via Slack** using the Slack MCP `slack_send_message` tool with the channel ID and composed message.

6. Report success or failure to the user.

### Step 8: Launch tidied transcript agent (background, fire-and-forget)

**Skip this step** if the user did not select "Create tidied transcript" in Step 7a.

Launch with `Agent(model: "sonnet", run_in_background: true)`. This is the last thing you do — the Google Doc is saved and Slack/email are already sent. On launch, tell the user the tidied transcript is generating in the background and will be saved to [paths] when done. When the agent finishes later, acknowledge its completion notification with a one-liner (e.g. "Tidied transcript saved to X.").

**Agent prompt (must be self-contained — the agent has no access to this skill file):**

Before launching, `Read` `references/tidying-instructions.md` and paste its contents verbatim into the prompt.

Include:
1. The **absolute path to the raw transcript** (`data/transcripts/{slug}.md` — the `saved_to` value from Step 2) plus an explicit instruction to `Read` that file itself. **Do not paste the transcript body** — the agent reads it from disk, keeping the large transcript out of the orchestrator's context.
2. The **confirmed participant names** from Step 2.5.
3. Instruction to write the tidied transcript to: `~/.claude/skills/summarise-granola/data/tidied-transcripts/{slug}--transcript.md` (create the directory with `mkdir -p`).
4. If a project folder was determined in Step 5, also instruct the agent to copy the final file to: `~/Documents/Projects/{folder}/calls/transcripts/{slug}--transcript.md` (after creating the parent directory with `mkdir -p`).
5. The tidying guidelines from `references/tidying-instructions.md` (pasted verbatim).

## File locations

- **Raw transcripts:** `~/.claude/skills/summarise-granola/data/transcripts/`
- **Tidied transcripts:** `~/.claude/skills/summarise-granola/data/tidied-transcripts/`
- **Summaries:** `~/.claude/skills/summarise-granola/data/summaries/`

Files use the pattern:
- Raw transcripts: `YYYY-MM-DD-meeting-title-slug.md`
- Tidied transcripts: `YYYY-MM-DD-meeting-title-slug--transcript.md`
- Summaries: `YYYY-MM-DD-meeting-title-slug--summary.md`

## Transcript format

- `**Me**:` - User's microphone (the person running Granola)
- `**Other**:` - System audio (other participants)

## User info

Read the user's name from `config.json` (`user_name` field). Use this name in summaries, speaker label substitution, doc titles, and email sign-offs.

## Script reference

| Command | Description |
|---------|-------------|
| `check` | Check for recent call or list 5 most recent for selection |
| `list` | List all meetings with transcripts |
| `get <id>` | Get transcript by document ID |
| `recent [n]` | Get nth most recent transcript (default: 1) |

## Tips

- For long meetings, consider summarising in sections
- Ask what the user wants to focus on if the meeting covered multiple topics
- Include participant names from the meeting title when relevant

## People registry

There are two data stores for per-person metadata. **Check config.json first** for meeting doc URLs (setup-time), then fall back to the runtime registry.

### config.json — meeting doc URLs (setup-time)

The `meeting_docs` field in `config.json` maps person names to their meeting doc URLs. This is the primary source for meeting doc lookups.

### data/people.json — runtime-accumulated data

The registry at `~/.claude/skills/summarise-granola/data/people.json` stores metadata accumulated during skill runs: email addresses, Slack channels, project associations, and cached meeting docs discovered via search.

**Format:**
```json
{
  "people": {
    "jane-smith": {
      "full_name": "Jane Smith",
      "initials": "js",
      "email": "jane@example.com",
      "slack_dm_channel": {"channel_id": "D...", "workspace": "...", "slack_connect": false},
      "default_project": "coaching-jane",
      "meeting_doc": {
        "url": "https://docs.google.com/document/d/...",
        "title": "Jane / User 1:1",
        "cached_at": "2026-01-20"
      }
    }
  }
}
```

**Key format:** Lowercase, hyphenated full name (e.g., "Jane Smith" → "jane-smith")

**Fields:**
- `full_name` — display name
- `initials` — lowercase initials for doc lookup (e.g., "js", "ab")
- `email` — email address for sending call notes (Step 7c), or `null`
- `slack_dm_channel` — Slack DM details for sending call notes (Step 7d), or `null`. Object with `channel_id`, `workspace`, and `slack_connect` (boolean)
- `default_project` — project folder name for auto-association (Step 5), or `null`
- `meeting_doc` — Google Doc reference for call summaries (Step 4), or `null`

**Lookup order:**
1. **config.json** `meeting_docs` (for meeting doc URLs)
2. **data/people.json** (for all other fields, and as meeting doc fallback)
3. **Google Drive MCP search** (last resort for meeting docs)

**Adding entries:**
- Meeting doc URLs: saved to `config.json` during setup or when discovered via search (user is asked)
- Email, Slack, project data: saved to `data/people.json` automatically during skill runs
- Manual edits to either file
