# SPDX-FileCopyrightText: 2026 Malte Dreyer
# SPDX-License-Identifier: MIT
"""
Glossary of the organisation: fixed translations of names and terms (bodies,
degrees, units, titles) that apply to every translation of an installation.

The glossary is a CSV file (UTF-8) maintained by the operator, configured with
glossary.organization_file. Each row holds one term for one language pair:

    source_lang,target_lang,term,translation
    de,en,Akademischer Senat,Academic Senate
    de,en,Studierendenservice,Student Services

Lines starting with '#' are comments. The file is re-read when it changes, so
edits take effect without a restart.

Precedence in a translation: entries the user types in the interface, then
this glossary, then the automatically collected terms. Only entries whose
term occurs in the document are used, so a large glossary does not enlarge
every request.
"""

import csv
import io
import logging
import re
import threading
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = ("source_lang", "target_lang", "term", "translation")


class OrganizationGlossary:
    """Loads the organisation glossary and answers per language pair."""

    def __init__(self, path: Optional[Path], delimiter: str = ","):
        self.path = Path(path) if path else None
        self.delimiter = delimiter or ","
        self._lock = threading.Lock()
        self._mtime: Optional[float] = None
        self._entries: Dict[Tuple[str, str], Dict[str, str]] = {}
        self.problems: list = []

    @property
    def configured(self) -> bool:
        return self.path is not None

    def _reload_if_changed(self):
        if self.path is None:
            return
        try:
            mtime = self.path.stat().st_mtime
        except FileNotFoundError:
            if self._mtime is not None or not self.problems:
                logger.warning(f"Organisation glossary not found: {self.path}")
            self._entries, self._mtime = {}, None
            self.problems = ["file not found"]
            return
        if mtime == self._mtime:
            return
        self._entries, self.problems = self._read(self.path.read_bytes())
        self._mtime = mtime
        count = sum(len(v) for v in self._entries.values())
        logger.info(f"Organisation glossary loaded: {count} entries in "
                    f"{len(self._entries)} language pair(s)")
        for problem in self.problems[:10]:
            logger.warning(f"Organisation glossary: {problem}")

    def _read(self, data: bytes):
        from .document_processing import decode_text

        entries: Dict[Tuple[str, str], Dict[str, str]] = {}
        problems = []
        text = decode_text(data)
        lines = [line for line in text.splitlines() if not line.lstrip().startswith('#')]
        reader = csv.DictReader(io.StringIO("\n".join(lines)), delimiter=self.delimiter)
        fields = [f.strip().lower() for f in (reader.fieldnames or [])]
        if not all(column in fields for column in REQUIRED_COLUMNS):
            return {}, [f"header must contain the columns {', '.join(REQUIRED_COLUMNS)}"]
        reader.fieldnames = fields
        for number, row in enumerate(reader, start=2):
            source = (row.get("source_lang") or "").strip().lower()
            target = (row.get("target_lang") or "").strip().lower()
            term = (row.get("term") or "").strip()
            translation = (row.get("translation") or "").strip()
            if not (source and target and term and translation):
                problems.append(f"row {number} incomplete, ignored")
                continue
            pair = entries.setdefault((source, target), {})
            if term in pair and pair[term] != translation:
                problems.append(f"row {number}: '{term}' defined twice, the later entry wins")
            pair[term] = translation
        return entries, problems

    def entries_for(self, source_lang: str, target_lang: str) -> Dict[str, str]:
        """All entries of a language pair; with automatic detection, all for the target."""
        with self._lock:
            self._reload_if_changed()
            target = (target_lang or "").lower()
            source = (source_lang or "").lower()
            if source and source != "auto":
                return dict(self._entries.get((source, target), {}))
            merged: Dict[str, str] = {}
            for (_, pair_target), pair in self._entries.items():
                if pair_target == target:
                    merged.update(pair)
            return merged

    def relevant_entries(self, source_lang: str, target_lang: str, text: str,
                         case_sensitive: bool = False) -> Dict[str, str]:
        """
        Entries of the language pair whose term occurs in the text.

        Inflected forms count as occurrences ("des Akademischen Senats" for
        "Akademischer Senat"): every word of the term is matched by its stem,
        i.e. without the last two letters for words longer than five letters.
        A term sent although it does not occur only costs a line in the prompt;
        a term missed would not be applied.
        """
        flags = 0 if case_sensitive else re.IGNORECASE
        return {term: translation
                for term, translation in self.entries_for(source_lang, target_lang).items()
                if _term_pattern(term, flags).search(text)}


@lru_cache(maxsize=20000)
def _term_pattern(term: str, flags: int) -> "re.Pattern":
    """Regular expression matching a term including inflected forms."""
    parts = []
    for word in term.split():
        stem = word[:-2] if len(word) > 5 else word
        tail = r'\w{0,4}' if len(word) > 5 else r'\w{0,2}'
        parts.append(re.escape(stem) + tail)
    return re.compile(r'(?<!\w)' + r'\s+'.join(parts) + r'(?!\w)', flags)
