# SPDX-FileCopyrightText: 2026 Malte Dreyer
# SPDX-License-Identifier: MIT
"""
Document models for the unified translation pipeline.
All document types use the same processing pipeline.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Union
from enum import Enum
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


class ElementType(Enum):
    """Types of document elements for unified processing"""
    TEXT = "text"
    HEADING = "heading"  # Generic heading (kept for compatibility)
    # Specific heading levels (used by the processors)
    HEADING1 = "h1"
    HEADING2 = "h2"
    HEADING3 = "h3"
    HEADING4 = "h4"
    HEADING5 = "h5"
    HEADING6 = "h6"
    PARAGRAPH = "paragraph"
    LIST = "list"
    LIST_ITEM = "list_item"
    LIST_ORDERED = "list_ordered"
    LIST_UNORDERED = "list_unordered"
    TABLE = "table"
    TABLE_ROW = "table_row"
    TABLE_CELL = "table_cell"
    CODE_BLOCK = "code_block"
    BLOCKQUOTE = "blockquote"
    IMAGE = "image"
    LINK = "link"
    FOOTNOTE = "footnote"
    METADATA = "metadata"
    # Additional types needed by processors
    QUOTE = "quote"
    LINE_BREAK = "line_break"
    HORIZONTAL_RULE = "horizontal_rule"


@dataclass
class DocumentMetadata:
    """Unified metadata for all document types"""
    title: Optional[str] = None
    author: Optional[str] = None
    subject: Optional[str] = None
    creator: Optional[str] = None
    producer: Optional[str] = None
    creation_date: Optional[str] = None
    modification_date: Optional[str] = None
    keywords: List[str] = field(default_factory=list)

    # File information
    file_path: Optional[Path] = None
    file_size: int = 0
    page_count: int = 0
    word_count: int = 0
    char_count: int = 0

    # Processing information
    extraction_method: str = "unified_pipeline"
    processing_timestamp: Optional[str] = None

    # Language and content
    detected_language: Optional[str] = None
    has_tables: bool = False
    has_images: bool = False
    has_links: bool = False

    # Custom metadata
    custom_fields: Dict[str, Any] = field(default_factory=dict)

    # Additional fields used by the processors
    filename: Optional[str] = None
    format: Optional[str] = None
    created_date: Optional[str] = None
    modified_date: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert metadata to dictionary"""
        return {
            'title': self.title,
            'author': self.author,
            'subject': self.subject,
            'creator': self.creator,
            'producer': self.producer,
            'creation_date': self.creation_date,
            'modification_date': self.modification_date,
            'keywords': self.keywords,
            'file_path': str(self.file_path) if self.file_path else None,
            'file_size': self.file_size,
            'page_count': self.page_count,
            'word_count': self.word_count,
            'char_count': self.char_count,
            'extraction_method': self.extraction_method,
            'processing_timestamp': self.processing_timestamp,
            'detected_language': self.detected_language,
            'has_tables': self.has_tables,
            'has_images': self.has_images,
            'has_links': self.has_links,
            'custom_fields': self.custom_fields,
            'filename': self.filename,
            'format': self.format,
            'created_date': self.created_date,
            'modified_date': self.modified_date
        }


@dataclass
class DocumentElement:
    """A unified document element for all document types"""
    type: ElementType
    content: str
    level: int = 0  # For headings, list nesting, etc.
    attributes: Dict[str, Any] = field(default_factory=dict)
    position: int = 0  # Position in document
    metadata: Dict[str, Any] = field(default_factory=dict)

    # Table-specific properties
    is_header: bool = False
    table_data: Optional[List[List[str]]] = None

    # Formatting preservation
    formatting: Dict[str, Any] = field(default_factory=dict)

    # Additional fields used by the processors
    children: List['DocumentElement'] = field(default_factory=list)
    parent: Optional['DocumentElement'] = None

    def __post_init__(self):
        """Post-initialization processing"""
        if self.content is None:
            self.content = ""

        # Auto-detect table data for table elements
        if self.type == ElementType.TABLE and not self.table_data:
            self.table_data = self._extract_table_data()

    def _extract_table_data(self) -> Optional[List[List[str]]]:
        """Extract table data from markdown table content"""
        if not self.content or '|' not in self.content:
            return None

        try:
            lines = [line.strip() for line in self.content.split('\n') if line.strip()]
            if len(lines) < 2:
                return None

            table_data = []
            for i, line in enumerate(lines):
                # Skip separator lines (containing ---)
                if '---' in line:
                    continue

                # Parse table row
                if line.startswith('|') and line.endswith('|'):
                    cells = [cell.strip() for cell in line[1:-1].split('|')]
                    table_data.append(cells)
                elif '|' in line:
                    cells = [cell.strip() for cell in line.split('|')]
                    table_data.append(cells)

            return table_data if table_data else None

        except Exception as e:
            logger.warning(f"Failed to extract table data: {e}")
            return None

    def estimate_tokens(self, chars_per_token: float = 4.0) -> int:
        """Estimate token count for this element"""
        return max(1, int(len(self.content) / chars_per_token))

    def is_table_element(self) -> bool:
        """Check if this is a table-related element"""
        return self.type in [ElementType.TABLE, ElementType.TABLE_ROW, ElementType.TABLE_CELL]

    def is_code_element(self) -> bool:
        """Check if this is a code-related element"""
        return self.type == ElementType.CODE_BLOCK

    # Methods used by the processors
    def is_heading(self) -> bool:
        """Check if element is a heading"""
        return self.type in [
            ElementType.HEADING, ElementType.HEADING1, ElementType.HEADING2,
            ElementType.HEADING3, ElementType.HEADING4, ElementType.HEADING5, ElementType.HEADING6
        ]

    def get_heading_level(self) -> Optional[int]:
        """Get heading level if element is a heading"""
        if self.type == ElementType.HEADING1:
            return 1
        elif self.type == ElementType.HEADING2:
            return 2
        elif self.type == ElementType.HEADING3:
            return 3
        elif self.type == ElementType.HEADING4:
            return 4
        elif self.type == ElementType.HEADING5:
            return 5
        elif self.type == ElementType.HEADING6:
            return 6
        elif self.type == ElementType.HEADING:
            return self.level or 1
        return None

    def to_markdown(self) -> str:
        """Convert element to markdown representation"""
        if self.type == ElementType.HEADING1:
            return f"# {self.content}"
        elif self.type == ElementType.HEADING2:
            return f"## {self.content}"
        elif self.type == ElementType.HEADING3:
            return f"### {self.content}"
        elif self.type == ElementType.HEADING4:
            return f"#### {self.content}"
        elif self.type == ElementType.HEADING5:
            return f"##### {self.content}"
        elif self.type == ElementType.HEADING6:
            return f"###### {self.content}"
        elif self.type == ElementType.HEADING:
            level = self.level or 1
            return f"{'#' * level} {self.content}"
        elif self.type == ElementType.PARAGRAPH:
            return self.content
        elif self.type == ElementType.LIST_ITEM:
            indent = "  " * ((self.level or 1) - 1)
            return f"{indent}- {self.content}"
        elif self.type == ElementType.CODE_BLOCK:
            language = self.metadata.get('language', '')
            return f"```{language}\n{self.content}\n```"
        elif self.type in [ElementType.QUOTE, ElementType.BLOCKQUOTE]:
            lines = self.content.split('\n')
            return '\n'.join(f"> {line}" for line in lines)
        elif self.type == ElementType.TABLE:
            return self.content  # Table should already be in markdown format
        elif self.type == ElementType.HORIZONTAL_RULE:
            return "---"
        else:
            return self.content

    def get_text_content(self) -> str:
        """Get plain text content without formatting"""
        # For table elements, return formatted table
        if self.type == ElementType.TABLE and self.table_data:
            return self._format_table_as_text()

        return self.content.strip()

    def _format_table_as_text(self) -> str:
        """Format table data as plain text"""
        if not self.table_data:
            return self.content

        try:
            lines = []
            for i, row in enumerate(self.table_data):
                if i == 0:
                    # Header row
                    lines.append('| ' + ' | '.join(row) + ' |')
                    lines.append('| ' + ' | '.join(['---'] * len(row)) + ' |')
                else:
                    # Data row
                    lines.append('| ' + ' | '.join(row) + ' |')

            return '\n'.join(lines)
        except Exception:
            return self.content

    def to_dict(self) -> Dict[str, Any]:
        """Convert element to dictionary"""
        return {
            'type': self.type.value,
            'content': self.content,
            'level': self.level,
            'attributes': self.attributes,
            'position': self.position,
            'metadata': self.metadata,
            'is_header': self.is_header,
            'table_data': self.table_data,
            'formatting': self.formatting,
            'estimated_tokens': self.estimate_tokens()
        }


@dataclass
class MarkdownDocument:
    """Unified markdown document representation for all input types"""
    content: str
    elements: List[DocumentElement] = field(default_factory=list)
    metadata: DocumentMetadata = field(default_factory=DocumentMetadata)
    source_file: Optional[Path] = None
    # Additional fields used by the processors
    raw_text: str = ""
    processing_time: float = 0.0

    def __post_init__(self):
        """Post-initialization processing"""
        if not self.elements and self.content:
            self.elements = self._parse_elements()

        # Set raw_text if not provided
        if not self.raw_text:
            self.raw_text = self.content

        # Update metadata with content analysis
        self._update_metadata()

    def _parse_elements(self) -> List[DocumentElement]:
        """Parse markdown content into elements"""
        elements = []
        lines = self.content.split('\n')

        current_position = 0
        i = 0

        while i < len(lines):
            line = lines[i].strip()

            if not line:
                i += 1
                continue

            # Parse different element types
            if line.startswith('#'):
                # Heading
                level = len(line) - len(line.lstrip('#'))
                text = line.lstrip('# ').strip()

                # Map to specific heading types
                heading_types = {
                    1: ElementType.HEADING1,
                    2: ElementType.HEADING2,
                    3: ElementType.HEADING3,
                    4: ElementType.HEADING4,
                    5: ElementType.HEADING5,
                    6: ElementType.HEADING6
                }
                heading_type = heading_types.get(level, ElementType.HEADING)

                elements.append(DocumentElement(
                    type=heading_type,
                    content=text,
                    level=level,
                    position=current_position
                ))

            elif '|' in line and i + 1 < len(lines) and '---' in lines[i + 1]:
                # Table
                table_lines = [line]
                j = i + 1
                while j < len(lines) and ('|' in lines[j] or not lines[j].strip()):
                    if lines[j].strip():
                        table_lines.append(lines[j])
                    j += 1

                table_content = '\n'.join(table_lines)
                elements.append(DocumentElement(
                    type=ElementType.TABLE,
                    content=table_content,
                    position=current_position
                ))
                i = j - 1

            elif line.startswith('```'):
                # Code block
                code_lines = [line]
                j = i + 1
                while j < len(lines):
                    code_lines.append(lines[j])
                    if lines[j].strip().startswith('```'):
                        break
                    j += 1

                code_content = '\n'.join(code_lines)
                elements.append(DocumentElement(
                    type=ElementType.CODE_BLOCK,
                    content=code_content,
                    position=current_position
                ))
                i = j

            elif line.startswith(('- ', '* ', '+ ')) or line.lstrip().startswith(('1. ', '2. ', '3.')):
                # List
                list_lines = [line]
                j = i + 1
                while j < len(lines):
                    next_line = lines[j].strip()
                    if not next_line:
                        j += 1
                        continue
                    if not (next_line.startswith(('- ', '* ', '+ ')) or next_line.lstrip().startswith(tuple(f'{n}. ' for n in range(1, 10)))):
                        break
                    list_lines.append(lines[j])
                    j += 1

                list_content = '\n'.join(list_lines)
                elements.append(DocumentElement(
                    type=ElementType.LIST,
                    content=list_content,
                    position=current_position
                ))
                i = j - 1

            else:
                # Regular paragraph
                paragraph_lines = [line]
                j = i + 1
                while j < len(lines):
                    next_line = lines[j].strip()
                    if (not next_line or
                        next_line.startswith('#') or
                        '|' in next_line or
                        next_line.startswith('```') or
                        next_line.startswith(('- ', '* ', '+ ')) or
                        next_line.lstrip().startswith(tuple(f'{n}. ' for n in range(1, 10)))):
                        break
                    paragraph_lines.append(lines[j])
                    j += 1

                paragraph_content = '\n'.join(paragraph_lines).strip()
                if paragraph_content:
                    elements.append(DocumentElement(
                        type=ElementType.PARAGRAPH,
                        content=paragraph_content,
                        position=current_position
                    ))
                i = j - 1

            current_position += 1
            i += 1

        return elements

    def _update_metadata(self):
        """Update metadata based on content analysis"""
        if not self.metadata:
            self.metadata = DocumentMetadata()

        # Count content
        self.metadata.word_count = len(self.content.split())
        self.metadata.char_count = len(self.content)

        # Analyze elements
        has_tables = any(el.type == ElementType.TABLE for el in self.elements)
        has_links = '[' in self.content and '](' in self.content
        has_images = '![' in self.content

        self.metadata.has_tables = has_tables
        self.metadata.has_links = has_links
        self.metadata.has_images = has_images

    # Methods used by the processors
    def find_elements_by_type(self, element_type: ElementType) -> List[DocumentElement]:
        """Find all elements of a specific type"""
        return [element for element in self.elements if element.type == element_type]

    def get_headings_hierarchy(self) -> List[Dict[str, Any]]:
        """Get hierarchical structure of headings"""
        headings = []
        current_path = []

        for element in self.elements:
            if element.is_heading():
                level = element.get_heading_level() or 1

                # Build hierarchical structure
                heading_info = {
                    'level': level,
                    'content': element.content,
                    'element': element,
                    'children': []
                }

                # Find parent heading
                while current_path and current_path[-1]['level'] >= level:
                    current_path.pop()

                if current_path:
                    current_path[-1]['children'].append(heading_info)
                else:
                    headings.append(heading_info)

                current_path.append(heading_info)

        return headings

    def get_text_content(self) -> str:
        """Get full text content"""
        return self.content

    def get_elements_by_type(self, element_type: ElementType) -> List[DocumentElement]:
        """Get all elements of a specific type"""
        return [el for el in self.elements if el.type == element_type]

    def get_tables(self) -> List[DocumentElement]:
        """Get all table elements"""
        return self.get_elements_by_type(ElementType.TABLE)

    def get_headings(self) -> List[DocumentElement]:
        """Get all heading elements"""
        heading_types = [
            ElementType.HEADING, ElementType.HEADING1, ElementType.HEADING2,
            ElementType.HEADING3, ElementType.HEADING4, ElementType.HEADING5, ElementType.HEADING6
        ]
        return [el for el in self.elements if el.type in heading_types]

    def estimate_total_tokens(self, chars_per_token: float = 4.0) -> int:
        """Estimate total token count"""
        return max(1, int(len(self.content) / chars_per_token))

    def get_structure_summary(self) -> Dict[str, Any]:
        """Get document structure summary"""
        element_counts = {}
        for element in self.elements:
            element_type = element.type.value
            element_counts[element_type] = element_counts.get(element_type, 0) + 1

        return {
            'total_elements': len(self.elements),
            'element_counts': element_counts,
            'estimated_tokens': self.estimate_total_tokens(),
            'word_count': self.metadata.word_count,
            'has_tables': self.metadata.has_tables,
            'has_images': self.metadata.has_images,
            'has_links': self.metadata.has_links
        }

    def to_dict(self) -> Dict[str, Any]:
        """Convert document to dictionary"""
        return {
            'content': self.content,
            'elements': [el.to_dict() for el in self.elements],
            'metadata': self.metadata.to_dict(),
            'source_file': str(self.source_file) if self.source_file else None,
            'structure_summary': self.get_structure_summary()
        }


@dataclass
class ProcessingOptions:
    """Unified processing options for all document types"""
    # Content extraction
    extract_text: bool = True
    extract_tables: bool = True
    extract_images: bool = False
    extract_links: bool = True
    extract_footnotes: bool = True

    # Structure detection
    detect_headers: bool = True
    detect_lists: bool = True
    detect_paragraphs: bool = True

    # Formatting preservation
    preserve_formatting: bool = True
    preserve_structure: bool = True
    repair_tables: bool = True

    # Language processing
    detect_language: bool = False

    # Output options
    output_format: str = "markdown"
    include_metadata: bool = True

    # Processing limits
    max_file_size_mb: int = 50
    max_pages: Optional[int] = None

    # Additional options used by the processors
    max_file_size: int = field(init=False)  # Computed property
    timeout: int = 300  # 5 minutes
    min_header_size_ratio: float = 1.2
    merge_paragraphs: bool = False
    clean_text: bool = True

    def __post_init__(self):
        """Post-initialization processing"""
        # Convert MB to bytes for compatibility
        self.max_file_size = self.max_file_size_mb * 1024 * 1024

    def to_dict(self) -> Dict[str, Any]:
        """Convert options to dictionary"""
        return {
            'extract_text': self.extract_text,
            'extract_tables': self.extract_tables,
            'extract_images': self.extract_images,
            'extract_links': self.extract_links,
            'extract_footnotes': self.extract_footnotes,
            'detect_headers': self.detect_headers,
            'detect_lists': self.detect_lists,
            'detect_paragraphs': self.detect_paragraphs,
            'preserve_formatting': self.preserve_formatting,
            'preserve_structure': self.preserve_structure,
            'repair_tables': self.repair_tables,
            'detect_language': self.detect_language,
            'output_format': self.output_format,
            'include_metadata': self.include_metadata,
            'max_file_size_mb': self.max_file_size_mb,
            'max_pages': self.max_pages,
            'timeout': self.timeout,
            'min_header_size_ratio': self.min_header_size_ratio,
            'merge_paragraphs': self.merge_paragraphs,
            'clean_text': self.clean_text
        }


@dataclass
class ProcessingResult:
    """Result of unified document processing"""
    success: bool
    document: Optional[MarkdownDocument] = None
    error_message: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    processing_time: float = 0.0
    extracted_metadata: Dict[str, Any] = field(default_factory=dict)

    # Processing statistics
    pages_processed: int = 0
    elements_extracted: int = 0
    tables_found: int = 0
    images_found: int = 0

    # Class methods used by the processors
    @classmethod
    def success_result(cls,
                      document: MarkdownDocument,
                      processing_time: float = 0.0,
                      warnings: List[str] = None,
                      metadata: Dict[str, Any] = None) -> 'ProcessingResult':
        """Create a successful processing result."""
        return cls(
            success=True,
            document=document,
            processing_time=processing_time,
            warnings=warnings or [],
            extracted_metadata=metadata or {},
            elements_extracted=len(document.elements) if document else 0,
            tables_found=len(document.get_tables()) if document else 0
        )

    @classmethod
    def error_result(cls,
                    error_message: str,
                    processing_time: float = 0.0,
                    warnings: List[str] = None,
                    metadata: Dict[str, Any] = None) -> 'ProcessingResult':
        """Create a failed processing result."""
        return cls(
            success=False,
            error_message=error_message,
            processing_time=processing_time,
            warnings=warnings or [],
            extracted_metadata=metadata or {}
        )

    def add_warning(self, warning: str):
        """Add a warning to the result"""
        self.warnings.append(warning)

    def get_summary(self) -> str:
        """Get a summary of the processing result"""
        if self.success:
            if self.document:
                element_count = len(self.document.elements)
                word_count = self.document.metadata.word_count
                return f"Success: {element_count} elements, {word_count} words, {self.processing_time:.2f}s"
            else:
                return f"Success: {self.processing_time:.2f}s"
        else:
            return f"Error: {self.error_message}"

    def to_dict(self) -> Dict[str, Any]:
        """Convert result to dictionary"""
        return {
            'success': self.success,
            'document': self.document.to_dict() if self.document else None,
            'error_message': self.error_message,
            'warnings': self.warnings,
            'processing_time': self.processing_time,
            'extracted_metadata': self.extracted_metadata,
            'pages_processed': self.pages_processed,
            'elements_extracted': self.elements_extracted,
            'tables_found': self.tables_found,
            'images_found': self.images_found
        }


# Helper functions for unified processing

def create_processing_options(**kwargs) -> ProcessingOptions:
    """Create processing options with defaults"""
    return ProcessingOptions(**kwargs)


def create_markdown_document(content: str, source_file: Optional[Path] = None) -> MarkdownDocument:
    """Create a markdown document from content"""
    return MarkdownDocument(content=content, source_file=source_file)


def merge_documents(documents: List[MarkdownDocument]) -> MarkdownDocument:
    """Merge multiple documents into one"""
    if not documents:
        return MarkdownDocument(content="")

    if len(documents) == 1:
        return documents[0]

    # Merge content
    merged_content = "\n\n".join(doc.content for doc in documents)

    # Merge elements
    merged_elements = []
    position_offset = 0

    for doc in documents:
        for element in doc.elements:
            new_element = DocumentElement(
                type=element.type,
                content=element.content,
                level=element.level,
                attributes=element.attributes.copy(),
                position=element.position + position_offset,
                metadata=element.metadata.copy(),
                is_header=element.is_header,
                table_data=element.table_data.copy() if element.table_data else None,
                formatting=element.formatting.copy()
            )
            merged_elements.append(new_element)

        position_offset += len(doc.elements)

    # Create merged document
    merged_doc = MarkdownDocument(
        content=merged_content,
        elements=merged_elements,
        source_file=documents[0].source_file
    )

    # Merge metadata
    merged_doc.metadata.word_count = sum(doc.metadata.word_count for doc in documents)
    merged_doc.metadata.char_count = sum(doc.metadata.char_count for doc in documents)
    merged_doc.metadata.page_count = sum(doc.metadata.page_count for doc in documents)

    return merged_doc


# Export list for clean imports
__all__ = [
    'ElementType',
    'DocumentMetadata',
    'DocumentElement',
    'MarkdownDocument',
    'ProcessingOptions',
    'ProcessingResult',
    'create_processing_options',
    'create_markdown_document',
    'merge_documents'
]