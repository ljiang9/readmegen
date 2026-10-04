"""Enables `python -m readmegen`."""
import os
import sys

try:
    from .readmegen import main
except ImportError:  # running the file directly: python readmegen/__main__.py
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from readmegen import main

sys.exit(main())
