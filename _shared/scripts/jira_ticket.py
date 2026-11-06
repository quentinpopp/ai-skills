#!/usr/bin/env python3
"""Read-only Jira ticket parsing shared by the PO validation and release skills.

* ``adf_to_md``            Atlassian Document Format -> Markdown (descriptions, comments).
* ``section``              Top-level nodes under a heading, also German and plain-paragraph titles.
* ``checklist_items``      Validation Checklist items with their DONE/TODO state.
* ``collect_jira``         One ticket: type, status, description, ACs, checklist, comments, sub-tasks.
* ``po_validations``       "PO Validation (YYYY-MM-DD)" comments and addenda parsed into result rows.

Uses ``jira_issue.request`` for auth; nothing is written to Jira.
"""

from __future__ import annotations

import re

import jira_issue

CHECKLIST_HEADING = re.compile(r"validation\s+checklist|validierungs[\s-]*check(liste)?|prüf(liste|schritte)", re.IGNORECASE)
AC_HEADING = re.compile(r"acceptance\s+criteria|abnahmekriterien|akzeptanzkriterien", re.IGNORECASE)
# A short plain paragraph without sentence punctuation acts as a section title in hand-written tickets.
PLAIN_TITLE_MAX = 40

PO_VALIDATION_TITLE = re.compile(r"PO[\s-]+Validation(?P<addendum>\s+addendum)?", re.IGNORECASE)
_STAGE = r"(?:DEV|NON-?PROD|PROD)"
_STAGE_PREFIX = re.compile(rf"^\s*\**\s*(?P<stages>{_STAGE}(?:\s*[/+&,]\s*{_STAGE})*)\b\s*\**\s*[:\-–]?", re.IGNORECASE)
# Reviewer note appended to a checklist item, e.g. "… → KUETHOR: verified on NONPROD".
_ITEM_NOTE = re.compile(r"\s*(?:→|->)\s*(?P<who>[A-Z][A-Z0-9_.-]{2,})\s*:\s*(?P<note>.+)$")


# --------------------------------------------------------------------------- #
# ADF -> Markdown
# --------------------------------------------------------------------------- #


def inline(nodes) -> str:
    out = []
    for n in nodes or []:
        t = n.get("type")
        if t == "text":
            text = n.get("text", "")
            for m in n.get("marks", []) or []:
                if m.get("type") == "code":
                    text = f"`{text}`"
                elif m.get("type") == "strong":
                    text = f"**{text}**"
                elif m.get("type") == "link":
                    text = f"[{text}]({m.get('attrs', {}).get('href')})"
            out.append(text)
        elif t == "hardBreak":
            out.append("\n")
        elif t in ("inlineCard", "blockCard"):
            out.append(n.get("attrs", {}).get("url", ""))
        elif t in ("mention", "emoji"):
            out.append(n.get("attrs", {}).get("text", ""))
        else:
            out.append(inline(n.get("content")))
    return "".join(out)


def adf_to_md(node, indent: int = 0) -> str:
    if not isinstance(node, dict):
        return ""
    t = node.get("type")
    content = node.get("content") or []
    pad = "  " * indent
    if t == "doc":
        return "\n\n".join(s for s in (adf_to_md(c) for c in content) if s.strip())
    if t == "heading":
        return "#" * node.get("attrs", {}).get("level", 2) + " " + inline(content)
    if t == "paragraph":
        return pad + inline(content)
    if t in ("bulletList", "orderedList", "taskList"):
        lines = []
        for i, item in enumerate(content, 1):
            if item.get("type") == "taskItem":
                box = "[x]" if item.get("attrs", {}).get("state") == "DONE" else "[ ]"
                lines.append(f"{pad}- {box} {inline(item.get('content'))}")
                continue
            if item.get("type") in ("bulletList", "orderedList", "taskList"):
                lines.append(adf_to_md(item, indent + 1))
                continue
            marker = f"{i}." if t == "orderedList" else "-"
            parts = [inline(c.get("content")) if c.get("type") == "paragraph" else adf_to_md(c, indent + 1)
                     for c in item.get("content", [])]
            lines.append(f"{pad}{marker} {parts[0] if parts else ''}")
            lines.extend(p for p in parts[1:] if p.strip())
        return "\n".join(lines)
    if t == "codeBlock":
        return f"```\n{inline(content)}\n```"
    if t == "table":
        rows = []
        for r_i, row in enumerate(content):
            cells = [" ".join(adf_to_md(c).strip() for c in cell.get("content", [])).replace("\n", " ")
                     for cell in row.get("content", [])]
            rows.append("| " + " | ".join(cells) + " |")
            if r_i == 0:
                rows.append("|" + "---|" * len(cells))
        return "\n".join(rows)
    if t in ("panel", "blockquote", "expand", "nestedExpand"):
        return "\n".join(adf_to_md(c, indent) for c in content)
    if t == "rule":
        return "---"
    return inline(content)


# --------------------------------------------------------------------------- #
# Sections and checklist
# --------------------------------------------------------------------------- #


def pseudo_heading_level(n: dict) -> int | None:
    """Heading level; 7 for a bold-only paragraph, 8 for a short plain title line (older tickets)."""
    if n.get("type") == "heading":
        return n.get("attrs", {}).get("level", 2)
    if n.get("type") == "paragraph":
        texts = [c for c in n.get("content") or [] if c.get("type") == "text" and c.get("text", "").strip()]
        if texts and all(any(m.get("type") == "strong" for m in c.get("marks") or []) for c in texts):
            return 7
        line = "".join(c.get("text", "") for c in texts).strip()
        if (texts and len(texts) == len(n.get("content") or []) and len(line) <= PLAIN_TITLE_MAX
                and not re.search(r"[.!?,;)`]$", line)):
            return 8
    return None


def section(doc: dict, heading: re.Pattern) -> list[dict]:
    """Top-level nodes after a matching heading, up to the next heading of the same or higher level."""
    out, level = [], None
    for n in (doc or {}).get("content", []) or []:
        lvl = pseudo_heading_level(n)
        if lvl is not None:
            if level is not None and lvl <= level:
                break
            if level is None and heading.search(inline(n.get("content"))):
                level = lvl
                continue
        if level is not None:
            out.append(n)
    return out


def stage_of(text: str) -> str | None:
    """Highest stage named in the item prefix ("DEV/NONPROD: ..." -> NONPROD), else None."""
    m = _STAGE_PREFIX.match(text or "")
    if not m:
        return None
    stages = {s.upper().replace("-", "") for s in re.findall(_STAGE, m.group("stages"), re.IGNORECASE)}
    return next(s for s in ("PROD", "NONPROD", "DEV") if s in stages)


def checklist_items(doc: dict) -> list[dict]:
    sect = section(doc, CHECKLIST_HEADING)
    items: list = []
    jira_issue._walk_task_items(sect, items)
    if items:
        out = [{"state": i.get("attrs", {}).get("state"), "text": jira_issue.adf_text(i).strip()} for i in items]
    else:
        md = "\n".join(adf_to_md(n) for n in sect)
        out = [{"state": "DONE" if "[x]" in line else "TODO", "text": re.sub(r"^\s*-\s*(\[[ x]\]\s*)?", "", line)}
               for line in md.splitlines() if line.strip().startswith("-")]
    for item in out:
        item["stage"] = stage_of(item["text"])
        note = _ITEM_NOTE.search(item["text"])
        item["note"] = f"{note.group('who')}: {note.group('note').strip()}" if note else None
    return out


# --------------------------------------------------------------------------- #
# PO Validation comments
# --------------------------------------------------------------------------- #


def result_class(result: str) -> str:
    """passed / open / failed / other for a PO Validation result cell."""
    r = (result or "").lower()
    if "❌" in r or "fail" in r or "nicht ok" in r:
        return "failed"
    if "⏳" in r or "open" in r or "offen" in r:
        return "open"
    if "✅" in r or "pass" in r or r.strip() in ("ok", "done", "erledigt"):
        return "passed"
    return "other"


def _table_rows(md: str) -> list[dict]:
    rows, header = [], None
    for line in md.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            if header is not None and rows:
                break
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?|-*", c) for c in cells):
            continue
        if header is None:
            header = [c.lower().strip("* ") for c in cells]
            continue
        row = dict(zip(header, cells))
        item = row.get("item") or row.get("check") or row.get("prüfpunkt") or (cells[1] if len(cells) > 1 else "")
        result = row.get("result") or row.get("ergebnis") or (cells[2] if len(cells) > 2 else "")
        evidence = row.get("evidence") or row.get("nachweis") or (cells[3] if len(cells) > 3 else "")
        rows.append({"no": row.get("#", cells[0]), "item": item, "stage": stage_of(item), "result": result,
                     "result_class": result_class(result), "evidence": evidence})
    return rows


def po_validations(comments: list[dict]) -> list[dict]:
    """PO Validation comments (body_md as returned by collect_jira), oldest first."""
    out = []
    for c in comments:
        body = c.get("body_md") or ""
        head = "\n".join(body.strip().splitlines()[:3])
        m = PO_VALIDATION_TITLE.search(head)
        if not m:
            continue
        date = re.search(r"\d{4}-\d{2}-\d{2}", head)
        rows = _table_rows(body)
        notes = [ln.strip() for ln in body.splitlines()
                 if ln.strip() and not ln.strip().startswith(("|", "#")) and not PO_VALIDATION_TITLE.search(ln)]
        out.append({
            "date": date.group(0) if date else (c.get("created") or "")[:10],
            "created": c.get("created"),
            "author": c.get("author"),
            "addendum": bool(m.group("addendum")),
            "rows": rows,
            "notes": notes,
        })
    return out


# --------------------------------------------------------------------------- #
# Collection
# --------------------------------------------------------------------------- #


def collect_jira(key: str, n_comments: int) -> dict:
    """One ticket; ``n_comments`` limits the latest comments (0 = none, -1 = all)."""
    data = jira_issue.request(
        "GET", f"/rest/api/3/issue/{key}?fields=summary,status,issuetype,priority,assignee,description,"
               "labels,parent,issuelinks,comment,subtasks,fixVersions,resolutiondate")
    f = data.get("fields", {})
    doc = f.get("description") or {}
    is_adf = isinstance(doc, dict)
    comments = (f.get("comment") or {}).get("comments", [])
    if n_comments >= 0:
        comments = comments[-n_comments:] if n_comments else []

    def linked(link):
        other = link.get("outwardIssue") or link.get("inwardIssue") or {}
        return {"type": (link.get("type") or {}).get("name"), "key": other.get("key"),
                "summary": (other.get("fields") or {}).get("summary"),
                "status": ((other.get("fields") or {}).get("status") or {}).get("name")}

    parent = f.get("parent") or {}
    return {
        "key": key,
        "url": jira_issue.issue_url(key),
        "type": (f.get("issuetype") or {}).get("name"),
        "status": (f.get("status") or {}).get("name"),
        "status_category": ((f.get("status") or {}).get("statusCategory") or {}).get("key"),
        "resolved": f.get("resolutiondate"),
        "priority": (f.get("priority") or {}).get("name"),
        "assignee": (f.get("assignee") or {}).get("displayName"),
        "summary": f.get("summary"),
        "labels": f.get("labels"),
        "parent": parent.get("key"),
        "parent_summary": (parent.get("fields") or {}).get("summary"),
        "links": [linked(l) for l in f.get("issuelinks", []) or []],
        # Stories are split into component sub-tasks whose keys the MRs and commits often carry.
        "subtasks": [{"key": s.get("key"), "summary": (s.get("fields") or {}).get("summary"),
                      "status": ((s.get("fields") or {}).get("status") or {}).get("name"),
                      "status_category": (((s.get("fields") or {}).get("status") or {}).get("statusCategory")
                                          or {}).get("key")}
                     for s in f.get("subtasks", []) or []],
        "description_md": adf_to_md(doc) if is_adf else str(doc),
        "acceptance_criteria_md": "\n\n".join(adf_to_md(n) for n in section(doc, AC_HEADING)) if is_adf else "",
        "validation_checklist": checklist_items(doc) if is_adf else [],
        "comments": [
            {"author": (c.get("author") or {}).get("displayName"), "created": c.get("created"),
             "body_md": adf_to_md(c.get("body")) if isinstance(c.get("body"), dict) else str(c.get("body"))}
            for c in comments
        ],
    }
