import assert from 'node:assert/strict';
import { resolveModelId, toOpenAIModelsResponse } from './dist/models.js';

for (const [effort, id] of [['low', 'M320'], ['medium', 'M319'], ['high', 'M318']]) {
  assert.equal(resolveModelId('google-antigravity/gemini-3.8-flash', effort), `MODEL_PLACEHOLDER_${id}`);
  assert.notEqual(resolveModelId('google-antigravity/gemini-3.7-flash', effort), `MODEL_PLACEHOLDER_${id}`);
  assert.equal(resolveModelId('claude-opus-4-6', effort), 'MODEL_PLACEHOLDER_M26');
}
assert.throws(() => resolveModelId('nonexistent-model', 'medium'), /Unsupported model/);
assert(toOpenAIModelsResponse().data.some(m => m.id === 'google-antigravity/gemini-3.8-flash'));
console.log('Model routing: OK');
