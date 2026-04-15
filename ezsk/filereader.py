from __future__ import annotations

import csv
import io
import re
import sys

from ezsk.display import print_error


def read_queries_from_file(filepath: str, columns: str | None = None) -> list[str]:
    """Read queries from a file, optionally parsing delimited data with column selection."""
    try:
        with open(filepath) as f:
            content = f.read()
    except FileNotFoundError:
        print_error(f"File not found: {filepath}")
        sys.exit(1)

    if columns is None:
        return [line.strip() for line in content.splitlines() if line.strip()]

    return _parse_delimited(content, columns)


def _parse_delimited(content: str, columns: str) -> list[str]:
    """Parse delimited content and build queries from column specification."""
    # Sniff delimiter
    try:
        dialect = csv.Sniffer().sniff(content[:8192])
    except csv.Error:
        dialect = csv.excel  # fallback to comma

    # Determine if we're using numeric indices
    is_template = "{" in columns
    if is_template:
        col_refs = re.findall(r"\{([^}]+)\}", columns)
    else:
        col_refs = [c.strip() for c in columns.split(",")]

    use_numeric = all(ref.isdigit() for ref in col_refs)

    reader = csv.reader(io.StringIO(content), dialect)
    rows = list(reader)
    if not rows:
        return []

    # Resolve header
    header: list[str] | None = None
    data_start = 0
    if not use_numeric:
        try:
            has_header = csv.Sniffer().has_header(content[:8192])
        except csv.Error:
            has_header = True
        if has_header:
            header = [h.strip().lower() for h in rows[0]]
            data_start = 1
        else:
            print_error("Column names specified but file has no header row.")
            sys.exit(1)

    # Build column indices
    indices: list[int] = []
    if use_numeric:
        for ref in col_refs:
            idx = int(ref)
            if idx < 1:
                print_error(f"Column index must be >= 1, got {idx}")
                sys.exit(1)
            indices.append(idx - 1)  # convert to 0-based
    else:
        assert header is not None
        for ref in col_refs:
            ref_lower = ref.strip().lower()
            try:
                indices.append(header.index(ref_lower))
            except ValueError:
                available = ", ".join(rows[0])
                print_error(f"Column '{ref}' not found. Available columns: {available}")
                sys.exit(1)

    # Build template function
    if is_template:
        def format_row(row: list[str]) -> str:
            result = columns
            for ref, idx in zip(col_refs, indices):
                if idx >= len(row):
                    return ""
                result = result.replace("{" + ref + "}", row[idx].strip())
            return result.strip()
    else:
        def format_row(row: list[str]) -> str:
            parts = []
            for idx in indices:
                if idx >= len(row):
                    return ""
                parts.append(row[idx].strip())
            return " - ".join(parts)

    # Process rows
    queries: list[str] = []
    max_idx = max(indices)
    for row in rows[data_start:]:
        if max_idx >= len(row):
            continue
        query = format_row(row)
        if query:
            queries.append(query)

    return queries
