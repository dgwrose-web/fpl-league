#!/usr/bin/env python3
"""Inline a data.json into index.html to produce a single self-contained file."""
import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
data = Path(sys.argv[1]).read_text()
html = (ROOT / "docs" / "index.html").read_text()
html = html.replace("<script>\n/* ============================================================ utilities */",
                    "<script>\nwindow.__FPL_DATA__ = " + data + ";\n/* ==== utilities */", 1)
Path(sys.argv[2]).write_text(html)
print("wrote", sys.argv[2], f"{len(html)/1024:.0f} KB")
