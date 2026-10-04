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
import_from_app("slack",       config={"bot_token": "xoxb-...", "channel_ids": ["C123"]})
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

**Jira & Linear — the tracker pair** (on-demand by default, auto-feed capable):

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

**Slack — bounded channel history** (pull-on-demand):

- **Slack** (`slack`) imports message text from explicit conversation IDs through
  the Slack Web API. It requires `bot_token` (or `SLACK_BOT_TOKEN`) and
  `channel_ids` (or comma-separated `SLACK_CHANNEL_IDS`). Supplying channel
  IDs directly avoids adding channel-discovery scopes just to find names.
- Credential validation uses `auth.test`; history uses
  `conversations.history` with cursor pagination. Each request asks for at most
  **15 messages**, which stays compatible with Slack's stricter
  distribution-specific history limit as well as the higher internal-app tier.
  `max_messages` (default 100) caps a whole run; optional `oldest` accepts a
  Slack timestamp lower bound.
- Stored memories keep Slack provenance in metadata: channel id, user/bot id,
  message timestamp, thread timestamp/reply count, team id/name, and an ISO
  `captured_at` derived from Slack's timestamp. The first slice deliberately
  does not expand threads with `conversations.replies`: doing so multiplies
  API calls per parent message and belongs behind a separate opt-in.

Jira, Linear, and Slack run when you call `import_from_app` (or the sync route). Since #374 there is
also an opt-in feed: `auto_sync` in `.stackmemory/config.json` runs chosen
connectors on a timer while the server lives (disabled by default), and
`background=true` on the sync route runs one slow sync as a tracked job instead
of holding the HTTP connection. Repeat calls are safe: the admission gate
dedupes, and `/api/connectors/sync` records last-synced state per connector.

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
- `include_file_history` (default False): one memory per file with its touch
  history — who touched it, how often, last change. Without arguments it
  reports the most-touched files in the walked range; `history_paths` names
  files explicitly, `history_max_files` (default 20) and `history_max_touches`
  (default 50) cap the work. This is the cheap answer to "whose hands has this
  file passed through".
- `include_blame` (default False): one memory per path with the line-author
  summary — share of lines per author plus the most recent touch. Raw blame is
  never stored (it changes with every commit, so verbatim rows would churn
  instead of deduping); same-second commits are ordered by the log, newest
  first. `blame_paths` defaults to `history_paths`, then the most-touched
  files; `blame_max_files` (default 10) caps it.
- `include_snapshot` (default False): one architecture snapshot of the revision
  — tracked-file counts per extension, top-level layout, entry points — keyed
  by HEAD sha, so re-syncing an unmoved HEAD dedupes instead of storing again.
- Read-only by construction: `fetch` only ever runs `git log`, `git blame`
  and `git ls-files`, so importing a repository cannot mutate a worktree,
  branch or index. History/blame paths must stay inside the repo root; `..`
  escapes are refused.

**GitHub — remote repository state**:

- **GitHub** (`github`) imports README content, open issues, optional open PRs,
  and selected files through the GitHub API. A PAT may be passed as `token`
  or supplied through `GITHUB_TOKEN`.
- CLI sync accepts connector-native lists *and* shell-friendly strings. For
  example, `--config repos=ali-ulu/levh` is one repository,
  `--config repos=owner/a,owner/b` is a comma-separated list, and a JSON array
  is also accepted. `include_files` accepts the same forms. Boolean options
  accept `true/false`; numeric limits are validated as integers.
- Token validation probes the first configured repository rather than
  `GET /user`. This supports both user PATs and GitHub Actions'
  repository-scoped installation token, which may read a repository while not
  representing a user identity.
- The repository includes a **Connector dogfood** workflow
  (`.github/workflows/connector-dogfood.yml`). It checks out full Git history,
  ingests LEVH through both `git` and `github` into an ephemeral database,
  then uploads only a JSON evidence report. It can be dispatched manually and also runs on `main` when the Git/GitHub connector or dogfood verifier changes. The report verifies
  `connector_sync` rows, stored provenance/types and recallability; neither
  the database nor token is uploaded.

Example:

```bash
levh sync git --project levh \
  --config repo_path=/path/to/levh \
  --config include_file_history=true \
  --config include_blame=true \
  --config include_snapshot=true

GITHUB_TOKEN=... levh sync github --project levh \
  --config repos=ali-ulu/levh \
  --config include_prs=true \
  --config include_files=README.md,server/connectors/github.py
```

**Background syncs & auto-feed** (opt-in, #374):

- `POST /api/connectors/sync` with `"background": true` answers `202` with a
  `job_id` at once; `GET /api/connectors/sync-jobs/{job_id}` polls it
  (`pending` → `running` → `done`/`error`, with the ingest report and
  per-stage `timing_ms`). Jobs are process-local and best-effort: a restart
  loses the job records, never the stored memories.
- `.stackmemory/config.json` may carry an `auto_sync` section
  (`enabled`, `interval_seconds`, `jobs: [{connector, config, project}]`)
  that runs while the server lives. Secrets never go in the file: any config
  value of the form `"env:NAME"` resolves from the process environment —
  `"token": "env:GITHUB_TOKEN"` reads `GITHUB_TOKEN`, and a missing variable
  fails that job loudly instead of syncing half-configured.
