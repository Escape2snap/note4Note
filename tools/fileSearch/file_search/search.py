"""In-memory keyword search with line/page context."""

from __future__ import annotations

import re
from bisect import bisect_left, bisect_right
from typing import Any, Iterator, Sequence

from .store import Document

MAX_QUERY_CHARS = 500
DEFAULT_LIMIT = 20
MAX_LIMIT = 200
DEFAULT_CONTEXT_CHARS = 60
MAX_CONTEXT_CHARS = 5000


class SearchError(ValueError):
    """Raised for an invalid search request."""


def _paragraphs(lines: Sequence[str]) -> Iterator[tuple[int, list[str]]]:
    """Yield (zero-based-line-index, non-empty lines), split by blank lines."""

    index = 0
    total = len(lines)
    while index < total:
        if not lines[index]:
            index += 1
            continue
        start = index
        while index < total and lines[index]:
            index += 1
        yield start, list(lines[start:index])


def _cumulative_ends(lines: Sequence[str]) -> list[int]:
    ends: list[int] = []
    cursor = 0
    for line in lines:
        cursor += len(line)
        ends.append(cursor)
    return ends


def _line_at_offset(ends: Sequence[int], offset: int, is_end: bool) -> int:
    if is_end:
        line_index = bisect_left(ends, offset)
    else:
        line_index = bisect_right(ends, offset)
    return min(max(line_index, 0), len(ends) - 1)


def _context(
    lines: Sequence[str],
    match_start: int,
    match_end: int,
    context_chars: int,
) -> tuple[str, list[dict[str, Any]], dict[str, int]]:
    """Build a character-window snippet that preserves source line breaks.

    Match offsets are measured against "".join(lines), because line breaks are
    ignored for matching (so "科学" also matches "科\n学").
    """

    ends = _cumulative_ends(lines)
    total_chars = ends[-1]
    context_start = max(0, match_start - context_chars)
    context_end = min(total_chars, match_end + context_chars)

    start_line_index = _line_at_offset(ends, context_start, is_end=False)
    end_line_index = _line_at_offset(ends, context_end, is_end=True)
    start_line_offset = ends[start_line_index - 1] if start_line_index else 0
    end_line_offset = ends[end_line_index - 1] if end_line_index else 0

    context_start_local = context_start - start_line_offset
    context_end_local = context_end - end_line_offset

    segments: list[str] = []
    context_lines: list[dict[str, Any]] = []
    for index in range(start_line_index, end_line_index + 1):
        line = lines[index]
        left = context_start_local if index == start_line_index else 0
        right = context_end_local if index == end_line_index else len(line)
        if right < left:
            right = left
        segment = line[left:right]
        segments.append(segment)
        context_lines.append({"line": index + 1, "text": segment})

    prefix = "…" if context_start > 0 else ""
    suffix = "…" if context_end < total_chars else ""
    body = "\n".join(segments)
    snippet = prefix + body + suffix

    prefix_len = len(prefix)
    # Newlines added while joining the context segments shift all offsets after
    # the first segment. The number of such newlines before the match start/end
    # equals the number of crossed line boundaries.
    match_start_line = _line_at_offset(ends, match_start, is_end=False)
    match_end_line = _line_at_offset(ends, match_end, is_end=True)
    highlight_start = (
        prefix_len
        + (match_start - context_start)
        + (match_start_line - start_line_index)
    )
    highlight_end = (
        prefix_len
        + (match_end - context_start)
        + (match_end_line - start_line_index)
    )

    return snippet, context_lines, {"start": highlight_start, "end": highlight_end}


def search_documents(
    documents: Sequence[Document],
    query: str,
    *,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
    context_chars: int = DEFAULT_CONTEXT_CHARS,
    page: int | None = None,
    case_sensitive: bool = False,
) -> dict[str, Any]:
    """Search the supplied in-memory documents and return JSON-safe results."""

    if not isinstance(query, str) or not query.strip():
        raise SearchError("q 不能为空")
    if len(query) > MAX_QUERY_CHARS:
        raise SearchError(f"q 最长 {MAX_QUERY_CHARS} 个字符")
    if limit < 1 or limit > MAX_LIMIT:
        raise SearchError(f"limit 必须在 1..{MAX_LIMIT} 之间")
    if offset < 0:
        raise SearchError("offset 必须 >= 0")
    if context_chars < 0 or context_chars > MAX_CONTEXT_CHARS:
        raise SearchError(f"context 必须在 0..{MAX_CONTEXT_CHARS} 之间")
    if page is not None and page < 1:
        raise SearchError("page 必须 >= 1")

    flags = 0 if case_sensitive else re.IGNORECASE
    pattern = re.compile(re.escape(query), flags)

    total = 0
    results: list[dict[str, Any]] = []

    for document in documents:
        meta = document.meta
        for page_index, page_lines in enumerate(document.lines):
            page_number = page_index + 1
            if page is not None and page_number != page:
                continue
            for run_line_start, run in _paragraphs(page_lines):
                joined = "".join(run)
                if not joined:
                    continue
                ends = _cumulative_ends(run)
                line_starts = [0]
                for line in run[:-1]:
                    line_starts.append(line_starts[-1] + len(line))

                for match in pattern.finditer(joined):
                    total += 1
                    if total <= offset or len(results) >= limit:
                        continue

                    start = match.start()
                    end = match.end()
                    start_line_index = _line_at_offset(ends, start, is_end=False)
                    end_line_index = _line_at_offset(ends, end, is_end=True)
                    start_line_offset = line_starts[start_line_index]
                    end_line_offset = line_starts[end_line_index]
                    start_column = start - start_line_offset + 1
                    end_column = end - end_line_offset
                    start_line_number = run_line_start + start_line_index + 1
                    end_line_number = run_line_start + end_line_index + 1

                    snippet, context_lines, highlight = _context(
                        run, start, end, context_chars
                    )
                    results.append(
                        {
                            "file_id": meta.id,
                            "file_name": meta.name,
                            "path": meta.relative_path,
                            "format": meta.format,
                            "page": page_number,
                            "line": start_line_number,
                            "end_line": end_line_number,
                            "start_column": start_column,
                            "end_column": end_column,
                            "match": joined[start:end],
                            "snippet": snippet,
                            "highlight": highlight,
                            "context_lines": context_lines,
                            "page_count": meta.page_count,
                        }
                    )

    return {
        "query": query,
        "total": total,
        "returned": len(results),
        "offset": offset,
        "limit": limit,
        "context_chars": context_chars,
        "case_sensitive": case_sensitive,
        "has_more": offset + len(results) < total,
        "results": results,
    }
