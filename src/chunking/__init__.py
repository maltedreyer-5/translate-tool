# SPDX-FileCopyrightText: 2026 Malte Dreyer
# SPDX-License-Identifier: MIT
"""
Chunking module for the document translation system.
One chunking approach for all document types.
"""

from .simple_chunker import (
    # Core classes
    SimpleChunker,
    SimpleChunk,
    SimpleChunkingResult,
    ChunkType,

    # Utility functions - vereinfacht ohne DocumentType
    simple_chunk_text,
    estimate_chunks_needed,
    estimate_processing_time,

    # Migration helpers
    migrate_from_smart_chunker_result,

    # Table structure helper
    TableStructure
)

# Vereinfachtes __all__ ohne PDF-spezifische Exports
__all__ = [
    # Core chunking classes
    'SimpleChunker',
    'SimpleChunk',
    'SimpleChunkingResult',
    'ChunkType',

    # Utility functions
    'simple_chunk_text',
    'estimate_chunks_needed',
    'estimate_processing_time',

    # Migration and compatibility
    'migrate_from_smart_chunker_result',

    # Helper classes
    'TableStructure'
]