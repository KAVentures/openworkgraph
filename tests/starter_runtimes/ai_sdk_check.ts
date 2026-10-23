// Runs the generated AI SDK starter against the real installed `ai` package.
import { buildTools, UnmappedStepError } from './agent.ts';

const tools = buildTools();
const ids = Object.keys(tools);
if (ids.join() !== 'step_001,step_002,step_003,step_004,step_005') throw new Error('unexpected tools ' + ids.join());
for (const id of ids) {
  const placeholder = tools[id] as { needsApproval?: unknown; description?: string; execute?: (...args: unknown[]) => Promise<unknown> };
  if (placeholder.needsApproval !== true) throw new Error(id + ' does not require approval');
  if (String(placeholder.description).includes('Ignore')) throw new Error(id + ' leaked an activity label');
  let refused = false;
  try {
    await placeholder.execute?.({ case_reference: 'synthetic' }, { toolCallId: 'synthetic', messages: [] });
  } catch (error) {
    refused = error instanceof UnmappedStepError;
  }
  if (!refused) throw new Error(id + ' executed instead of refusing');
}
console.log('AI SDK starter runtime check passed: ' + ids.length + ' approval-gated tools refuse to execute.');
