#!/usr/bin/env python3
"""Shared draft-checking engine for the ticket-drafting skills.

`draft-bug-ticket`, `draft-task-ticket` and `draft-epic-and-stories` all check a
Markdown draft for the same things: required sections in a fixed order, section
budgets, Simplified Technical English, unresolved placeholders and synonym
rotation. Only the section names and a handful of type-specific rules differ.

Those common parts live here. A skill's own `check_draft.py` declares a
`DraftSpec`, adds its type-specific checks and calls `run_cli`.

The single source of truth for the *rules* themselves is
`skills/_shared/ticket-voice.md` — keep the word lists below in sync with it.

Exit codes (identical for every checker built on this module):
    0  no errors (warnings may be present)
    1  at least one error
    2  usage error / file not readable
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

# --------------------------------------------------------------------------- #
# Shared rule data — the PO voice, identical for every ticket type
# --------------------------------------------------------------------------- #

HEDGING = [
    "somewhat", "arguably", "presumably", "seemingly", "it seems", "it appears",
    "maybe", "perhaps", "probably", "possibly", "may potentially", "might possibly",
    "in some cases", "more or less", "rather unclear", "we believe", "i think",
    "should probably", "kind of", "sort of", "to some extent", "fairly", "quite a bit",
]

BUZZWORDS = [
    "seamless", "seamlessly", "robust", "leverage", "leveraging", "holistic",
    "state-of-the-art", "cutting-edge", "deep dive", "streamlined", "streamline",
    "synergy", "best-in-class", "world-class", "game changer", "next-generation",
    "turnkey", "frictionless", "empower", "unlock", "delightful", "rock-solid",
]

# Frozen verb phrases -> the live verb to use instead.
FROZEN_VERBS = [
    (r"\bperform(s|ed)?\s+(a|an|the)\s+\w+(ion|ing|ment)\b", "use the verb directly (e.g. 'validates')"),
    (r"\bcarr(y|ies|ied)\s+out\s+(a|an|the)\s+\w+", "use the verb directly (e.g. 'updates')"),
    (r"\bprovide(s|d)?\s+support\s+for\b", "use 'supports'"),
    (r"\bmake(s|)\s+use\s+of\b", "use 'uses'"),
    (r"\bgive(s|n)?\s+(an?\s+)?indication\s+of\b", "use 'indicates'"),
    (r"\bis\s+in\s+the\s+process\s+of\b", "name the action directly"),
    (r"\btake(s|n)?\s+into\s+(account|consideration)\b", "use 'considers'"),
    (r"\bconduct(s|ed)?\s+(a|an|the)\s+\w+", "use the verb directly"),
    (r"\bhas\s+the\s+ability\s+to\b", "use 'can'"),
]

PASSIVE = re.compile(
    r"\b(is|are|was|were|be|been|being)\s+(\w+ed|built|kept|left|sent|set|shown|written|"
    r"thrown|caught|lost|found|held|read)\b(?!\s+by\s+the\s+developer)",
    re.IGNORECASE,
)

# One term per concept. The first entry of a group is the term to keep.
COMMON_SYNONYM_GROUPS = [
    ["campaign", "rollout campaign", "campaign entity"],
    ["stage", "environment", "deployment target"],
    ["user", "operator", "actor", "end user"],
    ["request", "call", "invocation"],
]

# Unresolved placeholders. Open points are clarified during intake, never parked
# in the draft. `**Ticket:** TBD` is the one legitimate exception before publishing.
PLACEHOLDER_PATTERNS = [
    (r"\bTBD\b", "unresolved 'TBD'"),
    (r"\bTODO\b", "unresolved 'TODO'"),
    (r"\bFIXME\b", "unresolved 'FIXME'"),
    (r"\bXXX\b", "unresolved 'XXX'"),
    (r"\?\?\?", "unresolved '???'"),
    (r"<[a-z][\w .'/|-]*>", "unfilled template placeholder"),
]

MAX_SENTENCE_WORDS = 20
RUN_ON_WORDS = 30

# Acceptance Criteria stay flat until they stop being readable. See ticket-voice.md.
AC_FLAT_LIMIT = 5


# --------------------------------------------------------------------------- #
# Spec
# --------------------------------------------------------------------------- #


@dataclass
class DraftSpec:
    """Everything that differs between ticket types."""

    kind: str
    required_sections: list[str]
    ordered_sections: list[str]
    optional_sections: list[str] = field(default_factory=list)
    # Exactly one of these must be present (e.g. bug: repro steps vs. occurrence).
    either_or_sections: list[str] = field(default_factory=list)
    forbidden_section_patterns: list[tuple[str, str]] = field(default_factory=list)
    header_fields: list[str] = field(default_factory=list)
    # Header fields whose absence is an error rather than a warning.
    required_header_fields: list[str] = field(default_factory=list)
    synonym_groups: list[list[str]] = field(default_factory=list)
    max_words: int = 700
    max_pointer_bullets: int = 5
    max_evidence_lines: int = 25
    # Type-specific checks: fn(lines, sections, spec) -> list[Finding]
    extra_checks: list[Callable] = field(default_factory=list)
    description: str = "Check a draft for structure, budget and STE rules."

    def all_synonym_groups(self) -> list[list[str]]:
        return COMMON_SYNONYM_GROUPS + self.synonym_groups

    def known_sections(self) -> set[str]:
        names = (self.required_sections + self.ordered_sections
                 + self.optional_sections + self.either_or_sections)
        return {n.lower() for n in names}


class Finding:
    def __init__(self, line: int, level: str, rule: str, message: str, text: str = ""):
        self.line = line
        self.level = level
        self.rule = rule
        self.message = message
        self.text = text.strip()

    def as_dict(self) -> dict:
        return {
            "line": self.line,
            "level": self.level,
            "rule": self.rule,
            "message": self.message,
            "text": self.text,
        }


# --------------------------------------------------------------------------- #
# Parsing helpers
# --------------------------------------------------------------------------- #


def strip_code_blocks(lines: list[str]) -> list[tuple[int, str]]:
    """Return (line_number, text) for prose lines only: no fences, tables or headings."""
    out: list[tuple[int, str]] = []
    in_fence = False
    for idx, raw in enumerate(lines, start=1):
        stripped = raw.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if stripped.startswith("#") or stripped.startswith("|") or not stripped:
            continue
        out.append((idx, raw))
    return out


def prose_blocks(lines: list[str]) -> list[tuple[int, str]]:
    """Join wrapped prose into blocks, so a sentence spanning several lines is measured once.

    A block ends at a blank line, a heading, a fence, a table row, or the start of a new
    list item. The reported line number is the first line of the block.
    """
    blocks: list[tuple[int, str]] = []
    current: list[str] = []
    start = 0
    in_fence = False

    def flush() -> None:
        nonlocal current, start
        if current:
            blocks.append((start, " ".join(part.strip() for part in current)))
            current = []

    for idx, raw in enumerate(lines, start=1):
        stripped = raw.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            flush()
            continue
        if in_fence:
            continue
        if not stripped or stripped.startswith("#") or stripped.startswith("|") or stripped == "---":
            flush()
            continue
        if re.match(r"^\s*([-*+]|\d+\.)\s+", raw):
            flush()
            start = idx
            current = [stripped]
            continue
        if re.match(r"^\*\*[\w /]+:\*\*", stripped):
            flush()
            blocks.append((idx, stripped))
            continue
        if not current:
            start = idx
        current.append(stripped)
    flush()
    return blocks


def find_sections(lines: list[str]) -> list[tuple[int, int, str]]:
    """Return (line_number, level, title) for every ATX heading outside code fences."""
    sections: list[tuple[int, int, str]] = []
    in_fence = False
    for idx, raw in enumerate(lines, start=1):
        stripped = raw.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = re.match(r"^(#{1,6})\s+(.*?)\s*$", stripped)
        if m:
            title = re.sub(r"\s*\(.*?\)\s*$", "", m.group(2)).strip()
            sections.append((idx, len(m.group(1)), title))
    return sections


def section_body(lines: list[str], sections: list[tuple[int, int, str]], title: str) -> list[tuple[int, str]]:
    """Lines belonging to the first section whose title matches, excluding the heading."""
    for pos, (line_no, level, name) in enumerate(sections):
        if name.lower() != title.lower():
            continue
        end = len(lines)
        for next_line, next_level, _ in sections[pos + 1:]:
            if next_level <= level:
                end = next_line - 1
                break
        return [(i + 1, lines[i]) for i in range(line_no, end)]
    return []


def subsections_of(sections: list[tuple[int, int, str]], title: str) -> list[tuple[int, int, str]]:
    """Headings nested under the given section."""
    out: list[tuple[int, int, str]] = []
    for pos, (line_no, level, name) in enumerate(sections):
        if name.lower() != title.lower():
            continue
        for nxt in sections[pos + 1:]:
            if nxt[1] <= level:
                break
            out.append(nxt)
        break
    return out


def bullets_in(body: list[tuple[int, str]]) -> list[tuple[int, str]]:
    return [(n, t) for n, t in body if re.match(r"^\s*[-*+]\s+", t)]


def split_sentences(text: str) -> list[str]:
    text = re.sub(r"`[^`]*`", "CODE", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z(])", text.strip())
    return [p.strip() for p in parts if p.strip()]


def word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9_./-]+", text))


# --------------------------------------------------------------------------- #
# Shared checks
# --------------------------------------------------------------------------- #


def check_structure(lines: list[str], sections: list[tuple[int, int, str]],
                    spec: DraftSpec) -> list[Finding]:
    findings: list[Finding] = []
    titles = {name.lower(): line_no for line_no, _, name in sections}

    for required in spec.required_sections:
        if required.lower() not in titles:
            findings.append(Finding(0, "error", "missing-section", f"section '{required}' is missing"))

    if spec.either_or_sections:
        present = [s for s in spec.either_or_sections if s.lower() in titles]
        if not present:
            joined = "' or '".join(spec.either_or_sections)
            findings.append(Finding(0, "error", "missing-section", f"add either '{joined}'"))
        elif len(present) > 1:
            joined = "' / '".join(spec.either_or_sections)
            findings.append(Finding(titles[present[1].lower()], "error", "duplicate-section",
                                    f"keep only one of '{joined}'"))

    known = spec.known_sections()
    # Sub-headings inside Acceptance Criteria are the system groups from
    # ticket-voice.md, so they are free-form by design.
    ac_line = titles.get("acceptance criteria", 0)
    ac_end = len(lines)
    if ac_line:
        for line_no, level, _ in sections:
            if line_no > ac_line and level <= 2:
                ac_end = min(ac_end, line_no)

    for line_no, level, name in sections:
        if level == 1 or name.lower() in known:
            continue
        if ac_line and ac_line < line_no < ac_end:
            continue
        low = name.lower()
        for pattern, reason in spec.forbidden_section_patterns:
            if re.search(pattern, low):
                findings.append(Finding(line_no, "error", "forbidden-section",
                                        f"'{name}': {reason}", name))
                break

    order_seen = [(titles[s.lower()], s) for s in spec.ordered_sections if s.lower() in titles]
    for (line_a, name_a), (line_b, name_b) in zip(order_seen, order_seen[1:]):
        if line_b < line_a:
            findings.append(Finding(line_b, "error", "section-order",
                                    f"'{name_b}' must come after '{name_a}'"))

    head = "\n".join(lines[:20])
    for field_name in spec.header_fields:
        if not re.search(rf"\*\*{re.escape(field_name)}", head, re.IGNORECASE):
            level = "error" if field_name in spec.required_header_fields else "warning"
            findings.append(Finding(1, level, "missing-header-field",
                                    f"header field '{field_name}' is missing"))
    return findings


def check_budgets(lines: list[str], sections: list[tuple[int, int, str]],
                  spec: DraftSpec, max_words: int) -> list[Finding]:
    findings: list[Finding] = []

    pointers = bullets_in(section_body(lines, sections, "Technical Pointers"))
    if len(pointers) > spec.max_pointer_bullets:
        findings.append(Finding(pointers[spec.max_pointer_bullets][0], "error", "pointer-budget",
                                f"Technical Pointers has {len(pointers)} bullets, "
                                f"max {spec.max_pointer_bullets}"))

    evidence = [(n, t) for n, t in section_body(lines, sections, "Evidence") if t.strip()]
    if len(evidence) > spec.max_evidence_lines:
        findings.append(Finding(evidence[spec.max_evidence_lines][0], "warning", "evidence-budget",
                                f"Evidence has {len(evidence)} non-empty lines, target max "
                                f"{spec.max_evidence_lines}; attach the full log instead"))

    total = sum(word_count(t) for _, t in strip_code_blocks(lines))
    if total > max_words:
        findings.append(Finding(0, "warning", "length-budget",
                                f"draft has ~{total} words of prose, target max {max_words}"))
    return findings


def check_ac_grouping(lines: list[str], sections: list[tuple[int, int, str]],
                      spec: DraftSpec) -> list[Finding]:
    """The one AC layout rule shared by every ticket type.

    Flat while the list stays readable; grouped by system once it does not. Mixing
    both — a few loose bullets plus some groups — is what makes two tickets look
    like they came from different authors.
    """
    findings: list[Finding] = []
    body = section_body(lines, sections, "Acceptance Criteria")
    if not body:
        return findings

    groups = subsections_of(sections, "Acceptance Criteria")
    group_lines = {line_no for line_no, _, _ in groups}
    first_group = min(group_lines) if group_lines else None

    all_bullets = bullets_in(body)
    loose = [b for b in all_bullets if first_group is None or b[0] < first_group]

    if groups and loose:
        findings.append(Finding(loose[0][0], "error", "ac-layout",
                                f"{len(loose)} acceptance criteria sit outside the groups; "
                                "either group all of them or none"))
    elif not groups and len(all_bullets) > AC_FLAT_LIMIT:
        findings.append(Finding(all_bullets[AC_FLAT_LIMIT][0], "warning", "ac-layout",
                                f"{len(all_bullets)} flat acceptance criteria, "
                                f"group them by system above {AC_FLAT_LIMIT}"))
    return findings


def check_ac_form(lines: list[str], sections: list[tuple[int, int, str]],
                  prescriptive: list[tuple[str, str]]) -> list[Finding]:
    """'shall' form plus the ban on prescribing an implementation."""
    findings: list[Finding] = []
    body = section_body(lines, sections, "Acceptance Criteria")
    if not body:
        return findings

    text = "\n".join(t for _, t in body)
    if not re.search(r"\bshall\b", text, re.IGNORECASE):
        findings.append(Finding(body[0][0], "error", "ac-form",
                                "no AC uses 'shall'; state binding, observable behaviour"))

    for line_no, raw in body:
        if not re.match(r"^\s*[-*+]\s+", raw):
            continue
        for pattern, reason in prescriptive:
            if re.search(pattern, raw, re.IGNORECASE):
                findings.append(Finding(line_no, "error", "ac-prescriptive", reason, raw))
                break
    return findings


def boilerplate_lines(lines: list[str], sections: list[tuple[int, int, str]]) -> set[int]:
    """Line numbers of the fixed closing sections, where passive voice is expected."""
    numbers: set[int] = set()
    for title in ("Definition of Done", "Validation Checklist"):
        for line_no, _ in section_body(lines, sections, title):
            numbers.add(line_no)
    return numbers


def check_style(lines: list[str], boilerplate: set[int]) -> list[Finding]:
    findings: list[Finding] = []

    for line_no, text in prose_blocks(lines):
        if text.startswith(">") or re.match(r"^\[.*\]$", text):
            continue
        low = text.lower()

        for word in HEDGING:
            if re.search(rf"(?<![\w-]){re.escape(word)}(?![\w-])", low):
                findings.append(Finding(line_no, "error", "hedging",
                                        f"remove the hedge '{word}'", text))
        for word in BUZZWORDS:
            if re.search(rf"(?<![\w-]){re.escape(word)}(?![\w-])", low):
                findings.append(Finding(line_no, "error", "buzzword",
                                        f"remove the buzzword '{word}'", text))
        for pattern, hint in FROZEN_VERBS:
            m = re.search(pattern, low)
            if m:
                findings.append(Finding(line_no, "warning", "frozen-verb",
                                        f"'{m.group(0)}': {hint}", text))
        if line_no not in boilerplate:
            m = PASSIVE.search(text)
            if m:
                findings.append(Finding(line_no, "warning", "passive-voice",
                                        f"'{m.group(0)}': name the acting system", text))

        for sentence in split_sentences(text):
            words = word_count(sentence)
            commas = sentence.count(",")
            if words > RUN_ON_WORDS:
                findings.append(Finding(line_no, "error", "run-on",
                                        f"{words} words, {commas} comma(s); split it", sentence))
            elif words > MAX_SENTENCE_WORDS:
                findings.append(Finding(line_no, "warning", "long-sentence",
                                        f"{words} words, target max {MAX_SENTENCE_WORDS}", sentence))
    return findings


def check_placeholders(lines: list[str]) -> list[Finding]:
    """Open points belong in the intake, not in the draft."""
    findings: list[Finding] = []
    for line_no, text in prose_blocks(lines):
        # `**Ticket:** TBD` is legitimate until the ticket is created.
        if re.match(r"^\*\*Ticket:\*\*", text):
            continue
        stripped = re.sub(r"`[^`]*`", "", text)
        for pattern, message in PLACEHOLDER_PATTERNS:
            m = re.search(pattern, stripped)
            if m:
                findings.append(Finding(line_no, "error", "placeholder",
                                        f"{message}: clarify it in the intake or drop the point",
                                        text))
                break
    return findings


def check_synonyms(lines: list[str], spec: DraftSpec) -> list[Finding]:
    findings: list[Finding] = []
    prose_text = " ".join(t for _, t in prose_blocks(lines)).lower()
    for group in spec.all_synonym_groups():
        used = [w for w in group if re.search(rf"(?<![\w-]){re.escape(w)}(?![\w-])", prose_text)]
        if len(used) > 1:
            findings.append(Finding(0, "warning", "synonym-rotation",
                                    f"one term per concept: {', '.join(used)} — keep '{used[0]}'"))
    return findings


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #


def run(path: Path, spec: DraftSpec, max_words: int) -> list[Finding]:
    lines = path.read_text(encoding="utf-8").splitlines()
    sections = find_sections(lines)

    findings: list[Finding] = []
    findings += check_structure(lines, sections, spec)
    findings += check_budgets(lines, sections, spec, max_words)
    findings += check_ac_grouping(lines, sections, spec)
    findings += check_style(lines, boilerplate_lines(lines, sections))
    findings += check_placeholders(lines)
    findings += check_synonyms(lines, spec)
    for extra in spec.extra_checks:
        findings += extra(lines, sections, spec)

    findings.sort(key=lambda f: (f.line, f.rule))
    return findings


def run_cli(spec: DraftSpec, argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=spec.description)
    parser.add_argument("draft", type=Path)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--max-words", type=int, default=spec.max_words)
    parser.add_argument("--quiet", action="store_true", help="errors only")
    args = parser.parse_args(argv)

    if not args.draft.is_file():
        print(f"error: not a file: {args.draft}", file=sys.stderr)
        return 2

    findings = run(args.draft, spec, args.max_words)
    if args.quiet:
        findings = [f for f in findings if f.level == "error"]

    errors = sum(1 for f in findings if f.level == "error")
    warnings = sum(1 for f in findings if f.level == "warning")

    if args.json:
        print(json.dumps({"file": str(args.draft), "kind": spec.kind,
                          "errors": errors, "warnings": warnings,
                          "findings": [f.as_dict() for f in findings]}, indent=2))
    else:
        for f in findings:
            where = f"{args.draft}:{f.line}" if f.line else str(args.draft)
            snippet = f"  |  {f.text[:90]}" if f.text else ""
            print(f"{where}: {f.level.upper()} [{f.rule}] {f.message}{snippet}")
        print(f"\n{errors} error(s), {warnings} warning(s)")
        if not errors:
            print("structure and style gate: PASS")

    return 1 if errors else 0
