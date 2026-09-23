import assert from 'node:assert/strict';
import fs from 'node:fs';

const content = fs.readFileSync('browser_extension/browser_signals.js','utf8');
const background = fs.readFileSync('browser_extension/browser_signal_enrichment.js','utf8');
const manifest = JSON.parse(fs.readFileSync('browser_extension/manifest.json','utf8'));
const ui = fs.readFileSync('dashboard/browser_signals.js','utf8');

// No permission expansion: the sensor remains on the pre-v0.58 permission set.
assert.deepEqual(manifest.permissions, ['tabs','webNavigation','storage','alarms']);
assert.equal(manifest.permissions.includes('downloads'), false);
assert.equal(manifest.permissions.includes('history'), false);
assert.equal(manifest.permissions.includes('microphone'), false);
assert.ok(manifest.background.scripts.includes('browser_signal_enrichment.js'));
assert.ok(manifest.content_scripts[0].js.includes('browser_signals.js'));

// Navigation Timing only, rounded/bounded, with no Resource Timing URL collection.
assert.match(content, /performance\.getEntriesByType\("navigation"\)/);
assert.match(content, /rounded_to_ms:\s*50/);
assert.match(content, /resource_urls_captured:\s*false/);
assert.match(content, /page_contents_captured:\s*false/);
assert.doesNotMatch(content, /getEntriesByType\(["']resource["']\)/);

// Optional file signal may use MIME type only. It must never inspect identifying
// filename/path/size/content APIs.
assert.match(content, /file\?\.type/);
assert.match(content, /file_upload_category/);
assert.match(content, /filename_captured:\s*false/);
assert.match(content, /exact_size_captured:\s*false/);
assert.match(content, /file_contents_captured:\s*false/);
assert.doesNotMatch(content, /file\?\.name|file\.name|webkitRelativePath|FileReader|arrayBuffer\s*\(|\.text\s*\(\)|file\?\.size|file\.size/);

// File category is off by default and both signal types are gated before send.
assert.match(background, /file_upload_category:\s*false/);
assert.match(background, /action === "performance_timing" && !settings\.performance_timing/);
assert.match(background, /action === "file_upload_category" && !settings\.file_upload_category/);
assert.match(background, /\/v1\/browser-context/);

// UI tells the user exactly what is captured and does not use surveillance framing.
assert.match(ui, /What is captured/);
assert.match(ui, /Off by default/);
assert.match(ui, /No filename, path, exact size, hash, or file contents/i);
assert.match(ui, /Never added by these signals/i);
assert.doesNotMatch(ui, /productivity score|rage click|notification-driven|microphone in use/i);

console.log('bounded browser metadata signal privacy contract ok');
