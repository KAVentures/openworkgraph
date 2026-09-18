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

document.querySelector('#pair').addEventListener('click', pair);
refreshStatus();
