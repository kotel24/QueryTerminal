#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import sqlite3
import time
import csv
from dataclasses import dataclass
from typing import List, Tuple, Optional, Callable, Dict

BANNER = "Query Terminal (SQLite). Type .help"
HELP = """\
Meta-commands:
  .help                 Show this help
  .exit                 Exit
  .open <path>          Open/create SQLite database file
  .tables [name]        List tables; if <name> given, print content
  .schema [name]        Print schema
  .dump                 Dump database as SQL
  .import <csv> <table> Import data from CSV file

SQL:
  - Multiline input supported; finish with ';'
"""

HISTFILE = os.path.expanduser("~/.qt_history")
HISTLEN = 2000


def _format_table(headers: List[str], rows: List[Tuple]) -> str:
    if not headers:
        return "(no columns)"

    clean_rows = []
    for r in rows:
        clean_rows.append([str("" if v is None else v).replace("\n", "\\n") for v in r])

    widths = [len(h) for h in headers]
    for r in clean_rows:
        for i, v in enumerate(r):
            if i < len(widths):
                widths[i] = max(widths[i], len(v))

    def fmt_row(row_data: List[str]) -> str:
        return " | ".join(v.ljust(widths[i]) for i, v in enumerate(row_data))

    sep = "-+-".join("-" * w for w in widths)
    out = [fmt_row(headers), sep]
    out.extend(fmt_row(r) for r in clean_rows)
    return "\n".join(out)


def _print_table(headers: List[str], rows: List[Tuple]) -> None:
    if not headers:
        print("(no columns)")
        return
    print(_format_table(headers, rows))
    if not rows:
        print("(empty)")


@dataclass
class Runtime:
    conn: sqlite3.Connection
    path: str


class QT:
    def __init__(self, path: str = ":memory:") -> None:
        self.rt = self._open(path)
        self._buffer: List[str] = []
        self._meta: Dict[str, Callable[[List[str]], None]] = {
            ".help": self._m_help,
            ".exit": self._m_exit,
            ".open": self._m_open,
            ".tables": self._m_tables,
            ".schema": self._m_schema,
            ".dump": self._m_dump,
            ".import": self._m_import,
        }
        self._timer = False
        self._readline = None

    def _prompt(self) -> str:
        name = os.path.basename(self.rt.path)
        return f"{name}$ " if not self._buffer else "... "

    def _read_line(self) -> Optional[str]:
        try:
            return input(self._prompt()).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            self._m_exit([])
            return None

    def _setup_readline(self) -> None:
        try:
            import readline
        except ImportError:
            try:
                import pyreadline3 as readline
            except ImportError:
                return

        self._readline = readline
        try:
            readline.read_history_file(HISTFILE)
        except FileNotFoundError:
            pass
        readline.set_history_length(HISTLEN)
        try:
            delims = readline.get_completer_delims()
            for ch in "._":
                delims = delims.replace(ch, "")
            readline.set_completer_delims(delims)
        except Exception:
            pass
        readline.set_completer(self._completer_func)
        try:
            if 'libedit' in readline.__doc__:
                readline.parse_and_bind("bind ^I rl_complete")
            else:
                readline.parse_and_bind("tab: complete")
        except Exception:
            pass

    def _save_history(self) -> None:
        if self._readline:
            try:
                self._readline.write_history_file(HISTFILE)
            except Exception:
                pass

    def _list_tables(self) -> List[str]:
        try:
            q = "SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%'"
            return [r[0] for r in self.rt.conn.execute(q)]
        except:
            return []

    def _completer_func(self, text: str, state: int) -> Optional[str]:
        if not self._readline:
            return None
        buffer = self._readline.get_line_buffer()

        if buffer.lstrip().startswith("."):
            options = [c for c in self._meta.keys() if c.startswith(text)]
        else:
            keywords = ["SELECT", "FROM", "WHERE", "INSERT", "UPDATE", "DELETE", "Create", "TABLE", "JOIN"]
            tables = self._list_tables()
            pool = keywords + tables
            options = [w for w in pool if w.upper().startswith(text.upper())]

        options = sorted(list(set(options)))
        return options[state] if state < len(options) else None

    def _m_help(self, _: List[str]) -> None:
        print(HELP)

    def _m_exit(self, _: List[str]) -> None:
        self._save_history()
        self.rt.conn.close()
        print("Bye.")
        sys.exit(0)

    def _m_open(self, args: List[str]) -> None:
        if not args:
            print("Usage: .open <path>")
            return
        self.rt.conn.close()
        self.rt = self._open(args[0])
        print(f"Opened {self.rt.path}")

    def _m_tables(self, args: List[str]) -> None:
        try:
            if not args:
                names = self._list_tables()
                print(" ".join(names) if names else "(no tables)")
                return
            name = args[0]
            quoted = f'"{name}"'
            cur = self.rt.conn.execute(f"SELECT * FROM {quoted}")
            headers = [d[0] for d in cur.description]
            rows = cur.fetchall()
            _print_table(headers, rows)
        except sqlite3.Error as e:
            print(f"SQL error: {e}")

    def _m_schema(self, args: List[str]) -> None:
        q = "SELECT sql FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%'"
        if args:
            q += f" AND name = '{args[0]}'"
        try:
            rows = self.rt.conn.execute(q).fetchall()
            for (sql,) in rows:
                if sql:
                    print(sql.strip() + ";\n")
        except sqlite3.Error as e:
            print(e)

    def _m_dump(self, _: List[str]) -> None:
        try:
            for line in self.rt.conn.iterdump():
                print(line)
        except sqlite3.Error as e:
            print(f"SQL error: {e}")

    def _m_import(self, args: List[str]) -> None:
        if len(args) < 2:
            print("Usage: .import <filename.csv> <table_name>")
            return

        filepath, table_name = args[0], args[1]
        if not os.path.exists(filepath):
            print(f"Error: File '{filepath}' not found.")
            return

        try:
            with open(filepath, 'r', encoding='utf-8-sig') as f:
                reader = csv.reader(f)
                headers = next(reader, None)
                if not headers:
                    print("Error: CSV file is empty.")
                    return

                safe_headers = [h.strip().replace(" ", "_") for h in headers]
                cols_def = ", ".join([f'"{col}" TEXT' for col in safe_headers])

                self.rt.conn.execute(f"CREATE TABLE IF NOT EXISTS {table_name} ({cols_def})")

                placeholders = ", ".join(["?"] * len(safe_headers))
                query = f"INSERT INTO {table_name} VALUES ({placeholders})"

                self.rt.conn.executemany(query, reader)
                self.rt.conn.commit()
                print(f"Success: Imported data into '{table_name}'.")
        except Exception as e:
            print(f"Import failed: {e}")

    def _exec_sql(self, sql: str) -> None:
        start = time.perf_counter()
        try:
            cur = self.rt.conn.execute(sql)
            if cur.description:
                headers = [d[0] for d in cur.description]
                rows = cur.fetchall()
                _print_table(headers, rows)
            else:
                print("OK")
        except sqlite3.Error as e:
            try:
                self.rt.conn.rollback()
            except sqlite3.Error:
                pass
            print(f"SQL error: {e}")
        finally:
            if self._timer:
                ms = (time.perf_counter() - start) * 1000
                print(f"(Time: {ms:.2f} ms)")

    def _open(self, path: str) -> Runtime:
        if path != ":memory:":
            d = os.path.dirname(os.path.abspath(path))
            if d and not os.path.exists(d):
                os.makedirs(d, exist_ok=True)
        conn = sqlite3.connect(path)
        conn.isolation_level = None
        return Runtime(conn=conn, path=path)

    def run(self) -> None:
        self._setup_readline()
        print(BANNER)
        while True:
            line = self._read_line()
            if line is None:
                break
            if not line:
                continue

            if not self._buffer and line.startswith("."):
                parts = line.split()
                cmd = parts[0]
                if cmd in self._meta:
                    self._meta[cmd](parts[1:])
                else:
                    print(f"Unknown command: {cmd}")

                if self._readline:
                    self._readline.add_history(line)
                self._save_history()
                continue

            self._buffer.append(line)
            if line.endswith(";"):
                sql = "\n".join(self._buffer).strip()
                self._buffer.clear()
                self._exec_sql(sql)
                if self._readline:
                    self._readline.add_history(sql.replace("\n", " "))
                self._save_history()
# @brief
def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else ":memory:"
    app = QT(path)
    try:
        app.run()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()