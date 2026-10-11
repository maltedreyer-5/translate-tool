#!/usr/bin/env python
# SPDX-FileCopyrightText: 2026 Malte Dreyer
# SPDX-License-Identifier: MIT
"""
Entry point of the document translation interface.
"""

import sys
from pathlib import Path

# Make the project root importable (package 'src')
sys.path.insert(0, str(Path(__file__).parent))

def main():
    """Start the translation application"""
    try:
        # The application lives in src/app.py
        from src.app import main as app_main
        app_main()
    except ImportError as e:
        print(f"Could not import the application modules: {e}")
        print("\nInstall the dependencies with: pip install -r requirements.txt")

        sys.exit(1)
    except KeyboardInterrupt:
        print("\n\nApplication stopped by user.")
        sys.exit(0)
    except Exception as e:
        print(f"Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()