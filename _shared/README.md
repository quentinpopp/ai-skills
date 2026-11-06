# Shared scripts

Tooling used by more than one skill. A skill references these scripts instead of copying them or
writing inline REST calls.

> **[`jira.md`](./jira.md) is the single source of truth for Jira access** — credentials, the
> client, Markdown/ADF rules, an example workflow graph and Jira Cloud endpoint traps. Skills
> link to it instead of restating the auth block.
>
> **[`checkpoints.md`](./checkpoints.md) is the single source of truth for pausing and resuming** —
> the `PAUSE!` command, the handoff file under `$HANDOFF_DIR`, and the resume flow. Used by
> any implementation skill that works across several sittings.
>
> **[`ticket-voice.md`](./ticket-voice.md) is the single source of truth for how a ticket reads** —
> Simplified Technical English, title grammar per issue type, the one acceptance-criteria layout
> rule, and the shared wording of *Validation Checklist*, *Definition of Done*, *Evidence* and
> *Technical Pointers*. Used by ticket-drafting skills. `scripts/draft_check.py` enforces the
> mechanical half of it.
>
> **[`review-drafts.md`](./review-drafts.md) is the single source of truth for showing a document
> to the user** — every Markdown draft, report or comment the user reviews is written to a file,
> opened with `code "<file>"` in VS Code and published from that same file.

| Script | Used by | Purpose |
|---|---|---|
| `scripts/md2adf.py` | ticket-drafting skills | Converts a Markdown draft to Atlassian Document Format and validates the tree before it reaches Jira. |
| `scripts/draft_check.py` | ticket-drafting skills | Checker engine behind a skill's `scripts/check_draft.py`: parsing helpers, the shared structure/budget/style checks and the `DraftSpec` that carries everything type-specific. Enforces `ticket-voice.md`. |
| `scripts/jira_issue.py` | ticket-drafting, delivery and review skills, `bug-hunter` | Jira REST v3 client: `createmeta`, `get`, `baseline`, `create`, `subtask`, `update`, `verify`, `comment`, `transitions`, `transition`, `assign`, `search`, `checkbox`, `attach`. Imports `md2adf`, so descriptions **and comments** are converted on the fly. |
| `scripts/jira_ticket.py` | validation / release-preview skills | Read-only ticket parsing on top of `jira_issue`: ADF → Markdown, sections under English/German/plain-paragraph headings, *Validation Checklist* items with stage (`DEV`/`NONPROD`/`PROD`) and reviewer notes, `collect_jira`, and `po_validations` for `PO Validation (YYYY-MM-DD)` comments. |

## Comments

`comment` publishes a Markdown body as ADF, exactly like a description — so headings, lists,
tables and `code` spans survive. Use `--draft <file>` for longer comments and `--text` for
one-liners (`\n` becomes a line break). `--no-tasklist` keeps `- [ ]` items as plain bullets
instead of interactive Jira checkboxes, which is what reporting comments want.

```powershell
& $python "$shared\jira_issue.py" comment PROJ-1234 --draft "comment.md" --dry-run
```

## Workflow

`transition KEY --to "<status>"` routes through intermediate hops when the target is not directly
reachable (e.g. `To Do` → `In Progress` → `Review`), accepts status aliases, and
re-verifies every hop against the ticket's live transitions before applying it. The static graph
it plans with lives in `STANDARD_WORKFLOW` and mirrors `jira.md` §4 — keep both in sync.

## Usage

```powershell
$shared = "$env:SKILLS_ROOT\_shared\scripts"

python "$shared\jira_issue.py" create --project $env:JIRA_PROJECT_KEY --type Bug `
    --summary "<title>" --draft "<draft>.md" --dry-run
```

All scripts are pure Python 3.9+ with no third-party dependencies. If `python` on Windows opens
the Microsoft Store, use `py -3`.

## Credentials

`jira_issue.py` reads `JIRA_BASE_URL` (required), `JIRA_EMAIL` and `JIRA_API_TOKEN` from the
environment, with a persisted user-scope fallback on Windows. `--email <address>`, placed before
the subcommand, overrides `JIRA_EMAIL`. The script never prints the token. If the token is
missing it exits with a message, so the calling skill can switch to its manual flow.

## Rules

- The scripts handle mechanics only. Approval gates, wording and ticket content stay with the
  skill that calls them.
- `jira_issue.py` never publishes without being told to. Use `--dry-run` to produce the payload
  preview that the PO approves.
- Keep skill-specific rules out of these files. A bug-specific or story-specific check belongs in
  that skill's own `scripts/` folder.
- Changing a shared script affects every calling skill. Check the other callers first.
