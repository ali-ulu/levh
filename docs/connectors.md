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
import_from_app("git",         config={"repo_path": "/path/to/repo"})   # local, read-only
import_from_app("jira",        config={"base_url": "https://x.atlassian.net", "email": "me@x.com", "api_token": "..."})
import_from_app("linear",      config={"api_key": "lin_api_xxx", "team_ids": ["..."]})
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

**Jira & Linear — the tracker pair** (pull-on-demand, no background worker):

- **Jira** (`jira`): Jira Cloud REST API v3. Each issue → a memory led by
  `KEY: summary`, with status, type, assignee, priority, and the description
  (Atlassian Document Format is flattened to text). Requires `base_url`,
  `email`, and `api_token` (or `JIRA_BASE_URL` / `JIRA_EMAIL` / `JIRA_API_TOKEN`).
  Defaults to a bounded `updated >= -90d` JQL so a first sync cannot walk an
  entire site; override with `jql`. Options: `max_issues`, `include_comments`.
- **Linear** (`linear`): Linear GraphQL API. Each issue → a memory led by
  `IDENTIFIER: title`, with state, assignee, team, project, and labels. Requires
  `api_key` (or `LINEAR_API_KEY`). Options: `team_ids`, `project_ids`,
  `max_issues`, `include_comments`.

Both run only when you call `import_from_app` (or the sync route) — there is no
scheduler in LEVH, so "sync" means one fetch per invocation. Repeat calls are
safe: the admission gate dedupes, and `/api/connectors/sync` records last-synced
state per connector.

**Git — why the code looks like this** (local, read-only, offline):

- **Git** (`git`): reads a local repository's own history by shelling out to the
  `git` binary. Each commit → a memory led by `<short-sha>: <subject>`, with the
  body, the author and the date in metadata — so a later recall can answer *"who
  changed this, when, and why?"*. Unlike the `github` connector (which pulls a
  remote repository's README, issues and PRs over the API), this one reads commit
  history from disk and never touches the network.
- Requires `repo_path` — the repository **root**, the directory holding `.git`.
  A subdirectory is refused with the correct root named in the error: git walks
  upward from any directory, so accepting a nested path would let a directory
  that merely sits beneath some unrelated repository import that repository's
  entire history.
- Options: `ref` (default `HEAD`, or a branch/tag/sha), `max_commits` (default
  200), `since` / `since_days`, `author`, `include_body` (default True),
  `body_chars` (default 2000), `timeout_seconds` (default 120).
- Read-only by construction: `fetch` only ever runs `git log`, so importing a
  repository cannot mutate a worktree, branch or index.
