"""Source compatibility checks."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).parent.parent / "custom_components" / "ha_watcher"


@pytest.mark.parametrize("path", sorted(PACKAGE.glob("*.py")), ids=lambda p: p.name)
def test_modules_parse_as_python_3_13(path: Path) -> None:
    """No Python 3.14-only syntax (such as PEP 758 except clauses) sneaks in."""
    ast.parse(path.read_text(encoding="utf-8"), str(path), feature_version=(3, 13))
