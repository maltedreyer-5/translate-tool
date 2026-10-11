# SPDX-FileCopyrightText: 2026 Malte Dreyer
# SPDX-License-Identifier: MIT
"""
Base document processor abstract class.
Defines the interface for all document processors in the unified pipeline.
UNIFIED PIPELINE: All document types follow the same processing workflow after initial conversion.
"""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
import aiofiles

from ..models.document import (
    MarkdownDocument,
    ProcessingOptions,
    ProcessingResult,
    DocumentMetadata,
    DocumentElement,
    ElementType
)

logger = logging.getLogger(__name__)


class DocumentProcessor(ABC):
    """
    Abstract base class for document processors in the unified pipeline.
    Each format-specific processor must inherit from this class and convert
    their input to the standard MarkdownDocument format.

    UNIFIED PIPELINE WORKFLOW:
    Input → Document Processor → Markdown → Standard Translation → Output
    """

    def __init__(self, options: Optional[ProcessingOptions] = None):
        """
        Initialize processor with options.

        Args:
            options: Processing options, uses defaults if not provided
        """
        self.options = options or ProcessingOptions()
        self._processing_stats = {}

    @abstractmethod
    async def process(self, file_path: Path) -> ProcessingResult:
        """
        Process a document file and extract structured content.
        All processors must convert their input to MarkdownDocument format
        for the unified pipeline.

        Args:
            file_path: Path to the document file

        Returns:
            ProcessingResult containing the processed document or error
        """
        pass

    @abstractmethod
    def can_process(self, file_path: Path) -> bool:
        """
        Check if this processor can handle the given file.

        Args:
            file_path: Path to check

        Returns:
            True if the processor can handle this file type
        """
        pass

    @abstractmethod
    def get_supported_formats(self) -> List:
        """
        Get list of document formats this processor supports.

        Returns:
            List of supported DocumentFormat enums
        """
        pass

    async def validate_file(self, file_path: Path) -> Tuple[bool, Optional[str]]:
        """
        Validate file before processing in unified pipeline.

        Args:
            file_path: Path to validate

        Returns:
            Tuple of (is_valid, error_message)
        """
        # Check if file exists
        if not file_path.exists():
            return False, f"File not found: {file_path}"

        # Check if it's a file (not directory)
        if not file_path.is_file():
            return False, f"Path is not a file: {file_path}"

        # Check file size
        file_size = file_path.stat().st_size
        if file_size > self.options.max_file_size:
            max_mb = self.options.max_file_size / (1024 * 1024)
            return False, f"File too large: {file_size / (1024 * 1024):.2f}MB (max: {max_mb:.0f}MB)"

        if file_size == 0:
            return False, "File is empty"

        # Check if processor can handle this file
        if not self.can_process(file_path):
            return False, f"Unsupported file format: {file_path.suffix}"

        return True, None

    async def read_file_async(self, file_path: Path, mode: str = 'rb') -> bytes:
        """
        Read file asynchronously for unified pipeline processing.

        Args:
            file_path: Path to read
            mode: File open mode

        Returns:
            File contents
        """
        async with aiofiles.open(file_path, mode) as f:
            return await f.read()

    def create_metadata(self, file_path: Path, **kwargs) -> DocumentMetadata:
        """
        Create document metadata from file with robust kwargs handling.
        Unified approach for all document types.

        Args:
            file_path: Path to document
            **kwargs: Additional metadata fields (will override defaults if provided)

        Returns:
            DocumentMetadata object
        """
        stat = file_path.stat()

        # Start with file system metadata as defaults
        base_metadata = {
            'filename': file_path.name,
            'format': self._detect_format(file_path),
            'file_size': stat.st_size,
            'created_date': time.ctime(stat.st_ctime),
            'modified_date': time.ctime(stat.st_mtime),
            'word_count': 0,
            'char_count': 0,
            'page_count': 1,
            'title': '',
            'author': '',
            'subject': '',
            'keywords': [],
        }

        # Override with provided kwargs, but handle conflicts gracefully
        for key, value in kwargs.items():
            if value is not None and value != '':  # Only override with meaningful values
                base_metadata[key] = value

        # Handle special cases for date fields
        if 'document_created_date' in kwargs:
            base_metadata['document_created_date'] = kwargs['document_created_date']
        if 'document_modified_date' in kwargs:
            base_metadata['document_modified_date'] = kwargs['document_modified_date']

        try:
            metadata = DocumentMetadata(**base_metadata)
            return metadata
        except TypeError as e:
            # If there are still conflicts, log them and return basic metadata
            logger.warning(f"Metadata creation conflict: {e}")

            # Fallback to minimal metadata
            minimal_metadata = {
                'filename': file_path.name,
                'format': self._detect_format(file_path),
                'file_size': stat.st_size,
                'created_date': time.ctime(stat.st_ctime),
                'modified_date': time.ctime(stat.st_mtime),
            }

            try:
                return DocumentMetadata(**minimal_metadata)
            except Exception as final_error:
                logger.error(f"Even minimal metadata failed: {final_error}")
                # Return absolutely minimal metadata
                return DocumentMetadata(
                    filename=file_path.name,
                    format=self._detect_format(file_path),
                    file_size=stat.st_size
                )

    def _detect_format(self, file_path: Path):
        """Detect document format from file extension - unified approach."""
        from . import DocumentFormat  # Local import to avoid circular dependency

        suffix = file_path.suffix.lower()
        format_map = {
            '.pdf': DocumentFormat.PDF,
            '.docx': DocumentFormat.DOCX,
            '.doc': DocumentFormat.DOC,
            '.txt': DocumentFormat.TXT,
            '.md': DocumentFormat.MARKDOWN,
            '.html': DocumentFormat.HTML,
            '.htm': DocumentFormat.HTML,
        }
        return format_map.get(suffix, DocumentFormat.UNKNOWN)

    def merge_consecutive_elements(self, elements: List[DocumentElement],
                                  element_type: ElementType) -> List[DocumentElement]:
        """
        Merge consecutive elements of the same type.
        Standard processing step in unified pipeline.

        Args:
            elements: List of elements to process
            element_type: Type of elements to merge

        Returns:
            List with merged elements
        """
        if not elements:
            return elements

        try:
            merged = []
            current_merge = None

            for element in elements:
                if element.type == element_type and current_merge and current_merge.type == element_type:
                    # Merge with current
                    current_merge.content += "\n" + element.content
                else:
                    if current_merge:
                        merged.append(current_merge)
                    current_merge = element

            if current_merge:
                merged.append(current_merge)

            return merged
        except Exception as e:
            logger.warning(f"Error merging consecutive elements: {e}")
            return elements

    def detect_headings_by_style(self, elements: List[Dict[str, Any]],
                                min_size_ratio: float = None) -> List[DocumentElement]:
        """
        Detect headings based on font size or style information.
        Standard heading detection for unified pipeline.

        Args:
            elements: Raw elements with style information
            min_size_ratio: Minimum size ratio to consider as heading

        Returns:
            List of DocumentElements with proper heading types
        """
        min_size_ratio = min_size_ratio or self.options.min_header_size_ratio
        processed = []

        try:
            # Calculate average font size
            sizes = [el.get('font_size', 12) for el in elements if 'font_size' in el]
            avg_size = sum(sizes) / len(sizes) if sizes else 12

            for idx, el in enumerate(elements):
                element_type = ElementType.PARAGRAPH
                level = None

                # Check if it's a heading based on size
                if 'font_size' in el:
                    size_ratio = el['font_size'] / avg_size

                    if size_ratio >= min_size_ratio * 2:
                        element_type = ElementType.HEADING1
                        level = 1
                    elif size_ratio >= min_size_ratio * 1.7:
                        element_type = ElementType.HEADING2
                        level = 2
                    elif size_ratio >= min_size_ratio * 1.5:
                        element_type = ElementType.HEADING3
                        level = 3
                    elif size_ratio >= min_size_ratio * 1.3:
                        element_type = ElementType.HEADING4
                        level = 4
                    elif size_ratio >= min_size_ratio * 1.1:
                        element_type = ElementType.HEADING5
                        level = 5
                    elif size_ratio >= min_size_ratio:
                        element_type = ElementType.HEADING6
                        level = 6

                # Check for bold/italic as potential headings
                if element_type == ElementType.PARAGRAPH:
                    if el.get('bold', False) and len(el.get('text', '')) < 100:
                        element_type = ElementType.HEADING4
                        level = 4

                processed.append(DocumentElement(
                    type=element_type,
                    content=el.get('text', ''),
                    metadata=el.get('metadata', {}),
                    level=level,
                    position=idx
                ))
        except Exception as e:
            logger.warning(f"Error detecting headings by style: {e}")
            # Return simple paragraph elements as fallback
            for idx, el in enumerate(elements):
                processed.append(DocumentElement(
                    type=ElementType.PARAGRAPH,
                    content=el.get('text', ''),
                    metadata=el.get('metadata', {}),
                    position=idx
                ))

        return processed

    def detect_lists(self, elements: List[DocumentElement]) -> List[DocumentElement]:
        """
        Detect and mark list items based on content patterns.
        Standard list detection for unified pipeline.

        Args:
            elements: List of elements to process

        Returns:
            List with properly marked list items
        """
        processed = []
        list_patterns = [
            (r'^\s*[-*•]\s+', ElementType.LIST_UNORDERED),
            (r'^\s*\d+[\.\)]\s+', ElementType.LIST_ORDERED),
            (r'^\s*[a-z][\.\)]\s+', ElementType.LIST_ORDERED),
            (r'^\s*[A-Z][\.\)]\s+', ElementType.LIST_ORDERED),
        ]

        import re

        try:
            for element in elements:
                if element.type == ElementType.PARAGRAPH:
                    for pattern, list_type in list_patterns:
                        if re.match(pattern, element.content):
                            element.type = ElementType.LIST_ITEM
                            # Calculate indent level based on leading spaces
                            indent = len(element.content) - len(element.content.lstrip())
                            element.level = (indent // 2) + 1
                            element.metadata['list_type'] = list_type
                            # Clean the content
                            element.content = re.sub(pattern, '', element.content)
                            break

                processed.append(element)
        except Exception as e:
            logger.warning(f"Error detecting lists: {e}")
            processed = elements

        return processed

    def clean_text(self, text: str) -> str:
        """
        Clean and normalize text for unified pipeline.

        Args:
            text: Text to clean

        Returns:
            Cleaned text
        """
        if not text:
            return ""

        try:
            # Remove excessive whitespace
            text = ' '.join(text.split())

            # Fix common encoding issues
            replacements = {
                '"': '"',
                '"': '"',
                ''': "'",
                ''': "'",
                '…': '...',
                '–': '-',
                '—': '--',
                '\u200b': '',  # Zero-width space
                '\xa0': ' ',   # Non-breaking space
            }

            for old, new in replacements.items():
                text = text.replace(old, new)

            return text.strip()
        except Exception as e:
            logger.warning(f"Error cleaning text: {e}")
            return str(text) if text else ""

    def split_into_sentences(self, text: str) -> List[str]:
        """
        Split text into sentences - standard approach for unified pipeline.

        Args:
            text: Text to split

        Returns:
            List of sentences
        """
        try:
            import re

            # Simple sentence splitter (can be enhanced)
            sentence_endings = r'[.!?。]\s+'
            sentences = re.split(sentence_endings, text)

            # Clean and filter
            sentences = [s.strip() for s in sentences if s.strip()]

            return sentences
        except Exception as e:
            logger.warning(f"Error splitting sentences: {e}")
            return [text] if text else []

    def calculate_statistics(self, document: MarkdownDocument) -> Dict[str, Any]:
        """
        Calculate document statistics for unified pipeline.

        Args:
            document: Document to analyze

        Returns:
            Dictionary of statistics
        """
        try:
            stats = {
                'total_elements': len(document.elements),
                'word_count': document.metadata.word_count,
                'char_count': document.metadata.char_count,
                'estimated_tokens': document.estimate_total_tokens(),
                'structure_summary': document.get_structure_summary(),
                'heading_count': len(document.find_elements_by_type(ElementType.HEADING1)) +
                               len(document.find_elements_by_type(ElementType.HEADING2)) +
                               len(document.find_elements_by_type(ElementType.HEADING3)),
                'paragraph_count': len(document.find_elements_by_type(ElementType.PARAGRAPH)),
                'list_count': len(document.find_elements_by_type(ElementType.LIST_ITEM)),
                'table_count': len(document.find_elements_by_type(ElementType.TABLE)),
                'processing_mode': 'unified_pipeline'
            }

            return stats
        except Exception as e:
            logger.warning(f"Error calculating statistics: {e}")
            return {
                'total_elements': len(document.elements) if document.elements else 0,
                'word_count': 0,
                'char_count': 0,
                'estimated_tokens': 0,
                'structure_summary': {},
                'heading_count': 0,
                'paragraph_count': 0,
                'list_count': 0,
                'table_count': 0,
                'processing_mode': 'unified_pipeline'
            }

    async def process_with_timeout(self, file_path: Path) -> ProcessingResult:
        """
        Process document with timeout in unified pipeline.

        Args:
            file_path: Path to process

        Returns:
            ProcessingResult
        """
        try:
            result = await asyncio.wait_for(
                self.process(file_path),
                timeout=self.options.timeout
            )
            return result
        except asyncio.TimeoutError:
            return ProcessingResult(
                success=False,
                error_message=f"Processing timeout ({self.options.timeout}s exceeded)"
            )
        except Exception as e:
            logger.error(f"Unified pipeline processing error: {str(e)}", exc_info=True)
            return ProcessingResult(
                success=False,
                error_message=str(e)
            )