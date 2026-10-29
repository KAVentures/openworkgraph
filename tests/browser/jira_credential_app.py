"""Owned credential replacement controls and counts; no real provider access."""
import json
import os
from pathlib import Path
import jira_retained_app as base
from test_jira_credential_rebind import RenewableProvider, OLD, NEW

app = base.app
provider = RenewableProvider()
base.provider = provider
scratch = Path(os.environ['OWG_BROWSER_TEST_DIR'])
counts = {'history_reads': 0, 'issue_reads': 0, 'account_reads': 0}
actual = provider.request

def recorded(request):
    path = request.url.path
    if path.endswith('/changelog'):
        counts['history_reads'] += 1
    elif '/issue/' in path:
        counts['issue_reads'] += 1
    elif path.endswith('/myself'):
        counts['account_reads'] += 1
    (scratch/'provider-counts.json').write_text(json.dumps(counts))
    return actual(request)
provider.request = recorded

@app.middleware('http')
async def owned_configured_credential(request, call_next):
    control = scratch/'credential-control.json'
    mode = json.loads(control.read_text()).get('mode') if control.exists() else 'old'
    provider.expected = OLD if mode == 'old' else NEW if mode == 'renewed' else 'owned-other-token'
    provider.account = 'other-owned-account' if mode == 'other' else 'owned-account'
    os.environ['OWG_JIRA_OAUTH_ACCESS_TOKEN'] = provider.expected
    return await call_next(request)
