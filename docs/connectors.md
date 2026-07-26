# App Connectors

Import data from your existing tools directly into LEVH's memory layer.
Every import can be namespaced under a project.

```python
import_from_app("calendar",    config={"ics_path": "/path/to/calendar.ics"})   # or ics_url
import_from_app("email",       config={"mbox_path": "/path/to/mail.mbox"})     # or eml_path / eml_dir
import_from_app("transcript",  config={"transcript_path": "/path/to/meeting.vtt"})  # or transcript_dir
import_from_app("notion",      config={"api_key": "ntn_xxx", "database_ids": ["..."]})
import_from_app("obsidian",    config={"vault_path": "/path/to/vault"})
import_from_app("github",      config={"token": "ghp_xxx", "repos": ["owner/repo"]})
import_from_app("local_files", config={"directory": "/path/to/project"})
```

**Calendar, Email & Transcripts — the work-life capture trio** (roadmap Phase 1):
*when/who* + *correspondence* + *what was said*. All parse the universal *offline*
export formats with zero extra dependencies, so no OAuth, no API keys, nothing leaves
your machine:

- **Calendar** (`.ics`): the format Google Calendar, Outlook, and Apple Calendar all
  export. Each event → a memory with title, time, attendees, and location — so you can
  ask *"what did I discuss with X last week?"*. Optional `past_days`/`future_days` window.
- **Email** (`.mbox` / `.eml`): Gmail Takeout, Thunderbird, Apple Mail, Outlook export.
  Each message → a memory with sender, recipients, subject, date, and a body excerpt —
  ask *"what did Dana email me about pricing?"*. Options: `past_days`, `max_messages`,
  `body_chars`, `exclude_senders` (skip no-reply/notification noise).
- **Transcripts** (`.vtt` / `.srt` / `.txt`): Zoom, Google Meet, Teams, Otter, Fireflies,
  or Whisper output. Each meeting → one **summarized** memory (LLM if `OPENAI_API_KEY` is
  set, offline extractive otherwise) with the speaker list and a transcript excerpt — ask
  *"what did we decide in the roadmap call?"*. Options: `summarize`, `max_chars`.

Or use the **Import from Apps** panel in the dashboard's Settings page.
