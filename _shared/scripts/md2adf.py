#!/usr/bin/env python3
"""Convert a bug/story Markdown document into an Atlassian Document Format (ADF) document.

Usage:
    python md2adf.py <input.md> <output.json> [options]

Options:
    --no-tasklist   Render "- [ ] item" as plain bullets instead of ADF task items.
    --quiet         Suppress the summary written to stderr.

The output is a complete ADF ``doc`` node, ready to be used as the ``description``
field of a Jira REST API v3 create/update call.

Supported Markdown: ATX headings, paragraphs (with lazy continuation), fenced code
blocks, pipe tables, blockquotes, bullet/ordered lists with indentation-based
nesting, task list items, horizontal rules, and the inline marks ``code``,
``**strong**``, ``*emphasis*``, ``~~strike~~`` and ``[text](url)``.

Deliberately NOT supported: images, reference links, raw HTML, and footnotes.
None of these belong in a Jira ticket description, and silently mangling them
would be worse than leaving them as literal text.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import uuid

# --------------------------------------------------------------------------- #
# Inline parsing
# --------------------------------------------------------------------------- #

INLINE_RE = re.compile(
    r"(?P<code>`[^`]+`)"
    r"|(?P<link>\[[^\]]+\]\([^)\s]+\))"
    r"|(?P<strong>\*\*(?:[^*]|\*(?!\*))+\*\*)"
    r"|(?P<strike>~~[^~]+~~)"
    r"|(?P<em>(?<![\w*])\*[^*\s][^*]*\*(?![\w*]))"
)

LINK_RE = re.compile(r"^\[([^\]]+)\]\(([^)\s]+)\)$")

# ADF rule: the `code` mark is exclusive with the formatting marks. Only `link`
# may be combined with it. Markdown like **`CREATED`** is expressible in
# Markdown but not in ADF, and Jira rejects the whole document with a generic
# "not valid ADF content" error, so the conflict is resolved here: `code` wins,
# because it preserves the literal token the ticket is talking about.
CODE_INCOMPATIBLE = {"strong", "em", "strike", "underline", "subsup", "textColor"}


def add_mark(node: dict, mark: dict) -> None:
    """Attach a mark to an inline node, honouring ADF's mark exclusivity rules."""
    marks = node.setdefault("marks", [])
    existing = {m["type"] for m in marks}

    if mark["type"] in CODE_INCOMPATIBLE and "code" in existing:
        return
    if mark["type"] == "code":
        marks[:] = [m for m in marks if m["type"] not in CODE_INCOMPATIBLE]

    if mark["type"] not in {m["type"] for m in marks}:
        marks.append(mark)



def _text_node(text: str, marks: list | None = None) -> dict | None:
    """ADF rejects empty text nodes, so filter them out at the source."""
    if not text:
        return None
    node: dict = {"type": "text", "text": text}
    if marks:
        node["marks"] = marks
    return node


def inline(text: str) -> list:
    """Parse inline Markdown into a list of ADF inline nodes."""
    nodes: list = []
    pos = 0

    for m in INLINE_RE.finditer(text):
        if m.start() > pos:
            node = _text_node(text[pos : m.start()])
            if node:
                nodes.append(node)

        kind = m.lastgroup
        raw = m.group()

        if kind == "code":
            node = _text_node(raw[1:-1], [{"type": "code"}])
            if node:
                nodes.append(node)
        elif kind == "link":
            lm = LINK_RE.match(raw)
            if lm:
                label, href = lm.group(1), lm.group(2)
                # Link text may itself contain code/strong marks.
                for child in inline(label):
                    add_mark(child, {"type": "link", "attrs": {"href": href}})
                    nodes.append(child)
            else:
                node = _text_node(raw)
                if node:
                    nodes.append(node)
        elif kind == "strong":
            for child in inline(raw[2:-2]):
                add_mark(child, {"type": "strong"})
                nodes.append(child)
        elif kind == "strike":
            for child in inline(raw[2:-2]):
                add_mark(child, {"type": "strike"})
                nodes.append(child)
        elif kind == "em":
            for child in inline(raw[1:-1]):
                add_mark(child, {"type": "em"})
                nodes.append(child)

        pos = m.end()

    if pos < len(text):
        node = _text_node(text[pos:])
        if node:
            nodes.append(node)

    return nodes


def paragraph(text: str) -> dict:
    content = inline(text)
    if not content:
        content = [{"type": "text", "text": " "}]
    return {"type": "paragraph", "content": content}


# --------------------------------------------------------------------------- #
# Block-level helpers
# --------------------------------------------------------------------------- #

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
TABLE_DELIM_RE = re.compile(r"^\|[\s:|-]+\|$")
BULLET_RE = re.compile(r"^(\s*)[-*]\s+(.*)$")
ORDERED_RE = re.compile(r"^(\s*)\d+[.)]\s+(.*)$")
TASK_RE = re.compile(r"^\[([ xX])\]\s*(.*)$")
BLOCK_START_RE = re.compile(r"^(#{1,6}\s|```|\||>|\s*[-*]\s|\s*\d+[.)]\s|---\s*$)")
FIELD_LINE_RE = re.compile(r"^\*\*[^*]+:\*\*")


def split_row(line: str) -> list[str]:
    """Split a table row on unescaped pipes that are not inside inline code."""
    cells: list[str] = []
    buf: list[str] = []
    in_code = False
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == "\\" and i + 1 < len(line) and line[i + 1] == "|":
            buf.append("|")
            i += 2
            continue
        if ch == "`":
            in_code = not in_code
        if ch == "|" and not in_code:
            cells.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
        i += 1
    cells.append("".join(buf).strip())

    # A pipe table row starts and ends with '|', producing empty edge cells.
    if cells and cells[0] == "":
        cells.pop(0)
    if cells and cells[-1] == "":
        cells.pop()
    return cells


def cell(kind: str, text: str) -> dict:
    return {"type": kind, "attrs": {}, "content": [paragraph(text)]}


def local_id() -> str:
    return str(uuid.uuid4())


# --------------------------------------------------------------------------- #
# List parsing (indentation-aware, supports nesting)
# --------------------------------------------------------------------------- #


def parse_list(lines: list[str], start: int, indent: int, use_tasklist: bool):
    """Parse a (possibly nested) list beginning at ``lines[start]``.

    Returns ``(node, next_index)``. Sibling items share the same indent; a deeper
    indent starts a nested list attached to the preceding list item.
    """
    items: list[dict] = []
    i = start
    n = len(lines)

    first = lines[start]
    ordered = bool(ORDERED_RE.match(first))
    matcher = ORDERED_RE if ordered else BULLET_RE

    # A task list only applies to bullet lists whose first item is a checkbox.
    m_first = matcher.match(first)
    is_tasklist = (
        use_tasklist
        and not ordered
        and m_first is not None
        and TASK_RE.match(m_first.group(2)) is not None
    )

    while i < n:
        line = lines[i]
        if not line.strip():
            # A blank line ends the list unless the next line continues it.
            if i + 1 < n and (BULLET_RE.match(lines[i + 1]) or ORDERED_RE.match(lines[i + 1])):
                nxt = BULLET_RE.match(lines[i + 1]) or ORDERED_RE.match(lines[i + 1])
                if len(nxt.group(1)) >= indent:
                    i += 1
                    continue
            break

        m = matcher.match(line)
        other = (BULLET_RE if ordered else ORDERED_RE).match(line)

        if m and len(m.group(1)) == indent:
            text = m.group(2)
            state = None
            if is_tasklist:
                tm = TASK_RE.match(text)
                if tm:
                    state = "DONE" if tm.group(1).lower() == "x" else "TODO"
                    text = tm.group(2)
            elif not ordered:
                text = re.sub(r"^\[[ xX]\]\s*", "", text)
            i += 1

            # Lazy continuation lines belong to the current item.
            while (
                i < n
                and lines[i].strip()
                and not BULLET_RE.match(lines[i])
                and not ORDERED_RE.match(lines[i])
                and lines[i].startswith(" ")
            ):
                text += " " + lines[i].strip()
                i += 1

            if is_tasklist:
                items.append(
                    {
                        "type": "taskItem",
                        "attrs": {"localId": local_id(), "state": state or "TODO"},
                        "content": inline(text) or [{"type": "text", "text": " "}],
                    }
                )
                continue

            item_content: list = [paragraph(text)]

            # A deeper indent starts a nested list inside this item.
            nxt = BULLET_RE.match(lines[i]) if i < n else None
            nxt = nxt or (ORDERED_RE.match(lines[i]) if i < n else None)
            if nxt and len(nxt.group(1)) > indent:
                child, i = parse_list(lines, i, len(nxt.group(1)), use_tasklist)
                item_content.append(child)

            items.append({"type": "listItem", "content": item_content})
            continue

        if (m and len(m.group(1)) < indent) or (other and len(other.group(1)) < indent):
            break
        if other and len(other.group(1)) == indent:
            # List type switched at the same level - end this list.
            break
        break

    if is_tasklist:
        node = {"type": "taskList", "attrs": {"localId": local_id()}, "content": items}
    elif ordered:
        node = {"type": "orderedList", "attrs": {"order": 1}, "content": items}
    else:
        node = {"type": "bulletList", "content": items}
    return node, i


# --------------------------------------------------------------------------- #
# Document parsing
# --------------------------------------------------------------------------- #


def convert(md: str, use_tasklist: bool = True) -> dict:
    lines = md.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    content: list = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]
        s = line.strip()

        if not s:
            i += 1
            continue

        # Horizontal rule
        if re.fullmatch(r"-{3,}|\*{3,}|_{3,}", s):
            content.append({"type": "rule"})
            i += 1
            continue

        # Heading
        m = HEADING_RE.match(s)
        if m:
            content.append(
                {
                    "type": "heading",
                    "attrs": {"level": len(m.group(1))},
                    "content": inline(m.group(2)),
                }
            )
            i += 1
            continue

        # Fenced code block
        if s.startswith("```"):
            lang = s[3:].strip()
            i += 1
            buf: list[str] = []
            while i < n and not lines[i].strip().startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1  # closing fence
            attrs = {"language": lang} if lang else {}
            node: dict = {"type": "codeBlock", "attrs": attrs}
            body = "\n".join(buf)
            if body:
                node["content"] = [{"type": "text", "text": body}]
            content.append(node)
            continue

        # Pipe table
        if s.startswith("|") and i + 1 < n and TABLE_DELIM_RE.match(lines[i + 1].strip()):
            header = split_row(s)
            i += 2
            rows: list[list[str]] = []
            while i < n and lines[i].strip().startswith("|"):
                rows.append(split_row(lines[i].strip()))
                i += 1
            table_rows = [
                {"type": "tableRow", "content": [cell("tableHeader", h) for h in header]}
            ]
            for r in rows:
                cells = [
                    cell("tableCell", r[c] if c < len(r) else "")
                    for c in range(len(header))
                ]
                table_rows.append({"type": "tableRow", "content": cells})
            content.append(
                {
                    "type": "table",
                    "attrs": {"isNumberColumnEnabled": False, "layout": "default"},
                    "content": table_rows,
                }
            )
            continue

        # Blockquote
        if s.startswith(">"):
            buf = []
            while i < n and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip().lstrip(">").strip())
                i += 1
            text = " ".join(x for x in buf if x)
            content.append({"type": "blockquote", "content": [paragraph(text)]})
            continue

        # Lists
        if BULLET_RE.match(line) or ORDERED_RE.match(line):
            m_list = BULLET_RE.match(line) or ORDERED_RE.match(line)
            node, i = parse_list(lines, i, len(m_list.group(1)), use_tasklist)
            content.append(node)
            continue

        # "**Field:** value" header lines stay one paragraph each.
        if FIELD_LINE_RE.match(s):
            content.append(paragraph(s))
            i += 1
            continue

        # Paragraph with lazy continuation
        buf = [s]
        i += 1
        while (
            i < n
            and lines[i].strip()
            and not BLOCK_START_RE.match(lines[i])
            and not FIELD_LINE_RE.match(lines[i].strip())
        ):
            buf.append(lines[i].strip())
            i += 1
        content.append(paragraph(" ".join(buf)))

    return {"type": "doc", "version": 1, "content": content}


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


def validate(node, path: str = "doc") -> list[str]:
    """Catch the structural mistakes Jira rejects with an unhelpful 400."""
    problems: list[str] = []

    if isinstance(node, dict):
        ntype = node.get("type")
        if ntype == "text" and not node.get("text"):
            problems.append(f"{path}: empty text node")
        if ntype == "text":
            mark_types = {m.get("type") for m in node.get("marks", []) or []}
            clash = mark_types & CODE_INCOMPATIBLE
            if "code" in mark_types and clash:
                problems.append(
                    f"{path}: 'code' mark combined with {sorted(clash)} - invalid ADF"
                )
        if ntype in {"paragraph", "heading"} and not node.get("content"):
            problems.append(f"{path}: empty {ntype}")
        if ntype in {"bulletList", "orderedList", "taskList"} and not node.get("content"):
            problems.append(f"{path}: empty {ntype}")
        if ntype == "listItem" and not node.get("content"):
            problems.append(f"{path}: empty listItem")
        for idx, child in enumerate(node.get("content", []) or []):
            problems.extend(validate(child, f"{path}.{ntype or '?'}[{idx}]"))

    return problems


def summarize(doc: dict) -> str:
    counts: dict[str, int] = {}
    for block in doc["content"]:
        counts[block["type"]] = counts.get(block["type"], 0) + 1
    parts = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
    return f"{len(doc['content'])} top-level blocks ({parts})"


# --------------------------------------------------------------------------- #


def main() -> int:
    ap = argparse.ArgumentParser(description="Convert Markdown to Jira ADF JSON.")
    ap.add_argument("input", help="Path to the Markdown source file")
    ap.add_argument("output", help="Path for the generated ADF JSON file")
    ap.add_argument(
        "--no-tasklist",
        action="store_true",
        help='Render "- [ ] item" as plain bullets instead of ADF task items',
    )
    ap.add_argument("--quiet", action="store_true", help="Suppress the summary output")
    args = ap.parse_args()

    with open(args.input, "r", encoding="utf-8") as fh:
        md = fh.read()

    doc = convert(md, use_tasklist=not args.no_tasklist)

    problems = validate(doc)
    if problems:
        print("ADF validation problems:", file=sys.stderr)
        for p in problems[:20]:
            print(f"  - {p}", file=sys.stderr)
        return 1

    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False)

    if not args.quiet:
        print(f"{args.input} -> {args.output}", file=sys.stderr)
        print(summarize(doc), file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
