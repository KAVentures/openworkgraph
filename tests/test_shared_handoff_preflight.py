"""Contract checks for the authenticated, read-only shared handoff preflight UI."""
from pathlib import Path

PAGE = Path(__file__).resolve().parents[1] / "gateway" / "processes.html"


def test_handoff_preflight_is_inside_authenticated_selected_process():
    html = PAGE.read_text(encoding="utf-8")
    selected = html.index('<section id="selected" hidden>')
    preflight = html.index('id="handoffPreflight"')
    end = html.index('</section>', selected)
    assert selected < preflight < end
    assert 'id="handoffPacket"' in html
    assert 'id="handoffStatus" role="status" aria-live="polite"' in html


def test_handoff_preflight_reads_fresh_authorized_process_without_upload():
    html = PAGE.read_text(encoding="utf-8")
    block = html.split('let handoffGeneration=0;', 1)[1].split('      })();', 1)[0]
    assert 'await call("/v1/human/processes/"+encodeURIComponent(p.workflow_id))' in block
    assert 'fetch(' not in block
    assert 'file.text()' in block
    assert 'file.size>262144' in block
    assert 'current.latest_revision!==p.revision' in block
    assert 'current.process.content_sha256!==p.content_sha256' in block
    assert 'current.archived' in block
    assert 'ticket!==handoffGeneration||selected!==chosen' in block
    assert 'execution_authorized!==false' in block
    assert 'source_read_authorized!==false' in block


def test_handoff_preflight_invalidates_on_session_and_selection_changes():
    html = PAGE.read_text(encoding="utf-8")
    assert 'function clear(releaseBusy=false) {\n          resetHandoff(' in html
    assert 'function render(data) {\n          resetHandoff(' in html
    assert 'handoffPacket").addEventListener("change",()=>resetHandoff(' in html
    assert 'Consent, owner approval, source access, retention and execution authorization remain unverified.' in html
