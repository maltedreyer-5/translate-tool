# SPDX-FileCopyrightText: 2026 Malte Dreyer
# SPDX-License-Identifier: MIT
"""
Data models package for the document translation system.
"""

from .document import (
    # Enums
    ElementType,

    # Data classes
    DocumentElement,
    DocumentMetadata,
    ProcessingOptions,
    MarkdownDocument,
    ProcessingResult,
)

__all__ = [
    # Enums
    'ElementType',

    # Data classes
    'DocumentElement',
    'DocumentMetadata',
    'ProcessingOptions',
    'MarkdownDocument',
    'ProcessingResult',
]