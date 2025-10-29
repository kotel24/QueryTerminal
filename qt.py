#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import sqlite3
import time
from dataclasses import dataclass
from typing import List, Tuple, Optional, Callable

# ───────── constants ───────── #

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
HISTLEN = 2000

# ───────── formatting ───────── #

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
        print("(no columns)"); return
    print(_format_table(headers, rows))
    if not rows:
        print("(empty)")

# ───────── state ───────── #

@dataclass
class Runtime:
    conn: sqlite3.Connection
    path: str

# ───────── app ───────── #

class QT:
    """SQLite console client with meta-commands, history, and auto-complete (macOS/Unix)."""

    def __init__(self, path: str = ":memory:") -> None:
        self.rt = self._open(path)
        self._buffer: List[str] = []
        self._meta: dict[str, Callable[[List[str]], None]] = {
            ".help": self._m_help,
            ".exit": self._m_exit,
            ".open": self._m_open,
            ".tables": self._m_tables,
            ".schema": self._m_schema,
            ".dump": self._m_dump,
        }
        self._timer = False
        self._readline = None  # set in _setup_readline()

    # ── I/O & readline ── #

    def _prompt(self) -> str:
        name = os.path.basename(self.rt.path)
        return f"{name}$ " if not self._buffer else "... "

    def _read_line(self) -> Optional[str]:
        try:
            return input(self._prompt()).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            self._m_exit([])
            return None  # unreachable

    def _setup_readline(self) -> None:
        """Enable history + bash-like keybindings + completion on macOS/Unix."""
        try:
            import readline  # builtin on macOS
        except Exception:
            self._readline = None
            return

        self._readline = readline

        # history persistence
        try:
            readline.read_history_file(HISTFILE)
        except FileNotFoundError:
            pass
        readline.set_history_length(HISTLEN)

        # treat '.' and '_' as word chars (table.column, snake_case)
        try:
            delims = readline.get_completer_delims()
            for ch in "._":
                delims = delims.replace(ch, "")
            readline.set_completer_delims(delims)
        except Exception:
            pass

        # bash-like keybindings
        try:
            readline.parse_and_bind("set editing-mode emacs")
            readline.parse_and_bind("tab: complete")
            readline.parse_and_bind('"\\e[A": history-search-backward')  # Up
            readline.parse_and_bind('"\\e[B": history-search-forward')   # Down
            readline.parse_and_bind('"\\C-a": beginning-of-line')
            readline.parse_and_bind('"\\C-e": end-of-line')
            readline.parse_and_bind('"\\C-k": kill-line')
            readline.parse_and_bind('"\\C-u": unix-line-discard')
            readline.parse_and_bind('"\\C-r": reverse-search-history')
            readline.parse_and_bind('"\\C-l": clear-screen')
        except Exception:
            pass

        # completer
        def completer(text: str, state: int) -> Optional[str]:
            try:
                return self._complete(text, state)
            except Exception:
                return None
        readline.set_completer(completer)

    def _save_history(self) -> None:
        if not self._readline:
            return
        try:
            self._readline.write_history_file(HISTFILE)
        except Exception:
            pass

    # ── completion helpers ── #

    def _list_tables(self) -> List[str]:
        q = ("SELECT name FROM sqlite_master "
             "WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' ORDER BY 1")
        return [r[0] for r in self.rt.conn.execute(q)]

    def _list_columns(self, table: str) -> List[str]:
        try:
            quoted = '"' + table.replace('"', '""') + '"'
            cur = self.rt.conn.execute(f"PRAGMA table_info({quoted})")
            return [row[1] for row in cur.fetchall()]
        except sqlite3.Error:
            return []

    def _all_columns(self) -> List[str]:
        cols: List[str] = []
        for t in self._list_tables():
            cols.extend(self._list_columns(t))
        seen = set(); uniq = []
        for c in cols:
            if c not in seen:
                seen.add(c); uniq.append(c)
        return uniq

    def _complete(self, text: str, state: int) -> Optional[str]:
        import re
        rl = self._readline
        if rl is None:
            return None
        buffer = rl.get_line_buffer()
        beg = rl.get_begidx()
        before = buffer[:beg].strip()

        # meta-commands
        if before.startswith("."):
            tokens = before.split()
            if len(tokens) <= 1:
                candidates = sorted([m for m in self._meta if m.startswith(text or "")])
            else:
                cmd = tokens[0]
                if cmd in (".tables", ".schema"):
                    candidates = [n for n in self._list_tables() if n.startswith(text or "")]
                else:
                    candidates = []
        else:
            # SQL heuristics
            toks = re.findall(r"[A-Za-z_][A-Za-z0-9_]*|[.,()]|<=|>=|<>|!=|=|\\*|;", before, re.IGNORECASE)
            toks = [t for t in toks if t != ";"]
            upper_tokens = [t.upper() for t in toks if re.match(r"[A-Za-z_]", t)]
            want_table = want_column = False
            KEY_TABLE = {"FROM", "JOIN", "UPDATE", "INTO", "TABLE"}
            KEY_COLUMN = {"SELECT", "WHERE", "ON", "GROUP", "ORDER", "HAVING", "SET"}
            for i in range(len(upper_tokens) - 1, -1, -1):
                tok = upper_tokens[i]
                if tok in KEY_TABLE: want_table = True; break
                if tok in KEY_COLUMN: want_column = True; break

            m = re.search(r'([A-Za-z_][A-Za-z0-9_]*)\.$', before)
            if m:
                tname = m.group(1)
                cols = self._list_columns(tname)
                candidates = [c for c in cols if c.startswith(text or "")]
            elif want_table:
                candidates = [n for n in self._list_tables() if n.startswith(text or "")]
            elif want_column:
                candidates = [c for c in self._all_columns() if c.startswith(text or "")]
            else:
                KW = [
                    "SELECT", "FROM", "WHERE", "JOIN", "LEFT", "RIGHT", "INNER", "OUTER",
                    "GROUP", "BY", "ORDER", "HAVING", "LIMIT", "INSERT", "INTO", "VALUES",
                    "UPDATE", "SET", "DELETE", "CREATE", "TABLE", "VIEW", "INDEX",
                ]
                candidates = [k for k in KW if k.startswith((text or "").upper())]
                candidates += [n for n in self._list_tables() if n.startswith(text or "")]
        candidates = sorted(set(candidates))
        return candidates[state] if state < len(candidates) else None

    # ── meta-commands ── #

    def _m_help(self, _: List[str]) -> None:
        print(HELP)

    def _m_exit(self, _: List[str]) -> None:
        try:
            self._save_history()
            self.rt.conn.close()
        finally:
            print("Bye."); sys.exit(0)

    def _m_open(self, args: List[str]) -> None:
        if not args:
            print("Usage: .open <path>"); return
        self.rt = self._open(args[0])
        print(f"Opened {self.rt.path}")

    def _m_tables(self, args: List[str]) -> None:
        """ .tables -> list names; .tables <name> -> print contents """
        try:
            if not args:
                names = self._list_tables()
                print(" ".join(names) if names else "(no tables)")
                return
            name = args[0]
            exists = self.rt.conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name = ?",
                (name,)
            ).fetchone()
            if not exists:
                print(f"(no such table or view: {name})"); return
            quoted = '"' + name.replace('"', '""') + '"'
            cur = self.rt.conn.execute(f"SELECT * FROM {quoted}")
            headers = [d[0] for d in cur.description]
            rows = cur.fetchall()
            _print_table(headers, rows)
        except sqlite3.Error as e:
            print(f"SQL error: {e}")

    def _m_schema(self, args: List[str]) -> None:
        """ .schema -> all schema; .schema <name> -> specific object """
        try:
            if not args:
                q = ("SELECT name, sql FROM sqlite_master "
                     "WHERE type IN ('table','view','index','trigger') "
                     "AND name NOT LIKE 'sqlite_%' ORDER BY type, name")
                rows = self.rt.conn.execute(q).fetchall()
                if not rows:
                    print("(empty schema)"); return
                for name, sql in rows:
                    print(f"-- {name}")
                    print((sql or "(no schema)").strip() + ";\n")
                return
            name = args[0]
            q = ("SELECT sql FROM sqlite_master "
                 "WHERE name = ? AND type IN ('table','view','index','trigger')")
            rows = self.rt.conn.execute(q, (name,)).fetchall()
            if not rows:
                print(f"(no such object: {name})"); return
            for (sql,) in rows:
                print((sql or "(no schema)").strip() + ";")
        except sqlite3.Error as e:
            print(f"SQL error: {e}")

    def _m_dump(self, _: List[str]) -> None:
        try:
            for line in self.rt.conn.iterdump():
                print(line)
        except sqlite3.Error as e:
            print(f"SQL error: {e}")

    # ── SQL exec ── #

    def _exec_sql(self, sql: str) -> None:
        start = time.perf_counter()
        try:
            cur = self.rt.conn.execute(sql)
            if cur.description is None:
                print("OK")
            else:
                headers = [d[0] for d in cur.description]
                rows = cur.fetchall()
                _print_table(headers, rows)
        except sqlite3.Error as e:
            try: self.rt.conn.rollback()
            except sqlite3.Error: pass
            print(f"SQL error: {e}")
        finally:
            if self._timer:
                ms = (time.perf_counter() - start) * 1000
                print(f"(Time: {ms:.2f} ms)")

    # ── lifecycle ── #

    def _open(self, path: str) -> Runtime:
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        conn = sqlite3.connect(path)
        conn.isolation_level = None  # autocommit
        return Runtime(conn=conn, path=path)

    def _handle_meta(self, line: str) -> bool:
        parts = line.split()
        fn = self._meta.get(parts[0])
        if not fn:
            return False
        try:
            fn(parts[1:])
        except sqlite3.Error as e:
            print(f"Error: {e}")
        return True

    def run(self) -> None:
        self._setup_readline()
        print(BANNER)
        while True:
            line = self._read_line()
            if line is None:
                break
            if not line:
                continue

            # meta
            if not self._buffer and line.startswith("."):
                if not self._handle_meta(line):
                    print("Unknown command. Type .help")
                if self._readline and line:
                    self._readline.add_history(line)
                self._save_history()
                continue

            # multiline SQL
            self._buffer.append(line)
            if line.endswith(";"):
                sql = "\n".join(self._buffer).strip()
                self._buffer.clear()
                self._exec_sql(sql)
                if self._readline and sql:
                    # store completed SQL as single history entry (flattened)
                    self._readline.add_history(sql.replace("\n", " "))
                self._save_history()

# ───────── entrypoint ───────── #

def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else ":memory:"
    QT(path).run()

if __name__ == "__main__":
    main()