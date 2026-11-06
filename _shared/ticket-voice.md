# Ticket Voice — the shared style of tickets

Single source of truth for how every ticket reads, no matter which skill produced it
(bug, task and epic/story drafting skills, and story intake).

The PO writes with one voice. A bug, a task and a story that arrive in the same sprint must
look like the same author wrote them. The rules below are therefore not per-skill preferences
— they are the house style. A skill may **add** a type-specific rule; it must not restate or
soften a rule from this file.

---

## 1. Writing style — Simplified Technical English

The developer must understand the ticket on one read. Apply these rules to every section, and
to the chat messages that present the draft.

- **One term per concept.** Choose "item" or "record", then keep it. No synonym rotation.
- **Short sentences, one statement each.** Target 20 words. Split run-on sentences.
- **Active voice, present tense, concrete subject.** "The item service keeps the item in state A", not
  "the item is being kept in state A".
- **Direct statements.** No hedging and no softeners: drop "somewhat", "it seems", "arguably",
  "may potentially", "in some cases" unless the uncertainty is real. If a fact is unconfirmed,
  write "unconfirmed" once.
- **Live verbs.** Use "fails", "stops", "keeps". Avoid frozen verb phrases such as "perform a
  validation of", "provide support for", "carry out an update".
- **No marketing and no buzzwords.** No "seamless", "robust", "leverage", "holistic",
  "state-of-the-art", "deep dive", "streamlined", "best-in-class", "synergy".
- **Facts, not adjectives.** "The item stays in state A for 6 days" beats "the item is
  stuck for a very long time".
- **Lists instead of paragraphs** when three or more items belong together.
- **Each fact once.** A detail belongs either in the behaviour section or in *Evidence*, not
  both.
- **No unresolved placeholders.** `TBD`, `TODO` and unfilled `<…>` never reach the PO.

The checkers enforce these rules deterministically (§6). The exact word lists live in
`skills/_shared/scripts/draft_check.py`; keep the two files in sync when you extend either.

---

## 2. Titles — deliberately different per type

The title grammar differs by issue type **on purpose**, because the reader scans a backlog that
mixes all three. The grammar itself says what kind of work the row is.

| Type | Grammar | Example | Anti-example |
|---|---|---|---|
| Bug | Symptom, declarative, no imperative | `Item stays in state A after an update` | `Make the update transactional` |
| Task | Imperative, target behaviour of the delivery process | `Block merges when a required check is missing` | `Pipeline is broken` |
| Epic | `<system prefix>: <verb phrase>` | `Service X: Manage items via API and UI` | `Item improvements` |
| Story | `<system prefix>: <verb phrase>`, inherited from the epic | `Service X API: Manage items via API and UI` | `Backend part 2` |

Common to all: the PO must still recognise the title six months later. No ticket ID inside the
title, no fix inside the title.

---

## 3. Acceptance criteria — one layout rule for every type

- **"shall" is binding.** Every AC uses "shall", states observable behaviour, and names the
  acting system: "Service X shall reject …". "should"/"must" are not AC verbs.
- **What, not how.** An AC never prescribes a class, a method, an annotation or a refactoring.
  Naming an existing endpoint path, error code or data limit is allowed, because it bounds
  scope. Do not invent new identifiers, enum values, field names, UI widgets or colours in an
  AC. Anything deeper belongs in the developer's ADR.
- **Budget.** A story targets 10 ACs or fewer. Above 10 it is a split candidate.
- **Layout: flat up to 5, grouped above.** Up to five ACs, use a flat bullet list. From six,
  group them under headings. Stories group by feature area (`### <Feature area>`), because
  the reader thinks in user outcomes; the service split lives in the sub-tasks. Bugs and
  tasks group by `### <System>: <Area>`. Every AC still names its acting system. Never mix —
  loose bullets plus groups in one section is the single clearest sign that two tickets had
  different authors.
- **One coherent outcome per AC.** Split an AC that needs an "and" to stay true.
- **Cover the failure path.** State what must hold when a downstream step fails, not only the
  happy path.
- **Stable numbering in epics.** Epic ACs carry `AC-N` labels that survive rewordings, so
  stories can reference them. Story ACs need no numbers. No ticket IDs inside ACs — the
  Story → AC mapping lives in the epic's *Story Structure*.

---

## 4. Shared sections — identical wording everywhere

These sections appear in more than one ticket type. Where they appear, they read the same.

**Validation Checklist** — what a human executes in a deployed environment, written as
`scenario -> expected outcome`, prefixed with the stage:

```
- [ ] DEV: <scenario the PO can create -> observable expected result>
- [ ] NONPROD: <check with real integration, if applicable>
- [ ] PROD: <authorized post-deployment check, only if needed and agreed>
```

Aim for 5 or fewer manual items. More than 5 means the ticket is a split candidate — flag it,
never silently drop checks. Automated coverage belongs to the sub-tasks, not here.

**Definition of Done** — fixed base, in this wording:

```
- All acceptance criteria are fulfilled.
- Required automated coverage and manual validation are complete, with evidence recorded.
- Changes are reviewed and merged in every affected repository.
```

Bugs add: the reported scenario no longer occurs in the named environment; regression coverage
fails without the fix; affected records are repaired or a decision not to repair is recorded.
Tasks add: the deployment and the rollback path are both executed or explicitly deferred.
Add only a genuinely distinct completion condition. Never repeat ACs, payload rules or test
cases in the DoD.

**Evidence** — quote the shortest excerpt that proves the statement. Max 25 lines. A log line
keeps its timestamp and correlation ID. Never paste a whole file.

**Technical Pointers** — at most 5 bullets, each `path/to/file.kt:LINE — what the reader finds
there`. Pointers orient the developer; they do not design the fix.

**Sub-task description** — the same two sections for every parent type, nothing above them:

```
## Scope
<one to three sentences: the component and the part of the parent it delivers>

## Acceptance Criteria
- <the parent ACs this sub-task delivers, with their labels where the parent has them>
```

No `Parent:` line, no ticket key and no metadata block in the body. Jira holds the parent in its
own field and shows it above the description, so a copy in the body only duplicates it.
`jira_issue.py` refuses a description that contains a `Parent:` line.

---

## 5. Interaction with the PO

- **Take inventory first.** Never ask for what the attached logs, the linked ticket or the repo
  already answer. Ask only for what stays undecidable after reading.
- **One structured form, not an interview.** Collect every open question in a single `ask_user`
  form. Follow-up rounds are allowed when an answer opens a genuinely new question, but the
  default is one form. This supersedes any per-skill "one question at a time" instruction.
- **Propose, do not interrogate.** State the expected behaviour you inferred and ask for
  confirmation, instead of asking the PO to write it.
- **Two separate approval gates.** Markdown approval and "create this in Jira now" are distinct
  decisions. Show the exact payload — project key, issue type, summary, rendered description —
  and get explicit approval before any writing call, even when the markdown was approved.
- **Open the draft in the editor** (`code "<draft>.md"`) before presenting it, so the PO reads
  it in VS Code, not in the chat transcript (`skills/_shared/review-drafts.md`).
- **Present the diff, not the file**, when updating an existing ticket.

Jira access, the ADF conversion and the workflow transitions are documented once in
`skills/_shared/jira.md`. Do not restate them in a skill.

---

## 6. The checkers

Every drafting skill ships a `scripts/check_draft.py` that shares the engine in
`$SKILLS_ROOT/_shared/scripts/draft_check.py`. Run it **before** showing a draft to the PO:

```powershell
python "$env:SKILLS_ROOT\<skill>\scripts\check_draft.py" "<draft>.md"
```

| Skill | Checker | Kinds |
|---|---|---|
| `draft-bug-ticket` | `scripts/check_draft.py` | bug |
| `draft-task-ticket` | `scripts/check_draft.py` | task |
| `draft-epic-and-stories` | `scripts/check_draft.py` | epic, story (`--kind`, else from the H1) |

The checker reports missing, forbidden or misordered sections, ACs that prescribe code, broken
AC layout, over-budget sections, unresolved placeholders, hedging, buzzwords, frozen verbs,
run-on sentences, passive voice and synonym rotation. Each finding carries a line number and
the offending text. Fix what it reports, then re-run until it exits `0`. `--quiet` gives errors
only, `--json` machine-readable output.

**The checker judges form, not content. A clean run is not PO approval.**

---

## 7. What never enters a ticket

- A solution proposal, a refactoring plan or a technical design — that is the ADR.
- A root-cause analysis — that is the developer's work after the ticket exists.
- An open-questions section — resolve the questions and fold the decisions into the ACs.
- Configuration values, YAML snippets or full payloads that belong in the repo.
- An incidental finding mixed into the main problem — give it its own AC or its own ticket.
