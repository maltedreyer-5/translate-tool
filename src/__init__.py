# SPDX-FileCopyrightText: 2026 Malte Dreyer
# SPDX-License-Identifier: MIT
"""
Document translation interface.
A web-based tool for translating texts and documents with local LLMs.
"""

__version__ = "1.0.0"
__author__ = "Translation Interface Team"

from .config import Config, load_config
from .llm_client import LLMClient

__all__ = [
    "Config",
    "load_config",
    "LLMClient",
    "__version__",
]