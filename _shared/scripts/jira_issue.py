#!/usr/bin/env python3
"""Jira REST v3 helper for the ticket-drafting skills (bugs, epics, stories).

Handles the mechanics only: auth, Markdown -> ADF conversion, create, update with a
conflict check, read-back verification and attachments. Approval gates stay with the
skill; this script never decides to publish on its own.

Subcommands:
    createmeta --project PROJ
    get KEY [--adf-out file.json] [--baseline-out file.json]
    baseline KEY --out file.json
    create --project PROJ --type Bug --summary "..." --draft draft.md [--parent KEY]
           [--priority High] [--dry-run]
    subtask --parent KEY --summary "..." [--draft draft.md | --text "..."] [--dry-run]
    update KEY --draft draft.md [--summary "..."] [--baseline file.json] [--force] [--dry-run]
    verify KEY --draft draft.md
    comment KEY (--draft file.md | --text "...") [--no-tasklist] [--dry-run]
    transitions KEY
    transition KEY --to "Review" [--assign-me] [--max-hops N] [--dry-run]
    assign KEY [--to me|ACCOUNT_ID]
    search --jql "project = PROJ AND status = 'Review'" [--fields ...] [--max N]
    checkbox KEY --item "<excerpt>" [--uncheck] [--dry-run]
    attach KEY --file path

Credentials:
    JIRA_EMAIL      Atlassian account e-mail
    JIRA_API_TOKEN  Atlassian API token / PAT
    JIRA_BASE_URL   required, e.g. https://your-company.atlassian.net
The token value is never printed.
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import md2adf  # noqa: E402  (same folder, no package)

# Jira content contains arrows, dashes and umlauts; the default Windows console codec (cp1252)
# raises UnicodeEncodeError on them and would abort a command mid-output.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

BASELINE_FIELDS = ["summary", "description", "status", "priority", "parent", "updated"]

_EMAIL_OVERRIDE: str | None = None


# --------------------------------------------------------------------------- #
# Environment / auth
# --------------------------------------------------------------------------- #


def env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(name) or default


def auth_header() -> str:
    email = _EMAIL_OVERRIDE or env("JIRA_EMAIL")
    token = env("JIRA_API_TOKEN")
    if not email:
        raise SystemExit(
            "error: JIRA_EMAIL not set. Pass --email <account-email>, or use the manual "
            "publishing flow in the skill."
        )
    if not token:
        raise SystemExit(
            "error: JIRA_API_TOKEN not set. Use the manual publishing flow in the skill instead."
        )
    pair = f"{email}:{token}".encode("utf-8")
    return "Basic " + base64.b64encode(pair).decode("ascii")


def base_url() -> str:
    url = env("JIRA_BASE_URL")
    if not url:
        raise SystemExit("error: JIRA_BASE_URL not set, e.g. https://your-company.atlassian.net")
    return url.rstrip("/")


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #


def request(method: str, path: str, payload: dict | None = None) -> dict:
    url = f"{base_url()}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", auth_header())
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json; charset=utf-8")
    try:
        with urllib.request.urlopen(req) as response:
            body = response.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1500]
        raise SystemExit(f"error: Jira {method} {path} failed with HTTP {exc.code}\n{detail}")
    except urllib.error.URLError as exc:
        raise SystemExit(f"error: cannot reach Jira: {exc.reason}")


def upload(path_suffix: str, file_path: Path) -> dict:
    boundary = uuid.uuid4().hex
    mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"\r\n'
        f"Content-Type: {mime}\r\n\r\n"
    ).encode("utf-8")
    body = head + file_path.read_bytes() + f"\r\n--{boundary}--\r\n".encode("utf-8")

    req = urllib.request.Request(f"{base_url()}{path_suffix}", data=body, method="POST")
    req.add_header("Authorization", auth_header())
    req.add_header("X-Atlassian-Token", "no-check")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    try:
        with urllib.request.urlopen(req) as response:
            return json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1000]
        raise SystemExit(f"error: attachment upload failed with HTTP {exc.code}\n{detail}")


# --------------------------------------------------------------------------- #
# ADF helpers
# --------------------------------------------------------------------------- #


def strip_meta_block(markdown: str) -> str:
    """Drop the leading title and metadata block of a draft.

    Everything before the first level-2 heading is ticket metadata: the H1 title, the bold
    key/value lines (ticket key, systems, environment, reporter, severity) and the horizontal
    rule that closes the block. Jira carries that information in its own fields, so the
    description starts at the first section.
    """
    lines = markdown.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("## "):
            return "\n".join(lines[index:]) + "\n"
    print("error: --strip-meta needs a level-2 heading; the draft has none.", file=sys.stderr)
    raise SystemExit(3)


_PARENT_LINE = re.compile(r"^\s*(?:[-*]\s+)?(?:\*\*|__)?parent(?:\*\*|__)?\s*:", re.IGNORECASE)


def reject_parent_line(markdown: str) -> None:
    """Refuse a description that names its parent ticket in the body.

    Jira holds the parent in its own field and shows it above the description. A "Parent:"
    line in the body duplicates that field and goes stale when the issue moves.
    """
    in_code = False
    for number, line in enumerate(markdown.splitlines(), start=1):
        if line.lstrip().startswith("```"):
            in_code = not in_code
            continue
        if not in_code and _PARENT_LINE.match(line):
            print(
                f"error: line {number} names the parent in the description: {line.strip()}\n"
                "       Jira holds the parent in its own field. Remove the line from the draft.",
                file=sys.stderr,
            )
            raise SystemExit(3)


def markdown_to_adf(markdown: str, use_tasklist: bool = True) -> dict:
    doc = md2adf.convert(markdown, use_tasklist=use_tasklist)
    problems = md2adf.validate(doc)
    if problems:
        print("error: the generated ADF is invalid:", file=sys.stderr)
        for problem in problems[:20]:
            print(f"  {problem}", file=sys.stderr)
        raise SystemExit(3)
    return doc


def draft_to_adf(
    draft: Path, use_tasklist: bool = True, strip_meta: bool = False, description: bool = False
) -> dict:
    markdown = draft.read_text(encoding="utf-8")
    if strip_meta:
        markdown = strip_meta_block(markdown)
    if description:
        reject_parent_line(markdown)
    return markdown_to_adf(markdown, use_tasklist=use_tasklist)


def adf_text(node) -> str:
    """Flatten an ADF document to plain text for read-back comparison."""
    if isinstance(node, dict):
        if node.get("type") == "text":
            return node.get("text", "")
        return " ".join(adf_text(child) for child in node.get("content", []))
    if isinstance(node, list):
        return " ".join(adf_text(child) for child in node)
    return ""


def issue_url(key: str) -> str:
    return f"{base_url()}/browse/{key}"


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #


def cmd_createmeta(args) -> int:
    data = request("GET", f"/rest/api/3/issue/createmeta?projectKeys={args.project}&expand=projects.issuetypes")
    for project in data.get("projects", []):
        names = [t.get("name") for t in project.get("issuetypes", [])]
        print(f"{project.get('key')}: {', '.join(n for n in names if n)}")
    return 0


def cmd_get(args) -> int:
    fields = ",".join(BASELINE_FIELDS)
    data = request("GET", f"/rest/api/3/issue/{args.key}?fields={fields}")
    f = data.get("fields", {})
    print(f"{data.get('key')}  {issue_url(data.get('key', args.key))}")
    print(f"summary : {f.get('summary')}")
    print(f"status  : {(f.get('status') or {}).get('name')}")
    print(f"priority: {(f.get('priority') or {}).get('name')}")
    print(f"parent  : {(f.get('parent') or {}).get('key')}")
    print(f"updated : {f.get('updated')}")
    body = adf_text(f.get("description"))
    print(f"description: {len(body)} characters")
    if args.adf_out:
        Path(args.adf_out).write_text(json.dumps(f.get("description"), indent=2), encoding="utf-8")
        print(f"wrote {args.adf_out}")
    if args.baseline_out:
        _write_baseline(args.key, Path(args.baseline_out))
    return 0


def _write_baseline(key: str, out: Path) -> dict:
    fields = ",".join(BASELINE_FIELDS)
    data = request("GET", f"/rest/api/3/issue/{key}?fields={fields}")
    f = data.get("fields", {})
    baseline = {
        "key": key,
        "summary": f.get("summary"),
        "description_text": adf_text(f.get("description")),
        "updated": f.get("updated"),
    }
    out.write_text(json.dumps(baseline, indent=2), encoding="utf-8")
    print(f"baseline written: {out}")
    return baseline


def cmd_baseline(args) -> int:
    _write_baseline(args.key, Path(args.out))
    return 0


def cmd_create(args) -> int:
    description = draft_to_adf(
        Path(args.draft), use_tasklist=not args.no_tasklist, strip_meta=args.strip_meta, description=True
    )
    fields: dict = {
        "project": {"key": args.project},
        "issuetype": {"name": args.type},
        "summary": args.summary[:255],
        "description": description,
    }
    if args.parent:
        fields["parent"] = {"key": args.parent}
    if args.priority:
        fields["priority"] = {"name": args.priority}

    print(f"project : {args.project}")
    print(f"type    : {args.type}")
    print(f"summary : {fields['summary']}")
    print(f"parent  : {args.parent or '-'}")
    print(f"priority: {args.priority or '-'}")
    print(f"body    : {md2adf.summarize(description)}")
    if args.dry_run:
        print("\ndry run: nothing was sent to Jira")
        return 0

    data = request("POST", "/rest/api/3/issue", {"fields": fields})
    key = data.get("key", "")
    print(f"\nCreated {key}: {issue_url(key)}")
    return 0


def cmd_update(args) -> int:
    fields: dict = {}
    if args.draft:
        fields["description"] = draft_to_adf(
            Path(args.draft), use_tasklist=not args.no_tasklist, strip_meta=args.strip_meta, description=True
        )
    if args.summary:
        fields["summary"] = args.summary[:255]
    if not fields:
        raise SystemExit("error: nothing to update; pass --draft and/or --summary")

    if args.baseline and not args.force:
        stored = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
        current = request("GET", f"/rest/api/3/issue/{args.key}?fields=summary,description,updated")
        cf = current.get("fields", {})
        drift = []
        if stored.get("summary") != cf.get("summary"):
            drift.append("summary")
        if stored.get("description_text") != adf_text(cf.get("description")):
            drift.append("description")
        if drift:
            print(f"error: {args.key} changed in Jira since the baseline: {', '.join(drift)}")
            print(f"       baseline updated={stored.get('updated')} current updated={cf.get('updated')}")
            print("       reconcile with the PO, or re-run with --force after an explicit decision.")
            return 4

    print(f"{args.key}: updating {', '.join(sorted(fields))}")
    if "description" in fields:
        print(f"body: {md2adf.summarize(fields['description'])}")
    if args.dry_run:
        print("dry run: nothing was sent to Jira")
        return 0

    request("PUT", f"/rest/api/3/issue/{args.key}", {"fields": fields})
    print(f"Updated {args.key}: {issue_url(args.key)}")
    return 0


def cmd_verify(args) -> int:
    local = draft_to_adf(Path(args.draft), strip_meta=args.strip_meta)
    remote = request("GET", f"/rest/api/3/issue/{args.key}?fields=description")
    remote_doc = remote.get("fields", {}).get("description")

    def headings(doc) -> list[str]:
        return [adf_text(n).strip() for n in (doc or {}).get("content", []) if n.get("type") == "heading"]

    local_h, remote_h = headings(local), headings(remote_doc)
    print(f"local : {md2adf.summarize(local)}")
    print(f"remote: {md2adf.summarize(remote_doc or {'content': []})}")

    missing = [h for h in local_h if h not in remote_h]
    extra = [h for h in remote_h if h not in local_h]
    for h in missing:
        print(f"MISSING in Jira: {h}")
    for h in extra:
        print(f"ONLY in Jira   : {h}")
    if missing or extra:
        return 5
    print("read-back check: PASS")
    return 0


def cmd_comment(args) -> int:
    if bool(args.draft) == bool(args.text):
        raise SystemExit("error: pass exactly one of --draft <file> or --text <markdown>")
    if args.draft:
        body = draft_to_adf(Path(args.draft), use_tasklist=not args.no_tasklist)
    else:
        body = markdown_to_adf(args.text.replace("\\n", "\n"), use_tasklist=not args.no_tasklist)

    print(f"{args.key}: commenting")
    print(f"body: {md2adf.summarize(body)}")
    if args.dry_run:
        print("dry run: nothing was sent to Jira")
        return 0

    data = request("POST", f"/rest/api/3/issue/{args.key}/comment", {"body": body})
    print(f"Commented on {args.key} (id {data.get('id')}): {issue_url(args.key)}")
    return 0


def cmd_subtask(args) -> int:
    if args.draft and args.text:
        raise SystemExit("error: pass at most one of --draft <file> or --text <markdown>")
    fields: dict = {
        "parent": {"key": args.parent},
        "issuetype": {"name": args.type},
        "summary": args.summary[:255],
    }
    parent = request("GET", f"/rest/api/3/issue/{args.parent}?fields=project")
    fields["project"] = {"key": parent.get("fields", {}).get("project", {}).get("key")}
    if args.draft:
        fields["description"] = draft_to_adf(Path(args.draft), description=True)
    elif args.text:
        text = args.text.replace("\\n", "\n")
        reject_parent_line(text)
        fields["description"] = markdown_to_adf(text)

    print(f"parent  : {args.parent} (project {fields['project']['key']})")
    print(f"type    : {args.type}")
    print(f"summary : {fields['summary']}")
    if "description" in fields:
        print(f"body    : {md2adf.summarize(fields['description'])}")
    if args.dry_run:
        print("dry run: nothing was sent to Jira")
        return 0

    data = request("POST", "/rest/api/3/issue", {"fields": fields})
    key = data.get("key", "")
    print(f"Created {key}: {issue_url(key)}")
    return 0


# --------------------------------------------------------------------------- #
# Workflow
# --------------------------------------------------------------------------- #

# Example workflow map used only to *plan* a multi-hop route. Replace it with your own
# project's status names and transitions (run the 'transitions' subcommand to inspect them).
# Every hop is still verified against the ticket's live transitions before it is applied.
STANDARD_WORKFLOW: dict[str, list[str]] = {
    "To Do": ["In Progress", "Waiting", "Done"],
    "Waiting": ["In Progress", "To Do", "Done"],
    "In Progress": ["Review", "Done"],
    "Review": ["Done", "In Progress"],
    "Done": ["To Do"],
}

# Lower-case input aliases mapped to the canonical status names above.
STATUS_ALIASES = {
    "open": "To Do",
    "in progress": "In Progress",
    "closed": "Done",
    "waiting": "Waiting",
    "review": "Review",
}


def canonical_status(name: str) -> str:
    return STATUS_ALIASES.get(name.strip().lower(), name.strip())


def fetch_status(key: str) -> str:
    data = request("GET", f"/rest/api/3/issue/{key}?fields=status")
    return data.get("fields", {}).get("status", {}).get("name", "")


def fetch_transitions(key: str) -> list[dict]:
    return request("GET", f"/rest/api/3/issue/{key}/transitions").get("transitions", [])


def plan_route(current: str, target: str, max_hops: int) -> list[str] | None:
    """Breadth-first route through the static workflow map, current exclusive."""
    if current == target:
        return []
    queue: list[tuple[str, list[str]]] = [(current, [])]
    seen = {current}
    while queue:
        node, path = queue.pop(0)
        for neighbour in STANDARD_WORKFLOW.get(node, []):
            if neighbour in seen:
                continue
            route = path + [neighbour]
            if neighbour == target:
                return route if len(route) <= max_hops else None
            if len(route) < max_hops:
                seen.add(neighbour)
                queue.append((neighbour, route))
    return None


def cmd_transitions(args) -> int:
    status = fetch_status(args.key)
    print(f"{args.key} is in '{status}'")
    for t in fetch_transitions(args.key):
        print(f"  [{t.get('id')}] {t.get('name'):<18} -> {t.get('to', {}).get('name')}")
    return 0


def cmd_transition(args) -> int:
    target = canonical_status(args.to)
    status = fetch_status(args.key)
    if status == target:
        print(f"{args.key} is already in '{target}'; nothing to do")
        return 0

    route = plan_route(status, target, args.max_hops)
    if route is None:
        available = [t.get("to", {}).get("name") for t in fetch_transitions(args.key)]
        print(f"error: no route from '{status}' to '{target}' within {args.max_hops} hop(s).")
        print(f"       directly reachable from '{status}': {', '.join(a for a in available if a)}")
        print("       for a non-standard workflow (e.g. Epic), transition hop by hop.")
        return 6

    print(f"{args.key}: '{status}' -> {' -> '.join(route)}")
    if args.dry_run:
        print("dry run: nothing was sent to Jira")
        return 0

    for hop in route:
        available = fetch_transitions(args.key)
        match = next((t for t in available if t.get("to", {}).get("name") == hop), None)
        if match is None:
            names = ", ".join(t.get("to", {}).get("name", "") for t in available)
            print(f"error: '{hop}' is not available from '{fetch_status(args.key)}'; got: {names}")
            print("       the workflow changed - re-check with the 'transitions' subcommand.")
            return 6
        request("POST", f"/rest/api/3/issue/{args.key}/transitions", {"transition": {"id": match["id"]}})
        print(f"  applied '{match.get('name')}' -> {hop}")

    if args.assign_me:
        assign_to_me(args.key)
    print(f"{args.key} is now in '{fetch_status(args.key)}': {issue_url(args.key)}")
    return 0


def assign_to_me(key: str) -> None:
    me = request("GET", "/rest/api/3/myself")
    current = request("GET", f"/rest/api/3/issue/{key}?fields=assignee").get("fields", {})
    assignee = current.get("assignee") or {}
    if assignee.get("accountId") == me.get("accountId"):
        print(f"  {key} is already assigned to {assignee.get('displayName')}")
        return
    request("PUT", f"/rest/api/3/issue/{key}/assignee", {"accountId": me.get("accountId")})
    print(f"  assigned {key} to {me.get('displayName')}")


def cmd_assign(args) -> int:
    if args.to == "me":
        assign_to_me(args.key)
    else:
        request("PUT", f"/rest/api/3/issue/{args.key}/assignee", {"accountId": args.to})
        print(f"  assigned {args.key} to {args.to}")
    return 0


def cmd_search(args) -> int:
    query = urllib.parse.urlencode({"jql": args.jql, "maxResults": args.max, "fields": args.fields})
    data = request("GET", f"/rest/api/3/search/jql?{query}")
    issues = data.get("issues", [])
    if not issues:
        print("no issues matched")
        return 0
    for issue in issues:
        f = issue.get("fields", {})
        print(
            f"{issue.get('key'):<14} {f.get('issuetype', {}).get('name', ''):<10} "
            f"{f.get('status', {}).get('name', ''):<16} {f.get('summary', '')}"
        )
    print(f"\n{len(issues)} issue(s)")
    return 0


def _walk_task_items(node, found: list) -> None:
    if isinstance(node, dict):
        if node.get("type") == "taskItem":
            found.append(node)
        for child in node.get("content", []) or []:
            _walk_task_items(child, found)
    elif isinstance(node, list):
        for child in node:
            _walk_task_items(child, found)


def cmd_checkbox(args) -> int:
    """Tick (or untick) one Validation-Checklist item in an issue description."""
    state = "TODO" if args.uncheck else "DONE"
    issue = request("GET", f"/rest/api/3/issue/{args.key}?fields=description")
    description = issue.get("fields", {}).get("description")

    if isinstance(description, dict):
        items: list = []
        _walk_task_items(description, items)
        matches = [i for i in items if args.item.strip().lower() in adf_text(i).strip().lower()]
        if not matches:
            print(f"error: no checklist item of {args.key} contains {args.item!r}.")
            print("       items found:")
            for item in items:
                print(f"         [{item.get('attrs', {}).get('state')}] {adf_text(item).strip()}")
            return 7
        if len(matches) > 1:
            print(f"error: {args.item!r} matches {len(matches)} items; pass a longer, unique excerpt.")
            return 7
        target = matches[0]
        before = target.get("attrs", {}).get("state")
        print(f"{args.key}: [{before}] -> [{state}]  {adf_text(target).strip()}")
        if before == state:
            print("nothing to do; the item already has that state")
            return 0
        if args.dry_run:
            print("dry run: nothing was sent to Jira")
            return 0
        target.setdefault("attrs", {})["state"] = state
        request("PUT", f"/rest/api/3/issue/{args.key}", {"fields": {"description": description}})

    elif isinstance(description, str):  # legacy wiki-markup checklist
        old = f"* [{'x' if args.uncheck else ' '}] {args.item}"
        new = f"* [{' ' if args.uncheck else 'x'}] {args.item}"
        if old not in description:
            print(f"error: {old!r} not found verbatim in the description of {args.key}.")
            print("       re-check for wording drift instead of editing around it.")
            return 7
        print(f"{args.key}: {old!r} -> {new!r}")
        if args.dry_run:
            print("dry run: nothing was sent to Jira")
            return 0
        request("PUT", f"/rest/api/3/issue/{args.key}", {"fields": {"description": description.replace(old, new, 1)}})

    else:
        print(f"error: {args.key} has no description to edit")
        return 7

    print(f"Updated {args.key}: {issue_url(args.key)}")
    return 0


def cmd_attach(args) -> int:
    file_path = Path(args.file)
    if not file_path.is_file():
        raise SystemExit(f"error: not a file: {file_path}")
    result = upload(f"/rest/api/2/issue/{args.key}/attachments", file_path)
    for item in result if isinstance(result, list) else [result]:
        print(f"attached {item.get('filename')} ({item.get('size')} bytes) to {args.key}")
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main() -> int:
    global _EMAIL_OVERRIDE
    parser = argparse.ArgumentParser(description="Jira REST v3 helper for ticket-drafting skills.")
    parser.add_argument("--email", help="Atlassian account e-mail; overrides JIRA_EMAIL")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("createmeta", help="list the issue types of a project")
    p.add_argument("--project", required=True)
    p.set_defaults(func=cmd_createmeta)

    p = sub.add_parser("get", help="read an issue")
    p.add_argument("key")
    p.add_argument("--adf-out")
    p.add_argument("--baseline-out")
    p.set_defaults(func=cmd_get)

    p = sub.add_parser("baseline", help="store the pre-edit state for the conflict check")
    p.add_argument("key")
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_baseline)

    p = sub.add_parser("create", help="create an issue from a Markdown draft")
    p.add_argument("--project", required=True)
    p.add_argument("--type", default="Bug")
    p.add_argument("--summary", required=True)
    p.add_argument("--draft", required=True)
    p.add_argument("--parent")
    p.add_argument("--priority")
    p.add_argument("--no-tasklist", action="store_true")
    p.add_argument("--strip-meta", action="store_true", help="drop the title and metadata block above the first section")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_create)

    p = sub.add_parser("update", help="update an issue from a Markdown draft")
    p.add_argument("key")
    p.add_argument("--draft")
    p.add_argument("--summary")
    p.add_argument("--baseline")
    p.add_argument("--force", action="store_true")
    p.add_argument("--no-tasklist", action="store_true")
    p.add_argument("--strip-meta", action="store_true", help="drop the title and metadata block above the first section")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_update)

    p = sub.add_parser("verify", help="compare the published description with the draft")
    p.add_argument("key")
    p.add_argument("--draft", required=True)
    p.add_argument("--strip-meta", action="store_true", help="compare as published with --strip-meta")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("comment", help="add a comment to an issue from Markdown (converted to ADF)")
    p.add_argument("key")
    p.add_argument("--draft", help="Markdown file holding the comment body")
    p.add_argument("--text", help="inline Markdown comment body; \\n is treated as a line break")
    p.add_argument("--no-tasklist", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_comment)

    p = sub.add_parser("subtask", help="create a sub-task under an existing issue")
    p.add_argument("--parent", required=True)
    p.add_argument("--type", default="Sub-task")
    p.add_argument("--summary", required=True)
    p.add_argument("--draft", help="Markdown file holding the sub-task description")
    p.add_argument("--text", help="inline Markdown description; \\n is treated as a line break")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_subtask)

    p = sub.add_parser("transitions", help="list the transitions available from the current status")
    p.add_argument("key")
    p.set_defaults(func=cmd_transitions)

    p = sub.add_parser("transition", help="move an issue to a target status, via intermediate hops if needed")
    p.add_argument("key")
    p.add_argument("--to", required=True, help="target status name; English aliases are accepted")
    p.add_argument("--assign-me", action="store_true", help="assign the issue to the token owner afterwards")
    p.add_argument("--max-hops", type=int, default=3)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_transition)

    p = sub.add_parser("assign", help="set the assignee of an issue")
    p.add_argument("key")
    p.add_argument("--to", default="me", help="'me' (default) or an Atlassian accountId")
    p.set_defaults(func=cmd_assign)

    p = sub.add_parser("search", help="run a JQL search via the v3 search endpoint")
    p.add_argument("--jql", required=True)
    p.add_argument("--fields", default="summary,status,issuetype")
    p.add_argument("--max", type=int, default=50)
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("checkbox", help="tick a Validation-Checklist item in an issue description")
    p.add_argument("key")
    p.add_argument("--item", required=True, help="unique excerpt of the checklist item text")
    p.add_argument("--uncheck", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_checkbox)

    p = sub.add_parser("attach", help="attach a file to an issue")
    p.add_argument("key")
    p.add_argument("--file", required=True)
    p.set_defaults(func=cmd_attach)

    args = parser.parse_args()
    _EMAIL_OVERRIDE = args.email
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
