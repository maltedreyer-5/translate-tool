# SPDX-FileCopyrightText: 2026 Malte Dreyer
# SPDX-License-Identifier: MIT
"""
Document processing package.
Provides unified interface for processing various document formats.
UNIFIED PIPELINE: All document types follow the same processing workflow after markdown conversion.
"""

import logging
from pathlib import Path
from typing import Optional, List, Type
from enum import Enum

from .base_processor import DocumentProcessor
from .pdf_processor import PDFProcessor
from .word_processor import WordProcessor
from .markdown_converter import MarkdownConverter, MarkdownValidator
from ..models.document import (
    ProcessingOptions,
    ProcessingResult,
    MarkdownDocument,
    DocumentElement,
    ElementType,
    DocumentMetadata
)

logger = logging.getLogger(__name__)

# Compatibility names for external callers that still import them.
class DocumentFormat:
    """Format constants (compatibility; the pipeline itself is format-independent)"""
    PDF = "pdf"
    DOCX = "docx"
    DOC = "doc"
    TXT = "txt"
    MARKDOWN = "markdown"
    HTML = "html"
    UNKNOWN = "unknown"

class TableData:
    """Simple table container (compatibility)"""
    def __init__(self, headers=None, rows=None):
        self.headers = headers or []
        self.rows = rows or []

    def to_markdown(self):
        if not self.headers:
            return ""

        lines = []
        # Header
        lines.append('| ' + ' | '.join(str(h) for h in self.headers) + ' |')
        # Separator
        lines.append('| ' + ' | '.join(['---'] * len(self.headers)) + ' |')
        # Rows
        for row in self.rows:
            padded_row = (row + [''] * len(self.headers))[:len(self.headers)]
            lines.append('| ' + ' | '.join(str(cell) for cell in padded_row) + ' |')

        return '\n'.join(lines)

# Export main classes for unified pipeline
__all__ = [
    'DocumentProcessor',
    'PDFProcessor',
    'WordProcessor',
    'MarkdownConverter',
    'MarkdownValidator',
    'DocumentProcessorFactory',
    'TextProcessor',
    'process_document',
    'DocumentFormat',  # compatibility
    'TableData'        # compatibility
]


def decode_text(data: bytes) -> str:
    """
    Decode an uploaded text file: UTF-8 (with or without BOM), UTF-16 with
    BOM, otherwise Windows-1252, the usual encoding of older Western text
    files (a superset of Latin-1 for printable characters).
    """
    if data.startswith((b'\xff\xfe', b'\xfe\xff')):
        return data.decode('utf-16')
    try:
        return data.decode('utf-8-sig')
    except UnicodeDecodeError:
        pass
    try:
        return data.decode('cp1252')
    except UnicodeDecodeError:
        return data.decode('latin-1')


class TextProcessor(DocumentProcessor):
    """
    Unified processor for plain text and markdown files.
    Part of the unified pipeline - processes text directly to standard format.
    """

    def can_process(self, file_path: Path) -> bool:
        """Check if file is plain text or markdown."""
        return file_path.suffix.lower() in ['.txt', '.md', '.markdown']

    def get_supported_formats(self) -> List:
        """Return supported formats."""
        return [DocumentFormat.TXT, DocumentFormat.MARKDOWN]

    async def process(self, file_path: Path) -> ProcessingResult:
        """Process text file through unified pipeline."""
        import time
        from ..models.document import DocumentElement, ElementType, DocumentMetadata

        start_time = time.time()

        # Validate file
        is_valid, error_msg = await self.validate_file(file_path)
        if not is_valid:
            return ProcessingResult.error_result(error_msg)

        try:
            # Read bytes and decode explicitly: the default text encoding of
            # the server must not decide how an uploaded file is read.
            content = decode_text(await self.read_file_async(file_path, mode='rb'))

            # Create standard metadata (unified format)
            metadata = self.create_metadata(file_path)

            # Parse content based on format (unified approach)
            elements = []
            if file_path.suffix.lower() in ['.md', '.markdown']:
                elements = self._parse_markdown(content)
            else:
                elements = self._parse_plain_text(content)

            # Create document with unified structure
            document = MarkdownDocument(
                content=content,
                elements=elements,
                metadata=metadata,
                raw_text=content,
                processing_time=time.time() - start_time
            )

            return ProcessingResult.success_result(
                document=document,
                processing_time=time.time() - start_time
            )

        except Exception as e:
            logger.error(f"Unified text processing error: {str(e)}", exc_info=True)
            return ProcessingResult.error_result(f"Text processing failed: {str(e)}")

    def _parse_plain_text(self, text: str) -> List:
        """Parse plain text into standard elements."""
        from ..models.document import DocumentElement, ElementType

        elements = []
        paragraphs = text.split('\n\n')

        for i, paragraph in enumerate(paragraphs):
            if paragraph.strip():
                element = DocumentElement(
                    type=ElementType.PARAGRAPH,
                    content=paragraph.strip(),
                    position=i
                )
                elements.append(element)

        return elements

    def _parse_markdown(self, markdown: str) -> List:
        """Parse markdown into standard elements."""
        import re
        from ..models.document import DocumentElement, ElementType

        elements = []
        lines = markdown.split('\n')
        current_paragraph = []
        position = 0

        for line in lines:
            # Check for headings
            heading_match = re.match(r'^(#{1,6})\s+(.+)', line)
            if heading_match:
                # Save current paragraph
                if current_paragraph:
                    elements.append(DocumentElement(
                        type=ElementType.PARAGRAPH,
                        content=' '.join(current_paragraph),
                        position=position
                    ))
                    position += 1
                    current_paragraph = []

                # Add heading
                level = len(heading_match.group(1))
                elements.append(DocumentElement(
                    type=ElementType(f'h{level}'),
                    content=heading_match.group(2).strip(),
                    level=level,
                    position=position
                ))
                position += 1

            # Check for list items
            elif re.match(r'^\s*[-*+]\s+', line):
                # Save current paragraph
                if current_paragraph:
                    elements.append(DocumentElement(
                        type=ElementType.PARAGRAPH,
                        content=' '.join(current_paragraph),
                        position=position
                    ))
                    position += 1
                    current_paragraph = []

                # Add list item
                content = re.sub(r'^\s*[-*+]\s+', '', line)
                elements.append(DocumentElement(
                    type=ElementType.LIST_ITEM,
                    content=content,
                    position=position
                ))
                position += 1

            # Check for empty lines (paragraph breaks)
            elif not line.strip():
                if current_paragraph:
                    elements.append(DocumentElement(
                        type=ElementType.PARAGRAPH,
                        content=' '.join(current_paragraph),
                        position=position
                    ))
                    position += 1
                    current_paragraph = []

            # Regular text line
            else:
                current_paragraph.append(line)

        # Don't forget last paragraph
        if current_paragraph:
            elements.append(DocumentElement(
                type=ElementType.PARAGRAPH,
                content=' '.join(current_paragraph),
                position=position
            ))

        return elements


class DocumentProcessorFactory:
    """
    Factory for creating appropriate document processors.
    UNIFIED PIPELINE: All processors follow the same workflow after initial conversion.
    """

    # Registry of available processors
    _processors: List[Type[DocumentProcessor]] = [
        PDFProcessor,
        WordProcessor,
        TextProcessor,
    ]

    @classmethod
    def create_processor(cls,
                        file_path: Path,
                        options: Optional[ProcessingOptions] = None) -> Optional[DocumentProcessor]:
        """
        Create appropriate processor for file.
        All processors will use the unified pipeline after initial conversion.

        Args:
            file_path: Path to document
            options: Processing options

        Returns:
            Processor instance or None if no processor available
        """
        for processor_class in cls._processors:
            try:
                processor = processor_class(options)
                if processor.can_process(file_path):
                    logger.info(f"Using {processor_class.__name__} for a {file_path.suffix} file")
                    return processor
            except ImportError as e:
                logger.warning(f"{processor_class.__name__} not available: {e}")
                continue
            except Exception as e:
                logger.error(f"Error creating {processor_class.__name__}: {e}")
                continue

        logger.error(f"No processor available for {file_path.suffix} files")
        return None

    @classmethod
    def register_processor(cls, processor_class: Type[DocumentProcessor]):
        """
        Register a new processor class for unified pipeline.

        Args:
            processor_class: Processor class to register
        """
        if processor_class not in cls._processors:
            cls._processors.append(processor_class)
            logger.info(f"Registered processor: {processor_class.__name__}")

    @classmethod
    def get_supported_formats(cls) -> List[str]:
        """
        Get list of all supported file extensions in unified pipeline.

        Returns:
            List of supported extensions
        """
        formats = set()
        for processor_class in cls._processors:
            try:
                processor = processor_class()
                for format_enum in processor.get_supported_formats():
                    if format_enum == DocumentFormat.PDF:
                        formats.add('.pdf')
                    elif format_enum == DocumentFormat.DOCX:
                        formats.add('.docx')
                    elif format_enum == DocumentFormat.DOC:
                        formats.add('.doc')
                    elif format_enum == DocumentFormat.TXT:
                        formats.add('.txt')
                    elif format_enum == DocumentFormat.MARKDOWN:
                        formats.update(['.md', '.markdown'])
            except:
                continue

        return sorted(list(formats))


async def process_document(file_path: Path,
                          options: Optional[ProcessingOptions] = None) -> ProcessingResult:
    """
    High-level function to process any supported document through unified pipeline.

    Workflow:
    - Input → Document Processor → Markdown → Standard Pipeline → Output
    - ALL document types use the SAME workflow after markdown conversion

    Args:
        file_path: Path to document
        options: Processing options

    Returns:
        ProcessingResult with document or error
    """
    # Create processor
    processor = DocumentProcessorFactory.create_processor(file_path, options)

    if not processor:
        return ProcessingResult.error_result(
            f"Unsupported file format: {file_path.suffix}"
        )

    # Process with timeout if specified
    if options and options.timeout:
        return await processor.process_with_timeout(file_path)
    else:
        return await processor.process(file_path)