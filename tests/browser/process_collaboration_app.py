"""Loopback-only synthetic Gateway for the browser acceptance test.

No production identity verifier is replaced: this separate test app uses the
same explicit verifier injection as unit tests and an isolated temporary DB.
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from test_process_collaboration import app as build_app, model

scratch = Path(os.environ['OWG_BROWSER_TEST_DIR'])
first = {'format': 'openworkgraph.versioned-process-export.v1',
         'title': 'Shared support process', 'process_model': model().model_dump()}
(scratch / 'process.json').write_text(json.dumps(first), encoding='utf-8')
first['process_model']['goal'] = 'Resolve a selected case after independent review'
(scratch / 'process-v2.json').write_text(json.dumps(first), encoding='utf-8')
app = build_app(scratch)
