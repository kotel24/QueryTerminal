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
