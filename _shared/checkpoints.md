# Session Checkpoints & Resume

Shared mechanism for any skill that implements a story or feature across several sittings. A story rarely finishes in one sitting —
a meeting starts, the day ends, or the context window fills up. **Work must never be lost at a
session boundary, and the developer must never have to reconstruct the state by hand.**

Other skills (drafting tickets, reviewing an MR, log analysis) are short-lived and do not need
this.

---

## 1. The `PAUSE!` Command

**`PAUSE!` is the developer-facing command.** Whenever the developer types `PAUSE!` — at any
point, mid-step, without any further explanation — immediately stop what you are doing and run
the checkpoint procedure in §3. Do not start another tool call chain, do not "just finish this one
file", and do not ask a clarifying question first. Assume the developer is already walking away.

Also run the procedure when:
- **the context window gets tight** (roughly ≥ 80 % used, or before you would suggest `/compact`),
  proactively and without being asked;
- **a natural unit of work completes** — a sub-task committed and Jira-transitioned, a mockup pass
  signed off, a review round addressed, an ADR section finalized. Here you *offer* it rather than
  doing it: `ask_user` with choices `["Continue to <next step>", "PAUSE! — checkpoint and stop"]`.

Phrases that plainly mean the same thing ("lass uns pausieren", "Feierabend", "ich muss ins
Meeting", "stop for today") count as `PAUSE!` too. When in doubt, ask with the two choices above
instead of guessing.

## 2. Tell the Developer the Command Exists

The command is useless if it is unknown. **State it once per story, early** — in the same message
where you confirm the branch and scope, as a single line:

> Tip: type `PAUSE!` at any time and I will checkpoint the story so you can pick it up later.

Repeat it only in the proactive offers from §1 (as the choice label), never in every turn.

## 3. Checkpoint Procedure

Run these steps in order, then end the turn.

1. **Leave the branch in a sane state.** Do not leave compile errors or a broken build behind.
   Run the domain skill's formatter/linter on already-changed files and fix findings now
   (`mvn spotless:apply`, `pnpm run lint:fix` or your project's equivalent).
   If something is genuinely half-written and cannot compile in the time available, say so
   explicitly in the handoff file's **Known State** instead of hiding it.
2. **Decide about uncommitted work.** Propose a WIP commit
   if a meaningful unit is complete; otherwise leave it uncommitted and record that fact. Never
   commit silently, and never `git stash` — a stash is invisible to the next session.
3. **Sync the `todos` table** so `status` reflects reality (`done` / `in_progress` / `pending`,
   `blocked` with a reason). This is the fine-grained resume mechanism.
4. **Write the handoff file** (§4).
5. **Offer a Jira comment** (§5) — gated, never automatic.
6. **Summarise in the chat** (§6).
7. **End the turn with no further tool calls**, so the platform's own session checkpoint captures
   a clean state.

## 4. Handoff File

**Location:** `$HANDOFF_DIR/<TICKET-ID>.md` (env var `HANDOFF_DIR`, e.g. `~/.agent-handoffs`) — one file per ticket, created with
`New-Item -ItemType Directory -Force` if the folder is missing.

Deliberately **outside any repository**: a story can span several repos (backend + UI), the file must
never end up in a merge request, and a new session must find it regardless of its working
directory. Overwrite the same file on every checkpoint — it describes the *current* state, not a
history.

```markdown
# Handoff: <TICKET-ID> — <story title>

**Updated:** <YYYY-MM-DD HH:mm>
**Jira:** $JIRA_BASE_URL/browse/<TICKET-ID> — status `<Jira status>`
**ADR:** `<repo>/docs/adrs/<ticket-id-lowercase>-<slug>.md`
**MR:** <url or "not opened yet">

## Resume with
> Continue <TICKET-ID>, handoff is in $HANDOFF_DIR/<TICKET-ID>.md

## Repos & branches
| Repo | Branch | Uncommitted changes | Builds |
|---|---|---|---|
| `my-service` | `feature/PROJ-XXXX-<slug>` | none / `path/a.kt`, `path/b.kt` | yes / no |

## Done so far
- [x] <step> — `path/to/file`, `path/to/other`

## Next concrete action
- [ ] <the single next thing to do>
  - Entry point: `path/to/StartHere.kt:123`
  - Why: <one or two sentences of context that are not obvious from the code>

## Open questions for the developer
- <question that blocks progress, or "none">

## Known State
- Tests run: <none / which ones / failing: ...>
- Formatting/lint: clean / not applied because <reason>
- Jira sub-tasks: <ID> in `<status>`, ...
- Anything half-finished or knowingly broken: <...>
```

Keep it short enough to read in under a minute. Paths with line numbers beat prose.

## 5. Optional Jira Comment

After writing the handoff file, ask (short `ask_user`, choices `["Post it", "Skip"]`) whether to
post a status comment on the ticket — useful when the PO or a colleague should see where the story
stands, e.g. over a longer break. Never post it automatically, and never for a pause of a few
minutes.

If approved, post the **Done so far / Next action / Open questions** sections only — no local file
paths, no branch names beyond the branch itself, nothing about the handoff file (it is local to
this machine and meaningless to others):

```powershell
$shared = "$env:SKILLS_ROOT\_shared\scripts"
python "$shared\jira_issue.py" comment <TICKET-ID> --draft "<handoff-comment>.md" --no-tasklist --dry-run
```

`--no-tasklist` keeps `- [ ]` as plain bullets instead of interactive Jira checkboxes — a status
report must not add checkboxes people feel obliged to tick. See `jira.md`.

## 6. Chat Summary

The file is the durable record; the chat summary is what the developer actually reads before
closing the laptop. Give it as a normal formatted message — **not** inside an `ask_user` — in a
few sentences plus the resume line:

> **Checkpoint for PROJ-7249 written.** Done: the `OrderFilter` DTO and its mapper, committed
> as `feat(api): [#PROJ-7249] add order filter DTO`. Next: wire the filter into
> `OrderQueryService.search()` (`OrderQueryService.kt:88`). Open question: whether an empty
> filter should return all orders or none — I did not implement either yet.
> Uncommitted: none. Build: green. Sub-task PROJ-7250 is still `In Progress`.
>
> **Resume:** `/resume` this session, or start a new one and say
> *"Continue PROJ-7249"* — I will read `$HANDOFF_DIR/PROJ-7249.md`.

## 7. Resuming

`/resume` (same session, full context preserved) is the better path; the handoff file is the
safety net that also works in a brand-new session, on another day, from another directory.

When a developer asks to continue a ticket — "Continue PROJ-7249", "where were we?" — or names a
ticket that has a handoff file:

1. Read `$HANDOFF_DIR/<TICKET-ID>.md` **first**, before any other context
   gathering. If it does not exist, say so and fall back to a normal intake.
2. Read the project's contributor docs (e.g. `AGENTS.md`) and the ADR named in the file.
3. Verify the handoff against reality instead of trusting it — it may be days old: `git status`
   and `git log --oneline -5` in each listed repo, the current Jira status
   (`jira_issue.py get <TICKET-ID>`), and the MR state if one is open. **Report any drift**
   (someone else pushed, the ticket moved, the branch was merged) before doing anything.
4. Check the `todos` table for the fine-grained next action.
5. Summarise the state in a few sentences, answer any open question from the file *with the
   developer*, then confirm the next step via a short `ask_user` before touching code.

Once a story is merged and closed, delete its handoff file as part of the post-merge cleanup so the folder only ever lists genuinely open work.

To see what is still open across all stories: `Get-ChildItem $env:HANDOFF_DIR\*.md`.
