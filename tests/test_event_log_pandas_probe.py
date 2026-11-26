"""DuckDB's per-value pandas probe must not search the import path again for every bound value."""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap


def test_missing_pandas_is_remembered_once_and_binding_stays_fast():
    code = textwrap.dedent("""
        import importlib.util, sys
        missing = importlib.util.find_spec("pandas") is None
        from server import event_log_workspace  # noqa: F401
        import duckdb
        searches = []
        class Counting:
            @staticmethod
            def find_spec(name, path=None, target=None):
                if name == "pandas":
                    searches.append(name)
                return None
        sys.meta_path.insert(0, Counting)
        conn = duckdb.connect()
        rows = conn.execute("SELECT count(*) FROM (SELECT unnest(?) AS x)", [[["a", "b"]] * 5000]).fetchone()
        assert rows == (5000,)
        if missing:
            assert sys.modules.get("pandas", "absent") is None
            assert not searches, len(searches)
        print("ok")
    """)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    done = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0 and done.stdout.strip() == "ok", done.stderr
