# SPDX-FileCopyrightText: 2026 Malte Dreyer
# SPDX-License-Identifier: MIT
"""
PDF document processor with pymupdf4llm integration.
UNIFIED PIPELINE: PDF is treated like any other file type via pymupdf4llm → Markdown → Standard Pipeline
No PDF-specific optimizations, complexity analysis or metadata flags.
Falls back to PyMuPDF if pymupdf4llm is not available.
"""

import logging
import time
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
import re

# Try pymupdf4llm first (preferred for LLM workflows)
try:
    import pymupdf4llm
    PYMUPDF4LLM_AVAILABLE = True
except ImportError:
    PYMUPDF4LLM_AVAILABLE = False

# Fallback to standard PyMuPDF
try:
    import fitz  # PyMuPDF
    PYMUPDF_AVAILABLE = True
except ImportError:
    PYMUPDF_AVAILABLE = False
    logging.warning("PyMuPDF not installed. PDF processing will not be available.")

from .base_processor import DocumentProcessor
from ..models.document import (
    MarkdownDocument,
    DocumentElement,
    ElementType,
    ProcessingOptions,
    ProcessingResult,
    DocumentMetadata
)

from ..config import get_config

logger = logging.getLogger(__name__)


class PDFProcessor(DocumentProcessor):
    """
    Simplified PDF processor with pymupdf4llm integration.
    UNIFIED PIPELINE: PDF is treated like any other file type - direct pymupdf4llm → Markdown conversion
    Automatically uses pymupdf4llm if available, falls back to PyMuPDF.
    """

    def __init__(self, options: Optional[ProcessingOptions] = None, config=None):
        """Initialize PDF processor with unified pipeline approach."""
        super().__init__(options)

        self.config = config or get_config()

        if not PYMUPDF_AVAILABLE:
            raise ImportError("PyMuPDF is required for PDF processing. Install with: pip install PyMuPDF")

        self.use_pymupdf4llm = PYMUPDF4LLM_AVAILABLE

        if self.use_pymupdf4llm:
            logger.info("Using pymupdf4llm for LLM-optimized PDF processing in unified pipeline")
        else:
            logger.info("Using standard PyMuPDF for PDF processing in unified pipeline")
            logger.warning("For better LLM integration, install pymupdf4llm: pip install pymupdf4llm")

    def can_process(self, file_path: Path) -> bool:
        """Check if file is a PDF."""
        return file_path.suffix.lower() == '.pdf' and PYMUPDF_AVAILABLE

    def get_supported_formats(self) -> List:
        """Return supported formats."""
        from . import DocumentFormat  # Local import to avoid circular dependency
        return [DocumentFormat.PDF]

    async def process(self, file_path: Path) -> ProcessingResult:
        """
        Process PDF file with simplified extraction - direct pymupdf4llm → Markdown conversion.

        Args:
            file_path: Path to PDF file

        Returns:
            ProcessingResult with extracted content - standard metadata without PDF-specific flags
        """
        start_time = time.time()

        # Validate file
        is_valid, error_msg = await self.validate_file(file_path)
        if not is_valid:
            return ProcessingResult.error_result(error_msg)

        try:
            if self.use_pymupdf4llm:
                result = await self._process_with_pymupdf4llm(file_path, start_time)
            else:
                result = await self._process_with_pymupdf(file_path, start_time)

            # Standard result without PDF-specific enhancements
            return result

        except Exception as e:
            logger.error(f"Unified pipeline PDF processing error: {str(e)}", exc_info=True)
            return ProcessingResult.error_result(f"PDF processing failed: {str(e)}")

    async def _process_with_pymupdf4llm(self, file_path: Path, start_time: float) -> ProcessingResult:
        """
        Process PDF using pymupdf4llm - simplified for direct markdown conversion.

        Args:
            file_path: Path to PDF file
            start_time: Processing start time

        Returns:
            ProcessingResult with extracted content
        """
        try:
            # Extract metadata first using standard PyMuPDF
            pdf_document = fitz.open(str(file_path))
            metadata = self._extract_standard_metadata(pdf_document, file_path)
            total_pages = len(pdf_document)
            pdf_document.close()

            logger.info(f"Processing {total_pages}-page PDF with pymupdf4llm (unified pipeline)")

            # Use pymupdf4llm for direct markdown conversion
            md_text = self._to_markdown(file_path)

            if not md_text or not md_text.strip():
                logger.warning("pymupdf4llm returned empty content, falling back to PyMuPDF")
                return await self._process_with_pymupdf(file_path, start_time)

            # Parse markdown to elements
            elements = self._parse_markdown_to_elements(md_text)

            # Update metadata with text statistics
            total_text = md_text
            metadata.word_count = len(total_text.split())
            metadata.char_count = len(total_text)

            # Create document with standard metadata
            document = MarkdownDocument(
                content=md_text,  # required parameter
                elements=elements,
                metadata=metadata,
                raw_text=total_text,
                processing_time=time.time() - start_time
            )

            # Calculate statistics
            stats = self.calculate_statistics(document)
            logger.info(f"Unified pipeline PDF processing completed: {stats}")

            return ProcessingResult.success_result(
                document=document,
                processing_time=time.time() - start_time
            )

        except Exception as e:
            logger.warning(f"pymupdf4llm processing failed: {e}, falling back to PyMuPDF")
            return await self._process_with_pymupdf(file_path, start_time)

    def _ocr_options(self) -> Dict[str, Any]:
        """OCR settings from document_processing.pdf_ocr / pdf_ocr_language."""
        dp = getattr(self.config, 'document_processing', None)
        return {
            'use_ocr': bool(getattr(dp, 'pdf_ocr', True)),
            'ocr_language': getattr(dp, 'pdf_ocr_language', 'eng') or 'eng',
        }

    def _to_markdown(self, file_path: Path) -> str:
        """
        Convert a PDF with pymupdf4llm.

        The OCR options only exist on pymupdf4llm's layout path (installed
        together with pymupdf-layout). Without it, OCR is not available and
        the conversion runs without these options.
        """
        kwargs = dict(
            page_chunks=False,  # Get single markdown for better flow
            write_images=False  # Images are not extracted
        )
        if getattr(pymupdf4llm, '_use_layout', False):
            kwargs.update(self._ocr_options())
        try:
            return pymupdf4llm.to_markdown(str(file_path), **kwargs)
        except TypeError:
            # Older pymupdf4llm without OCR parameters.
            for key in ('use_ocr', 'ocr_language'):
                kwargs.pop(key, None)
            return pymupdf4llm.to_markdown(str(file_path), **kwargs)

    def _parse_markdown_to_elements(self, markdown_text: str) -> List[DocumentElement]:
        """
        Parse markdown text to DocumentElements - simplified structure recognition.

        Args:
            markdown_text: Markdown content from pymupdf4llm

        Returns:
            List of DocumentElements
        """
        elements = []
        lines = markdown_text.split('\n')
        current_paragraph = []
        position = 0

        for line in lines:
            line = line.rstrip()

            # Empty line - end current paragraph
            if not line:
                if current_paragraph:
                    content = ' '.join(current_paragraph).strip()
                    if content:
                        elements.append(DocumentElement(
                            type=ElementType.PARAGRAPH,
                            content=content,
                            position=position
                        ))
                        position += 1
                    current_paragraph = []
                continue

            # Check for headings
            heading_match = re.match(r'^(#{1,6})\s+(.+)', line)
            if heading_match:
                # Save current paragraph
                if current_paragraph:
                    content = ' '.join(current_paragraph).strip()
                    if content:
                        elements.append(DocumentElement(
                            type=ElementType.PARAGRAPH,
                            content=content,
                            position=position
                        ))
                        position += 1
                    current_paragraph = []

                # Add heading
                level = len(heading_match.group(1))
                heading_type = getattr(ElementType, f'HEADING{level}', ElementType.HEADING3)

                elements.append(DocumentElement(
                    type=heading_type,
                    content=heading_match.group(2).strip(),
                    level=level,
                    position=position
                ))
                position += 1
                continue

            # Check for list items
            list_match = re.match(r'^(\s*)([-*+]|\d+\.)\s+(.+)', line)
            if list_match:
                # Save current paragraph
                if current_paragraph:
                    content = ' '.join(current_paragraph).strip()
                    if content:
                        elements.append(DocumentElement(
                            type=ElementType.PARAGRAPH,
                            content=content,
                            position=position
                        ))
                        position += 1
                    current_paragraph = []

                # Add list item
                indent = len(list_match.group(1))
                list_content = list_match.group(3)

                elements.append(DocumentElement(
                    type=ElementType.LIST_ITEM,
                    content=list_content,
                    level=(indent // 2) + 1,
                    metadata={'list_marker': list_match.group(2)},
                    position=position
                ))
                position += 1
                continue

            # Check for tables
            if '|' in line and line.count('|') >= 2:
                # Save current paragraph
                if current_paragraph:
                    content = ' '.join(current_paragraph).strip()
                    if content:
                        elements.append(DocumentElement(
                            type=ElementType.PARAGRAPH,
                            content=content,
                            position=position
                        ))
                        position += 1
                    current_paragraph = []

                # Simple table handling
                table_lines = [line]

                # Look ahead for more table rows (simplified)
                remaining_lines = lines[lines.index(line) + 1:]
                for next_line in remaining_lines:
                    if '|' in next_line and next_line.count('|') >= 2:
                        table_lines.append(next_line)
                    elif next_line.strip() == '':
                        break  # End of table
                    else:
                        break

                # Create table element
                table_content = '\n'.join(table_lines)
                elements.append(DocumentElement(
                    type=ElementType.TABLE,
                    content=table_content,
                    metadata={
                        'table_rows': len(table_lines),
                        'estimated_columns': line.count('|') - 1 if line.startswith('|') and line.endswith('|') else line.count('|') + 1
                    },
                    position=position
                ))
                position += 1
                continue

            # Check for code blocks
            if line.startswith('```'):
                # Save current paragraph
                if current_paragraph:
                    content = ' '.join(current_paragraph).strip()
                    if content:
                        elements.append(DocumentElement(
                            type=ElementType.PARAGRAPH,
                            content=content,
                            position=position
                        ))
                        position += 1
                    current_paragraph = []

                # Collect entire code block
                code_lines = [line]
                code_language = line[3:].strip() if len(line) > 3 else ''

                # Find closing ```
                remaining_lines = lines[lines.index(line) + 1:]
                for next_line in remaining_lines:
                    code_lines.append(next_line)
                    if next_line.strip() == '```':
                        break

                code_content = '\n'.join(code_lines)
                elements.append(DocumentElement(
                    type=ElementType.CODE_BLOCK,
                    content=code_content,
                    metadata={'language': code_language},
                    position=position
                ))
                position += 1
                continue

            # Regular text line - add to current paragraph
            current_paragraph.append(line)

        # Don't forget the last paragraph
        if current_paragraph:
            content = ' '.join(current_paragraph).strip()
            if content:
                elements.append(DocumentElement(
                    type=ElementType.PARAGRAPH,
                    content=content,
                    position=position
                ))

        # Post-process elements for better structure
        elements = self._post_process_elements(elements)

        return elements

    async def _process_with_pymupdf(self, file_path: Path, start_time: float) -> ProcessingResult:
        """
        Fallback processing using standard PyMuPDF.

        Args:
            file_path: Path to PDF file
            start_time: Processing start time

        Returns:
            ProcessingResult with extracted content
        """
        try:
            # Open PDF document
            pdf_document = fitz.open(str(file_path))

            # Create metadata
            metadata = self._extract_standard_metadata(pdf_document, file_path)

            # Extract content from all pages
            elements = []
            raw_text_parts = []

            for page_num in range(len(pdf_document)):
                page = pdf_document[page_num]
                page_elements, page_text = self._process_page(page, page_num)
                elements.extend(page_elements)
                raw_text_parts.append(page_text)

                # Log progress for long documents
                if page_num % 10 == 0 and page_num > 0:
                    logger.info(f"Processed {page_num}/{len(pdf_document)} pages")

            pdf_document.close()

            # Post-process elements
            elements = self._post_process_elements(elements)

            # Create markdown content from elements
            raw_text = '\n'.join(raw_text_parts)

            # Generate markdown content from elements
            markdown_parts = []
            for element in elements:
                if hasattr(element, 'to_markdown'):
                    markdown_parts.append(element.to_markdown())
                else:
                    markdown_parts.append(element.content)

            # Use raw_text as fallback if no markdown content generated
            markdown_content = '\n\n'.join(markdown_parts) if markdown_parts else raw_text

            # Create document
            document = MarkdownDocument(
                content=markdown_content,  # required parameter
                elements=elements,
                metadata=metadata,
                raw_text=raw_text,
                processing_time=time.time() - start_time
            )

            # Calculate statistics
            stats = self.calculate_statistics(document)
            logger.info(f"Standard PDF processing completed (unified pipeline): {stats}")

            return ProcessingResult.success_result(
                document=document,
                processing_time=time.time() - start_time
            )

        except Exception as e:
            logger.error(f"PyMuPDF processing error: {str(e)}", exc_info=True)
            return ProcessingResult.error_result(f"PyMuPDF processing failed: {str(e)}")

    def _extract_standard_metadata(self, pdf_document: fitz.Document, file_path: Path) -> DocumentMetadata:
        """Extract standard metadata from PDF document - without PDF-specific flags."""
        pdf_metadata = pdf_document.metadata

        # Standard metadata extraction without PDF-specific optimization flags
        metadata = self.create_metadata(
            file_path,
            page_count=len(pdf_document),
            title=pdf_metadata.get('title', ''),
            author=pdf_metadata.get('author', ''),
            subject=pdf_metadata.get('subject', ''),
            keywords=pdf_metadata.get('keywords', '').split(',') if pdf_metadata.get('keywords') else []
        )

        # Add standard PDF metadata without optimization flags
        if hasattr(metadata, 'custom_fields'):
            metadata.custom_fields.update({
                'pdf_version': pdf_metadata.get('format', ''),
                'pdf_creator': pdf_metadata.get('creator', ''),
                'pdf_producer': pdf_metadata.get('producer', ''),
                'creation_date': pdf_metadata.get('creationDate', ''),
                'modification_date': pdf_metadata.get('modDate', ''),
                'document_format': 'pdf',
                'processing_mode': 'unified_pipeline'
            })

        return metadata

    def _process_page(self, page: fitz.Page, page_num: int) -> Tuple[List[DocumentElement], str]:
        """
        Process a single PDF page (fallback method) - simplified extraction.

        Args:
            page: PyMuPDF page object
            page_num: Page number (0-indexed)

        Returns:
            Tuple of (elements, raw_text)
        """
        elements = []

        # Get text with detailed information
        blocks = page.get_text("dict")

        # Extract raw text for reference
        raw_text = page.get_text()

        # Process text blocks
        block_elements = self._process_text_blocks(blocks, page_num)
        elements.extend(block_elements)

        # Extract tables if enabled
        if self.options.extract_tables:
            tables = self._extract_tables(page, page_num)
            elements.extend(tables)

        return elements, raw_text

    def _process_text_blocks(self, blocks: Dict, page_num: int) -> List[DocumentElement]:
        """
        Process text blocks from page dictionary (fallback method).

        Args:
            blocks: Dictionary containing page blocks
            page_num: Current page number

        Returns:
            List of DocumentElements
        """
        elements = []

        for block_idx, block in enumerate(blocks.get("blocks", [])):
            if block.get("type") == 0:  # Text block
                block_elements = self._process_text_block(block, page_num, block_idx)
                elements.extend(block_elements)
            elif block.get("type") == 1:  # Image block
                if self.options.extract_images:
                    # Placeholder for image
                    elements.append(DocumentElement(
                        type=ElementType.IMAGE,
                        content=f"[Image on page {page_num + 1}]",
                        metadata={
                            'page': page_num,
                            'block': block_idx,
                            'bbox': block.get("bbox")
                        },
                        position=len(elements)
                    ))

        return elements

    def _process_text_block(self, block: Dict, page_num: int, block_idx: int) -> List[DocumentElement]:
        """
        Process individual text block (fallback method).

        Args:
            block: Text block dictionary
            page_num: Current page number
            block_idx: Block index on page

        Returns:
            List of DocumentElements
        """
        elements = []
        current_paragraph = []
        current_style = None

        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span.get("text", "").strip()
                if not text:
                    continue

                # Extract style information
                style = {
                    'font': span.get("font"),
                    'size': span.get("size"),
                    'flags': span.get("flags"),  # Contains bold, italic info
                    'color': span.get("color"),
                }

                # Check if style changed significantly
                if current_style and self._is_style_change(current_style, style):
                    # Save current paragraph
                    if current_paragraph:
                        element = self._create_element_from_text(
                            ' '.join(current_paragraph),
                            current_style,
                            page_num,
                            len(elements)
                        )
                        if element:
                            elements.append(element)
                        current_paragraph = []

                current_paragraph.append(text)
                current_style = style

        # Save remaining paragraph
        if current_paragraph:
            element = self._create_element_from_text(
                ' '.join(current_paragraph),
                current_style,
                page_num,
                len(elements)
            )
            if element:
                elements.append(element)

        return elements

    def _is_style_change(self, style1: Dict, style2: Dict) -> bool:
        """Check if there's a significant style change."""
        # Check font size change (more than 2 points)
        if abs(style1.get('size', 12) - style2.get('size', 12)) > 2:
            return True

        # Check font family change
        if style1.get('font') != style2.get('font'):
            return True

        # Check bold/italic change
        if style1.get('flags', 0) != style2.get('flags', 0):
            return True

        return False

    def _create_element_from_text(self, text: str, style: Dict,
                                  page_num: int, position: int) -> Optional[DocumentElement]:
        """
        Create DocumentElement from text and style information.

        Args:
            text: Text content
            style: Style dictionary
            page_num: Page number
            position: Element position

        Returns:
            DocumentElement or None
        """
        # Clean text
        text = self.clean_text(text)
        if not text:
            return None

        # Detect element type based on style and content
        element_type = self._detect_element_type(text, style)

        # Create element
        element = DocumentElement(
            type=element_type,
            content=text,
            metadata={
                'page': page_num,
                'font': style.get('font'),
                'font_size': style.get('size'),
                'bold': bool(style.get('flags', 0) & 2**4),
                'italic': bool(style.get('flags', 0) & 2**1),
            },
            position=position
        )

        # Set heading level if applicable
        if element.type.value.startswith('h'):
            element.level = int(element.type.value[1])

        return element

    def _detect_element_type(self, text: str, style: Dict) -> ElementType:
        """
        Detect element type based on text and style.

        Args:
            text: Text content
            style: Style information

        Returns:
            Detected ElementType
        """
        font_size = style.get('size', 12)
        is_bold = bool(style.get('flags', 0) & 2**4)
        text_length = len(text)

        # Check for list patterns
        list_patterns = [
            (r'^\s*[-•*]\s+', ElementType.LIST_ITEM),
            (r'^\s*\d+[\.\)]\s+', ElementType.LIST_ITEM),
            (r'^\s*[a-zA-Z][\.\)]\s+', ElementType.LIST_ITEM),
        ]

        for pattern, elem_type in list_patterns:
            if re.match(pattern, text):
                return elem_type

        # Detect headings based on font size and style
        if text_length < 200:  # Headings are usually short
            if font_size >= 24:
                return ElementType.HEADING1
            elif font_size >= 20:
                return ElementType.HEADING2
            elif font_size >= 18:
                return ElementType.HEADING3
            elif font_size >= 16 and is_bold:
                return ElementType.HEADING4
            elif font_size >= 14 and is_bold:
                return ElementType.HEADING5
            elif is_bold and text_length < 100:
                return ElementType.HEADING6

        # Check for code block patterns
        if style.get('font', '').lower() in ['courier', 'consolas', 'monaco', 'monospace']:
            return ElementType.CODE_BLOCK

        # Default to paragraph
        return ElementType.PARAGRAPH

    def _extract_tables(self, page: fitz.Page, page_num: int) -> List[DocumentElement]:
        """
        Extract tables from PDF page (fallback method) - simplified.

        Args:
            page: PyMuPDF page object
            page_num: Page number

        Returns:
            List of table DocumentElements
        """
        elements = []

        try:
            # Simplified table detection
            tables = self._detect_tables_simple(page)

            for table_idx, table_data in enumerate(tables):
                table_element = DocumentElement(
                    type=ElementType.TABLE,
                    content=table_data.to_markdown(),
                    metadata={
                        'page': page_num,
                        'table_index': table_idx,
                        'rows': len(table_data.rows),
                        'columns': len(table_data.headers)
                    },
                    position=0  # Will be set during post-processing
                )
                elements.append(table_element)

        except Exception as e:
            logger.warning(f"Table extraction failed on page {page_num}: {str(e)}")

        return elements

    def _detect_tables_simple(self, page: fitz.Page) -> List:
        """
        Simplified table detection using heuristic approach.

        Args:
            page: PyMuPDF page object

        Returns:
            List of TableData objects
        """
        from . import TableData  # Local import to avoid circular dependency

        tables = []

        # Get page text with layout preservation
        text = page.get_text("text")
        lines = text.split('\n')

        # Simple pattern matching for tables
        potential_table = []
        in_table = False

        for line in lines:
            # Check for table separators
            if re.match(r'^[\s\|\+\-]+$', line) and len(line) > 10:
                in_table = True
                potential_table.append(line)
            elif in_table and line.strip():
                # Check if line has multiple columns
                if '|' in line or re.search(r'\s{2,}', line):
                    potential_table.append(line)
                else:
                    # End of table
                    if len(potential_table) > 2:
                        table = self._parse_simple_table(potential_table)
                        if table:
                            tables.append(table)
                    potential_table = []
                    in_table = False

        # Check last potential table
        if potential_table and len(potential_table) > 2:
            table = self._parse_simple_table(potential_table)
            if table:
                tables.append(table)

        return tables

    def _parse_simple_table(self, lines: List[str]) -> Optional:
        """
        Parse table from text lines (simplified approach).

        Args:
            lines: Lines potentially containing a table

        Returns:
            TableData object or None
        """
        from . import TableData  # Local import to avoid circular dependency

        if not lines:
            return None

        # Determine column separator
        separator = '|' if '|' in lines[0] else None

        if separator:
            # Parse pipe-separated table
            rows = []
            headers = None

            for line in lines:
                if re.match(r'^[\s\|\+\-]+$', line):
                    continue  # Skip separator lines

                cells = [cell.strip() for cell in line.split(separator)]
                cells = [cell for cell in cells if cell]  # Remove empty cells

                if cells:
                    if headers is None:
                        headers = cells
                    else:
                        rows.append(cells)

            if headers and rows:
                return TableData(headers=headers, rows=rows)

        return None

    def _post_process_elements(self, elements: List[DocumentElement]) -> List[DocumentElement]:
        """
        Post-process elements to improve structure detection - simplified.

        Args:
            elements: Raw elements from processing

        Returns:
            Processed elements
        """
        if not elements:
            return elements

        # Remove empty elements
        elements = [el for el in elements if el and el.content.strip()]

        # Detect lists
        elements = self.detect_lists(elements)

        # Merge consecutive paragraphs if option is set
        if self.options.merge_paragraphs:
            elements = self.merge_consecutive_elements(elements, ElementType.PARAGRAPH)

        # Update positions
        for idx, element in enumerate(elements):
            element.position = idx

        return elements

    def get_processing_summary(self, result: ProcessingResult) -> str:
        """
        Get summary of PDF processing - simplified without optimization info.

        Args:
            result: Processing result

        Returns:
            Summary string
        """
        if not result.success or not result.document:
            return "PDF processing failed"

        summary_parts = [
            f"PDF processed successfully (unified pipeline)",
            f"Pages: {result.document.metadata.page_count}",
            f"Elements: {len(result.document.elements)}",
            f"Processing time: {result.processing_time:.2f}s"
        ]

        return " | ".join(summary_parts)