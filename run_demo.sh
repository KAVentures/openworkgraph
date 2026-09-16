#!/usr/bin/env bash
set -euo pipefail
python demo_data.py
exec uvicorn server.main:app --host 127.0.0.1 --port 8787
