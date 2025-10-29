#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import sqlite3
import time
from dataclasses import dataclass
from typing import List, Tuple, Optional, Callable

# ───────────────────────── constants ───────────────────────── #

BANNER = "Query Terminal (SQLite). Type .help"
HELP = """\
Meta-commands:
  .help                 Show this help
  .exit                 Exit
  .open <path>          Open/create SQLite database file
  .tables [name]        List tables/views; if <name> given, print that table/view
  .schema [name]        Print schema: all objects or the specified one
  .dump                 Dump the whole database as SQL

SQL:
  - Multiline input is supported; finish statements with ';'
"""

HISTFILE = os.path.expanduser("~/.qt_history")
HISTLEN = 1000
# ─────────────────────── utilities (format) ────────────────── #

def _format_table(headers: List[str], rows: List[Tuple]) -> str:
    if not headers:
        return "(no columns)"
    widths = [len(h) for h in headers]
    for r in rows:
        for i, v in enumerate(r):
            widths[i] = max(widths[i], len("" if v is None else str(v)))

    def fmt_row(row: Tuple | List) -> str:
        return " | ".join(str("" if v is None else v).ljust(widths[i])
                          for i, v in enumerate(row))

    sep = "-+-".join("-" * w for w in widths)
    out: List[str] = [fmt_row(headers), sep]
    out.extend(fmt_row(r) for r in rows)
    return "\n".join(out)


def _print_table(headers: List[str], rows: List[Tuple]) -> None:
    if not headers:
        print("(no columns)")
        return
    print(_format_table(headers, rows))
    if not rows:
        print("(empty)")

