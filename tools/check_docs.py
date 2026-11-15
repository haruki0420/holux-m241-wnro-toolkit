"""Check that every relative Markdown link and repo path in the docs resolves.

Run from the repository root:

    python tools/check_docs.py

Two kinds of reference are checked:

1. **Markdown links** -- ``[label](target)``. Absolute URLs, mailto, anchors and
   data URIs are skipped.
2. **Inline code paths** -- `` `docs/reference/01-protocol.md` `` and similar.
   These are not links, so a plain link checker misses them, and they are how a
   reorganisation silently rots a document: the file moves, the prose keeps the
   old name, and nothing fails.

Exits 1 if anything is missing. Fenced code blocks are skipped for links (they
contain shell commands, not references), but inline code is inspected only when
it looks like a path into this repository.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
SKIP_PREFIXES = ("http://", "https://", "mailto:", "#", "data:")
SKIP_DIRS = {".git", ".venv", "__pycache__", "build", "dist", "node_modules"}

#: Directories whose contents are referenced by path in the docs. A backticked
#: token that starts with one of these is treated as a repository path.
PATH_ROOTS = ("docs/", "src/", "tests/", "tools/", ".github/")
#: Paths the *user* creates. They are gitignored, so they must not be required
#: to exist in a fresh checkout.
USER_ROOTS = ("backup/", "vendor/", "dist/", "build/")
#: Extensions that are always user data or build output, never repo content.
USER_SUFFIXES = (".bin", ".gpx", ".kml", ".csv", ".log", ".jpg", ".jpeg", ".png")
#: Bare filenames that are referenced by name and must exist somewhere.
PATH_SUFFIXES = (".md", ".py", ".json", ".yml", ".yaml", ".toml", ".in", ".txt")
#: Files whose inline paths are historical records, not live references.
#: A changelog entry describes what was true at that release; renaming the file
#: it mentions would falsify the record.
INLINE_PATH_SKIP_FILES = {"CHANGELOG.md"}
#: Put this on a line to opt out of the inline-path check (historical tables).
IGNORE_MARKER = "path-check: ignore"


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


def code_paths_in(path: Path) -> list[tuple[int, str]]:
    """Backticked tokens that look like a path into this repository."""
    if path.name in INLINE_PATH_SKIP_FILES:
        return []
    found: list[tuple[int, str]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if IGNORE_MARKER in line:
            continue
        for match in INLINE_CODE_RE.finditer(line):
            token = match.group(1).strip()
            if not token or token.startswith(SKIP_PREFIXES):
                continue
            if any(ch in token for ch in "*?<>|$ "):
                continue
            if token.startswith(USER_ROOTS) or token.lower().endswith(USER_SUFFIXES):
                continue
            if token.startswith(PATH_ROOTS):
                found.append((number, token))
            elif token.count("/") == 0 and token.endswith(PATH_SUFFIXES):
                # A bare filename: only complain if no file anywhere matches.
                found.append((number, token))
    return found


def _resolves(document: Path, token: str) -> bool:
    cleaned = token.split("#", 1)[0].split(":", 1)[0].strip()
    if not cleaned:
        return True
    if cleaned.startswith(PATH_ROOTS) or "/" in cleaned:
        return (document.parent / cleaned).resolve().exists() or (REPO / cleaned).exists()
    # Bare filename: accept a match anywhere in the tree.
    return any(REPO.rglob(cleaned))


def main() -> int:
    problems: list[str] = []
    link_count = 0
    path_count = 0
    documents = markdown_files()

    for document in documents:
        rel_doc = document.relative_to(REPO)
        for number, target in links_in(document):
            cleaned = target.split("#", 1)[0]
            if not cleaned:
                continue
            link_count += 1
            if not (document.parent / cleaned).resolve().exists():
                problems.append(f"{rel_doc}:{number}: missing link target {target}")
        for number, token in code_paths_in(document):
            path_count += 1
            if not _resolves(document, token):
                problems.append(f"{rel_doc}:{number}: missing path `{token}`")

    print(
        f"checked {link_count} relative link(s) and {path_count} inline path(s) "
        f"in {len(documents)} markdown file(s)"
    )
    for problem in problems:
        print(f"  [!] {problem}")
    if problems:
        print(f"{len(problems)} broken reference(s)")
        return 1
    print("all links resolve")
    return 0


if __name__ == "__main__":
    sys.exit(main())
