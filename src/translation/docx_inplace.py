# SPDX-FileCopyrightText: 2026 Malte Dreyer
# SPDX-License-Identifier: MIT
"""
Translation inside the original Word file.

The translated document is the original .docx with only the text replaced:
styles, page layout, headers and footers, footnotes and endnotes, text boxes,
images, fields, comments and character formatting stay as they are. Body
text, tables, headers, footers, footnotes, endnotes and text boxes are
translated.

How a paragraph is sent to the model
------------------------------------
A paragraph is a sequence of runs. Consecutive runs with the same character
formatting form a *segment*. Hyperlinks, tracked insertions, content controls
and simple fields are segments of their own. Runs that are not plain text
(images, footnote references, complex fields, page breaks) are *atoms*.

    Das ist <s1>sehr</s1> wichtig<x2/>.

<sN>...</sN> carries the text of a formatted segment, <xN/> marks an atom,
<br/> a line break and <tab/> a tab. The model translates the text and keeps
the markers; the text of each segment is then written back into the first run
of that segment, so its formatting is kept. If the model moves markers, the
runs are moved accordingly. If the markers come back damaged, the paragraph
keeps its paragraph formatting and atoms, and its whole text goes into its
first segment (character formatting inside that paragraph is lost; this is
counted and reported).

Paragraphs are sent in groups, each wrapped in <pN>...</pN>, with the same
context, style reference and glossary as the text pipeline.
"""

import asyncio
import copy
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from lxml import etree

logger = logging.getLogger(__name__)

W_NS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
W = '{%s}' % W_NS
MC_FALLBACK = '{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback'
XML_SPACE = '{http://www.w3.org/XML/1998/namespace}space'

RT_FOOTNOTES = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes'
RT_ENDNOTES = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/endnotes'

# Run children that carry text (or nothing visible).
_TEXT_RUN_CHILDREN = {W + 'rPr', W + 't', W + 'tab', W + 'br', W + 'cr',
                      W + 'softHyphen', W + 'noBreakHyphen', W + 'lastRenderedPageBreak'}
# Containers that become one segment of their own.
_CONTAINERS = {W + 'hyperlink', W + 'ins', W + 'smartTag', W + 'sdt', W + 'fldSimple',
               W + 'customXml', W + 'moveTo'}
# Paragraph children that are left untouched (not part of any segment).
_SKIP = {W + 'pPr', W + 'del', W + 'moveFrom'}

_MARKER_RE = re.compile(r'<s(\d+)>(.*?)</s\1>|<x(\d+)\s*/>', re.DOTALL)
_PARAGRAPH_RE = re.compile(r'<p(\d+)>(.*?)</p\1>', re.DOTALL)
_ANY_TAG_RE = re.compile(r'</?(?:s|x|p)\d+\s*/?>')

MARKUP_INSTRUCTIONS = (
    "FORMAT OF THE TEXT: The text consists of paragraphs marked <pN>...</pN>. "
    "Translate every paragraph and return every paragraph with its marker, in "
    "the same order, each on its own line. Inside a paragraph, <sN>...</sN> "
    "marks text with its own formatting: translate the text inside and keep "
    "every such marker exactly once, around the words it belongs to (you may "
    "move it if the word order of the target language requires it). <xN/> "
    "stands for an object such as an image, a footnote reference or a field: "
    "keep it at the corresponding position. <br/> is a line break and <tab/> a "
    "tab: keep them. Do not add, remove, rename or nest markers. Output "
    "nothing but the marked paragraphs."
)


# --------------------------------------------------------------------------
# paragraph encoding
# --------------------------------------------------------------------------
@dataclass
class Segment:
    """Part of a paragraph: formatted text ('text') or an object ('atom')."""
    index: int
    kind: str
    units: List[Any] = field(default_factory=list)  # top-level elements of the paragraph
    text: str = ""


@dataclass
class EncodedParagraph:
    element: Any
    segments: List[Segment]
    encoded: str          # what the model sees
    plain: str            # visible text, for context, checks and display
    tagged: bool          # False: one plain text segment, no markers


def _run_text(run) -> str:
    """Text of a run, with <br/> and <tab/> tokens."""
    parts = []
    for child in run:
        if child.tag == W + 't':
            parts.append(child.text or "")
        elif child.tag == W + 'tab':
            parts.append("<tab/>")
        elif child.tag in (W + 'br', W + 'cr'):
            parts.append("<br/>")
        elif child.tag == W + 'noBreakHyphen':
            parts.append("-")
    return "".join(parts)


def _is_text_run(run) -> bool:
    for child in run:
        if child.tag not in _TEXT_RUN_CHILDREN:
            return False
        if child.tag == W + 'br' and child.get(W + 'type') in ('page', 'column'):
            return False
    return True


def _visible_runs(container) -> List[Any]:
    """Runs of a container that carry visible text (no deleted text)."""
    runs = []
    for run in container.iter(W + 'r'):
        ancestor, deleted = run.getparent(), False
        while ancestor is not None and ancestor is not container:
            if ancestor.tag in (W + 'del', W + 'moveFrom'):
                deleted = True
                break
            ancestor = ancestor.getparent()
        if not deleted:
            runs.append(run)
    return runs


def _container_is_text(container) -> bool:
    runs = _visible_runs(container)
    if not runs or not all(_is_text_run(r) for r in runs):
        return False
    # complex fields inside a container are not translated
    return not any(r.find(W + 'fldChar') is not None or r.find(W + 'instrText') is not None
                   for r in runs)


def _rpr_key(run) -> str:
    rpr = run.find(W + 'rPr')
    if rpr is None:
        return ""
    rpr = copy.deepcopy(rpr)
    # Proofing/revision marks do not change what the text looks like.
    for tag in ('lang', 'noProof', 'rPrChange'):
        for el in rpr.findall(W + tag):
            rpr.remove(el)
    return etree.tostring(rpr, method="c14n").decode()


def encode_paragraph(p, field_depth: int = 0) -> Tuple[Optional[EncodedParagraph], int]:
    """
    Split a paragraph into segments and build its marked text.

    field_depth is the nesting depth of complex fields at the start of the
    paragraph (fields can span paragraphs); the depth at the end is returned.
    Returns (None, depth) for paragraphs without translatable text.
    """
    segments: List[Segment] = []
    current: Optional[Segment] = None
    current_key = None
    field_atom: Optional[Segment] = None

    def new_segment(kind):
        seg = Segment(index=len(segments) + 1, kind=kind)
        segments.append(seg)
        return seg

    for child in p:
        tag = child.tag
        if tag in _SKIP or not isinstance(tag, str):
            continue

        if tag == W + 'r':
            fld = child.find(W + 'fldChar')
            fld_type = fld.get(W + 'fldCharType') if fld is not None else None
            if field_depth > 0 or fld_type == 'begin':
                # Complex field (TOC, page number, cross reference): kept as is.
                if field_atom is None:
                    field_atom = new_segment('atom')
                field_atom.units.append(child)
                if fld_type == 'begin':
                    field_depth += 1
                elif fld_type == 'end':
                    field_depth = max(0, field_depth - 1)
                if field_depth == 0:
                    field_atom = None
                current, current_key = None, None
                continue
            if _is_text_run(child):
                key = ('run', _rpr_key(child))
                if current is not None and current_key == key:
                    current.units.append(child)
                else:
                    current = new_segment('text')
                    current.units.append(child)
                    current_key = key
                continue
            seg = new_segment('atom')
            seg.units.append(child)
            current, current_key = None, None
            continue

        if tag in _CONTAINERS:
            seg = new_segment('text' if _container_is_text(child) else 'atom')
            seg.units.append(child)
            current, current_key = None, None
            continue

        # bookmarks, proofing marks, comment ranges, ...: neutral, stay in place

    for seg in segments:
        if seg.kind == 'text':
            runs = []
            for unit in seg.units:
                runs.extend([unit] if unit.tag == W + 'r' else _visible_runs(unit))
            seg.text = "".join(_run_text(r) for r in runs)

    text_segments = [s for s in segments if s.kind == 'text']
    plain = "".join(s.text for s in text_segments)
    plain_visible = plain.replace("<br/>", "\n").replace("<tab/>", "\t")
    if not re.search(r'[^\W\d_]', plain_visible):
        return None, field_depth  # nothing to translate (empty, numbers only)

    # Whitespace-only text segments are not sent; they stay as they are.
    meaningful = [s for s in segments if s.kind == 'atom' or s.text.strip()]
    if len(meaningful) == 1 and meaningful[0].kind == 'text' and len(text_segments) == 1:
        encoded = meaningful[0].text
        tagged = False
    else:
        parts = []
        for seg in segments:
            if seg.kind == 'atom':
                parts.append(f"<x{seg.index}/>")
            elif seg.text.strip():
                parts.append(f"<s{seg.index}>{seg.text}</s{seg.index}>")
            else:
                parts.append(seg.text)  # whitespace between markers
        encoded = "".join(parts)
        tagged = True

    return EncodedParagraph(element=p, segments=segments, encoded=encoded,
                            plain=plain_visible, tagged=tagged), field_depth


# --------------------------------------------------------------------------
# writing back
# --------------------------------------------------------------------------
def _segment_runs(seg: Segment) -> List[Any]:
    runs = []
    for unit in seg.units:
        runs.extend([unit] if unit.tag == W + 'r' else _visible_runs(unit))
    return runs


def _set_run_text(run, text: str):
    """Replace the text content of a run (keeps its formatting)."""
    for child in list(run):
        if child.tag in (W + 't', W + 'tab', W + 'br', W + 'cr', W + 'noBreakHyphen', W + 'softHyphen'):
            run.remove(child)
    for i, token in enumerate(re.split(r'(<br/>|<tab/>)', text)):
        if token == "<br/>":
            etree.SubElement(run, W + 'br')
        elif token == "<tab/>":
            etree.SubElement(run, W + 'tab')
        elif token:
            t = etree.SubElement(run, W + 't')
            t.text = token
            t.set(XML_SPACE, 'preserve')


def _write_segment(seg: Segment, text: str):
    runs = _segment_runs(seg)
    if not runs:
        return
    _set_run_text(runs[0], text)
    for run in runs[1:]:
        _set_run_text(run, "")


def parse_markers(encoded_paragraph: EncodedParagraph, answer: str):
    """
    Read the translated paragraph.

    Returns (order, texts) with the sequence of segment indices as answered and
    the text per segment, or None if the markers do not match the original.
    """
    expected_text = {s.index for s in encoded_paragraph.segments
                     if s.kind == 'text' and s.text.strip()}
    expected_atoms = {s.index for s in encoded_paragraph.segments if s.kind == 'atom'}
    order, texts, seen_text, seen_atoms = [], {}, [], []
    last_text_index = None
    position = 0
    pending_prefix = ""
    for match in _MARKER_RE.finditer(answer):
        outside = answer[position:match.start()]
        position = match.end()
        if outside.strip():
            if last_text_index is None:
                pending_prefix += outside
            else:
                texts[last_text_index] += outside
        if match.group(1):
            index = int(match.group(1))
            seen_text.append(index)
            texts[index] = pending_prefix + match.group(2)
            pending_prefix = ""
            last_text_index = index
        else:
            index = int(match.group(3))
            seen_atoms.append(index)
        order.append(index)
    tail = answer[position:]
    if tail.strip() and last_text_index is not None:
        texts[last_text_index] += tail
    if sorted(seen_text) != sorted(expected_text) or sorted(seen_atoms) != sorted(expected_atoms):
        return None
    if len(set(seen_text)) != len(seen_text) or len(set(seen_atoms)) != len(seen_atoms):
        return None
    if any(_ANY_TAG_RE.search(t) for t in texts.values()):
        return None
    return order, texts


def apply_translation(ep: EncodedParagraph, answer: str) -> bool:
    """
    Write the translation into the paragraph.

    Returns True if the formatting markers could be used, False if the
    fallback (whole text into the first segment) was applied.
    """
    answer = answer.strip("\n")
    if not ep.tagged:
        text_segments = [s for s in ep.segments if s.kind == 'text' and s.text.strip()]
        clean = _ANY_TAG_RE.sub('', answer)
        _write_segment(text_segments[0], clean)
        return True

    parsed = parse_markers(ep, answer)
    if parsed is None:
        clean = _ANY_TAG_RE.sub('', answer)
        text_segments = [s for s in ep.segments if s.kind == 'text' and s.text.strip()]
        _write_segment(text_segments[0], clean)
        for seg in text_segments[1:]:
            _write_segment(seg, "")
        return False

    order, texts = parsed
    by_index = {s.index: s for s in ep.segments}
    for index, text in texts.items():
        _write_segment(by_index[index], text)

    original_order = [s.index for s in ep.segments
                      if s.kind == 'atom' or (s.kind == 'text' and s.text.strip())]
    if order != original_order:
        _reorder(ep, order)
    return True


def _reorder(ep: EncodedParagraph, order: List[int]):
    """Move the elements of the segments into the answered order."""
    by_index = {s.index: s for s in ep.segments}
    moved = []
    for index in order:
        moved.extend(by_index[index].units)
    moved_ids = {id(u) for u in moved}
    p = ep.element
    children = list(p)
    first = next((i for i, c in enumerate(children) if id(c) in moved_ids), None)
    if first is None:
        return
    for child in children:
        if id(child) in moved_ids:
            p.remove(child)
    anchor = children[first - 1] if first > 0 else None
    insert_at = (list(p).index(anchor) + 1) if anchor is not None and anchor in list(p) else 0
    for offset, unit in enumerate(moved):
        p.insert(insert_at + offset, unit)


# --------------------------------------------------------------------------
# long paragraphs
# --------------------------------------------------------------------------
@dataclass
class Piece:
    """
    Part of a paragraph that is too long for one request.

    The pieces of a paragraph are translated separately; when all answers are
    there, they are joined and written back into the paragraph as one answer.
    A segment that had to be split appears in several pieces with the same
    marker; the parts are joined into one segment again.
    """
    parent: EncodedParagraph
    index: int
    count: int
    encoded: str
    plain: str
    space_before: bool = False   # the parent had whitespace at this boundary

    @property
    def element(self):
        return self.parent.element


_SENTENCE_SPLIT_RE = re.compile(r'(?<=[.!?:;。！？])\s+')
_TOKEN_RE = re.compile(r'<s(\d+)>(.*?)</s\1>|<x\d+\s*/>|[^<]+|<', re.DOTALL)


def _split_text(text: str, max_chars: int) -> List[str]:
    """Split text at sentence ends (else at spaces) into parts of at most max_chars."""
    if len(text) <= max_chars:
        return [text]
    sentences = _SENTENCE_SPLIT_RE.split(text)
    parts, current = [], ""
    for sentence in sentences:
        while len(sentence) > max_chars:  # a "sentence" without punctuation
            cut = sentence.rfind(" ", 0, max_chars)
            cut = cut if cut > 0 else max_chars
            if current:
                parts.append(current)
                current = ""
            parts.append(sentence[:cut])
            sentence = sentence[cut:].lstrip()
        if current and len(current) + 1 + len(sentence) > max_chars:
            parts.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}" if current else sentence
    if current:
        parts.append(current)
    return parts


def split_paragraph(ep: EncodedParagraph, max_chars: int) -> List[Piece]:
    """
    Split an encoded paragraph into pieces of at most about max_chars.

    Boundaries are placed between segments, and inside a segment only at
    sentence ends, so that every piece is complete text with complete markers.
    """
    if len(ep.encoded) <= max_chars:
        return []
    tokens: List[Tuple[str, Optional[int]]] = []   # (encoded text, segment index or None)
    if not ep.tagged:
        tokens = [(part, None) for part in _split_text(ep.encoded, max_chars)]
    else:
        for match in _TOKEN_RE.finditer(ep.encoded):
            if match.group(1):
                index = int(match.group(1))
                for part in _split_text(match.group(2), max_chars):
                    tokens.append((f"<s{index}>{part}</s{index}>", index))
            else:
                tokens.append((match.group(0), None))

    pieces_text: List[str] = []
    current = ""
    for text, _ in tokens:
        if current and len(current) + len(text) > max_chars:
            pieces_text.append(current)
            current = ""
        current += text if ep.tagged or not current else f" {text}"
    if current:
        pieces_text.append(current)
    if len(pieces_text) < 2:
        return []
    count = len(pieces_text)
    return [Piece(parent=ep, index=i, count=count, encoded=text,
                  plain=_ANY_TAG_RE.sub('', text).replace("<br/>", "\n").replace("<tab/>", "\t"),
                  space_before=i > 0)
            for i, text in enumerate(pieces_text)]


def join_piece_answers(answers: List[str], tagged: bool) -> str:
    """Join the translated pieces of one paragraph into one answer."""
    if not tagged:
        return " ".join(a.strip() for a in answers)
    joined = ""
    for answer in answers:
        answer = answer.strip("\n")
        if joined and not joined[-1].isspace() and answer and not answer[0].isspace() \
                and not re.match(r'<s(\d+)>', answer):
            joined += " "
        joined += answer
    # A segment split across pieces: "...</s3><s3>..." becomes one segment.
    return re.sub(r'\s*</s(\d+)>\s*<s\1>\s*', ' ', joined)


# --------------------------------------------------------------------------
# document traversal
# --------------------------------------------------------------------------
@dataclass
class Unit:
    """A group of paragraphs translated in one request."""
    paragraphs: List[Any]   # EncodedParagraph, or Piece of a long paragraph
    kind: str               # 'body', 'cell', 'row', 'header', 'note'
    heading: Optional[str] = None
    column: Optional[str] = None


def _is_heading(p) -> bool:
    ppr = p.find(W + 'pPr')
    if ppr is None:
        return False
    style = ppr.find(W + 'pStyle')
    if style is not None:
        value = (style.get(W + 'val') or '').lower()
        if value.startswith(('heading', 'berschrift', 'title', 'titel')):
            return True
    return ppr.find(W + 'outlineLvl') is not None


def _in_fallback(p) -> bool:
    ancestor = p.getparent()
    while ancestor is not None:
        if ancestor.tag == MC_FALLBACK:
            return True
        ancestor = ancestor.getparent()
    return False


def _cell_of(p):
    ancestor = p.getparent()
    while ancestor is not None:
        if ancestor.tag == W + 'tc':
            return ancestor
        if ancestor.tag in (W + 'body', W + 'hdr', W + 'ftr'):
            return None
        ancestor = ancestor.getparent()
    return None


def collect_paragraphs(root) -> List[Tuple[Any, EncodedParagraph]]:
    """All translatable paragraphs below root, in document order."""
    result = []
    depth = 0
    for p in root.iter(W + 'p'):
        encoded, depth = encode_paragraph(p, depth)
        if encoded is not None:
            result.append((p, encoded))
    return result


def _cell_text(tc) -> str:
    return " ".join("".join(t.text or "" for t in p.iter(W + 't')) for p in tc.iter(W + 'p')).strip()


def build_units(root, kind: str, table_mode: str, max_chars: int) -> List[Unit]:
    """
    Group the paragraphs below root into requests.

    Running text is grouped up to max_chars per request. Table cells are
    translated per cell ("cell") or per row ("row"), with the column header as
    context. Paragraphs of text boxes and of fallback copies are separate
    requests of their own kind; identical paragraphs are answered from a cache.
    """
    units: List[Unit] = []
    current: List[EncodedParagraph] = []
    current_chars = 0
    heading = None

    def flush():
        nonlocal current, current_chars
        if current:
            units.append(Unit(paragraphs=current, kind=kind, heading=heading))
        current, current_chars = [], 0

    row_units: Dict[int, Unit] = {}
    for p, ep in collect_paragraphs(root):
        tc = _cell_of(p)
        if tc is not None:
            flush()
            tr = tc.getparent()
            table = tr.getparent()
            column = None
            rows = table.findall(W + 'tr')
            if rows and tr is not rows[0]:
                cells = tr.findall(W + 'tc')
                header_cells = rows[0].findall(W + 'tc')
                position = cells.index(tc) if tc in cells else -1
                if 0 <= position < len(header_cells):
                    column = _cell_text(header_cells[position]) or None
            if table_mode == 'row':
                unit = row_units.get(id(tr))
                if unit is None:
                    unit = Unit(paragraphs=[], kind='row', heading=heading)
                    row_units[id(tr)] = unit
                    units.append(unit)
                unit.paragraphs.append(ep)
            else:
                units.append(Unit(paragraphs=[ep], kind='cell', heading=heading, column=column))
            continue
        if _is_heading(p):
            flush()
            heading = ep.plain.strip()[:200] or heading
        pieces = split_paragraph(ep, max_chars)
        if pieces:
            # Too long for one request: every piece is a request of its own.
            flush()
            for piece in pieces:
                units.append(Unit(paragraphs=[piece], kind=kind, heading=heading))
            continue
        if current and current_chars + len(ep.encoded) > max_chars:
            flush()
        current.append(ep)
        current_chars += len(ep.encoded)
    flush()
    return units


def note_roots(document) -> List[Tuple[Any, Any]]:
    """(part, root element) of the footnotes and endnotes parts, if present."""
    roots = []
    for rel in document.part.rels.values():
        if rel.reltype in (RT_FOOTNOTES, RT_ENDNOTES) and not rel.is_external:
            part = rel.target_part
            element = getattr(part, 'element', None) if hasattr(part, '_element') else None
            if element is None:
                element = etree.fromstring(part.blob)
            roots.append((part, element))
    return roots


def header_footer_roots(document) -> List[Any]:
    """Root elements of all header and footer parts, each once."""
    roots, seen = [], set()
    for section in document.sections:
        for name in ('header', 'first_page_header', 'even_page_header',
                     'footer', 'first_page_footer', 'even_page_footer'):
            hf = getattr(section, name)
            if hf.is_linked_to_previous:
                continue
            part = hf.part
            if id(part) in seen:
                continue
            seen.add(id(part))
            roots.append(part.element)
    return roots


def _skip_separator_notes(root):
    """Footnote separators are not text."""
    for note in list(root):
        if note.get(W + 'type') in ('separator', 'continuationSeparator', 'continuationNotice'):
            for p in note.iter(W + 'p'):
                p.set('{urn:translate-tool}skip', '1')


# --------------------------------------------------------------------------
# translation
# --------------------------------------------------------------------------
@dataclass
class InPlaceResult:
    output_path: Path
    paragraphs: int = 0
    requests: int = 0
    failed_paragraphs: int = 0
    formatting_fallbacks: int = 0
    sections: List[Dict[str, Any]] = field(default_factory=list)  # for the checks


class DocxInPlaceTranslator:
    """Translates a .docx file inside its own structure."""

    def __init__(self, engine, config):
        self.engine = engine
        self.client = engine.llm_client
        self.config = config
        translation = getattr(config, 'translation', None)
        self.table_mode = getattr(translation, 'table_mode', 'cell')
        chunking = getattr(config, 'simple_chunking', None)
        self.max_tokens = getattr(chunking, 'max_tokens', 700)
        self.max_chars = int(self.max_tokens * 3.5)  # set per source language in translate_file
        self._cache: Dict[Tuple[str, str], str] = {}
        # Answers of pieces of long paragraphs, per paragraph (by id).
        self._piece_answers: Dict[int, Dict[int, str]] = {}
        self._failed_parents: set = set()

    async def translate_file(
        self,
        source: Path,
        target: Path,
        source_lang: str,
        target_lang: str,
        style: str,
        formality: str,
        glossary: Dict[str, str],
        progress: Optional[Callable[[int, int], None]] = None,
        partial: Optional[Callable[[str], None]] = None,
    ) -> InPlaceResult:
        from docx import Document
        from .translation_engine import TranslationContext

        result = InPlaceResult(output_path=target)
        # Same section size as the text pipeline: max_tokens of
        # simple_chunking, converted with the source language's characters
        # per token.
        chars_per_token = 3.5
        if hasattr(self.config, 'get_chars_per_token'):
            try:
                chars_per_token = float(self.config.get_chars_per_token(source_lang))
            except Exception:
                pass
        self.max_chars = max(200, int(self.max_tokens * chars_per_token))

        def load():
            """Parse the file and build the requests (CPU-bound, runs in a thread)."""
            document = Document(str(source))
            units: List[Unit] = build_units(document.element.body, 'body', self.table_mode, self.max_chars)
            for root in header_footer_roots(document):
                units += build_units(root, 'header', self.table_mode, self.max_chars)
            notes = note_roots(document)
            for part, root in notes:
                _skip_separator_notes(root)
                for unit in build_units(root, 'note', self.table_mode, self.max_chars):
                    unit.paragraphs = [ep for ep in unit.paragraphs
                                       if ep.element.get('{urn:translate-tool}skip') != '1']
                    if unit.paragraphs:
                        units.append(unit)
            for _, root in notes:
                for p in root.iter(W + 'p'):
                    p.attrib.pop('{urn:translate-tool}skip', None)
            return document, units, notes

        # Parsing and saving large documents takes seconds; it must not block
        # the event loop that serves all sessions.
        document, units, notes = await asyncio.to_thread(load)

        result.paragraphs = len({id(getattr(ep, 'parent', ep)) for u in units for ep in u.paragraphs})
        all_plain = [ep.plain for u in units for ep in u.paragraphs]
        done = 0
        translated_plain: Dict[int, str] = {}

        def base_context(unit: Unit, index: int, reference: Optional[str]) -> "TranslationContext":
            context = TranslationContext(
                glossary=glossary or {},
                style=style,
                formality=formality,
                source_language=source_lang,
                target_language=target_lang,
                current_section=unit.heading,
                style_reference=reference,
            )
            flat_index = sum(len(u.paragraphs) for u in units[:index])
            before = " ".join(all_plain[max(0, flat_index - 3):flat_index])[-300:]
            after = " ".join(all_plain[flat_index + len(unit.paragraphs):
                                       flat_index + len(unit.paragraphs) + 3])[:300]
            if before:
                context.metadata['preceding_text'] = before
            if after:
                context.metadata['following_text'] = after
            return context

        async def run(index: int, reference: Optional[str]):
            nonlocal done
            unit = units[index]
            context = base_context(unit, index, reference)
            extra = ""
            if unit.kind == 'cell':
                extra = "TABLE CELL - translate the content of one table cell."
                if unit.column:
                    extra += f" Column: {unit.column}"
            elif unit.kind == 'row':
                extra = "TABLE ROW - every paragraph is one cell of the same table row."
            elif unit.kind == 'note':
                extra = "FOOTNOTES/ENDNOTES of the document."
            elif unit.kind == 'header':
                extra = "PAGE HEADER/FOOTER of the document."
            await self._translate_unit(unit, context, extra, result)
            translated_plain[index] = "\n".join(self._current_plain(ep) for ep in unit.paragraphs)
            done += 1
            if progress:
                progress(done, len(units))
            if partial:
                partial("\n\n".join(translated_plain[i] for i in sorted(translated_plain)))

        mode = self.engine._context_mode()
        reference_chars = self.engine._style_reference_chars()
        first_body = next((i for i, u in enumerate(units) if u.kind == 'body'), None)
        semaphore = asyncio.Semaphore(max(1, getattr(self.engine, 'max_workers', 3)))

        async def limited(index, reference):
            async with semaphore:
                await run(index, reference)

        if mode == 'sequential' and reference_chars > 0:
            reference = None
            for i, unit in enumerate(units):
                await run(i, reference if unit.kind == 'body' else None)
                if unit.kind == 'body':
                    reference = self._reference_from(unit, reference_chars) or reference
        elif mode == 'two_wave' and reference_chars > 0 and first_body is not None:
            await run(first_body, None)
            reference = self._reference_from(units[first_body], reference_chars)
            await asyncio.gather(*(limited(i, reference if units[i].kind == 'body' else None)
                                   for i in range(len(units)) if i != first_body))
        else:
            await asyncio.gather(*(limited(i, None) for i in range(len(units))))

        def save():
            # Parts that python-docx keeps as raw bytes get their new XML.
            for part, root in notes:
                if not hasattr(part, '_element'):
                    part._blob = etree.tostring(root, xml_declaration=True,
                                                encoding='UTF-8', standalone=True)
            document.save(str(target))

        await asyncio.to_thread(save)
        return result

    def _reference_from(self, unit: Unit, limit: int) -> Optional[str]:
        text = "\n".join(self._current_plain(ep) for ep in unit.paragraphs).strip()
        return text[-limit:] if text else None

    @staticmethod
    def _current_plain(ep) -> str:
        return "".join(t.text or "" for t in ep.element.iter(W + 't'))

    async def _translate_unit(self, unit: Unit, context, extra: str, result: InPlaceResult):
        """Translate one group of paragraphs; failed paragraphs are retried alone."""
        pending = []
        for ep in unit.paragraphs:
            cached = self._cache.get((ep.encoded, unit.kind))
            if cached is not None:
                self._apply(ep, cached, result)
            else:
                pending.append(ep)
        if not pending:
            return

        answers = await self._request(pending, context, extra, result)
        retry = []
        for number, ep in enumerate(pending, 1):
            answer = answers.get(number) if answers else None
            if answer is None:
                retry.append(ep)
                continue
            self._cache[(ep.encoded, unit.kind)] = answer
            self._apply(ep, answer, result)

        for ep in retry:
            single = await self._request([ep], context, extra, result) if len(pending) > 1 else None
            answer = single.get(1) if single else None
            if answer is None:
                parent = getattr(ep, 'parent', ep)
                if id(parent) not in self._failed_parents:
                    self._failed_parents.add(id(parent))
                    result.failed_paragraphs += 1
                logger.warning(f"Paragraph kept untranslated ({len(ep.plain)} chars)")
                continue
            self._cache[(ep.encoded, unit.kind)] = answer
            self._apply(ep, answer, result)

    def _apply(self, ep, answer: str, result: InPlaceResult):
        if isinstance(ep, Piece):
            answers = self._piece_answers.setdefault(id(ep.parent), {})
            answers[ep.index] = answer
            if len(answers) < ep.count or id(ep.parent) in self._failed_parents:
                return
            answer = join_piece_answers([answers[i] for i in range(ep.count)], ep.parent.tagged)
            ep = ep.parent
        original = ep.plain
        if not apply_translation(ep, answer):
            result.formatting_fallbacks += 1
        result.sections.append({
            'original_content': original,
            'translated_content': self._current_plain(ep),
            'position': len(result.sections),
        })

    async def _request(self, paragraphs: List[EncodedParagraph], context, extra: str,
                       result: InPlaceResult) -> Optional[Dict[int, str]]:
        text = "\n".join(f"<p{i}>{ep.encoded}</p{i}>" for i, ep in enumerate(paragraphs, 1))
        prompt_context = context.to_prompt_context()
        if extra:
            prompt_context = f"{prompt_context}\n\n{extra}" if prompt_context else extra
        result.requests += 1
        try:
            answer = await self.client.translate_text(
                text=text,
                source_lang=context.source_language,
                target_lang=context.target_language,
                style=context.style,
                context=prompt_context,
                glossary=context.glossary,
                formality=context.formality,
                extra_instructions=MARKUP_INSTRUCTIONS,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"Request for {len(paragraphs)} paragraph(s) failed: {type(e).__name__}: {e}")
            return None
        answer = self.engine._strip_reasoning(answer or "")
        found = {int(m.group(1)): m.group(2) for m in _PARAGRAPH_RE.finditer(answer)}
        if not found and len(paragraphs) == 1 and answer.strip():
            # A single paragraph answered without its <p1> marker.
            found = {1: answer.strip()}
        return found
