# AI Skills

Reusable GitHub Copilot / agent skills for Jira + GitLab workflows and Azure log analysis.

| Skill | What it does |
|---|---|
| `bug-hunter` | Picks one unassigned Jira Bug, fixes it with a red/green regression test, opens GitLab MRs, keeps pipelines green, reports back. Never merges. |
| `querying-azure-logs` | Queries Azure Log Analytics through the REST API for deployed services. |
| `_shared` | Not a skill: Jira client and docs (`jira.md`, `checkpoints.md`, `ticket-voice.md`, `review-drafts.md`) used by the skills. |

## Layout

`_shared` must sit **beside** the skill folders, because `bug-hunter` imports `../_shared/scripts`:

```
<SKILLS_ROOT>/
  _shared/
  bug-hunter/
  querying-azure-logs/
```

## Variables

| Variable | Example | Used by |
|---|---|---|
| `SKILLS_ROOT` | `~/skills` | all script examples |
| `JIRA_BASE_URL` (required) | `https://your-company.atlassian.net` | `jira_issue.py`, bug-hunter |
| `JIRA_EMAIL` | `you@example.com` | `jira_issue.py` (Basic auth) |
| `JIRA_API_TOKEN` | `<atlassian-api-token>` | `jira_issue.py` (Basic auth) |
| `JIRA_PROJECT_KEY` | `PROJ` | bug-hunter JQL, `createmeta` |
| `BUG_HUNTER_ASSIGNEE_ID` | `<jira-account-id>` | bug-hunter claim step |
| `GITLAB_HOST` | `git.example.com` | bug-hunter (`glab`, remote lookup) |
| `NOTIFY_WEBHOOK_URL` (optional, alias `TEAMS_WEBHOOK_URL`) | `https://example.com/webhook/...` | bug-hunter run report |
| `HANDOFF_DIR` | `~/.agent-handoffs` | `_shared/checkpoints.md` |
| `AZURE_TENANT_ID` | `<tenant-id>` | querying-azure-logs |
| `AZURE_SUBSCRIPTION_ID` | `<subscription-id>` | querying-azure-logs |
| `AZURE_LOG_ANALYTICS_WORKSPACE_ID` | `<workspace-id>` | querying-azure-logs |

Constants: Jira label `bug-hunter-waiting` and comment marker `bug-hunter-question`
(`bug-hunter/scripts/bug_hunter_jira.py`). Workflow/status names live in `STANDARD_WORKFLOW` and
`STATUS_ALIASES` in `_shared/scripts/jira_issue.py`; adapt them to your Jira workflow.

## Install

Copy or symlink the three folders into your agent's skills directory, keeping them together:

```powershell
New-Item -ItemType SymbolicLink -Path "<your-skills-dir>\bug-hunter" -Target "<repo>\bug-hunter"
New-Item -ItemType SymbolicLink -Path "<your-skills-dir>\querying-azure-logs" -Target "<repo>\querying-azure-logs"
New-Item -ItemType SymbolicLink -Path "<your-skills-dir>\_shared" -Target "<repo>\_shared"
$env:SKILLS_ROOT = "<your-skills-dir>"
```

## Run the scripts

Pure Python 3.9+, no dependencies (use `py -3` if `python` is unavailable on Windows):

```powershell
python "$env:SKILLS_ROOT\_shared\scripts\jira_issue.py" search --jql "project = $env:JIRA_PROJECT_KEY AND issuetype = Bug"
python "$env:SKILLS_ROOT\bug-hunter\scripts\bug_hunter_jira.py" comments PROJ-123
```

Add `--dry-run` to any writing `jira_issue.py` subcommand first. See `_shared/jira.md`.
