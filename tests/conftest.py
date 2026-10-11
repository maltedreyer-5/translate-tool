# SPDX-FileCopyrightText: 2026 Malte Dreyer
# SPDX-License-Identifier: MIT
"""Shared test setup."""
import os
import sys
from pathlib import Path

# Tests must not write a log file into the repository: an empty value
# disables the file handler (environment overrides config.yaml).
os.environ.setdefault("TRANSLATION_LOGGING__FILE", "")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TESTS = Path(__file__).resolve().parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))
