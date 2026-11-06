#!/usr/bin/env python3
"""Jira steps of the bug-hunter that jira_issue.py does not cover: waiting mark, question, comments.

Subcommands:
    wait-add KEY                      add the waiting mark (label)
    wait-remove KEY                   remove the waiting mark
    ask KEY --draft file.md           post a question comment that mentions the reporter
    comments KEY                      print all comments as JSON (id, author id, created, text)

Reuses the credentials and the request helper of jira_issue.py (skills/_shared/scripts).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_shared" / "scripts"))
import jira_issue  # noqa: E402

WAITING_LABEL = "bug-hunter-waiting"
MARKER = "bug-hunter-question"


def edit_label(key: str, op: str) -> None:
    jira_issue.request("PUT", f"/rest/api/3/issue/{key}", {"update": {"labels": [{op: WAITING_LABEL}]}})
    print(f"{key}: label {WAITING_LABEL} {op}")


def comments(key: str) -> list[dict]:
    result, start = [], 0
    while True:
        data = jira_issue.request("GET", f"/rest/api/3/issue/{key}/comment?startAt={start}&maxResults=100&orderBy=created")
        result += [
            {
                "id": c["id"],
                "author_id": c["author"]["accountId"],
                "created": c["created"],
                "text": jira_issue.adf_text(c["body"]),
            }
            for c in data["comments"]
        ]
        start += len(data["comments"])
        if not data["comments"] or start >= data["total"]:
            return result


def ask(key: str, draft: Path) -> None:
    text = draft.read_text(encoding="utf-8")
    if not text.lstrip().startswith(MARKER):
        raise SystemExit(f"The question must start with '{MARKER} (round N)'.")
    reporter = jira_issue.request("GET", f"/rest/api/3/issue/{key}?fields=reporter")["fields"]["reporter"]
    body = jira_issue.markdown_to_adf(text, use_tasklist=False)
    mention = {"type": "mention", "attrs": {"id": reporter["accountId"], "text": f"@{reporter['displayName']}"}}
    body["content"].append({"type": "paragraph", "content": [mention, {"type": "text", "text": " please answer here."}]})
    data = jira_issue.request("POST", f"/rest/api/3/issue/{key}/comment", {"body": body})
    print(f"Asked on {key} (comment {data.get('id')}), reporter {reporter['displayName']} mentioned")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("wait-add", "wait-remove", "comments", "ask"):
        p = sub.add_parser(name)
        p.add_argument("key")
        if name == "ask":
            p.add_argument("--draft", required=True, type=Path)
    args = parser.parse_args()
    if args.cmd == "wait-add":
        edit_label(args.key, "add")
    elif args.cmd == "wait-remove":
        edit_label(args.key, "remove")
    elif args.cmd == "comments":
        print(json.dumps(comments(args.key), indent=2, ensure_ascii=False))
    else:
        ask(args.key, args.draft)
    return 0


if __name__ == "__main__":
    sys.exit(main())
