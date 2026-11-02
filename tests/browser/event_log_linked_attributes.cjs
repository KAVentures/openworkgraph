const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const {spawn} = require('node:child_process');
const {tmpdir} = require('node:os');
const path = require('node:path');

(async () => {
  const scratch = await fs.mkdtemp(path.join(tmpdir(), 'owg-r9-linked-ui-'));
  const base = 'http://127.0.0.1:8787', python = process.env.OWG_BROWSER_PYTHON || 'python';
  const server = spawn(python, ['-m', 'uvicorn', 'process_logs_app:app', '--app-dir',
    'tests/browser', '--host', '127.0.0.1', '--port', '8787'], {
    env: {...process.env, OWG_BROWSER_TEST_DIR: scratch}, stdio: ['ignore', 'pipe', 'pipe']
  });
  let logs = '', browser, spawnError;
  server.on('error', error => {spawnError = error;});
  for (const pipe of [server.stdout, server.stderr])
    pipe.on('data', chunk => {logs = (logs + chunk).slice(-8000);});
  try {
    let ready = false;
    for (let i = 0; i < 100; i++) {
      if (spawnError) throw spawnError;
      if (server.exitCode !== null) throw new Error('Browser fixture exited: ' + logs);
      try {
        await fs.access(path.join(scratch, 'session.txt'));
        if ((await fetch(base + '/')).ok) {ready = true; break;}
      } catch (_) {}
      await new Promise(resolve => setTimeout(resolve, 200));
    }
    if (!ready) throw new Error('Browser fixture not ready: ' + logs);
    browser = await chromium.launch({
      ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE
        ? {executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE} : {}),
      headless: true, args: ['--no-sandbox']
    });
    const page = await browser.newPage({acceptDownloads: true}), errors = [];
    let joinedReads = 0;
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => {
      if (request.method() === 'POST' && request.url().includes('/linked-attribute-analysis'))
        joinedReads++;
    });
    const session = (await fs.readFile(path.join(scratch, 'session.txt'), 'utf8')).trim();
    await page.goto(base + '/#session=' + session);
    await page.locator('[role=tab][data-tab=evidence]').click();
    await page.locator('#owgEventLogCard').waitFor();
    const stamp = '1970-01-01T00:00:00Z';
    const doc = {
      objectTypes: [{name: 'order', attributes: []},
        {name: 'invoice', attributes: [{name: 'region', type: 'string'}]}],
      eventTypes: [{name: 'Work', attributes: []}],
      objects: [
        ...['o1', 'o2', 'o3', 'o4'].map(id => ({id, type: 'order', attributes: []})),
        {id: 'i1', type: 'invoice', attributes: [{name: 'region', time: stamp, value: 'EU'}]},
        {id: 'i2', type: 'invoice', attributes: [{name: 'region', time: stamp, value: '=1+2'}]}
      ],
      events: [
        ['e1', 'o1', ['i1'], '2024-01-01T08:00:00Z'],
        ['e2', 'o1', ['i1'], '2024-01-01T09:00:00Z'],
        ['e3', 'o2', ['i2'], '2024-01-01T10:00:00Z'],
        ['e4', 'o3', [], '2024-01-01T11:00:00Z'],
        ['e5', 'o4', ['i1', 'i2'], '2024-01-01T12:00:00Z']
      ].map(([id, order, invoices, time]) => ({
        id, type: 'Work', time, attributes: [],
        relationships: [order, ...invoices].map(objectId => ({objectId, qualifier: ''}))
      }))
    };
    await page.locator('#owgEventLogFile').setInputFiles({
      name: 'linked.jsonocel', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(doc))
    });
    await page.locator('#owgEventLogConsent').check();
    await page.locator('#owgEventLogImport').click();
    await page.locator('#owgEventLogStatus').filter({hasText: 'Imported 5 events'}).waitFor();
    await page.locator('#owgEventLogCaseType').selectOption('order');
    assert.equal(joinedReads, 0, 'selecting a log must not trigger a joined read');
    await page.locator('#owgEventLogAnalyze').click();
    await page.locator('#owgLinkedAttributes').waitFor();
    assert.equal(await page.locator('#owgLinkedType').inputValue(), 'invoice');
    assert.equal(await page.locator('#owgLinkedJSON').isDisabled(), true);
    await page.locator('#owgLinkedRun').click();
    assert.match(await page.locator('#owgLinkedStatus').textContent(), /Choose a different related object type/);
    assert.equal(joinedReads, 0, 'invalid request must be refused before POST');

    await page.locator('#owgLinkedAttribute').fill('region');
    const responsePromise = page.waitForResponse(response =>
      response.url().includes('/linked-attribute-analysis') && response.request().method() === 'POST');
    await page.locator('#owgLinkedRun').focus();
    await page.keyboard.press('Enter');
    const response = await responsePromise;
    assert.equal(response.status(), 200, await response.text());
    const packet = await response.json();
    assert.deepEqual(packet.status_counts, {
      resolved: 2, unlinked: 1, ambiguous: 1, missing_attribute: 0, oversize_attribute: 0
    });
    await page.locator('#owgLinkedStatus').filter({hasText: 'analysis complete'}).waitFor();
    const result = await page.locator('#owgLinkedResults').textContent();
    assert.match(result, /4 observed cases: 2 resolved, 1 unlinked, 1 ambiguous/);
    assert.match(result, /EU/);
    assert.match(result, /=1\+2/);
    assert.match(result, /3,600 s/);
    assert.equal(await page.locator('#owgLinkedResults tbody tr').count(), 2);
    assert.equal(joinedReads, 1);

    const [jsonDownload] = await Promise.all([
      page.waitForEvent('download'), page.locator('#owgLinkedJSON').click()
    ]);
    const saved = JSON.parse(await fs.readFile(await jsonDownload.path(), 'utf8'));
    assert.deepEqual(saved.status_counts, packet.status_counts);
    assert.equal(saved.join_basis, 'distinct_same_log_coobserved_event_object_links');
    const [csvDownload] = await Promise.all([
      page.waitForEvent('download'), page.locator('#owgLinkedCSV').click()
    ]);
    const csv = await fs.readFile(await csvDownload.path(), 'utf8');
    assert.match(csv, /'\=1\+2/,'formula-like source values must be escaped for spreadsheet import');
    assert.doesNotMatch(csv, /"=1\+2"/, 'unescaped spreadsheet formula must never be exported');

    await page.locator('#owgLinkedLimit').fill('0');
    assert.equal(await page.locator('#owgLinkedJSON').isDisabled(), true);
    await page.locator('#owgLinkedRun').click();
    assert.equal(joinedReads, 1, 'out-of-range group limit is rejected locally');
    await page.locator('#owgEventLogCaseType').selectOption('invoice');
    assert.equal(await page.locator('#owgLinkedAttributes').count(), 0,
      'changing case notion invalidates the old analysis and exports');
    assert.deepEqual(errors, []);
    console.log('R9 linked-attribute dashboard acceptance passed: real OCEL import, session-authenticated request, unknown/ambiguous coverage, keyboard, JSON/CSV formula safety, invalid-input refusal and case-change invalidation.');
  } finally {
    if (browser) await browser.close();
    server.kill('SIGTERM');
    await Promise.race([
      new Promise(resolve => server.once('exit', resolve)),
      new Promise(resolve => setTimeout(resolve, 5000))
    ]);
    if (server.exitCode === null) server.kill('SIGKILL');
    await fs.rm(scratch, {recursive: true, force: true});
  }
})().catch(error => {console.error(error); process.exit(1);});
