# SPDX-FileCopyrightText: 2026 Malte Dreyer
# SPDX-License-Identifier: MIT
"""
Translation package for the document translation system.

The runtime uses a single table-aware translation engine.
"""

from .translation_engine import (
    TranslationEngine,
    TranslationContext,
    TranslationTask,
    TranslationResult,
    TranslationStatus,
    translate_chunks,
    TableAwareTranslationEngine,
)

__all__ = [
    'TranslationEngine',
    'TranslationContext',
    'TranslationTask',
    'TranslationResult',
    'TranslationStatus',
    'translate_chunks',
    'TableAwareTranslationEngine',
    'get_translation_engine',
]

from .. import __version__  # one version for the whole project


def get_translation_engine(llm_client, config=None):
    """Return the translation engine used across the application."""
    return TableAwareTranslationEngine(llm_client, config)
