"""Owned saved process and actual generated minimized report for browser checks."""
import json
import sys

from process_logs_app import app, scratch, ROOT
sys.path.insert(0, str(ROOT / 'tests'))
from test_process_shadow_review import generated_report, model
from shared.lifespan import extend_lifespan
from shared.process_catalog import CatalogWrite
from server.workflow_evidence_routes import _catalog


def seed():
    saved = _catalog.save(CatalogWrite(title='Support shadow review', process_model=model(), user_confirmed=True))
    packet = _catalog.export(saved['workflow_id'], saved['revision'])
    report = generated_report(packet, scratch / 'project')
    (scratch / 'shadow-report.json').write_text(json.dumps(report), encoding='utf-8')


extend_lifespan(app, startup=seed)
