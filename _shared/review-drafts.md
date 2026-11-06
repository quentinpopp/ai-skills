# Review drafts open in VS Code

Every skill that writes a Markdown document for the user to read, check or approve — a ticket
draft, a release preview, release documentation, a Jira or GitLab comment, an ADR, an MR
description, a review report, an analysis report — opens that file in VS Code before it asks for
feedback. Agent session folders and playground paths are hard to
find by hand; the chat transcript is a poor place to read a long document.

## Rule

1. **Write the document to a file first.** Use the path the skill names; otherwise
   `<session-folder>\files\<descriptive-name>.md`. Do not keep a reviewable document only in the
   chat.
2. **Open it in the editor** right after writing it, before the review question:

   ```powershell
   code "<absolute-path>.md"
   ```

   `code` reuses the current VS Code window and shows the file in a new tab. Open several related
   files in one call (`code "<a>.md" "<b>.md"`).
3. **State the absolute path in the chat** in one line, next to a short summary (what the document
   covers, what needs the user's attention). Do not paste the whole document into the chat as
   well, unless the user asks for it.
4. **Edit the same file** when the user asks for changes. VS Code reloads it automatically; say
   which sections changed. Do not create `-v2` copies.
5. **Publish exactly that file.** The reviewed file is the payload source (`--draft <file>`,
   `--comment <file>`, the wiki publish script, `glab --description`). What the user approved is
   what gets posted.

If `code` is not on `PATH` (`Get-Command code` fails), say so once, give the absolute path and
continue. Never block a skill on the editor.

## Not covered

- Machine inputs that only the agent reads (`*.json` summaries, agent context files, collector
  output such as `release-validation.md`). Open them only when the user asks.
- Handoff checkpoints (`checkpoints.md`) — they are for the next session, not for review.
- Short confirmations that fit in one `ask_user` question.
