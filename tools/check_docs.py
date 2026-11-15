"""Check that every relative Markdown link in the repo resolves.

Run from the repository root:

    python tools/check_docs.py

Exits 1 if a link target is missing. Skips fenced code blocks, inline code,
absolute URLs, and pure anchors. This keeps the docs from rotting as files move.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
INLINE_CODE_RE = re.compile(r"`[^`]*`")
SKIP_PREFIXES = ("http://", "https://", "mailto:", "#", "data:")
SKIP_DIRS = {".git", ".venv", "__pycache__", "build", "dist", "node_modules"}


def markdown_files() -> list[Path]:
    out = []
    for path in REPO.rglob("*.md"):
        if any(part in SKIP_DIRS for part in path.relative_to(REPO).parts):
            continue
        out.append(path)
    return sorted(out)


def links_in(path: Path) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    in_fence = False
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for match in LINK_RE.finditer(INLINE_CODE_RE.sub("", line)):
            target = match.group(1)
            if target.startswith(SKIP_PREFIXES):
                continue
            found.append((number, target))
    return found


def main() -> int:
    problems: list[str] = []
    checked = 0
    for document in markdown_files():
        for number, target in links_in(document):
            cleaned = target.split("#", 1)[0]
            if not cleaned:
                continue
            resolved = (document.parent / cleaned).resolve()
            checked += 1
            if not resolved.exists():
                rel_doc = document.relative_to(REPO)
                problems.append(f"{rel_doc}:{number}: missing target {target}")
    print(f"checked {checked} relative link(s) in {len(markdown_files())} markdown file(s)")
    for problem in problems:
        print(f"  [!] {problem}")
    if problems:
        print(f"{len(problems)} broken link(s)")
        return 1
    print("all links resolve")
    return 0


if __name__ == "__main__":
    sys.exit(main())
