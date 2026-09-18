"""Split source files into retrievable chunks.

Strategy:
  * Prefer AST-aware chunking (function/class boundaries) via tree-sitter when
    it's installed. That keeps a function together in one chunk, which retrieves
    far better than arbitrary line windows.
  * Fall back to a language-agnostic sliding-window splitter otherwise, so the
    project runs out of the box with zero extra native dependencies.

Each chunk carries metadata (file path, start/end line, language) so the agent
can cite exact locations back to the user.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

# File extensions we index, mapped to a language label used for metadata + AST.
LANG_BY_EXT = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".java": "java",
    ".go": "go",
    ".rb": "ruby",
    ".rs": "rust",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cs": "c_sharp",
    ".php": "php",
    ".md": "markdown",
    ".sql": "sql",
    ".sh": "bash",
}

# Directories that are never worth indexing.
IGNORE_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build",
    ".next", ".repo_cache", "target", ".idea", ".vscode", "vendor",
}

MAX_CHUNK_CHARS = 1500
CHUNK_OVERLAP_LINES = 8
MAX_FILE_BYTES = 400_000  # skip anything bigger (minified bundles, data files)


@dataclass
class Chunk:
    repo_id: str
    path: str          # repo-relative path, forward-slashed
    language: str
    start_line: int
    end_line: int
    content: str
    symbol: str | None = None  # function/class name when known
    meta: dict = field(default_factory=dict)

    def chunk_id(self) -> str:
        return f"{self.repo_id}:{self.path}:{self.start_line}-{self.end_line}"


def iter_source_files(root: str):
    """Yield (abs_path, rel_path, language) for every indexable file under root."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS]
        for name in filenames:
            ext = os.path.splitext(name)[1].lower()
            lang = LANG_BY_EXT.get(ext)
            if not lang:
                continue
            abs_path = os.path.join(dirpath, name)
            try:
                if os.path.getsize(abs_path) > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            rel = os.path.relpath(abs_path, root).replace(os.sep, "/")
            yield abs_path, rel, lang


def chunk_repo(root: str, repo_id: str) -> list[Chunk]:
    chunks: list[Chunk] = []
    for abs_path, rel, lang in iter_source_files(root):
        try:
            with open(abs_path, "r", encoding="utf-8", errors="ignore") as fh:
                text = fh.read()
        except OSError:
            continue
        if not text.strip():
            continue
        chunks.extend(_chunk_file(text, rel, lang, repo_id))
    return chunks


def _chunk_file(text: str, path: str, lang: str, repo_id: str) -> list[Chunk]:
    ast_chunks = _try_ast_chunk(text, path, lang, repo_id)
    if ast_chunks is not None:
        return ast_chunks
    return _window_chunk(text, path, lang, repo_id)


# --------------------------------------------------------------------------- #
# AST-aware chunking (optional; requires tree-sitter)
# --------------------------------------------------------------------------- #
def _try_ast_chunk(text: str, path: str, lang: str, repo_id: str) -> list[Chunk] | None:
    try:
        from tree_sitter_language_pack import get_parser  # type: ignore
    except Exception:
        return None  # tree-sitter not installed -> caller falls back

    try:
        parser = get_parser(lang)
    except Exception:
        return None

    tree = parser.parse(text.encode("utf-8"))
    lines = text.splitlines()
    # Node types that represent a "unit" worth isolating, across languages.
    unit_types = {
        "function_definition", "function_declaration", "method_definition",
        "class_definition", "class_declaration", "method_declaration",
        "function_item", "impl_item", "arrow_function",
    }
    chunks: list[Chunk] = []

    def walk(node):
        if node.type in unit_types:
            start = node.start_point[0]
            end = node.end_point[0]
            content = "\n".join(lines[start : end + 1])
            if content.strip():
                symbol = _guess_symbol(node, text)
                for sub in _split_oversized(content, start):
                    s_start, s_content = sub
                    chunks.append(
                        Chunk(
                            repo_id=repo_id, path=path, language=lang,
                            start_line=s_start + 1,
                            end_line=s_start + len(s_content.splitlines()),
                            content=s_content, symbol=symbol,
                        )
                    )
            return  # don't descend into a unit we already captured
        for child in node.children:
            walk(child)

    walk(tree.root_node)
    # Capture module-level code that isn't inside any unit (imports, config).
    if not chunks:
        return _window_chunk(text, path, lang, repo_id)
    return chunks


def _guess_symbol(node, text: str) -> str | None:
    for child in node.children:
        if child.type in ("identifier", "name", "property_identifier"):
            return text[child.start_byte : child.end_byte]
    return None


def _split_oversized(content: str, start_line: int) -> list[tuple[int, str]]:
    """A single huge function is split into sub-windows so no chunk is too big."""
    if len(content) <= MAX_CHUNK_CHARS:
        return [(start_line, content)]
    lines = content.splitlines()
    out: list[tuple[int, str]] = []
    step = max(1, MAX_CHUNK_CHARS // 40)  # ~40 chars/line heuristic
    i = 0
    while i < len(lines):
        window = lines[i : i + step]
        out.append((start_line + i, "\n".join(window)))
        i += step
    return out


# --------------------------------------------------------------------------- #
# Fallback: sliding-window chunking
# --------------------------------------------------------------------------- #
def _window_chunk(text: str, path: str, lang: str, repo_id: str) -> list[Chunk]:
    lines = text.splitlines()
    chunks: list[Chunk] = []
    approx_lines = max(20, MAX_CHUNK_CHARS // 40)
    i = 0
    while i < len(lines):
        window = lines[i : i + approx_lines]
        content = "\n".join(window)
        if content.strip():
            chunks.append(
                Chunk(
                    repo_id=repo_id, path=path, language=lang,
                    start_line=i + 1, end_line=i + len(window),
                    content=content,
                )
            )
        if i + approx_lines >= len(lines):
            break
        i += approx_lines - CHUNK_OVERLAP_LINES
    return chunks
