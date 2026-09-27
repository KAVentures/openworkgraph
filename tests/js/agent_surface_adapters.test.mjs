import assert from 'node:assert/strict';
import fs from 'node:fs';

const source=fs.readFileSync('browser_extension/agent_surface_adapters.js','utf8');
const background=fs.readFileSync('browser_extension/background.js','utf8');
const manifest=JSON.parse(fs.readFileSync('browser_extension/manifest.json','utf8'));
assert.doesNotThrow(()=>new Function(source));
assert.ok(manifest.content_scripts[0].js.includes('agent_surface_adapters.js'));
assert.deepEqual(manifest.permissions,['tabs','webNavigation','storage','alarms']);

assert.match(source,/aria-busy/);
assert.match(source,/\[role=["']alert/);
assert.match(source,/\[role=["']dialog/);
assert.doesNotMatch(source,/querySelector\([^\n]*\.[A-Za-z_-][A-Za-z0-9_-]{4,}/);
assert.doesNotMatch(source,/FileSystem|FileReader|webkitRelativePath/);

new Function(source)();
const api=globalThis.__OWG_AGENT_SURFACE_ADAPTERS_FOR_TESTS__;
assert.ok(api);
assert.equal(api.providerForHost('chatgpt.com')?.key,'chatgpt');
assert.equal(api.providerForHost('claude.ai')?.key,'claude');
assert.equal(api.providerForHost('copilot.microsoft.com')?.key,'microsoft_copilot');
assert.equal(api.providerForHost('lovable.dev')?.key,'lovable');
assert.equal(api.providerForHost('gemini.google.com')?.key,'gemini');
assert.equal(api.providerForHost('example.com'),null);

globalThis.location={origin:'https://chatgpt.com',hostname:'chatgpt.com'};
const payload=api.structuralPayload({key:'chatgpt',name:'ChatGPT'},'agent_run_started','web-deadbeef','send_control');
assert.equal(payload.type,'workflow_observer_event');
assert.equal(payload.action,'agent_run_started');
assert.equal(payload.metadata.agent_provider,'chatgpt');
assert.equal(payload.metadata.agent_run_id,'web-deadbeef');
assert.equal(payload.page.url,'https://chatgpt.com/');
assert.equal(payload.page.title,'ChatGPT');
// background.js consumes these exact top-level fields, so an accidental payload
// wrapper would silently downgrade lifecycle events to generic browser_event.
assert.match(background,/message\.observed_at/);
assert.match(background,/message\.action/);
assert.match(background,/message\.metadata/);
assert.equal('payload' in payload,false);

const serialized=JSON.stringify(payload).toLowerCase();
for(const forbidden of ['prompt_text','response_text','tool_arguments','tool_result','chain_of_thought','clipboard_contents','file_path']){
  assert.equal(serialized.includes(forbidden),false,`must not send ${forbidden}`);
}
assert.match(source,/No DOM text or user\/model content is included/);
console.log('browser agent adapters remain structural and content-free');
