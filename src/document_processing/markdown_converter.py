# SPDX-FileCopyrightText: 2026 Malte Dreyer
# SPDX-License-Identifier: MIT
"""
Markdown converter for unified document output.
Converts document elements to clean, structured markdown.
UNIFIED PIPELINE: All document types are converted to the same markdown format,
with table validation and repair to prevent table corruption.
"""

import re
import logging
from typing import List, Dict, Any, Optional, Tuple
from ..models.document import (
    MarkdownDocument,
    DocumentElement,
    ElementType,
    DocumentMetadata
)

from ..markdown_utils import is_table_separator, count_table_columns

logger = logging.getLogger(__name__)

# Local TableData class to avoid circular imports
class TableData:
    """Local TableData class to avoid circular imports"""
    def __init__(self, headers=None, rows=None):
        self.headers = headers or []
        self.rows = rows or []

    def to_markdown(self):
        if not self.headers:
            return ""
        lines = []
        lines.append('| ' + ' | '.join(str(h) for h in self.headers) + ' |')
        lines.append('| ' + ' | '.join(['---'] * len(self.headers)) + ' |')
        for row in self.rows:
            padded_row = (row + [''] * len(self.headers))[:len(self.headers)]
            lines.append('| ' + ' | '.join(str(cell) for cell in padded_row) + ' |')
        return '\n'.join(lines)


class TableValidator:
    """Validates and analyzes markdown table structures for unified pipeline."""

    @staticmethod
    def is_valid_markdown_table(content: str) -> bool:
        """
        Comprehensive validation of markdown table structure.
        Standard validation for all document types in unified pipeline.

        Args:
            content: Table content to validate

        Returns:
            True if valid markdown table
        """
        if not content or not content.strip():
            return False

        lines = [line.strip() for line in content.strip().split('\n') if line.strip()]

        if len(lines) < 2:
            logger.debug("Table validation failed: less than 2 lines")
            return False

        # Check for separator line (should be second line)
        separator_line = lines[1] if len(lines) > 1 else ""
        if not TableValidator._is_valid_separator(separator_line):
            logger.debug("Table validation failed: invalid separator line")
            return False

        # Check consistent column count
        column_counts = []
        for i, line in enumerate(lines):
            col_count = TableValidator._count_columns(line)
            if col_count == 0:
                logger.debug(f"Table validation failed: line {i} has 0 columns")
                return False
            column_counts.append(col_count)

        # All lines should have same column count
        if len(set(column_counts)) > 1:
            logger.debug(f"Table validation failed: inconsistent column counts {column_counts}")
            return False

        # Skip separator line validation for column consistency
        non_separator_counts = [column_counts[i] for i in range(len(column_counts)) if i != 1]
        if len(set(non_separator_counts)) > 1:
            logger.debug(f"Table validation failed: inconsistent non-separator column counts {non_separator_counts}")
            return False

        logger.debug(f"Table validation passed: {len(lines)} lines, {column_counts[0]} columns")
        return True

    @staticmethod
    def _is_valid_separator(line: str) -> bool:
        """Check if line is a valid table separator."""
        # Shared definition, see src/markdown_utils.py.
        return is_table_separator(line)

    @staticmethod
    def _count_columns(line: str) -> int:
        """Count columns in a table line."""
        return count_table_columns(line)

    @staticmethod
    def analyze_table_structure(content: str) -> Dict[str, Any]:
        """Analyze table structure and return metadata."""
        lines = [line.strip() for line in content.strip().split('\n') if line.strip()]

        if len(lines) < 2:
            return {'valid': False, 'error': 'Insufficient lines'}

        analysis = {
            'valid': TableValidator.is_valid_markdown_table(content),
            'row_count': len(lines) - 1,  # Exclude separator
            'column_count': TableValidator._count_columns(lines[0]) if lines else 0,
            'has_header': len(lines) > 1,
            'total_lines': len(lines),
            'alignment': TableValidator._detect_alignment(lines[1] if len(lines) > 1 else "")
        }

        return analysis

    @staticmethod
    def _detect_alignment(separator_line: str) -> List[str]:
        """Detect column alignment from separator line."""
        if not separator_line:
            return []

        parts = separator_line.split('|')
        parts = [p.strip() for p in parts if p.strip()]

        alignments = []
        for part in parts:
            if part.startswith(':') and part.endswith(':'):
                alignments.append('center')
            elif part.endswith(':'):
                alignments.append('right')
            else:
                alignments.append('left')

        return alignments


class TableReconstructor:
    """Reconstructs and repairs damaged table structures in unified pipeline."""

    @staticmethod
    def repair_table(content: str) -> Tuple[str, bool]:
        """
        Attempt to repair a malformed table.
        Standard table repair for all document types.

        Args:
            content: Potentially malformed table content

        Returns:
            Tuple of (repaired_content, was_repaired)
        """
        original_content = content
        was_repaired = False

        lines = [line.strip() for line in content.strip().split('\n') if line.strip()]

        if len(lines) < 2:
            logger.warning("Cannot repair table: insufficient lines")
            return content, False

        # Step 1: Ensure all lines have consistent column structure
        repaired_lines = []
        expected_columns = None

        for i, line in enumerate(lines):
            if i == 1:  # Skip separator line for column count detection
                repaired_lines.append(line)
                continue

            col_count = TableValidator._count_columns(line)

            if expected_columns is None:
                expected_columns = col_count

            if col_count != expected_columns:
                logger.info(f"Repairing table line {i}: {col_count} -> {expected_columns} columns")
                repaired_line = TableReconstructor._fix_column_count(line, expected_columns)
                repaired_lines.append(repaired_line)
                was_repaired = True
            else:
                repaired_lines.append(line)

        # Step 2: Ensure separator line is valid
        if len(repaired_lines) > 1:
            separator = repaired_lines[1]
            if not TableValidator._is_valid_separator(separator):
                logger.info("Repairing table separator line")
                repaired_lines[1] = TableReconstructor._create_separator(expected_columns or 2)
                was_repaired = True

        # Step 3: Ensure proper table borders
        for i, line in enumerate(repaired_lines):
            if not line.startswith('|') or not line.endswith('|'):
                logger.info(f"Adding table borders to line {i}")
                repaired_lines[i] = f"|{line.lstrip('|').rstrip('|')}|"
                was_repaired = True

        repaired_content = '\n'.join(repaired_lines)

        if was_repaired:
            logger.info("Table repair completed")
            logger.debug(f"Original: {len(original_content)} chars")
            logger.debug(f"Repaired: {len(repaired_content)} chars")

        return repaired_content, was_repaired

    @staticmethod
    def _fix_column_count(line: str, expected_columns: int) -> str:
        """Fix the column count of a table line."""
        line = line.strip().strip('|')
        cells = [cell.strip() for cell in line.split('|')]

        if len(cells) > expected_columns:
            # Too many columns, merge the excess
            fixed_cells = cells[:expected_columns-1] + [' '.join(cells[expected_columns-1:])]
        elif len(cells) < expected_columns:
            # Too few columns, pad with empty cells
            fixed_cells = cells + [''] * (expected_columns - len(cells))
        else:
            fixed_cells = cells

        return '| ' + ' | '.join(fixed_cells) + ' |'

    @staticmethod
    def _create_separator(columns: int) -> str:
        """Create a proper separator line for given column count."""
        return '| ' + ' | '.join(['---'] * columns) + ' |'

    @staticmethod
    def reconstruct_from_data(headers: List[str], rows: List[List[str]]) -> str:
        """Reconstruct table from structured data."""
        if not headers:
            logger.warning("Cannot reconstruct table: no headers")
            return ""

        lines = []

        # Header line
        header_line = '| ' + ' | '.join(str(h) for h in headers) + ' |'
        lines.append(header_line)

        # Separator line
        separator = '| ' + ' | '.join(['---'] * len(headers)) + ' |'
        lines.append(separator)

        # Data lines
        for row in rows:
            # Ensure row has same number of columns as headers
            padded_row = (row + [''] * len(headers))[:len(headers)]
            row_line = '| ' + ' | '.join(str(cell) for cell in padded_row) + ' |'
            lines.append(row_line)

        return '\n'.join(lines)


class MarkdownConverter:
    """
    Converts document elements to markdown format.
    UNIFIED PIPELINE: All document types are converted to the same markdown format.
    Tables are validated and repaired; helper methods create MarkdownDocument
    instances.
    """

    def __init__(self,
                 preserve_formatting: bool = True,
                 add_toc: bool = False,
                 max_heading_level: int = 6,
                 repair_tables: bool = True,
                 include_frontmatter: bool = True):
        """
        Initialize converter for unified pipeline.

        Args:
            preserve_formatting: Whether to preserve text formatting
            add_toc: Whether to add table of contents
            max_heading_level: Maximum heading level to use
            repair_tables: Whether to attempt table repairs
            include_frontmatter: Whether to prepend the document metadata
                (title, author, dates) as YAML frontmatter. The translation
                pipeline switches this off: the metadata is not content, must
                not be sent to the model and must not end up in exports.
        """
        self.include_frontmatter = include_frontmatter
        self.preserve_formatting = preserve_formatting
        self.add_toc = add_toc
        self.max_heading_level = max_heading_level
        self.repair_tables = repair_tables
        # Keep original heading numbering (e.g. "3.2 Ergebnisse") by default.
        self.strip_heading_numbers = False

        self.table_validator = TableValidator()
        self.table_reconstructor = TableReconstructor()

        # Statistics for unified pipeline
        self.conversion_stats = {
            'tables_processed': 0,
            'tables_repaired': 0,
            'tables_failed': 0,
            'tables_reconstructed': 0,
            'processing_mode': 'unified_pipeline'
        }

    def convert_document(self, document: MarkdownDocument) -> str:
        """
        Convert entire document to markdown in unified pipeline.

        Args:
            document: Document to convert

        Returns:
            Markdown string
        """
        parts = []

        # Reset statistics
        self.conversion_stats = {
            'tables_processed': 0,
            'tables_repaired': 0,
            'tables_failed': 0,
            'tables_reconstructed': 0,
            'processing_mode': 'unified_pipeline'
        }

        # Add metadata as frontmatter if present
        if self.include_frontmatter and self._should_add_frontmatter(document):
            parts.append(self._create_frontmatter(document))

        # Add table of contents if requested
        if self.add_toc:
            toc = self._generate_toc(document)
            if toc:
                parts.append(toc)
                parts.append('')  # Empty line after TOC

        # Convert each element using unified approach
        for element in document.elements:
            markdown = self.convert_element(element)
            if markdown:
                parts.append(markdown)

        # Log conversion statistics
        if self.conversion_stats['tables_processed'] > 0:
            logger.info(f"Unified pipeline table conversion stats: {self.conversion_stats}")

        # Join with appropriate spacing
        return self._join_markdown_parts(parts)

    def convert_document_from_elements(self, elements: List[DocumentElement],
                                     metadata: Optional[DocumentMetadata] = None) -> str:
        """
        Convert elements directly to markdown content.
        Helper method for processors that need to create markdown content.

        Args:
            elements: List of document elements
            metadata: Optional metadata

        Returns:
            Markdown content string
        """
        if not elements:
            return ""

        parts = []

        # Add frontmatter if metadata provided
        if metadata and (metadata.title or metadata.author or metadata.subject):
            parts.append(self._create_frontmatter_from_metadata(metadata))

        # Convert each element
        for element in elements:
            markdown = self.convert_element(element)
            if markdown:
                parts.append(markdown)

        return self._join_markdown_parts(parts)

    def create_markdown_document_from_elements(self,
                                             elements: List[DocumentElement],
                                             metadata: Optional[DocumentMetadata] = None,
                                             raw_text: str = "",
                                             processing_time: float = 0.0) -> MarkdownDocument:
        """
        Create a properly formatted MarkdownDocument from elements
        (including the required 'content' parameter).

        Args:
            elements: List of document elements
            metadata: Document metadata
            raw_text: Original raw text
            processing_time: Processing time

        Returns:
            Properly created MarkdownDocument
        """
        # Generate markdown content from elements
        markdown_content = self.convert_document_from_elements(elements, metadata)

        # Use raw_text as fallback if no markdown content generated
        if not markdown_content:
            markdown_content = raw_text or ""

        # Create document with required content parameter
        return MarkdownDocument(
            content=markdown_content,  # Required content parameter
            elements=elements,
            metadata=metadata or DocumentMetadata(),
            raw_text=raw_text,
            processing_time=processing_time
        )

    def convert_element(self, element: DocumentElement) -> str:
        """
        Convert single element to markdown using unified approach.

        Args:
            element: Element to convert

        Returns:
            Markdown string
        """
        converters = {
            ElementType.HEADING1: self._convert_heading,
            ElementType.HEADING2: self._convert_heading,
            ElementType.HEADING3: self._convert_heading,
            ElementType.HEADING4: self._convert_heading,
            ElementType.HEADING5: self._convert_heading,
            ElementType.HEADING6: self._convert_heading,
            ElementType.PARAGRAPH: self._convert_paragraph,
            ElementType.LIST_ITEM: self._convert_list_item,
            ElementType.LIST_ORDERED: self._convert_list,
            ElementType.LIST_UNORDERED: self._convert_list,
            ElementType.TABLE: self._convert_table_enhanced,
            ElementType.CODE_BLOCK: self._convert_code_block,
            ElementType.QUOTE: self._convert_quote,
            ElementType.IMAGE: self._convert_image,
            ElementType.HORIZONTAL_RULE: lambda e: '---',
            ElementType.LINE_BREAK: lambda e: '',
        }

        converter = converters.get(element.type, self._convert_generic)
        return converter(element)

    def _convert_table_enhanced(self, element: DocumentElement) -> str:
        """
        Convert a table element with validation and repair.
        Unified approach for all document types.

        Args:
            element: Table element to convert

        Returns:
            Valid markdown table string
        """
        self.conversion_stats['tables_processed'] += 1

        content = element.content
        if not content or not content.strip():
            logger.warning("Empty table content in unified pipeline")
            self.conversion_stats['tables_failed'] += 1
            return ""

        # Method 1: Check if already valid markdown table
        if self.table_validator.is_valid_markdown_table(content):
            logger.debug("Table is already valid markdown in unified pipeline")
            return content + '\n'

        # Method 2: Try to repair existing markdown table
        if self.repair_tables and ('|' in content):
            logger.info("Attempting to repair table in unified pipeline")
            repaired_content, was_repaired = self.table_reconstructor.repair_table(content)

            if was_repaired and self.table_validator.is_valid_markdown_table(repaired_content):
                logger.info("Table repair successful in unified pipeline")
                self.conversion_stats['tables_repaired'] += 1
                return repaired_content + '\n'

        # Method 3: Try to parse and reconstruct table
        table_data = self._parse_table_content(content)
        if table_data:
            logger.info("Reconstructing table from parsed data in unified pipeline")
            reconstructed = self.table_reconstructor.reconstruct_from_data(
                table_data['headers'],
                table_data['rows']
            )

            if self.table_validator.is_valid_markdown_table(reconstructed):
                logger.info("Table reconstruction successful in unified pipeline")
                self.conversion_stats['tables_reconstructed'] += 1
                return reconstructed + '\n'

        # Method 4: Format as structured text
        logger.warning("Table conversion failed in unified pipeline, formatting as structured text")
        self.conversion_stats['tables_failed'] += 1
        return self._format_table_as_text(content) + '\n'

    def _parse_table_content(self, content: str) -> Optional[Dict[str, Any]]:
        """
        Parse table content to extract headers and rows.
        Standard parsing for unified pipeline.

        Args:
            content: Table content

        Returns:
            Dict with headers and rows, or None if parsing fails
        """
        try:
            lines = [line.strip() for line in content.strip().split('\n') if line.strip()]

            if len(lines) < 2:
                return None

            # Try different parsing approaches

            # Approach 1: Already markdown-like with |
            if '|' in content:
                return self._parse_markdown_like_table(lines)

            # Approach 2: Tab-separated or space-separated
            return self._parse_separated_table(lines)

        except Exception as e:
            logger.warning(f"Table parsing failed in unified pipeline: {e}")
            return None

    def _parse_markdown_like_table(self, lines: List[str]) -> Optional[Dict[str, Any]]:
        """Parse markdown-like table with | separators."""
        try:
            headers = []
            rows = []

            for i, line in enumerate(lines):
                # Skip separator lines
                if re.match(r'^[\s\|\-:]+$', line):
                    continue

                # Extract cells
                cells = [cell.strip() for cell in line.split('|')]
                # Remove empty cells from start/end
                cells = [cell for cell in cells if cell or (cells.index(cell) > 0 and cells.index(cell) < len(cells) - 1)]

                if not cells:
                    continue

                if not headers:
                    headers = cells
                else:
                    rows.append(cells)

            if headers:
                return {'headers': headers, 'rows': rows}

        except Exception as e:
            logger.debug(f"Markdown-like table parsing failed: {e}")

        return None

    def _parse_separated_table(self, lines: List[str]) -> Optional[Dict[str, Any]]:
        """Parse tab or space-separated table."""
        try:
            # Try tab separation first
            if '\t' in '\n'.join(lines):
                separator = '\t'
            else:
                # Try multiple spaces
                separator = re.compile(r'\s{2,}')

            parsed_lines = []
            for line in lines:
                if separator == '\t':
                    cells = [cell.strip() for cell in line.split('\t')]
                else:
                    cells = [cell.strip() for cell in separator.split(line)]

                if cells and any(cell for cell in cells):  # At least one non-empty cell
                    parsed_lines.append(cells)

            if parsed_lines:
                headers = parsed_lines[0]
                rows = parsed_lines[1:] if len(parsed_lines) > 1 else []
                return {'headers': headers, 'rows': rows}

        except Exception as e:
            logger.debug(f"Separated table parsing failed: {e}")

        return None

    def _format_table_as_text(self, content: str) -> str:
        """Format table content as structured text when conversion fails."""
        lines = [line.strip() for line in content.strip().split('\n') if line.strip()]

        # Add a table indicator
        formatted_lines = ['**Table** (formatting preserved as text):', '']

        for line in lines:
            # Clean up the line but preserve structure
            cleaned_line = line.strip()
            if cleaned_line:
                # Add some indentation for visual separation
                formatted_lines.append(f"  {cleaned_line}")

        formatted_lines.append('')  # Empty line after table

        return '\n'.join(formatted_lines)

    def _convert_heading(self, element: DocumentElement) -> str:
        """Convert heading element in unified pipeline."""
        level = element.level or int(element.type.value[1])
        level = min(level, self.max_heading_level)

        # Clean heading text
        text = self._clean_heading_text(element.content)

        return '#' * level + ' ' + text

    def _convert_paragraph(self, element: DocumentElement) -> str:
        """Convert paragraph element in unified pipeline."""
        text = element.content

        # Apply formatting if preserved
        if self.preserve_formatting and element.metadata:
            if element.metadata.get('bold'):
                text = f"**{text}**"
            if element.metadata.get('italic'):
                text = f"*{text}*"
            if element.metadata.get('underline'):
                # Markdown doesn't support underline, use HTML
                text = f"<u>{text}</u>"

        return text + '\n'

    def _convert_list_item(self, element: DocumentElement) -> str:
        """Convert list item element in unified pipeline."""
        indent = '  ' * ((element.level or 1) - 1)

        # Determine bullet style
        if element.metadata.get('list_type') == 'ordered':
            # For ordered lists, we should track numbers
            # For simplicity, using '1.' for all items (markdown will renumber)
            bullet = '1.'
        else:
            # Use different bullets for different levels
            bullets = ['-', '*', '+']
            bullet = bullets[(element.level or 1) - 1] if element.level and element.level <= 3 else '-'

        return f"{indent}{bullet} {element.content}"

    def _convert_list(self, element: DocumentElement) -> str:
        """Convert list container element in unified pipeline."""
        if not element.children:
            return ''

        items = []
        for child in element.children:
            item_text = self._convert_list_item(child)
            items.append(item_text)

        return '\n'.join(items) + '\n'

    def _convert_code_block(self, element: DocumentElement) -> str:
        """Convert code block element in unified pipeline."""
        language = element.metadata.get('language', '')
        code = element.content

        # Ensure code doesn't contain ``` that would break the block
        code = code.replace('```', '` ` `')

        return f"```{language}\n{code}\n```\n"

    def _convert_quote(self, element: DocumentElement) -> str:
        """Convert quote element in unified pipeline."""
        lines = element.content.split('\n')
        quoted = '\n'.join(f"> {line}" for line in lines if line.strip())
        return quoted + '\n'

    def _convert_image(self, element: DocumentElement) -> str:
        """Convert image element in unified pipeline."""
        # Images are only indicated, not extracted
        # Could be enhanced to extract and link actual images
        alt_text = element.metadata.get('alt', 'Image')
        src = element.metadata.get('src', '')

        if src:
            return f"![{alt_text}]({src})\n"
        else:
            return f"[{element.content}]\n"

    def _convert_generic(self, element: DocumentElement) -> str:
        """Convert generic/unknown element in unified pipeline."""
        return element.content + '\n'

    def _clean_heading_text(self, text: str) -> str:
        """Clean heading text for markdown (without corrupting it with escapes)."""
        text = text.strip()

        # Optionally drop leading section numbers (e.g. "1.2.3 Heading").
        # Off by default so "3.2 Ergebnisse" keeps its numbering.
        if self.strip_heading_numbers:
            text = re.sub(r'^\d+(\.\d+)*\.?\s+', '', text)

        # Only escape what would actually break a heading line: a leading '#'
        # (which would change the heading level) or a stray leading marker.
        # Escaping '.', '-', '(', ')' etc. would surface as visible backslashes
        # in the Word export, so it is deliberately not done here.
        return text

    def _escape_markdown(self, text: str) -> str:
        """
        Escape only genuinely structural markdown characters.

        Escaping almost all punctuation (including '.', '-', '(', ')') would
        surface as literal backslashes downstream. Only backslash, backtick,
        asterisk, underscore and square brackets are escaped, which are the
        characters that actually create inline markup.
        """
        for char in ('\\', '`', '*', '_', '[', ']'):
            text = text.replace(char, '\\' + char)
        return text

    def _should_add_frontmatter(self, document: MarkdownDocument) -> bool:
        """Check if frontmatter should be added."""
        metadata = document.metadata
        return bool(metadata.title or metadata.author or metadata.subject)

    def _create_frontmatter(self, document: MarkdownDocument) -> str:
        """Create YAML frontmatter from metadata."""
        return self._create_frontmatter_from_metadata(document.metadata)

    def _create_frontmatter_from_metadata(self, metadata: DocumentMetadata) -> str:
        """Create YAML frontmatter from metadata object."""
        lines = ['---']

        if metadata.title:
            lines.append(f'title: "{metadata.title}"')
        if metadata.author:
            lines.append(f'author: "{metadata.author}"')
        if metadata.subject:
            lines.append(f'subject: "{metadata.subject}"')
        if metadata.keywords:
            lines.append(f'keywords: [{", ".join(metadata.keywords)}]')
        if metadata.created_date:
            lines.append(f'created: {metadata.created_date}')
        if metadata.modified_date:
            lines.append(f'modified: {metadata.modified_date}')

        lines.append('---')
        lines.append('')  # Empty line after frontmatter

        return '\n'.join(lines)

    def _generate_toc(self, document: MarkdownDocument) -> str:
        """Generate table of contents."""
        toc_lines = ['## Table of Contents', '']

        headings = document.get_headings_hierarchy()
        for heading in headings:
            self._add_toc_entry(toc_lines, heading, 0)

        return '\n'.join(toc_lines) if len(toc_lines) > 2 else ''

    def _add_toc_entry(self, toc_lines: List[str], heading: Dict[str, Any],
                       base_level: int):
        """Add TOC entry recursively."""
        level = heading['level']
        indent = '  ' * (level - 1)

        # Create anchor from heading text
        anchor = self._create_anchor(heading['content'])

        toc_lines.append(f"{indent}- [{heading['content']}](#{anchor})")

        # Add children
        for child in heading.get('children', []):
            self._add_toc_entry(toc_lines, child, base_level)

    def _create_anchor(self, heading_text: str) -> str:
        """Create markdown anchor from heading text."""
        # Convert to lowercase and replace spaces with hyphens
        anchor = heading_text.lower()
        anchor = re.sub(r'\s+', '-', anchor)
        # Remove special characters
        anchor = re.sub(r'[^\w\-]', '', anchor)
        return anchor

    def _join_markdown_parts(self, parts: List[str]) -> str:
        """Join markdown parts with appropriate spacing."""
        result = []
        prev_was_empty = True

        for part in parts:
            if not part:
                if not prev_was_empty:
                    result.append('')
                    prev_was_empty = True
            else:
                result.append(part)
                prev_was_empty = False

        return '\n'.join(result)

    def get_conversion_stats(self) -> Dict[str, Any]:
        """Get conversion statistics."""
        return self.conversion_stats.copy()


class MarkdownValidator:
    """
    Validates markdown output for common issues in unified pipeline,
    including table structure.
    """

    @staticmethod
    def validate(markdown: str) -> Dict[str, Any]:
        """
        Validate markdown content from unified pipeline.

        Args:
            markdown: Markdown to validate

        Returns:
            Validation results with warnings and errors
        """
        results = {
            'valid': True,
            'errors': [],
            'warnings': [],
            'table_issues': [],
            'pipeline_mode': 'unified'
        }

        # Check for unclosed code blocks
        if markdown.count('```') % 2 != 0:
            results['errors'].append('Unclosed code block detected')
            results['valid'] = False

        # Table validation
        table_issues = MarkdownValidator._validate_tables(markdown)
        results['table_issues'] = table_issues
        if table_issues:
            results['warnings'].extend([f"Table issue: {issue}" for issue in table_issues])

        # Check for broken tables (original logic)
        lines = markdown.split('\n')
        in_table = False
        for i, line in enumerate(lines):
            if '|' in line:
                if not in_table:
                    in_table = True
                    # Check if next line is separator
                    if i + 1 < len(lines) and '---' not in lines[i + 1]:
                        results['warnings'].append(f'Table at line {i+1} may be missing separator')
            else:
                in_table = False

        # Check for excessive blank lines
        if '\n\n\n\n' in markdown:
            results['warnings'].append('Excessive blank lines detected')

        # Check for broken links
        link_pattern = r'\[([^\]]+)\]\(([^\)]*)\)'
        for match in re.finditer(link_pattern, markdown):
            if not match.group(2):
                results['warnings'].append(f'Empty link found: [{match.group(1)}]')

        return results

    @staticmethod
    def _validate_tables(markdown: str) -> List[str]:
        """Validate all tables in markdown content."""
        issues = []

        # Find all potential tables
        table_pattern = r'(\|[^\n]*\|\s*\n[\s\S]*?(?=\n\n|\n(?!\|)|\Z))'

        for match in re.finditer(table_pattern, markdown, re.MULTILINE):
            table_content = match.group(1).strip()

            if not TableValidator.is_valid_markdown_table(table_content):
                line_number = markdown[:match.start()].count('\n') + 1
                issues.append(f"Invalid table structure at line {line_number}")

                # Analyze what's wrong
                analysis = TableValidator.analyze_table_structure(table_content)
                if not analysis['valid']:
                    issues.append(f"Table analysis: {analysis.get('error', 'unknown issue')}")

        return issues


def create_table_aware_converter(
    preserve_formatting: bool = True,
    repair_tables: bool = True,
    include_frontmatter: bool = True
) -> MarkdownConverter:
    """
    Factory function to create a table-aware markdown converter for unified pipeline.

    Args:
        preserve_formatting: Whether to preserve text formatting
        repair_tables: Whether to attempt table repairs

    Returns:
        Configured MarkdownConverter
    """
    return MarkdownConverter(
        preserve_formatting=preserve_formatting,
        repair_tables=repair_tables,
        include_frontmatter=include_frontmatter
    )