# SPDX-FileCopyrightText: 2026 Malte Dreyer
# SPDX-License-Identifier: MIT
"""
UI module for the document translation interface.

The application builds its Gradio interface in `app.py` and uses the export
handlers from this package.
"""

from .export_handlers import (
    EnhancedExportHandler,
    ExportHandler,            # alias of EnhancedExportHandler
    create_export_handlers,
    handle_export,
)

__all__ = [
    'EnhancedExportHandler',
    'ExportHandler',
    'create_export_handlers',
    'handle_export',
]

from .. import __version__  # one version for the whole project
