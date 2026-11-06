---
name: bug-hunter
description: "Autonomously picks one open, unassigned Bug from a Jira backlog, claims it, fixes it in the affected repositories with a red/green regression test, runs a local code review, opens one GitLab MR per repository labelled ai-generated, posts the review as an MR comment, keeps the MR pipelines green (max. 3 rounds) and links the MRs back on the Bug, which moves to Review. Decides simple questions itself and asks a developer only critical ones (Jira comment or MR thread), keeping the Bug in In Progress with a waiting mark; the next run resumes after the answer and answers MR comments. A Bug that does not reproduce gets a Jira comment instead of a fix. Never merges. Manually triggered; use when asked to hunt, pick or auto-fix a backlog bug, or to run the bug resolver / bug hunter."
---

# Skill: Bug Hunter

> One invocation = **one Bug** from backlog to reviewable MRs. The bug hunter decides simple
> questions itself and asks a developer only a critical question (§13), asynchronously in Jira or
> in its MR; the next run (§0a) picks up the answer. The developer reads, answers and approves.
>
> | # | Phase | AC |
> |---|---|---|
> | 0a | Resume a Bug with an answer or an MR comment; close expired waits | AC 8–9, 12–13 |
> | 1 | Select one Bug | AC 1–3 |
> | 2 | Claim it in Jira | AC 4 |
> | 3 | Derive the repositories | AC 5 |
> | 4 | Branch, reproduce, fix, commit | AC 6–8 |
> | 5 | Local checks | AC 9 |
> | 6 | Code review loop | AC 10–11 |
> | 7 | Push, MRs, review comment | AC 12–13 |
> | 8 | Pipeline loop | AC 14 |
> | 9 | Jira feedback and report | AC 17 |
> | 12 | Notification report (closes every run, incl. §1 no Bug, §3 abort, failure) | AC 18–23 |
> | 13 | Decide or ask: simple questions are decided, critical ones asked | AC 1–7, 11 |
>
> Two exits end a run early, and both still end in `Review`:
> §10 (draft MR) for unfinished work (AC 15), §11 (no implementation) for a Bug that does not
> reproduce or needs no change.
>
> **Not this skill:** fixing a Bug *with* a developer in the loop, or reviewing someone's open MR.
> Scheduling and remote execution are out of scope, as are notifications for other skills and
> answers through the notification channel.
>
> **Status names** (`In Progress`, `Review`) are the generic examples from `STANDARD_WORKFLOW` in
> `jira_issue.py`; adapt them there to your Jira workflow.

---

## Autonomy

Invoking this skill **is** the developer's approval for exactly these actions, for the one selected
Bug and the repositories derived in §3. This is a deliberate exception to any per-action approval
rules for Git and Jira in your repository guidelines:

- Jira: assign, comment (including a question that mentions the reporter), set and remove the
  waiting label `bug-hunter-waiting`, transition to `In Progress` and `Review`.
- Git: fetch, create worktree + branch, commit, push the new branch — also on a resumed Bug.
- GitLab: create MRs (or draft MRs), post MR notes and thread replies, mark an MR as draft or ready.
- Notification: post the run report to the team channel via `NOTIFY_WEBHOOK_URL` (§12).

Everything else stays forbidden: merging any MR (AC 16), force-push, rewriting pushed history,
pushing to the default branch, editing the Bug's description or fields not listed above, touching
other tickets, and committing secrets or user files. Do not call `ask_user` during the run; a
question is decided or asked asynchronously per §13 — never interactively.

---

## 0. Prerequisites

1. Read `_shared/jira.md` once — credentials, client, workflow graph. All Jira calls go
   through `jira_issue.py`:
   ```powershell
   $python = "python"
   $shared = "$env:SKILLS_ROOT\_shared\scripts"
   $bh = "$env:SKILLS_ROOT\bug-hunter\scripts\bug_hunter_jira.py"   # label, question, comments
   ```
   Run the scripts with `& $python ...` (use `py -3` instead of `python` if needed). Required variables:
   `SKILLS_ROOT`, `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`, `JIRA_PROJECT_KEY`,
   `GITLAB_HOST`, `BUG_HUNTER_ASSIGNEE_ID` (Jira account id the Bug is assigned to).
2. `glab auth status --hostname $env:GITLAB_HOST` must succeed. If Jira or `glab` auth fails,
   stop before §1 and tell the developer — nothing has been claimed yet.
3. `$root` = the workspace folder that holds the repositories as siblings (normally the session's
   working directory). Each repo is `$root\<repo>` with a `.git` folder.
4. Notification webhook (§12): `$env:NOTIFY_WEBHOOK_URL` (alias `TEAMS_WEBHOOK_URL`) holds the
   webhook URL of the team channel (e.g. a Teams Workflows webhook). The URL is a secret: never
   print, log or persist it. If it is unset,
   continue the run and only mention it in the final report — a missing webhook never stops a run
   (AC 21).
5. Create a run folder `<session-folder>\files\bug-hunter\` for drafts (MR descriptions, review,
   comments).

---

## 0a. Resume Before Selecting (AC 8–9, 12–13)

Runs before §1. It finds work a developer has unblocked, so a new Bug is only selected when there is
nothing to resume (AC 8). A note or comment **posted by the bug hunter starts with `bug-hunter`**
(`bug-hunter:` or `bug-hunter-question`); everything else is a human. The skill relies on this
prefix in Jira and GitLab, so every comment and MR note you write must carry it (§2, §7, §9, §13).

**Waiting Bugs** — the backlog filter for the waiting mark is `labels = bug-hunter-waiting`:

```powershell
& $python "$shared\jira_issue.py" search --max 50 --fields "summary,labels,created" `
  --jql "project = $env:JIRA_PROJECT_KEY AND issuetype = Bug AND labels = bug-hunter-waiting AND statusCategory = 'In Progress'"
```

Per Bug, read `& $python $bh comments <KEY>` and the threads of its MR (`fix/<KEY>-*`,
`glab api "projects/:id/merge_requests/<iid>/discussions"`). A question is **answered** when a human
note follows the bug hunter's `bug-hunter-question` (Jira comment or MR thread) and no later
`bug-hunter` note exists. A later `bug-hunter` note (e.g. a failure reason) means: no new answer.

**Bug MRs with a human comment (AC 9)** — also for Bugs already in `Review`: list the open MRs
labelled `ai-generated` per repository
(`glab api "projects/:id/merge_requests?state=opened&labels=ai-generated"`), take the Bug key from
the branch `fix/<KEY>-…`, and look for unresolved discussions whose last note is human.

**Order:** if candidates exist, resume the one with the oldest answer or comment, and only that one
(one Bug per run). Then continue as follows:

- Answer in Jira, no branch yet → restart at §3/§4 with the answer as an input of the ticket.
- Answer in an MR thread → check the branch out into a worktree
  (`git fetch $remote; git worktree add --track -b "fix/<KEY>-<slug>" "$root\.worktrees\<repo>\<KEY>" "$remote/fix/<KEY>-<slug>"`;
  if the local branch or worktree already exists, reuse it after `git pull --ff-only`), continue the fix with the
  answer, re-run §5 for what you touched, commit, push, and **reply in that thread** (`bug-hunter: …`
  what changed, commit SHA). Do not resolve the thread; the developer does. Then §8 and §9.
- Other human comment on an MR → address it the same way (change, or a reasoned reply when no
  change is needed) and reply in the same thread (AC 9). Do not open a new thread or MR note.
- Resolving the last question of a draft MR: when no open point is left, mark it ready
  (`glab mr update <iid> --ready`).
- Resume run fails (AC 13): comment the reason on the Bug (`bug-hunter: resume failed — <reason>`),
  reply the same in the affected MR thread, keep the waiting mark and leave the Bug in its status.
  Then post the notification report (§12, outcome `failure`).

**Expired waits (AC 12)** — for every waiting Bug without an answer whose last
`bug-hunter-question` is older than five working days (Mon–Fri, holidays are not considered):
remove the mark (`& $python $bh wait-remove <KEY>`), comment
`bug-hunter: no answer within five working days — handing over with open points`, add the open
question to the draft MR's `## Open points` (§10) if an MR exists, and move the Bug to `Review`
(§9). This is housekeeping: it does not use up the run's Bug; continue with the next step.

If nothing is to resume, continue with §1 and process the Bug **exactly as before** (AC 10).

---

## 1. Select One Bug (AC 1–3)

```powershell
& $python "$shared\jira_issue.py" search --max 50 `
  --fields "summary,priority,created,components,labels" `
  --jql "project = $env:JIRA_PROJECT_KEY AND issuetype = Bug AND statusCategory = 'To Do' AND assignee is EMPTY ORDER BY priority DESC, created DESC"
```

The JQL filter is fixed by AC 1; the `ORDER BY` puts the highest priority first and the newest
first among equal priorities (AC 2). Walk the list **in this order**, read each candidate with
`jira_issue.py get <KEY>`, and select the **first** one that passes every skip rule. Exactly one Bug
per run.

Skip a Bug silently (no Jira write) if any of these holds (AC 3):

| Skip rule | Signal |
|---|---|
| No clear reproduction | No steps, preconditions or request that let you trigger the defect; or observed vs. expected behaviour is not stated |
| Infra / Terraform | Component or text points to `infra`, Terraform, Helm values, Key Vault, networking, certificates, pipeline-only changes |
| Production data fix | The fix needs a data repair, SQL on a stage DB, or manual PROD intervention |
| Excludes AI | Label or text says so (`no-ai`, `ai-excluded`, "no AI") |

Record every skipped key with its rule for the final report. If no candidate is left, report
"no eligible Bug" with the skip table, post the notification report (§12, outcome `no eligible Bug`) and stop.

---

## 2. Claim the Bug (AC 4)

Before any analysis or code work:

```powershell
& $python "$shared\jira_issue.py" assign <KEY> --to $env:BUG_HUNTER_ASSIGNEE_ID   # technical/service account
& $python "$shared\jira_issue.py" comment <KEY> --no-tasklist `
  --text "bug-hunter: An AI agent picked up this Bug and works on a fix. The MR link follows here."
& $python "$shared\jira_issue.py" transition <KEY> --to "In Progress"
```

Do not use `transition --assign-me`; the assignee is the technical account, not the developer
running the session. From here on, the Bug is no longer `assignee is EMPTY`, so later runs do not
pick it again — every abort below must therefore leave a Jira comment with the reason.

---

## 3. Derive the Repositories (AC 5)

Derive **every** repository the fix touches, in this order: Jira components → labels → ticket text
(service names, endpoints, class/file names, UI page names). Confirm the list while reproducing in
§4: if the root cause turns out to sit in a further repository, add it there.

| Component | Repository |
|---|---|
| `<component-a>` | `<repo-a>` |
| `<component-b>` | `<repo-b>` |
| `<component-c>` | `<repo-c>` |

Replace this example table with your own component → repository mapping.

Abort (AC 5) with a Jira comment `bug-hunter: aborted — <reason>` and stop if:
- no repository can be derived,
- a needed repository is not in this table or `$root\<repo>\.git` does not exist.

The Bug stays assigned and `In Progress`; a human decides how to continue. Post the notification report
(§12, outcome `abort`) after the Jira comment.

---

## 4. Branch, Reproduce, Fix, Commit (AC 6–8)

**Branch (AC 6)** — never touch the developer's checkout; one worktree per repository, all with the
same branch name:

The remote is not always called `origin`: resolve it by URL, then use `$remote`
for every fetch and push.

```powershell
$remote = git -C "$root\<repo>" remote -v | Select-String "$([regex]::Escape($env:GITLAB_HOST)).*\(push\)" |
    ForEach-Object { ($_.Line -split '\s+')[0] } | Select-Object -First 1
git -C "$root\<repo>" fetch $remote
git -C "$root\<repo>" remote set-head $remote --auto
$base = git -C "$root\<repo>" symbolic-ref --short "refs/remotes/$remote/HEAD"   # e.g. gitlab/master
git -C "$root\<repo>" worktree add "$root\.worktrees\<repo>\<KEY>" -b "fix/<KEY>-<short-slug>" $base
```

If `$remote` or `$base` is empty, stop and treat it like an underivable repository (§3 abort) —
never branch from the local `HEAD`.

Work only in the `$root\.worktrees\<repo>\<KEY>` worktrees. Read each repo's contributor docs (e.g. `AGENTS.md`) first.

**Fix** — decompose the Bug, reproduce first, write a Root-Cause Note, make the smallest safe diff,
capture red/green evidence and add a regression guard, with these differences for an
autonomous run:

- Every developer confirmation gate is skipped. Questions follow §13:
  simple ones you decide, critical ones you ask asynchronously. The Root-Cause Notes go into
  the MR descriptions instead.
- If the defect does not reproduce, is already fixed, or needs no code change → §11.
- If the fix needs an architectural decision → ask per §13 (or §10 when no question is left).
- An API, event or DB-contract change is allowed only if every affected producer and consumer is
  among the derived repositories and changed in this run. Name it as a reviewer focus point.
- A hand-back to the developer is replaced by §5 onwards of this skill.

**Red/green evidence (AC 7)** — before committing, for every regression test you add: with the
production change reverted (`git stash push -- <production files>`), run the test and capture the
failure output; restore (`git stash pop`), run it again and capture the pass. The test must fail
**for the defect**, not for a compile error or a missing fixture. A test that passes without the
fix is not evidence — rewrite it or go to §10. In a multi-repo fix, every repository with a
production change gets its own regression test where the defect is observable there.

**Commit (AC 8)** — per repository, one commit per defect, Conventional Commit, footer with the AI model, context size and reasoning
level, plus the Copilot trailer:

```
fix(<scope>): [#<KEY>] <what is fixed>

<optional body: root cause in one or two sentences>

AI model: <model name> (<model id>), context size: <size>, reasoning level: <level>

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
```

Use the repo's git hooks as they are; never bypass them with `--no-verify`.

---

## 5. Local Checks (AC 9)

Before any MR, all of these must be green in every worktree:

| Repo type | Tests (every added or changed test) | Format / lint |
|---|---|---|
| Kotlin / Spring Boot | `mvn test -Dtest=<Class>` (unit), `mvn verify -DskipUTs=true -DskipITs=false -Dit.test=<Class>` (IT) | `mvn spotless:check` |
| Vue / PNPM | `pnpm run test:component:vitest -- <spec>`, `pnpm exec cypress run --component --spec <spec>` | `pnpm run lint` |

If formatting fails, run `mvn spotless:apply` / `pnpm run lint:fix`, commit the result, and re-run
the check. Do not run the full unit/integration suites locally — the MR pipelines own them
(AC 9a). If a check cannot run (missing toolchain, registry auth), that is an open point for §10,
not a pass.

---

## 6. Code Review Loop (AC 10–11)

Sizing gate: the review always runs as **one** sub-agent over all worktrees, because its value is
an independent perspective on the whole fix — including the contract between repositories — that
the author context cannot give. No further fan-out.

Launch the custom agent **`code-reviewer`** (fallback: built-in `code-review` with the same
inputs, disclosed in the report). Give it: each worktree path with its `$base` and HEAD SHAs, the
Bug's observed/expected behaviour and ACs, the Root-Cause Notes, the red/green table, and the §5
commands with their results — and tell it not to run checks itself, because §5 already did.

Write its report to `<run-folder>\review.md`.

- 🔴 Blocker, 🟠 Major and 🟡 Minor findings: fix them, re-run §5, commit
  (`fix(<scope>): [#<KEY>] address review: <topic>`), and review again.
- ⚪ Nits: optional, never a reason for another round.
- **At most 2 fix rounds** (AC 11). If 🔴 or 🟠 findings remain after round 2, continue with §7 but
  as draft MRs (§10).

The final `review.md` holds the last review result plus a short table of the rounds
(finding → fix commit).

---

## 7. Push, MRs, Review Comment (AC 12–13)

One MR per repository with commits. In a multi-repo fix, push and open the providing side first
(backend before UI), so the consumer MR can link to it.

```powershell
git -C "$root\.worktrees\<repo>\<KEY>" push -u $remote "fix/<KEY>-<short-slug>"
```

Write each MR description to `<run-folder>\mr-<repo>.md`, following
your team's MR conventions (title = Conventional Commit),
with these sections: summary; Root-Cause Notes; red/green table; regression-guard answers
(does a test pin the fix, does it fail without it); reviewer focus points; in a multi-repo fix, the links to the sibling MRs
and the merge order; the line `Generated autonomously by the bug-hunter skill — please review with
care.`; the `## Decisions made by the bug hunter` section (§13, AC 2: every question it decided
itself, the decision and its source; "none" if there was none); reference `Bug: <KEY> – "<summary>"`.

Run `glab` from inside each worktree:

```powershell
glab mr create --source-branch "fix/<KEY>-<short-slug>" --target-branch "<base without $remote/>" `
  --title "<title>" --description (Get-Content -Raw "<run-folder>\mr-<repo>.md") `
  --label "ai-generated" --remove-source-branch --yes   # add --draft when §10 applies
"bug-hunter: review`n`n" + (Get-Content -Raw "<run-folder>\review.md") | glab mr note create <iid> --resolvable=false
```

Reviewers are not set by the skill: the GitLab approval rules of each repo assign the team (AC 12).
Post the review as **one** note per MR (AC 13), not as separate threads.

---

## 8. Pipeline Loop (AC 14)

For each MR, wait for its newest MR pipeline. Read it from the MR, not from the branch: MR pipelines
run on the merged result, so their SHA differs from the pushed commit and `glab ci status --branch`
may show a different (branch) pipeline. Poll every 60–120 s; backend pipelines take 10–25 min.

```powershell
$pipeline = (glab api "projects/:id/merge_requests/<iid>/pipelines" | ConvertFrom-Json)[0]   # newest first
$pipeline.status   # wait while created/pending/running
```

Make sure the pipeline was created after your latest push (`created_at`). Then:

- `success` → done for this MR.
- `failed` → fetch the failed jobs and their logs
  (`glab api "projects/:id/pipelines/<id>/jobs"`, `glab api "projects/:id/jobs/<job>/trace"`), grep
  for the first real failure, fix it in the worktree, re-run §5 for what you touched, commit
  (`fix(<scope>): [#<KEY>] fix pipeline: <topic>`), push, wait again.
- **At most 3 fix rounds per MR.** A second identical failure after a fix means change the
  approach, not repeat it.
- Environment failures (registry 401/403, runner outage, missing CI variable) are not fixable from
  the branch: stop the loop for this MR and go to §10.

When every MR is done → §9.

---

## 9. Jira Feedback and Report (AC 17)

Every run that got past §2, except a §3 abort and a §13 question, ends here — with clean MRs, draft MRs (§10) or no
implementation (§11). If the Bug carries the waiting mark, remove it first
(`& $python $bh wait-remove <KEY>`):

```powershell
& $python "$shared\jira_issue.py" comment <KEY> --no-tasklist --draft "<run-folder>\jira-comment.md"
& $python "$shared\jira_issue.py" transition <KEY> --to "Review"
```

The comment starts with `bug-hunter:` and holds the outcome: every MR link (draft or not) with its pipeline result and the
one-line root cause — or, from §10/§11, the open points or findings. Transition to `Review` if the
live transitions offer it (`transition` checks this; if it is not offered, say so in the report —
do not route around it).

After the Jira steps, post the notification report (§12).

Final report to the developer (chat, short):
- Selected Bug, skipped Bugs with their skip rule.
- Repositories, branch, MR links, draft yes/no — or "no implementation" with the reason.
- Red/green table, review rounds, pipeline rounds and final status.
- Open points (if any).

Then clean up: remove a worktree if its MR is green and non-draft or it has no commits; otherwise
leave it for the human who takes over and name its path in the report. `git worktree remove` fails
on Windows with "Filename too long" for deep `node_modules`/`target` trees; then delete the folder
via the extended-length path and prune:

```powershell
git -C "$root\<repo>" worktree remove "$root\.worktrees\<repo>\<KEY>"
if (Test-Path "$root\.worktrees\<repo>\<KEY>") { cmd /c "rd /s /q \\?\$root\.worktrees\<repo>\<KEY>" }
git -C "$root\<repo>" worktree prune
```

---

## 10. Draft MR — the Exit for Unfinished Work (AC 15)

Reached when a fix exists in part but cannot finish cleanly: an architectural decision is needed,
a check cannot run, or a round limit is hit (§6, §8).

- Push and create the MRs with `--draft` (or `glab mr update <iid> --draft` if they already exist)
  for every repository with commits. If no repository has commits yet (e.g. an architectural
  decision blocks before the first change), there is no MR; the open points go into the Jira
  comment only.
- Add an `## Open points` section to each MR description: what blocks, what was tried, the current
  hypothesis, and what a human must decide.
- Continue with §9: the Jira comment lists the draft MRs and the open points, and the Bug moves to
  `Review`.

---

## 11. No Implementation — Bug Does Not Reproduce or Needs No Change

Reached from §4 when the defect cannot be reproduced or traced in code, is already fixed on the
default branch, or the reported behaviour turns out to be intended.

- Change no code and create no MR; discard any local experiments in the worktrees.
- Write the Jira comment for §9 with the findings: what was checked (steps, tests, code paths with
  `file:line`, branch/SHA), why no change is needed, and what the product owner should verify.
- Continue with §9: the Bug moves to `Review`.

---

## 12. Notification Report (AC 18–23)

Every run ends with **exactly one** message to the team channel behind `NOTIFY_WEBHOOK_URL`, scheduled or manual
(AC 18). It is the last step, after the Jira and GitLab steps, so a notification problem can never block
them (AC 23).

**Outcome** (AC 19) — exactly one of:

| Outcome | When |
|---|---|
| `MR ready` | §9 with non-draft MRs |
| `draft MR with open points` | §10 with MRs |
| `waiting for answer` | §13: a question was asked and the Bug waits |
| `no implementation` | §11, or §10 without any MR |
| `abort` | §3 abort |
| `no eligible Bug` | §1 without candidate |
| `failure` | the run breaks before reaching one of the above (AC 22), e.g. tool or auth error, crash |

**Content** (AC 20, 21): outcome, Bug key with link `$env:JIRA_BASE_URL/browse/<KEY>`
and summary (omit for `no eligible Bug`), every MR URL of the run (draft or not), and one line of
reason for `abort`, `no implementation` and `failure`. Nothing else: no credentials, tokens, URLs
with tokens, log excerpts or environment values. For `failure`, name the failed phase and the error
class only, never raw output.

```powershell
$card = @{
  type = "message"
  attachments = @(@{
    contentType = "application/vnd.microsoft.card.adaptive"
    content = @{
      '$schema' = "http://adaptivecards.io/schemas/adaptive-card.json"; type = "AdaptiveCard"; version = "1.4"
      body = @(
        @{ type = "TextBlock"; weight = "Bolder"; text = "bug-hunter: <outcome>" },
        @{ type = "TextBlock"; wrap = $true; text = "<Bug link + summary, MR links, reason — Markdown, one per line>" }
      )
    }
  })
} | ConvertTo-Json -Depth 10
try {
  Invoke-RestMethod -Method Post -Uri $env:NOTIFY_WEBHOOK_URL -ContentType "application/json; charset=utf-8" `
    -Body ([Text.Encoding]::UTF8.GetBytes($card)) -TimeoutSec 30 | Out-Null
} catch {
  Write-Warning "Notification post failed: $($_.Exception.GetType().Name)"   # never print the URL or the response
}
```

- If the webhook variable is unset or the post fails, the run is **not** failed or repeated: the
  Jira and GitLab results stand (AC 21). Name "Notification not delivered" in the final report.
- Failure path (AC 22): when any step throws before the normal end, post the `failure` message
  once, with the same `try/catch`, then stop. Do not post a second message if one was already sent.

---

## 13. Decide or Ask (AC 1–7, 11)

Every question that comes up while working a Bug is either decided or asked. Asking is the
exception.

**Decide yourself (AC 1–2)** when the ticket, the code, the tests or the repo conventions
(`AGENTS.md`, neighbouring code) answer it. Do not ask; write the decision into the MR description
section `## Decisions made by the bug hunter` as one line each: question → decision → source
(e.g. `Keep status code 404 → matches the existing tests in XyzControllerTest`). Without such a
decision, write "none".

**Ask (AC 3)** when a question touches any of:
- behaviour beyond the ticket,
- an API, event or database contract,
- stored data,
- roles or access,
- two equally supported options.

**Where (AC 4–5)**:

| Question about | Channel | How |
|---|---|---|
| Expected behaviour | Jira comment on the Bug, reporter mentioned | `& $python $bh ask <KEY> --draft <file>` |
| The code | Thread in the Bug's MR | `glab api "projects/:id/merge_requests/<iid>/discussions" -f body="$(Get-Content -Raw <file>)"` |

Code questions need an MR to live in: push the commits made so far and open the MR as draft (§7,
§10). If no commit exists yet, ask the question in the Jira comment instead.

**Format (AC 6)** — every question starts with `bug-hunter-question (round <N>)`, then the context
in two or three sentences, the options (A, B, …) with their consequences, and
`Proposal: <option> — <reason>`. Collect all critical questions of the Bug into **one** round.

**After asking (AC 7):** set the waiting mark and keep the Bug in `In Progress` — do not move it to
`Review`:

```powershell
& $python $bh wait-add <KEY>
```

Post the notification report (§12, outcome `waiting for answer`) and stop. Clean up as in §9, but keep a
worktree that holds commits.

**Rounds (AC 11):** `N` = highest earlier round number on this Bug (Jira and MR) + 1. A Bug has at
most two rounds. If a third question comes up, do not ask: write it as an open point, finish the
Bug as a draft MR with open points (§10) and move it to `Review` (§9). If no commit exists, there
is no MR to hold the open points (§10): they go into the Jira comment, and the outcome is
`no implementation`.

---

## Completion Gate

The run is done when exactly one of these is true and the report (chat and notification, §12) is posted:
- MRs with label `ai-generated`, green pipelines, review note, Jira comment, Bug in `Review`;
- draft MRs with open points, Jira comment, Bug in `Review`;
- no implementation, Jira comment with the findings, Bug in `Review`;
- an abort comment on the Bug (§3), Bug in `In Progress`;
- a question asked (§13): the question, the waiting mark `bug-hunter-waiting`, Bug in `In Progress`;
- a resumed Bug (§0a) ends in one of the outcomes above; if the resume failed, the reason is on
  the Bug and the waiting mark stays;
- no eligible Bug (§1), with nothing written to Jira.

If `Review` was not offered by Jira, the report says so. No MR was merged in any of these
outcomes. If the notification could not be delivered, the report says so; the outcome stands.

## Files

| File | Purpose |
|---|---|
| `_shared/jira.md`, `_shared/scripts/jira_issue.py` | Jira access, search, assign, comment, transition |
| `scripts/bug_hunter_jira.py` | Waiting mark, question comment with reporter mention, comment list |
| a code-reviewer agent (your own) | Reviewer for §6 |
