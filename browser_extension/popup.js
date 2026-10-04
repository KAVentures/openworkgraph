const API = "http://127.0.0.1:8787";
const ext = globalThis.browser ?? globalThis.chrome;
const SECRET_KEY = "openworkgraph_browser_pairing_secret";

async function currentSecret() {
  try {
    const stored = await ext.storage.local.get(SECRET_KEY);
    return String(stored?.[SECRET_KEY] || "");
  } catch (_) { return ""; }
}

async function refreshStatus() {
  const secret = await currentSecret();
  const intro = document.querySelector('#intro');
  const status = document.querySelector('#status');
  if (secret) {
    intro.textContent = 'Paired with this OpenWorkGraph installation.';
    intro.className = 'ok';
    status.textContent = 'If the dashboard still shows the sensor as disconnected, keep OpenWorkGraph running and reload this extension once.';
  } else {
    intro.textContent = 'Pairing is required before browser evidence can leave the extension.';
    intro.className = 'bad';
  }
}

async function oneClickPair() {
  const status = document.querySelector('#status');
  status.textContent = 'Opening OpenWorkGraph approval…';
  try {
    const started = await fetch(`${API}/v1/browser-pair/start`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: '{}',
      cache: 'no-store'
    });
    if (!started.ok) throw new Error('OpenWorkGraph is not reachable on this computer.');
    const request = await started.json();
    const requestId = String(request?.request_id || '');
    const approveUrl = String(request?.approve_url || '');
    if (!requestId || !approveUrl) throw new Error('OpenWorkGraph did not create a pairing request.');
    await ext.tabs.create({url: approveUrl});
    const deadline = Date.now() + 120000;
    while (Date.now() < deadline) {
      await new Promise(resolve => setTimeout(resolve, 900));
      const response = await fetch(`${API}/v1/browser-pair/status?request=${encodeURIComponent(requestId)}`, {cache: 'no-store'});
      if (response.status === 404) throw new Error('The pairing request expired. Try again.');
      if (!response.ok) continue;
      const payload = await response.json();
      if (!payload?.approved) continue;
      const secret = String(payload?.secret || '');
      if (!secret) throw new Error('OpenWorkGraph approved the request but returned no credential.');
      await ext.storage.local.set({[SECRET_KEY]: secret});
      status.textContent = 'Connected. Browser context can now flow to this local OpenWorkGraph.';
      await refreshStatus();
      return;
    }
    throw new Error('Approval timed out. Try Connect again.');
  } catch (error) {
    status.textContent = String(error?.message || 'Could not connect to OpenWorkGraph.');
  }
}

async function pair() {
  const code = String(document.querySelector('#code').value || '').replace(/\D/g, '');
  const status = document.querySelector('#status');
  if (code.length !== 8) {
    status.textContent = 'Enter the 8-digit code shown by the Workflow Observer dashboard.';
    return;
  }
  status.textContent = 'Pairing…';
  try {
    const response = await fetch(`${API}/v1/browser-pair`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({code}),
      cache: 'no-store'
    });
    if (!response.ok) throw new Error('Pairing code was rejected or expired.');
    const payload = await response.json();
    const secret = String(payload?.secret || '');
    if (!secret) throw new Error('The server did not return a pairing credential.');
    await ext.storage.local.set({[SECRET_KEY]: secret});
    status.textContent = 'Paired. Browser events will now be sent only after OpenWorkGraph proves its identity.';
    await refreshStatus();
  } catch (error) {
    status.textContent = String(error?.message || 'Could not pair the browser sensor.');
  }
}

document.querySelector('#connect').addEventListener('click', oneClickPair);
document.querySelector('#pair').addEventListener('click', pair);
refreshStatus();
