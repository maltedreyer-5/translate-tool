# SPDX-FileCopyrightText: 2026 Malte Dreyer
# SPDX-License-Identifier: MIT
"""
Language codes and the language names used inside model prompts.

The prompt (prompts/base_translation.txt) is written in German, so the names
inserted into it are German as well. They are independent of the language of
the user interface: changing the interface language must not change what the
model is told.
"""

from typing import Dict

from .prompt_texts import LANGUAGE_NAMES, TEXTS, language_name

AUTO = "auto"

# Language names as inserted into the German prompt template (compatibility;
# the per-language tables are in src/prompt_texts.py).
PROMPT_LANGUAGE_NAMES: Dict[str, str] = LANGUAGE_NAMES["de"]

PROMPT_AUTO_SOURCE = TEXTS["de"]["auto_source"]


def prompt_language_name(code: str) -> str:
    """Name of a language for use in the German prompt; unknown codes pass through."""
    return language_name(code, "de")
