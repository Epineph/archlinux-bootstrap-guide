#!/usr/bin/env python3
"""Run from the checkout without installing a Python package."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from archbootstrap.cli import entrypoint  # noqa: E402

entrypoint()
