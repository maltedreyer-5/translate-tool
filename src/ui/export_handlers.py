# SPDX-FileCopyrightText: 2026 Malte Dreyer
# SPDX-License-Identifier: MIT
"""
Export handlers for translated documents (Word, Markdown, HTML, JSON),
with table structure preserved in all formats.
"""

import gradio as gr
from typing import Optional, Dict, Any, Tuple, List
from pathlib import Path
import tempfile
import logging
from datetime import datetime
import json
import re
from bs4 import BeautifulSoup

from ..markdown_utils import is_table_separator, split_table_row, is_valid_table_lines

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Inline Markdown parsing (shared by the Word export)
# ---------------------------------------------------------------------------
# Ordered by precedence. Named groups let us tell which construct matched.
_INLINE_PATTERN = re.compile(
    r"""
      (?P<escape>\\[\\`*_{}\[\]()#+\-.!~|>])          # backslash-escaped char
    | (?P<code>`+)(?P<code_body>.+?)(?P=code)         # `inline code`
    | (?P<bolditalic>\*\*\*)(?P<bi_body>.+?)\*\*\*    # ***bold italic***
    | (?P<bold>\*\*)(?P<bold_body>.+?)\*\*            # **bold**
    | (?<![\w])(?P<boldu>__)(?P<boldu_body>.+?)__(?!\w)      # __bold__ (word boundary)
    | (?P<strike>~~)(?P<strike_body>.+?)~~            # ~~strikethrough~~
    | (?P<italic>\*)(?P<italic_body>.+?)\*            # *italic*
    | (?<![\w])(?P<italicu>_)(?P<italicu_body>.+?)_(?!\w)    # _italic_ (word boundary)
    | (?P<link>\[(?P<link_text>[^\]]+)\]\((?P<link_url>[^)\s]+)\))  # [text](url)
    """,
    re.VERBOSE | re.DOTALL,
)

# Characters the source-side converter may have backslash-escaped.
_ESCAPABLE = set("\\`*_{}[]()#+-.!~|>")


def parse_inline_markdown(text: str, style: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """
    Parse inline markdown into styled segments.

    Returns a flat list of segments:
        {'text': str, 'bold': bool, 'italic': bool,
         'code': bool, 'strike': bool, 'link': Optional[str]}

    Handles nested emphasis (e.g. **bold with *italic***), inline code, links
    and backslash escapes, so the Word export contains real formatting
    instead of raw markdown.
    """
    if style is None:
        style = {'bold': False, 'italic': False, 'code': False,
                 'strike': False, 'link': None}

    segments: List[Dict[str, Any]] = []
    if not text:
        return segments

    def add_plain(chunk: str):
        if chunk:
            segments.append({**style, 'text': chunk})

    pos = 0
    for m in _INLINE_PATTERN.finditer(text):
        if m.start() > pos:
            add_plain(text[pos:m.start()])
        pos = m.end()

        if m.group('escape') is not None:
            # Emit the escaped character literally (drop the backslash).
            add_plain(m.group('escape')[1])
        elif m.group('code') is not None:
            segments.append({**style, 'code': True, 'text': m.group('code_body')})
        elif m.group('bolditalic') is not None:
            segments.extend(parse_inline_markdown(
                m.group('bi_body'), {**style, 'bold': True, 'italic': True}))
        elif m.group('bold') is not None:
            segments.extend(parse_inline_markdown(
                m.group('bold_body'), {**style, 'bold': True}))
        elif m.group('boldu') is not None:
            segments.extend(parse_inline_markdown(
                m.group('boldu_body'), {**style, 'bold': True}))
        elif m.group('strike') is not None:
            segments.extend(parse_inline_markdown(
                m.group('strike_body'), {**style, 'strike': True}))
        elif m.group('italic') is not None:
            segments.extend(parse_inline_markdown(
                m.group('italic_body'), {**style, 'italic': True}))
        elif m.group('italicu') is not None:
            segments.extend(parse_inline_markdown(
                m.group('italicu_body'), {**style, 'italic': True}))
        elif m.group('link') is not None:
            segments.extend(parse_inline_markdown(
                m.group('link_text'), {**style, 'link': m.group('link_url')}))

    if pos < len(text):
        add_plain(text[pos:])

    return segments


def strip_markdown_escapes(text: str) -> str:
    """Remove backslash escapes (\\* -> *) without interpreting markdown."""
    out = []
    i = 0
    while i < len(text):
        if text[i] == '\\' and i + 1 < len(text) and text[i + 1] in _ESCAPABLE:
            out.append(text[i + 1])
            i += 2
        else:
            out.append(text[i])
            i += 1
    return ''.join(out)


class TableExportHandler:
    """Specialized handler for table export across different formats."""

    @staticmethod
    def validate_markdown_table(content: str) -> bool:
        """Validate if content contains a proper markdown table."""
        lines = [line.strip() for line in content.split('\n') if line.strip()]
        # Shared definition, see src/markdown_utils.py.
        return is_valid_table_lines(lines)

    @staticmethod
    def extract_table_data(table_markdown: str) -> Dict[str, Any]:
        """Extract structured data from markdown table."""
        lines = [line.strip() for line in table_markdown.split('\n') if line.strip()]

        if len(lines) < 3:
            return {'headers': [], 'rows': [], 'valid': False}

        try:
            # Parse header
            header_line = lines[0]
            headers = TableExportHandler._parse_table_row(header_line)

            # Skip separator line (lines[1])

            # Parse data rows
            rows = []
            for line in lines[2:]:
                if line.strip():
                    row_data = TableExportHandler._parse_table_row(line)
                    if row_data:
                        # Ensure row has same number of columns as headers
                        while len(row_data) < len(headers):
                            row_data.append('')
                        if len(row_data) > len(headers):
                            row_data = row_data[:len(headers)]
                        rows.append(row_data)

            return {
                'headers': headers,
                'rows': rows,
                'valid': True,
                'column_count': len(headers),
                'row_count': len(rows)
            }

        except Exception as e:
            logger.error(f"Error extracting table data: {e}")
            return {'headers': [], 'rows': [], 'valid': False}

    @staticmethod
    def _parse_table_row(line: str) -> List[str]:
        """Parse a table row into cells."""
        return split_table_row(line)

    @staticmethod
    def create_html_table(headers: List[str], rows: List[List[str]], table_class: str = "export-table") -> str:
        """Create properly formatted HTML table."""
        html_parts = [f'<table class="{table_class}">']

        # Create thead
        if headers:
            html_parts.append('<thead>')
            html_parts.append('<tr>')
            for header in headers:
                html_parts.append(f'<th>{TableExportHandler._escape_html(header)}</th>')
            html_parts.append('</tr>')
            html_parts.append('</thead>')

        # Create tbody
        if rows:
            html_parts.append('<tbody>')
            for row in rows:
                html_parts.append('<tr>')
                for cell in row:
                    html_parts.append(f'<td>{TableExportHandler._escape_html(cell)}</td>')
                html_parts.append('</tr>')
            html_parts.append('</tbody>')

        html_parts.append('</table>')
        return '\n'.join(html_parts)

    @staticmethod
    def _escape_html(text: str) -> str:
        """Escape HTML special characters."""
        return (text.replace('&', '&amp;')
                   .replace('<', '&lt;')
                   .replace('>', '&gt;')
                   .replace('"', '&quot;')
                   .replace("'", '&#39;'))


class EnhancedExportHandler:
    """
    Export handler with table support for all formats.
    """

    def __init__(self, temp_dir: Optional[Path] = None, include_word_header: bool = False):
        """
        Initialize export handler

        Args:
            temp_dir: Optional temporary directory for exports
            include_word_header: Prepend an information block (languages,
                style, date) to Word exports (export.word.include_header)
        """
        self.include_word_header = include_word_header
        self.temp_dir = temp_dir or Path(tempfile.mkdtemp())
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.table_handler = TableExportHandler()

    def _new_export_dir(self) -> Path:
        """
        Separate subdirectory per export.

        The handler (and with it temp_dir) is shared by all sessions, and
        default file names only have a resolution of one second. Without a
        directory of its own, simultaneous exports would overwrite each other.
        """
        return Path(tempfile.mkdtemp(dir=self.temp_dir))

    def export_markdown(
        self,
        content: str,
        filename: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Export content as Markdown file with enhanced table validation

        Args:
            content: Markdown content to export
            filename: Optional filename (without extension)
            metadata: Optional metadata to include as YAML frontmatter

        Returns:
            Path to exported file
        """
        try:
            # Generate filename if not provided
            if not filename:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"translation_{timestamp}"

            # Validate and fix tables in content
            content = self._validate_and_fix_tables(content)

            # Add frontmatter if metadata provided
            if metadata:
                frontmatter = self._generate_yaml_frontmatter(metadata)
                content = f"{frontmatter}\n{content}"

            # Write file
            file_path = self._new_export_dir() / f"{filename}.md"
            file_path.write_text(content, encoding='utf-8')

            logger.info(f"Exported Markdown to {file_path}")
            return str(file_path)

        except Exception as e:
            logger.error(f"Markdown export failed: {e}")
            raise

    def export_word(
        self,
        content: str,
        filename: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        lang: Optional[str] = None
    ) -> str:
        """
        Export content as a Word document.

        Args:
            content: Content to export (Markdown format)
            filename: Optional filename (without extension)
            metadata: Optional metadata to include
            lang: interface language for the optional information block

        Returns:
            Path to exported file
        """
        try:
            # Check for python-docx availability and import with proper error handling
            try:
                from docx import Document
                from docx.shared import Pt, Inches
                from docx.enum.text import WD_ALIGN_PARAGRAPH
                from docx.enum.table import WD_TABLE_ALIGNMENT
            except ImportError as import_error:
                logger.error(f"python-docx not available: {import_error}")
                raise ImportError("python-docx library is required for Word export. Install with: pip install python-docx")

            # Generate filename
            if not filename:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"translation_{timestamp}"

            # Create Word document
            doc = Document()

            # Document properties are always set; the visible information
            # block only on request (export.word.include_header).
            if metadata:
                self._add_metadata_to_word_doc(doc, metadata, WD_ALIGN_PARAGRAPH,
                                               include_header=self.include_word_header,
                                               lang=lang)

            # Process content with improved table handling
            self._process_content_for_word(doc, content)

            # Save document
            file_path = self._new_export_dir() / f"{filename}.docx"
            doc.save(str(file_path))

            logger.info(f"Exported Word document to {file_path}")
            return str(file_path)

        except ImportError as e:
            logger.error(f"Word export dependency missing: {e}")
            raise
        except Exception as e:
            logger.error(f"Word export failed: {e}")
            raise

    def _process_content_for_word(self, doc, content: str):
        """Process markdown content for Word with full block + inline handling."""
        blocks = self._split_content_into_blocks(content)

        for block in blocks:
            block_type = block['type']

            if block_type == 'table':
                self._add_enhanced_table_to_word(doc, block['content'])
            elif block_type == 'heading':
                self._add_heading_to_word(doc, block['content'], block.get('level', 1))
            elif block_type == 'list':
                self._add_list_to_word(doc, block['items'])
            elif block_type == 'code':
                self._add_code_to_word(doc, block['content'])
            elif block_type == 'quote':
                self._add_quote_to_word(doc, block['content'])
            elif block_type == 'hr':
                self._add_horizontal_rule_to_word(doc)
            else:  # paragraph
                self._add_paragraph_to_word(doc, block['content'])

    # ------------------------------------------------------------------
    # Block splitting
    # ------------------------------------------------------------------
    @staticmethod
    def _is_table_separator(line: str) -> bool:
        """True if the line is a markdown table separator (|---|:--:|...)."""
        return is_table_separator(line)

    @staticmethod
    def _is_horizontal_rule(line: str) -> bool:
        """True for --- / *** / ___ horizontal rules (3+ chars, no other text)."""
        s = line.strip()
        return bool(re.match(r'^(\*\s*){3,}$|^(-\s*){3,}$|^(_\s*){3,}$', s)) and '|' not in s

    @staticmethod
    def _strip_frontmatter(content: str) -> str:
        """Remove a leading YAML frontmatter block so it does not leak into Word."""
        if content.startswith('---'):
            m = re.match(r'^---\s*\n.*?\n---\s*(\n|$)', content, re.DOTALL)
            if m:
                return content[m.end():]
        return content

    def _split_content_into_blocks(self, content: str) -> List[Dict[str, Any]]:
        """Split markdown into logical blocks (headings, lists, tables, ...)."""
        content = self._strip_frontmatter(content)
        blocks: List[Dict[str, Any]] = []
        lines = content.split('\n')

        i = 0
        n = len(lines)
        while i < n:
            raw = lines[i]
            line = raw.strip()

            if not line:
                i += 1
                continue

            # Table: current line has a pipe and the NEXT line is a separator.
            if '|' in line and i + 1 < n and self._is_table_separator(lines[i + 1]):
                table_lines = [raw]
                i += 1
                while i < n and lines[i].strip() and '|' in lines[i]:
                    table_lines.append(lines[i])
                    i += 1
                blocks.append({'type': 'table', 'content': '\n'.join(table_lines)})
                continue

            # Horizontal rule (checked before headings/lists; not a table row).
            if self._is_horizontal_rule(line):
                blocks.append({'type': 'hr'})
                i += 1
                continue

            # Heading.
            if line.startswith('#'):
                level = len(line) - len(line.lstrip('#'))
                text = line.lstrip('#').strip()
                blocks.append({'type': 'heading', 'content': text, 'level': max(1, level)})
                i += 1
                continue

            # Fenced code block.
            if line.startswith('```'):
                code_lines = [raw]
                i += 1
                while i < n:
                    code_lines.append(lines[i])
                    if lines[i].strip().startswith('```'):
                        i += 1
                        break
                    i += 1
                blocks.append({'type': 'code', 'content': '\n'.join(code_lines)})
                continue

            # Block quote.
            if line.startswith('>'):
                quote_lines = []
                while i < n and lines[i].strip().startswith('>'):
                    quote_lines.append(lines[i].strip().lstrip('>').strip())
                    i += 1
                blocks.append({'type': 'quote', 'content': '\n'.join(quote_lines)})
                continue

            # List: collect consecutive item lines. A blank line ends the list,
            # so an unordered list followed by an ordered one stays separate.
            list_match = re.match(r'^(\s*)([-*+]|\d+[.)])\s+(.*)$', raw)
            if list_match:
                items = []
                while i < n:
                    m = re.match(r'^(\s*)([-*+]|\d+[.)])\s+(.*)$', lines[i])
                    if not m:
                        break
                    indent = len(m.group(1).replace('\t', '  '))
                    level = indent // 2  # 2 spaces == one nesting level
                    ordered = bool(re.match(r'^\d+[.)]$', m.group(2)))
                    items.append({'text': m.group(3).strip(),
                                  'level': level, 'ordered': ordered})
                    i += 1
                blocks.append({'type': 'list', 'items': items})
                continue

            # Paragraph: gather wrapped continuation lines.
            para_lines = [line]
            i += 1
            while i < n:
                nxt = lines[i].strip()
                if (not nxt or nxt.startswith('#') or nxt.startswith('```')
                        or nxt.startswith('>')
                        or ('|' in nxt and i + 1 < n and self._is_table_separator(lines[i + 1]))
                        or self._is_horizontal_rule(nxt)
                        or re.match(r'^(\s*)([-*+]|\d+[.)])\s+', lines[i])):
                    break
                para_lines.append(nxt)
                i += 1
            blocks.append({'type': 'paragraph', 'content': ' '.join(para_lines).strip()})

        return blocks

    # ------------------------------------------------------------------
    # Inline rendering helpers
    # ------------------------------------------------------------------
    def _render_inline(self, paragraph, text: str, force_bold: bool = False):
        """Render inline markdown into an existing paragraph as styled runs."""
        from docx.shared import Pt, RGBColor

        segments = parse_inline_markdown(text)
        if not segments:
            # Nothing parsed (e.g. empty) -> still drop any stray escapes.
            paragraph.add_run(strip_markdown_escapes(text))
            return

        for seg in segments:
            if seg.get('link'):
                self._add_hyperlink(paragraph, seg['text'], seg['link'])
                continue
            run = paragraph.add_run(seg['text'])
            if seg['bold'] or force_bold:
                run.bold = True
            if seg['italic']:
                run.italic = True
            if seg['strike']:
                run.font.strike = True
            if seg['code']:
                run.font.name = 'Courier New'
                run.font.size = Pt(10)

    @staticmethod
    def _add_hyperlink(paragraph, text: str, url: str):
        """Add a real clickable hyperlink run to a paragraph."""
        try:
            from docx.oxml.shared import OxmlElement, qn
            from docx.shared import RGBColor

            part = paragraph.part
            r_id = part.relate_to(
                url,
                "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
                is_external=True,
            )
            hyperlink = OxmlElement('w:hyperlink')
            hyperlink.set(qn('r:id'), r_id)

            new_run = OxmlElement('w:r')
            rpr = OxmlElement('w:rPr')
            color = OxmlElement('w:color')
            color.set(qn('w:val'), '0563C1')
            rpr.append(color)
            u = OxmlElement('w:u')
            u.set(qn('w:val'), 'single')
            rpr.append(u)
            new_run.append(rpr)

            t = OxmlElement('w:t')
            t.text = text
            new_run.append(t)
            hyperlink.append(new_run)
            paragraph._p.append(hyperlink)
        except Exception as e:
            logger.debug(f"Hyperlink creation failed, using plain text: {e}")
            paragraph.add_run(f"{text} ({url})")

    # ------------------------------------------------------------------
    # Block renderers
    # ------------------------------------------------------------------
    def _add_enhanced_table_to_word(self, doc, table_content: str):
        """Add table to Word document with inline-formatted cells."""
        try:
            from docx.enum.table import WD_TABLE_ALIGNMENT

            table_data = self.table_handler.extract_table_data(table_content)

            if not table_data['valid'] or not table_data['headers']:
                logger.warning("Invalid table data, adding as text")
                self._add_paragraph_to_word(doc, table_content)
                return

            headers = table_data['headers']
            rows = table_data['rows']

            table = doc.add_table(rows=len(rows) + 1, cols=len(headers))
            table.style = 'Table Grid'
            table.alignment = WD_TABLE_ALIGNMENT.LEFT

            header_row = table.rows[0]
            for c, header in enumerate(headers):
                cell = header_row.cells[c]
                cell.text = ''  # clear default empty paragraph run
                self._render_inline(cell.paragraphs[0], str(header), force_bold=True)

            for r, row_data in enumerate(rows):
                word_row = table.rows[r + 1]
                for c, cell_data in enumerate(row_data):
                    if c < len(word_row.cells):
                        cell = word_row.cells[c]
                        cell.text = ''
                        self._render_inline(cell.paragraphs[0], str(cell_data))

            doc.add_paragraph()
            logger.info(f"Added table: {len(headers)} columns, {len(rows)} rows")

        except Exception as e:
            logger.error(f"Enhanced table creation failed: {e}")
            self._add_paragraph_to_word(doc, table_content)

    def _add_heading_to_word(self, doc, content: str, level: int):
        """Add heading with inline formatting (and no leaked escapes)."""
        heading = doc.add_heading('', level=min(max(level, 1), 9))
        self._render_inline(heading, content)

    def _add_paragraph_to_word(self, doc, content: str):
        """Add paragraph with inline formatting."""
        if content.strip():
            para = doc.add_paragraph()
            self._render_inline(para, content)

    def _add_list_to_word(self, doc, items: List[Dict[str, Any]]):
        """Add a list, preserving per-item type and nesting level."""
        for item in items:
            level = min(item.get('level', 0), 2)  # Word ships styles up to level 3
            ordered = item.get('ordered', False)
            base = 'List Number' if ordered else 'List Bullet'
            style = base if level == 0 else f"{base} {level + 1}"
            try:
                para = doc.add_paragraph(style=style)
            except KeyError:
                para = doc.add_paragraph(style=base)
            self._render_inline(para, item.get('text', ''))

    def _add_code_to_word(self, doc, content: str):
        """Add a fenced code block verbatim in a monospace font."""
        from docx.shared import Pt

        lines = content.split('\n')
        if lines and lines[0].strip().startswith('```'):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith('```'):
            lines = lines[:-1]
        clean_content = '\n'.join(lines)

        para = doc.add_paragraph()
        run = para.add_run(clean_content)
        run.font.name = 'Courier New'
        run.font.size = Pt(10)

    def _add_quote_to_word(self, doc, content: str):
        """Add a block quote with inline formatting."""
        para = doc.add_paragraph(style='Quote')
        self._render_inline(para, content)

    def _add_horizontal_rule_to_word(self, doc):
        """Add a horizontal rule as a bottom-bordered empty paragraph."""
        try:
            from docx.oxml.shared import OxmlElement, qn
            para = doc.add_paragraph()
            p_pr = para._p.get_or_add_pPr()
            p_bdr = OxmlElement('w:pBdr')
            bottom = OxmlElement('w:bottom')
            bottom.set(qn('w:val'), 'single')
            bottom.set(qn('w:sz'), '6')
            bottom.set(qn('w:space'), '1')
            bottom.set(qn('w:color'), 'auto')
            p_bdr.append(bottom)
            p_pr.append(p_bdr)
        except Exception as e:
            logger.debug(f"Horizontal rule failed, using fallback: {e}")
            doc.add_paragraph('_' * 40)

    def _add_metadata_to_word_doc(self, doc, metadata: Dict[str, Any], WD_ALIGN_PARAGRAPH,
                                  include_header: bool = False, lang: Optional[str] = None):
        """
        Set document properties and optionally prepend an information block.

        The block is written in the interface language of the session that
        exported (lang); language names are the ones shown in the interface.
        """
        from ..i18n import t

        info = metadata.get('translation_info', {})
        if info and include_header:
            lang = lang or info.get('interface_language')
            title = doc.add_heading(t(lang, "export.heading"), level=1)
            title.alignment = WD_ALIGN_PARAGRAPH.CENTER

            doc.add_paragraph(t(lang, "export.from", language=info.get('source_language', '-')))
            doc.add_paragraph(t(lang, "export.to", language=info.get('target_language', '-')))
            if info.get('translation_style') not in (None, 'preserve_original'):
                doc.add_paragraph(t(lang, "export.style", style=info.get('style_display', '-')))
            date_format = '%d.%m.%Y %H:%M' if (lang or 'en').startswith('de') else '%Y-%m-%d %H:%M'
            doc.add_paragraph(t(lang, "export.created", date=datetime.now().strftime(date_format)))
            doc.add_paragraph()

        # Set document properties
        doc.core_properties.title = metadata.get('title', 'Translated Document')
        doc.core_properties.subject = metadata.get('subject', 'Translation')
        doc.core_properties.author = metadata.get('author', 'Document Translator')

    def export_html(
        self,
        content: str,
        filename: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        include_styling: bool = True
    ) -> str:
        """
        Export content as HTML file with improved table styling

        Args:
            content: Content to export (Markdown format)
            filename: Optional filename (without extension)
            metadata: Optional metadata to include
            include_styling: Whether to include CSS styling

        Returns:
            Path to exported file
        """
        try:
            # Generate filename
            if not filename:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"translation_{timestamp}"

            # Convert markdown to HTML with enhanced table processing
            html_content = self._convert_markdown_to_html_enhanced(content)

            # Create full HTML document
            html_doc = self._create_enhanced_html_document(
                html_content,
                title=metadata.get('title', 'Translated Document') if metadata else 'Translated Document',
                include_styling=include_styling
            )

            # Write file
            file_path = self._new_export_dir() / f"{filename}.html"
            file_path.write_text(html_doc, encoding='utf-8')

            logger.info(f"Exported HTML to {file_path}")
            return str(file_path)

        except Exception as e:
            logger.error(f"HTML export failed: {e}")
            raise

    def _convert_markdown_to_html_enhanced(self, content: str) -> str:
        """Convert markdown to HTML with enhanced table processing."""
        try:
            import markdown2

            # First pass - convert basic markdown
            # safe_mode="escape": raw HTML in the text (from the source
            # document or the model's answer) is shown as text, so the
            # exported file cannot carry active content such as scripts.
            html = markdown2.markdown(
                content,
                extras=['tables', 'fenced-code-blocks', 'header-ids'],
                safe_mode="escape"
            )

            # Table processing
            html = self._enhance_html_tables(html)

            return html

        except ImportError:
            # Fallback without markdown2
            return self._manual_markdown_to_html(content)

    def _enhance_html_tables(self, html: str) -> str:
        """Enhance HTML tables with better structure and validation."""
        try:
            soup = BeautifulSoup(html, 'html.parser')

            # Find and enhance all tables
            for table in soup.find_all('table'):
                self._enhance_single_html_table(table)

            return str(soup)

        except Exception as e:
            logger.warning(f"Table enhancement failed: {e}")
            return html

    def _enhance_single_html_table(self, table):
        """Enhance a single HTML table."""
        try:
            # Add CSS classes
            table['class'] = table.get('class', []) + ['enhanced-table']

            # Ensure proper table structure
            if not table.find('thead') and table.find('tr'):
                # Move first row to thead if it looks like headers
                first_row = table.find('tr')
                if first_row:
                    # Check if first row cells are mostly headers (th) or look like headers
                    cells = first_row.find_all(['td', 'th'])
                    if cells and (cells[0].name == 'th' or self._looks_like_header_row(first_row)):
                        # Create thead and move first row there
                        thead = table.new_tag('thead')
                        first_row.extract()
                        thead.append(first_row)
                        table.insert(0, thead)

                        # Convert td to th in header row
                        for cell in first_row.find_all('td'):
                            cell.name = 'th'

            # Ensure tbody exists for data rows
            if table.find('thead') and not table.find('tbody'):
                remaining_rows = table.find_all('tr')
                if remaining_rows:
                    tbody = table.new_tag('tbody')
                    for row in remaining_rows:
                        row.extract()
                        tbody.append(row)
                    table.append(tbody)

        except Exception as e:
            logger.warning(f"Single table enhancement failed: {e}")

    def _looks_like_header_row(self, row) -> bool:
        """Check if a table row looks like a header row."""
        cells = row.find_all(['td', 'th'])
        if not cells:
            return False

        # Check if cells contain typical header characteristics
        for cell in cells:
            text = cell.get_text().strip()
            if text and (text.isupper() or
                        len(text.split()) <= 3 or
                        any(keyword in text.lower() for keyword in ['name', 'id', 'type', 'date', 'time', 'value'])):
                return True

        return False

    def _manual_markdown_to_html(self, content: str) -> str:
        """Manual markdown to HTML conversion as fallback."""
        html_lines = []
        lines = content.split('\n')

        i = 0
        while i < len(lines):
            line = lines[i].strip()

            if not line:
                html_lines.append('<br>')
                i += 1
                continue

            # Handle tables
            if '|' in line and i + 1 < len(lines) and '---' in lines[i + 1]:
                table_lines = []
                while i < len(lines) and ('|' in lines[i] or '---' in lines[i] or not lines[i].strip()):
                    if lines[i].strip():
                        table_lines.append(lines[i])
                    i += 1

                if table_lines:
                    html_table = self._convert_table_to_html(table_lines)
                    html_lines.append(html_table)
                continue

            # Handle headings
            if line.startswith('#'):
                level = len(line) - len(line.lstrip('#'))
                text = line.lstrip('# ')
                html_lines.append(f'<h{level}>{text}</h{level}>')
                i += 1
                continue

            # Handle regular paragraphs
            html_lines.append(f'<p>{line}</p>')
            i += 1

        return '\n'.join(html_lines)

    def _convert_table_to_html(self, table_lines: List[str]) -> str:
        """Convert markdown table lines to HTML."""
        if len(table_lines) < 3:
            return '<p>' + '\n'.join(table_lines) + '</p>'

        try:
            # Parse table
            header_line = table_lines[0]
            headers = self.table_handler._parse_table_row(header_line)

            rows = []
            for line in table_lines[2:]:  # Skip separator
                if line.strip():
                    row_data = self.table_handler._parse_table_row(line)
                    rows.append(row_data)

            return self.table_handler.create_html_table(headers, rows)

        except Exception as e:
            logger.warning(f"Table conversion failed: {e}")
            return '<pre>' + '\n'.join(table_lines) + '</pre>'

    def _create_enhanced_html_document(
        self,
        content: str,
        title: str = "Document",
        include_styling: bool = True
    ) -> str:
        """
        Create full HTML document with enhanced table styling

        Args:
            content: HTML content
            title: Document title
            include_styling: Whether to include CSS

        Returns:
            Complete HTML document
        """
        css = """
        <style>
            body {
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
                line-height: 1.6;
                color: #333;
                max-width: 1200px;
                margin: 0 auto;
                padding: 20px;
                background: #f5f5f5;
            }
            .container {
                background: white;
                padding: 40px;
                border-radius: 8px;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            }
            h1, h2, h3, h4, h5, h6 {
                color: #2c3e50;
                margin-top: 24px;
                margin-bottom: 16px;
            }
            h1 { border-bottom: 2px solid #3498db; padding-bottom: 10px; }
            h2 { border-bottom: 1px solid #ecf0f1; padding-bottom: 8px; }
            
            /* TABLE STYLES */
            table, .enhanced-table {
                border-collapse: collapse;
                width: 100%;
                margin: 20px 0;
                box-shadow: 0 2px 8px rgba(0,0,0,0.1);
                overflow: hidden;
                border-radius: 8px;
            }
            
            th, td {
                border: 1px solid #ddd;
                padding: 12px 15px;
                text-align: left;
                vertical-align: top;
            }
            
            th {
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                font-weight: bold;
                text-transform: uppercase;
                font-size: 0.9em;
                letter-spacing: 0.5px;
            }
            
            tr:nth-child(even) {
                background-color: #f8f9fa;
            }
            
            tr:hover {
                background-color: #e3f2fd;
                transition: background-color 0.3s ease;
            }
            
            code {
                background: #f4f4f4;
                padding: 2px 6px;
                border-radius: 3px;
                font-family: 'Courier New', Courier, monospace;
            }
            pre {
                background: #2c3e50;
                color: white;
                padding: 15px;
                border-radius: 5px;
                overflow-x: auto;
            }
            pre code {
                background: none;
                color: inherit;
                padding: 0;
            }
            blockquote {
                border-left: 4px solid #3498db;
                margin: 0;
                padding-left: 20px;
                color: #666;
                font-style: italic;
            }
            ul, ol {
                margin: 15px 0;
                padding-left: 30px;
            }
            li {
                margin: 5px 0;
            }
        </style>
        """ if include_styling else ""

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    {css}
</head>
<body>
    <div class="container">
        {content}
    </div>
</body>
</html>"""

    def export_json(
        self,
        content: str,
        filename: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        chunks: Optional[list] = None
    ) -> str:
        """
        Export content as JSON file with enhanced table metadata

        Args:
            content: Content to export
            filename: Optional filename (without extension)
            metadata: Optional metadata to include
            chunks: Optional chunk data to include

        Returns:
            Path to exported file
        """
        try:
            # Generate filename
            if not filename:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"translation_{timestamp}"

            # Analyze content for enhanced metadata
            content_analysis = self._analyze_content_structure(content)

            # Create JSON structure
            json_data = {
                "timestamp": datetime.now().isoformat(),
                "content": content,
                "metadata": metadata or {},
                "content_analysis": content_analysis,
                "statistics": {
                    "characters": len(content),
                    "words": len(content.split()),
                    "lines": len(content.split('\n')),
                    "paragraphs": len([p for p in content.split('\n\n') if p.strip()]),
                    "tables": content_analysis.get('table_count', 0),
                    "headings": content_analysis.get('heading_count', 0)
                }
            }

            # Add chunks if provided
            if chunks:
                json_data["chunks"] = [
                    {
                        "position": chunk.get("position"),
                        "content": chunk.get("content"),
                        "token_count": chunk.get("token_count"),
                        "chunk_type": chunk.get("chunk_type", "text"),
                        "metadata": chunk.get("metadata", {})
                    }
                    for chunk in chunks
                ]

            # Write file
            file_path = self._new_export_dir() / f"{filename}.json"
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(json_data, f, indent=2, ensure_ascii=False)

            logger.info(f"Exported JSON to {file_path}")
            return str(file_path)

        except Exception as e:
            logger.error(f"JSON export failed: {e}")
            raise

    def _analyze_content_structure(self, content: str) -> Dict[str, Any]:
        """Analyze content structure for enhanced metadata."""
        analysis = {
            'table_count': 0,
            'heading_count': 0,
            'code_block_count': 0,
            'list_count': 0,
            'tables': []
        }

        lines = content.split('\n')

        # Count tables and analyze them
        i = 0
        while i < len(lines):
            line = lines[i].strip()

            if '|' in line and i + 1 < len(lines) and '---' in lines[i + 1]:
                # Found a table
                table_lines = [line]
                i += 1

                while i < len(lines) and ('|' in lines[i] or not lines[i].strip()):
                    if lines[i].strip():
                        table_lines.append(lines[i])
                    i += 1

                table_content = '\n'.join(table_lines)
                table_data = self.table_handler.extract_table_data(table_content)

                if table_data['valid']:
                    analysis['table_count'] += 1
                    analysis['tables'].append({
                        'columns': table_data['column_count'],
                        'rows': table_data['row_count'],
                        'headers': table_data['headers']
                    })
                continue

            if line.startswith('#'):
                analysis['heading_count'] += 1
            elif line.startswith('```'):
                analysis['code_block_count'] += 1
            elif re.match(r'^\s*[-*+]\s+', line) or re.match(r'^\s*\d+\.\s+', line):
                analysis['list_count'] += 1

            i += 1

        return analysis

    def _validate_and_fix_tables(self, content: str) -> str:
        """Validate and fix table structures in markdown content."""
        lines = content.split('\n')
        fixed_lines = []

        i = 0
        while i < len(lines):
            line = lines[i]

            # Check for potential table start
            if '|' in line:
                # Collect potential table lines
                table_lines = []
                table_start = i

                while i < len(lines) and ('|' in lines[i] or lines[i].strip() == ''):
                    if lines[i].strip():
                        table_lines.append(lines[i])
                    i += 1

                if len(table_lines) >= 2:
                    # Try to fix the table
                    fixed_table = self._fix_table_structure(table_lines)
                    if fixed_table:
                        fixed_lines.extend(fixed_table)
                        fixed_lines.append('')  # Add empty line after table
                        continue

                # If table fixing failed, add original lines
                fixed_lines.extend(lines[table_start:i])
            else:
                fixed_lines.append(line)
                i += 1

        return '\n'.join(fixed_lines)

    def _fix_table_structure(self, table_lines: List[str]) -> Optional[List[str]]:
        """Fix table structure issues."""
        if len(table_lines) < 2:
            return None

        try:
            # Parse first row to determine column count
            first_row = self.table_handler._parse_table_row(table_lines[0])
            expected_cols = len(first_row)

            if expected_cols == 0:
                return None

            fixed_lines = []

            # Fix header row
            fixed_lines.append('| ' + ' | '.join(first_row) + ' |')

            # Add or fix separator row
            if len(table_lines) > 1 and '---' in table_lines[1]:
                fixed_lines.append('| ' + ' | '.join(['---'] * expected_cols) + ' |')
                data_start = 2
            else:
                fixed_lines.append('| ' + ' | '.join(['---'] * expected_cols) + ' |')
                data_start = 1

            # Fix data rows
            for line in table_lines[data_start:]:
                if line.strip():
                    row_data = self.table_handler._parse_table_row(line)

                    # Ensure correct column count
                    while len(row_data) < expected_cols:
                        row_data.append('')
                    if len(row_data) > expected_cols:
                        row_data = row_data[:expected_cols]

                    fixed_lines.append('| ' + ' | '.join(row_data) + ' |')

            # Validate the fixed table
            table_content = '\n'.join(fixed_lines)
            if self.table_handler.validate_markdown_table(table_content):
                return fixed_lines

        except Exception as e:
            logger.warning(f"Table structure fixing failed: {e}")

        return None

    def _generate_yaml_frontmatter(self, metadata: Dict[str, Any]) -> str:
        """
        Generate YAML frontmatter for Markdown

        Args:
            metadata: Metadata dictionary

        Returns:
            YAML frontmatter string
        """
        lines = ["---"]

        for key, value in metadata.items():
            if isinstance(value, (str, int, float, bool)):
                lines.append(f"{key}: {value}")
            elif isinstance(value, list):
                lines.append(f"{key}:")
                for item in value:
                    lines.append(f"  - {item}")
            elif isinstance(value, dict):
                lines.append(f"{key}:")
                for k, v in value.items():
                    lines.append(f"  {k}: {v}")

        lines.append("---")
        return "\n".join(lines)

    def cleanup(self):
        """Clean up temporary files"""
        try:
            if self.temp_dir.exists():
                import shutil
                shutil.rmtree(self.temp_dir)
        except Exception as e:
            logger.warning(f"Could not clean up temp directory: {e}")


# Backwards compatibility
ExportHandler = EnhancedExportHandler


def create_export_handlers() -> gr.Row:
    """
    Create export handler UI components

    Returns:
        gr.Row: Export handler interface
    """
    handler = EnhancedExportHandler()

    with gr.Row() as export_container:
        with gr.Column(scale=1):
            export_format = gr.Dropdown(
                choices=["Markdown", "Word", "HTML", "PDF", "JSON"],
                value="Markdown",
                label="Export Format",
                interactive=True
            )

        with gr.Column(scale=1):
            include_metadata = gr.Checkbox(
                label="Include Metadata",
                value=True,
                interactive=True
            )

        with gr.Column(scale=1):
            export_btn = gr.Button(
                "Download Export",
                variant="primary",
                size="sm"
            )

        with gr.Column(scale=2):
            export_status = gr.Textbox(
                label="Export Status",
                value="Ready to export",
                interactive=False,
                max_lines=1
            )

    return export_container


def handle_export(
    content: str,
    format_type: str,
    include_metadata: bool,
    metadata: Optional[Dict[str, Any]] = None
) -> Tuple[str, str]:
    """
    Handle document export with robust table support

    Args:
        content: Content to export
        format_type: Export format
        include_metadata: Whether to include metadata
        metadata: Optional metadata

    Returns:
        Tuple of (file_path, status_message)
    """
    try:
        handler = EnhancedExportHandler()

        # Prepare metadata if requested
        export_metadata = metadata if include_metadata else None

        # Export based on format
        if format_type == "Markdown":
            file_path = handler.export_markdown(content, metadata=export_metadata)
        elif format_type == "Word":
            file_path = handler.export_word(content, metadata=export_metadata)
        elif format_type == "HTML":
            file_path = handler.export_html(content, metadata=export_metadata)
        elif format_type == "JSON":
            file_path = handler.export_json(content, metadata=export_metadata)
        else:
            return "", f"Unsupported format: {format_type}"

        filename = Path(file_path).name
        return file_path, f"✅ Exported as {format_type} to {filename}"

    except Exception as e:
        logger.error(f"Export failed: {e}")
        return "", f"❌ Export failed: {str(e)}"