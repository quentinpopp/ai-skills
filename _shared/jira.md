# Jira access — single source of truth

Everything the skills need to know about talking to Jira: credentials, the client, an example
workflow and the traps Jira Cloud has. **Skills reference this file instead of repeating the
auth block or hand-rolling REST calls.**

---

## 1. Credentials

Set once per machine by the developer, never by an agent, never written into a skill, ADR,
checkpoint or commit:

| Variable | Meaning |
|---|---|
| `JIRA_EMAIL` | Atlassian account e-mail |
| `JIRA_API_TOKEN` | Atlassian API token (create at id.atlassian.com → Security → API tokens) |
| `JIRA_BASE_URL` | **required**, e.g. `https://your-company.atlassian.net`; no default, the script fails clearly if unset |
| `JIRA_PROJECT_KEY` | your project key, e.g. `PROJ` (used in examples and as `--project` value) |

```powershell
[Environment]::SetEnvironmentVariable("JIRA_BASE_URL", "https://your-company.atlassian.net", "User")
[Environment]::SetEnvironmentVariable("JIRA_EMAIL", "<you>@example.com", "User")
[Environment]::SetEnvironmentVariable("JIRA_API_TOKEN", "<token>", "User")
```

Rules for every skill:

- **Never print, echo or log the token**, and never persist it in a file the repo tracks.
- `jira_issue.py` reads the process environment first and falls back to the persisted Windows
  user scope, so a token set after the shell started is still found. `--email <address>` before
  the subcommand overrides `JIRA_EMAIL`.
- **If the token is missing, do not ask the developer to set it up mid-task** — the script exits
  with a clear message; switch to the skill's manual copy-paste flow and say so once.

**Auth scheme:** this is Jira **Cloud**, so it is HTTP Basic with `<email>:<token>` base64-encoded
— the standard Atlassian API-token pattern. A bare bearer token reliably fails with HTTP 403
("Failed to parse Connect Session Auth Token", an error specific to Connect-app session tokens,
not user tokens). Do not try a bearer token first. If Basic auth fails with 401/403, the token is
rotated/revoked or the e-mail is wrong — ask the developer to re-check rather than guessing at
other schemes.

---

## 2. Use the shared client

```powershell
$shared = "$env:SKILLS_ROOT\_shared\scripts"
python "$shared\jira_issue.py" <subcommand> ...   # or: py -3 ...
```

`SKILLS_ROOT` is the folder that contains `_shared` next to the skill folders. If `python` on
Windows opens the Microsoft Store, use `py -3` or the full interpreter path.

| Subcommand | Purpose |
|---|---|
| `createmeta --project PROJ` | list issue types |
| `get KEY` | read an issue (`--adf-out`, `--baseline-out`) |
| `baseline KEY --out f.json` | snapshot for the pre-update conflict check |
| `create` / `subtask` | create an issue or a sub-task from a Markdown draft; refuses a `Parent:` line in the body |
| `update KEY` | update summary/description, with baseline conflict check |
| `verify KEY --draft f.md` | read-back check of the published description |
| `comment KEY` | post a Markdown comment, converted to ADF |
| `transitions KEY` | list the transitions available from the current status |
| `transition KEY --to "<status>"` | move to a target status, routing through intermediate hops |
| `assign KEY [--to me]` | set the assignee |
| `search --jql "..."` | JQL search via the current endpoint |
| `attach KEY --file p` | upload an attachment |

Every writing subcommand supports `--dry-run`. **Use it first and show the developer the preview**
— the script never decides to publish on its own; approval gates stay with the calling skill.

---

## 3. Content is Markdown, published as ADF

Descriptions **and comments** go through `md2adf.py`. Never send a Markdown or wiki-markup string
as a body: it lands as one unformatted blob and headings, lists, tables and `code` spans are lost.

- `AC-1`-style labels stay plain `text` nodes — Jira otherwise renders them as issue cards.
  Add link marks only for genuinely intended ticket links.
- `- [ ]` / `- [x]` become interactive ADF `taskList` nodes. Wanted in a description that carries
  a Validation Checklist; for a comment that merely *reports* results pass `--no-tasklist` so the
  items render as ordinary bullets.
- When round-tripping raw responses through PowerShell, parse with
  `ConvertFrom-Json -Depth 100 -DateKind String` — automatic date inference rewrites ISO
  timestamps inside ADF text nodes.
- After publishing, read the content back and compare semantically (ignore key order and
  harmless normalization such as omitted vs. empty `attrs`), not by matching words in a preview.

---

## 4. Workflow (generic example)

Workflows differ per project. Run `transitions KEY` to see yours, then adapt `STANDARD_WORKFLOW`
and `STATUS_ALIASES` in `jira_issue.py`. The shipped example:

```
To Do ──► In Progress ──► Review ──► Done
  │            ▲   ▲          │       │
  ▼            │   └──────────┘       │
Waiting ───────┘                      │
  ▲                                   │
  └────────── To Do ◄─────────────────┘ (reopen)
```

`transition KEY --to "Review"` plans the route itself (e.g. `To Do` → `In Progress` → `Review`)
and verifies every hop against the ticket's live transitions before applying it. Keep the map in
sync with your real workflow.

### Traps

- **A target may not be directly reachable** — a hop via an intermediate status can be mandatory,
  and the second transition only becomes visible after the first was applied.
- **Transition names are not status names.** Several transitions can land on the same status.
  Propose the **target status** to the developer, never the transition label.
- **Status names can be localized** per account while the issue changelog renders them in English.
  An `Accept-Language` header does **not** change this. Map spellings via `STATUS_ALIASES`.
- **Reopen vs. rework:** a "reopen" transition usually only applies to closed tickets and drops
  them to the first status; use the dedicated rework/back transition when a review sends work back.

---

## 5. Endpoint notes

- **`GET /rest/api/2/search?jql=...` was removed by Atlassian and returns HTTP 410 Gone**
  (confirmed 2026-09-25). The replacement is `GET /rest/api/3/search/jql?jql=...`, which is what
  `search` uses. A `410` on a search means the endpoint is wrong — not the JQL, not the
  credentials; do not start debugging either.
- **Rate limiting / transient errors:** rapid successive calls (e.g. looping over tickets with no
  delay) can intermittently return a misleading `404` ("Die Vorgänge existieren nicht…") even for
  valid, accessible tickets. Treat a `404` on a ticket already fetched successfully in the same
  session as transient — retry once or twice with a few seconds' delay before reporting it as a
  permissions or existence problem.
- Attachments use `/rest/api/2/issue/<KEY>/attachments` with `X-Atlassian-Token: no-check` and
  **no** `Content-Type` header (multipart) — `attach` handles this.
