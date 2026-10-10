#!/usr/bin/env python3
"""flow_tui.py must stay parseable on Python < 3.12.

A backslash inside an f-string expression is a SyntaxError before 3.12, and the
pane then exits at once with nothing on screen. CI runs 3.12, so check the
source for it directly. Also runs standalone: python3 tests/test_py_compat.py
"""
from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "scripts" / "flow_tui.py"


def test_no_backslash_in_fstring_expressions() -> None:
    text = SRC.read_text(encoding="utf-8")
    bad = []
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.FormattedValue):
            seg = ast.get_source_segment(text, node.value) or ""
            if "\\" in seg:
                bad.append(f"line {node.value.lineno}: {seg}")
    assert not bad, "backslash in f-string expression:\n" + "\n".join(bad)


if __name__ == "__main__":
    test_no_backslash_in_fstring_expressions()
    print("ok")
