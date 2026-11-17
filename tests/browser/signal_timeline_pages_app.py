"""Synthetic retained signal transitions for cursor paging browser acceptance."""
from datetime import datetime
from signal_overview_pages_app import app, logs
from server import event_log_workspace as workspace

with workspace._LOCK:
    conn = workspace._connect()
    try:
        at = datetime(2024, 3, 1)
        conn.executemany('INSERT INTO signal_transitions VALUES (?, ?, ?, ?, ?, ?)', [
            [logs[0], 'sig_0000000000000000', f'sevt_{i:024x}', at,
             'triggered' if i % 2 else 'cleared', 'a'*64]
            for i in range(205)])
    finally:
        conn.close()
