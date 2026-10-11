# SPDX-FileCopyrightText: 2026 Malte Dreyer
# SPDX-License-Identifier: MIT
"""
Simple chunker with table awareness.
Token-based splitting at headings, paragraphs and sentences instead of complex
boundary detection; tables are kept intact (or split by rows with the header
repeated). One chunking approach for all document types.
"""

import logging
import re

from ..markdown_utils import is_valid_table_lines
from dataclasses import dataclass
from typing import List, Optional, Dict, Any, Tuple, Union
from enum import Enum

from ..config import get_config

logger = logging.getLogger(__name__)


class ChunkType(Enum):
    """Type of content chunk."""
    TEXT = "text"
    TABLE = "table"
    CODE_BLOCK = "code_block"
    LIST = "list"


@dataclass
class SimpleChunk:
    """
    Lightweight chunk representation.
    """
    content: str
    position: int
    token_count: int
    chunk_type: ChunkType = ChunkType.TEXT
    metadata: Dict[str, Any] = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}

    @property
    def is_table(self) -> bool:
        """Check if chunk contains a table."""
        return self.chunk_type == ChunkType.TABLE

    @property
    def is_code(self) -> bool:
        """Check if chunk contains code."""
        return self.chunk_type == ChunkType.CODE_BLOCK

    @property
    def parent_heading(self) -> Optional[str]:
        """Get parent heading if available."""
        return self.metadata.get('parent_heading')


@dataclass
class SimpleChunkingResult:
    """Result of simple chunking operation - vereinfacht ohne DocumentType."""
    chunks: List[SimpleChunk]
    total_tokens: int
    chunk_count: int
    tables_found: int = 0
    code_blocks_found: int = 0
    chunking_strategy_used: str = "standard"

    def get_summary(self) -> str:
        """Get summary of chunking result."""
        summary_parts = [
            f"Chunked into {self.chunk_count} parts",
            f"{self.total_tokens} tokens total"
        ]

        if self.tables_found > 0:
            summary_parts.append(f"{self.tables_found} tables")
        if self.code_blocks_found > 0:
            summary_parts.append(f"{self.code_blocks_found} code blocks")

        summary_parts.append(f"Strategy: {self.chunking_strategy_used}")

        return " | ".join(summary_parts)


class TableStructure:
    """Helper class to analyze and handle table structures."""

    def __init__(self, content: str, start_pos: int, end_pos: int):
        self.content = content
        self.start_pos = start_pos
        self.end_pos = end_pos
        self.lines = [line.strip() for line in content.split('\n') if line.strip()]
        self.is_valid = self._validate_table()
        self.row_count = len(self.lines) - 1 if self.is_valid else 0  # -1 for separator
        self.col_count = self._count_columns()

    def _validate_table(self) -> bool:
        """Validate if this is a proper markdown table."""
        # Shared definition, see src/markdown_utils.py.
        return is_valid_table_lines(self.lines)

    def _count_columns(self) -> int:
        """Count columns in the table."""
        if not self.lines:
            return 0

        first_line = self.lines[0]
        if first_line.startswith('|') and first_line.endswith('|'):
            return first_line.count('|') - 1
        return first_line.count('|') + 1

    def can_split_by_rows(self, max_tokens: int, chars_per_token: float) -> bool:
        """Check if table can be split by rows to fit token limit."""
        if not self.is_valid or self.row_count <= 1:
            return False

        # Estimate tokens for header + separator + one data row
        header_separator = '\n'.join(self.lines[:2])
        min_chunk_size = len(header_separator) / chars_per_token

        return min_chunk_size <= max_tokens

    def split_by_rows(self, max_tokens: int, chars_per_token: float) -> List[str]:
        """Split table by rows while keeping header."""
        if not self.can_split_by_rows(max_tokens, chars_per_token):
            return [self.content]

        header_separator = '\n'.join(self.lines[:2])
        data_rows = self.lines[2:]

        chunks = []
        current_chunk_lines = [header_separator]
        current_tokens = len(header_separator) / chars_per_token

        for row in data_rows:
            row_tokens = len(row) / chars_per_token

            if current_tokens + row_tokens > max_tokens and len(current_chunk_lines) > 1:
                # Finalize current chunk
                chunks.append('\n'.join(current_chunk_lines))
                current_chunk_lines = [header_separator, row]
                current_tokens = len(header_separator) / chars_per_token + row_tokens
            else:
                current_chunk_lines.append(row)
                current_tokens += row_tokens

        # Add final chunk
        if len(current_chunk_lines) > 1:
            chunks.append('\n'.join(current_chunk_lines))

        return chunks


class SimpleChunker:
    """
    Simple chunker with table awareness, used for all document types.

    Design:
    - Token-based splitting without complex boundary detection
    - Simple sentence splitting as fallback
    - Table-aware processing to prevent fragmentation
    - One approach for all document types
    - Minimal metadata overhead
    """

    def __init__(self, max_tokens: int = 1500, chars_per_token: float = 4.0, config=None):
        """
        Initialize simple chunker with unified approach.

        Args:
            max_tokens: Maximum tokens per chunk
            chars_per_token: Estimated characters per token
            config: Optional configuration object
        """
        self.config = config or get_config()
        self.max_tokens = max_tokens
        self.chars_per_token = chars_per_token
        self.max_chars = int(max_tokens * chars_per_token)

        logger.debug(f"SimpleChunker initialized: max_tokens={max_tokens}, chars_per_token={chars_per_token}")

    def chunk_text(self, text: str, language: str = 'en') -> SimpleChunkingResult:
        """
        Chunk text with table awareness (all document types).

        Args:
            text: Text to chunk
            language: Language code for token estimation

        Returns:
            SimpleChunkingResult
        """
        if not text or not text.strip():
            return SimpleChunkingResult(
                chunks=[],
                total_tokens=0,
                chunk_count=0,
                chunking_strategy_used="empty"
            )

        logger.info(f"=== UNIFIED CHUNKING STARTED ===")
        logger.info(f"Text length: {len(text)} chars, Target: {self.max_tokens} tokens")

        # Step 1: Identify and extract special structures
        special_structures = self._identify_special_structures(text)
        logger.info(f"Found {len([s for s in special_structures if s['type'] == 'table'])} tables, "
                   f"{len([s for s in special_structures if s['type'] == 'code'])} code blocks")

        # Step 2: Process text with structure awareness
        chunks = self._process_with_structure_awareness(text, special_structures, language)

        # Step 3: Calculate statistics
        total_tokens = sum(chunk.token_count for chunk in chunks)
        tables_found = len([c for c in chunks if c.is_table])
        code_blocks_found = len([c for c in chunks if c.is_code])

        result = SimpleChunkingResult(
            chunks=chunks,
            total_tokens=total_tokens,
            chunk_count=len(chunks),
            tables_found=tables_found,
            code_blocks_found=code_blocks_found,
            chunking_strategy_used="unified_table_aware"
        )

        logger.info(f"=== UNIFIED CHUNKING COMPLETED ===")
        logger.info(f"Result: {result.get_summary()}")

        return result

    def _identify_special_structures(self, text: str) -> List[Dict[str, Any]]:
        """Identify tables, code blocks, and other special structures."""
        structures = []

        # Find markdown tables.
        # NOTE: the character class for the separator row must NOT contain \s,
        # because \s matches newlines: such a pattern swallows the line break
        # and grabs the first '|' of the data row, producing an inconsistent
        # column count, so that no table is recognised as valid.
        table_pattern = r'(\|[^\n]*\|[ \t]*\n\|[ \t\-\|:]*\|[ \t]*(?:\n\|[^\n]*\|[ \t]*)*)'
        for match in re.finditer(table_pattern, text, re.MULTILINE):
            table_content = match.group(1).strip()
            table_struct = TableStructure(table_content, match.start(), match.end())

            if table_struct.is_valid:
                structures.append({
                    'type': 'table',
                    'start': match.start(),
                    'end': match.end(),
                    'content': table_content,
                    'structure': table_struct
                })
                logger.debug(f"Found valid table: {table_struct.row_count} rows, {table_struct.col_count} cols")

        # Find code blocks
        code_pattern = r'```[\s\S]*?```'
        for match in re.finditer(code_pattern, text):
            structures.append({
                'type': 'code',
                'start': match.start(),
                'end': match.end(),
                'content': match.group(0)
            })

        # Sort by position
        structures.sort(key=lambda x: x['start'])

        return structures

    def _process_with_structure_awareness(
        self,
        text: str,
        structures: List[Dict[str, Any]],
        language: str
    ) -> List[SimpleChunk]:
        """Process text while preserving special structures."""
        chunks = []
        current_pos = 0
        chunk_position = 0

        for structure in structures:
            # Process text before this structure
            if current_pos < structure['start']:
                before_text = text[current_pos:structure['start']].strip()
                if before_text:
                    text_chunks = self._chunk_regular_text(before_text, language, chunk_position)
                    chunks.extend(text_chunks)
                    chunk_position += len(text_chunks)

            # Process the special structure
            if structure['type'] == 'table':
                table_chunks = self._process_table_structure(structure, language, chunk_position)
                chunks.extend(table_chunks)
                chunk_position += len(table_chunks)
            elif structure['type'] == 'code':
                code_chunk = self._process_code_structure(structure, language, chunk_position)
                chunks.append(code_chunk)
                chunk_position += 1

            current_pos = structure['end']

        # Process remaining text
        if current_pos < len(text):
            remaining_text = text[current_pos:].strip()
            if remaining_text:
                text_chunks = self._chunk_regular_text(remaining_text, language, chunk_position)
                chunks.extend(text_chunks)

        return chunks

    def _process_table_structure(
        self,
        structure: Dict[str, Any],
        language: str,
        position: int
    ) -> List[SimpleChunk]:
        """Process a table structure, potentially splitting large tables."""
        table_content = structure['content']
        table_struct = structure['structure']

        # Estimate tokens for the entire table
        estimated_tokens = self._estimate_tokens(table_content, language)

        if estimated_tokens <= self.max_tokens:
            # Table fits in one chunk
            logger.debug(f"Table fits in one chunk ({estimated_tokens} tokens)")
            return [SimpleChunk(
                content=table_content,
                position=position,
                token_count=estimated_tokens,
                chunk_type=ChunkType.TABLE,
                metadata={'rows': table_struct.row_count, 'columns': table_struct.col_count}
            )]

        # Table is too large, try to split by rows
        if table_struct.can_split_by_rows(self.max_tokens, self._get_chars_per_token(language)):
            logger.info(f"Splitting large table by rows (original: {estimated_tokens} tokens)")
            split_tables = table_struct.split_by_rows(self.max_tokens, self._get_chars_per_token(language))

            table_chunks = []
            for i, split_content in enumerate(split_tables):
                chunk_tokens = self._estimate_tokens(split_content, language)
                table_chunks.append(SimpleChunk(
                    content=split_content,
                    position=position + i,
                    token_count=chunk_tokens,
                    chunk_type=ChunkType.TABLE,
                    metadata={
                        'is_split_table': True,
                        'split_index': i,
                        'total_splits': len(split_tables),
                        'original_rows': table_struct.row_count,
                        'columns': table_struct.col_count
                    }
                ))

            logger.info(f"Table split into {len(table_chunks)} chunks")
            return table_chunks
        else:
            # Table can't be split properly, treat as single chunk (will exceed limit)
            logger.warning(f"Table too large and can't be split properly ({estimated_tokens} tokens > {self.max_tokens})")
            return [SimpleChunk(
                content=table_content,
                position=position,
                token_count=estimated_tokens,
                chunk_type=ChunkType.TABLE,
                metadata={
                    'oversized': True,
                    'rows': table_struct.row_count,
                    'columns': table_struct.col_count
                }
            )]

    def _process_code_structure(
        self,
        structure: Dict[str, Any],
        language: str,
        position: int
    ) -> SimpleChunk:
        """Process a code block structure."""
        code_content = structure['content']
        estimated_tokens = self._estimate_tokens(code_content, language)

        # Extract language from code block
        code_lang = None
        first_line = code_content.split('\n')[0] if '\n' in code_content else code_content
        if first_line.startswith('```') and len(first_line) > 3:
            code_lang = first_line[3:].strip()

        return SimpleChunk(
            content=code_content,
            position=position,
            token_count=estimated_tokens,
            chunk_type=ChunkType.CODE_BLOCK,
            metadata={'language': code_lang}
        )

    @staticmethod
    def _heading_of(paragraph: str) -> Optional[str]:
        """Return the heading text if the paragraph starts with a markdown heading."""
        first = paragraph.lstrip().split('\n', 1)[0].strip()
        if first.startswith('#'):
            return first.lstrip('#').strip() or None
        return None

    def _chunk_regular_text(self, text: str, language: str, start_position: int) -> List[SimpleChunk]:
        """
        Chunk regular text hierarchically.

        Preference order for split points: heading > paragraph > sentence.
        Cutting inside a train of thought costs translation quality, so a
        sentence split is only used when a single paragraph exceeds the limit.
        Every chunk also records the heading it belongs to, which is passed to
        the model as section context.
        """
        if not text.strip():
            return []

        chunks: List[SimpleChunk] = []
        paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]

        current_chunk = ""
        current_tokens = 0
        position = start_position
        current_heading: Optional[str] = None
        chunk_heading: Optional[str] = None
        # Avoid tiny context-free fragments when breaking at headings.
        min_chunk_tokens = max(1, int(self.max_tokens * 0.25))

        def flush():
            nonlocal current_chunk, current_tokens, position, chunk_heading
            if current_chunk:
                chunks.append(SimpleChunk(
                    content=current_chunk.strip(),
                    position=position,
                    token_count=current_tokens,
                    chunk_type=ChunkType.TEXT,
                    metadata={'parent_heading': chunk_heading} if chunk_heading else {}
                ))
                position += 1
                current_chunk = ""
                current_tokens = 0

        for paragraph in paragraphs:
            heading = self._heading_of(paragraph)
            if heading:
                # A heading is the strongest split point: start a new chunk
                # unless the current one is still very small.
                if current_chunk and current_tokens >= min_chunk_tokens:
                    flush()
                current_heading = heading

            if not current_chunk:
                chunk_heading = current_heading

            para_tokens = self._estimate_tokens(paragraph, language)

            if para_tokens > self.max_tokens:
                # Single paragraph too large -> last resort: split by sentences
                flush()
                sentence_chunks = self._split_paragraph_by_sentences(paragraph, language, position)
                for sc in sentence_chunks:
                    sc.metadata = dict(sc.metadata or {})
                    if current_heading:
                        sc.metadata['parent_heading'] = current_heading
                chunks.extend(sentence_chunks)
                position += len(sentence_chunks)
                chunk_heading = current_heading

            elif current_tokens + para_tokens > self.max_tokens:
                flush()
                chunk_heading = current_heading
                current_chunk = paragraph
                current_tokens = para_tokens

            else:
                if current_chunk:
                    current_chunk += "\n\n" + paragraph
                else:
                    current_chunk = paragraph
                current_tokens += para_tokens

        flush()
        return chunks

    def _split_paragraph_by_sentences(self, paragraph: str, language: str, start_position: int) -> List[SimpleChunk]:
        """Split large paragraph by sentences."""
        # Split *after* the sentence-ending punctuation so that it stays with
        # the sentence. Splitting on r'[.!?]\s+' would consume the mark,
        # which silently deletes every '.', '!' and '?' at a split point and
        # turns '!'/'?' into '.' when the parts are joined again.
        sentences = re.split(r'(?<=[.!?])\s+', paragraph)
        sentences = [s.strip() for s in sentences if s.strip()]

        chunks = []
        current_chunk = ""
        current_tokens = 0
        position = start_position

        for sentence in sentences:
            sentence_tokens = self._estimate_tokens(sentence, language)

            if current_tokens + sentence_tokens > self.max_tokens and current_chunk:
                # Finalize current chunk
                chunks.append(SimpleChunk(
                    content=current_chunk.strip(),
                    position=position,
                    token_count=current_tokens,
                    chunk_type=ChunkType.TEXT
                ))
                position += 1
                current_chunk = sentence
                current_tokens = sentence_tokens
            else:
                if current_chunk:
                    # Sentences already carry their punctuation.
                    current_chunk += " " + sentence
                else:
                    current_chunk = sentence
                current_tokens += sentence_tokens

        # Add final chunk
        if current_chunk:
            chunks.append(SimpleChunk(
                content=current_chunk.strip(),
                position=position,
                token_count=current_tokens,
                chunk_type=ChunkType.TEXT
            ))

        # Mark the parts so that reassembly joins them back into ONE paragraph
        # instead of inserting a blank line at every sentence split point.
        if len(chunks) > 1:
            for idx, chunk in enumerate(chunks):
                chunk.metadata = dict(chunk.metadata or {})
                chunk.metadata.update({
                    'is_split_paragraph': True,
                    'split_index': idx,
                    'total_splits': len(chunks),
                })

        return chunks

    def _estimate_tokens(self, text: str, language: str) -> int:
        """Estimate token count for text."""
        chars_per_token = self._get_chars_per_token(language)
        return max(1, int(len(text) / chars_per_token))

    def _get_chars_per_token(self, language: str) -> float:
        """Get characters per token for language with config support."""
        # First try to get from config
        if hasattr(self.config, 'get_chars_per_token'):
            return self.config.get_chars_per_token(language)

        # Fallback to simple mapping
        lang_mapping = {
            'en': 4.0,
            'de': 3.5,
            'fr': 3.8,
            'es': 3.9,
            'it': 3.9,
            'pt': 3.9,
            'nl': 3.6,
            'ru': 3.2,
            'zh': 1.5,
            'ja': 1.8,
            'ko': 2.0,
            'ar': 3.0,
            'hi': 2.5
        }
        return lang_mapping.get(language, self.chars_per_token)


# Backwards compatibility function - vereinfacht ohne DocumentType awareness
def simple_chunk_text(
    text: str,
    max_tokens: int = 1500,
    language: str = 'en',
    chars_per_token: Optional[float] = None
) -> SimpleChunkingResult:
    """
    Simple function to chunk text with table awareness (all document types).

    Args:
        text: Text to chunk
        max_tokens: Maximum tokens per chunk
        language: Language code
        chars_per_token: Optional override for chars per token

    Returns:
        SimpleChunkingResult
    """
    config = get_config()

    if chars_per_token is None:
        chars_per_token = config.get_chars_per_token(language)

    chunker = SimpleChunker(max_tokens=max_tokens, chars_per_token=chars_per_token, config=config)

    # Use unified chunking for all content
    return chunker.chunk_text(text, language)


# Migration and compatibility functions

def migrate_from_smart_chunker_result(smart_result) -> SimpleChunkingResult:
    """
    Migrate from SmartChunkerResult to SimpleChunkingResult format.

    Args:
        smart_result: Result from legacy SmartChunker

    Returns:
        SimpleChunkingResult with converted data
    """
    try:
        # Extract chunks and convert them
        chunks = []
        if hasattr(smart_result, 'chunks') and smart_result.chunks:
            for i, chunk in enumerate(smart_result.chunks):
                # Convert to SimpleChunk format
                chunk_type = ChunkType.TEXT  # Default
                if hasattr(chunk, 'is_table') and chunk.is_table:
                    chunk_type = ChunkType.TABLE
                elif hasattr(chunk, 'is_code') and chunk.is_code:
                    chunk_type = ChunkType.CODE_BLOCK

                simple_chunk = SimpleChunk(
                    content=getattr(chunk, 'content', ''),
                    position=getattr(chunk, 'position', i),
                    token_count=getattr(chunk, 'token_count', 0),
                    chunk_type=chunk_type,
                    metadata=getattr(chunk, 'metadata', {})
                )
                chunks.append(simple_chunk)

        # Calculate statistics
        total_tokens = getattr(smart_result, 'total_tokens', sum(c.token_count for c in chunks))
        chunk_count = getattr(smart_result, 'chunk_count', len(chunks))

        # Count tables and code blocks
        tables_found = len([c for c in chunks if c.chunk_type == ChunkType.TABLE])
        code_blocks_found = len([c for c in chunks if c.chunk_type == ChunkType.CODE_BLOCK])

        result = SimpleChunkingResult(
            chunks=chunks,
            total_tokens=total_tokens,
            chunk_count=chunk_count,
            tables_found=tables_found,
            code_blocks_found=code_blocks_found,
            chunking_strategy_used="migrated_from_smart"
        )

        logger.info(f"Migrated SmartChunker result: {result.get_summary()}")
        return result

    except Exception as e:
        logger.error(f"Migration from SmartChunker failed: {e}")
        # Return empty result as fallback
        return SimpleChunkingResult(chunks=[], total_tokens=0, chunk_count=0)


# Utility function for estimating chunks needed - vereinfacht
def estimate_chunks_needed(
    text: str,
    max_tokens: int = 1500,
    language: str = 'en'
) -> int:
    """
    Estimate how many chunks will be needed for given text (all document types).

    Args:
        text: Text to estimate chunks for
        max_tokens: Maximum tokens per chunk
        language: Language code for token estimation

    Returns:
        Estimated number of chunks needed
    """
    if not text or not text.strip():
        return 0

    config = get_config()
    chars_per_token = config.get_chars_per_token(language)

    # Estimate total tokens
    total_tokens = len(text) / chars_per_token

    # Calculate chunks needed
    chunks_needed = max(1, int(total_tokens / max_tokens)) + 1

    return chunks_needed


def estimate_processing_time(
    text: str,
    language: str = 'en',
    chunks_per_minute: float = 2.0
) -> float:
    """
    Estimate processing time for translation (all document types).

    Args:
        text: Text to process
        language: Language code
        chunks_per_minute: Estimated chunks processed per minute

    Returns:
        Estimated time in minutes
    """
    chunks_needed = estimate_chunks_needed(text, language=language)
    return chunks_needed / chunks_per_minute