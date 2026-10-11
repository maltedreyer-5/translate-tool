# SPDX-FileCopyrightText: 2026 Malte Dreyer
# SPDX-License-Identifier: MIT
"""
Markdown table helpers shared by the whole pipeline.

Chunker, translation engine, converter, exporter and reassembly must agree on
what a table is. If one component accepts a table that another rejects, the
table is either translated as running text or torn apart on reassembly, so
these rules exist exactly once.
"""

import re
from typing import List

# One delimiter cell: dashes with optional alignment colons (GFM).
_SEPARATOR_CELL_RE = re.compile(r'^:?-+:?$')


def split_table_row(line: str) -> List[str]:
    """Split a table row into stripped cell texts (outer pipes are optional)."""
    line = (line or "").strip()
    if line.startswith('|'):
        line = line[1:]
    if line.endswith('|'):
        line = line[:-1]
    return [cell.strip() for cell in line.split('|')]


def count_table_columns(line: str) -> int:
    """Number of columns of a table row."""
    line = (line or "").strip()
    if not line:
        return 0
    if line.startswith('|') and line.endswith('|'):
        return line.count('|') - 1
    return line.count('|') + 1


def is_table_separator(line: str) -> bool:
    """True for a delimiter row such as |---|:--:|---:| (at least one dash per cell)."""
    stripped = (line or "").strip()
    if '|' not in stripped or '-' not in stripped:
        return False
    cells = [c for c in split_table_row(stripped.replace(' ', '').replace('\t', ''))]
    if not cells or any(c == '' for c in cells):
        return False
    return all(_SEPARATOR_CELL_RE.match(c) for c in cells)


def is_valid_table_lines(lines: List[str]) -> bool:
    """
    True if the (non-empty, stripped) lines form a complete table:
    header row, delimiter row, at least one data row, consistent column count.
    """
    if len(lines) < 3:
        return False
    if not is_table_separator(lines[1]):
        return False
    counts = {count_table_columns(line) for line in lines}
    return len(counts) == 1 and next(iter(counts)) > 0
