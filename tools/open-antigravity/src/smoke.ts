/**
 * Smoke tests for the open-antigravity proxy.
 *
 * Tests the proxy end-to-end using the official Anthropic SDK,
 * exactly the same way Claude Code does.
 *
 * Usage:
 *   npm run smoke           — run all tests
 *   npm run smoke -- --fast — skip streaming test (faster)
 *
 * Prerequisites: proxy must be running (`npm run dev` or `npm start`)
 */

import Anthropic from '@anthropic-ai/sdk';

const BASE_URL = process.env.PROXY_URL || 'http://localhost:4000';
const API_KEY  = process.env.PROXY_KEY  || '1';
const MODEL    = process.env.MODEL      || 'claude-sonnet-4-20250514';
const FAST     = process.argv.includes('--fast');

// ─── Colour helpers ────────────────────────────────────────────────────────
const green  = (s: string) => `\x1b[32m${s}\x1b[0m`;
const red    = (s: string) => `\x1b[31m${s}\x1b[0m`;
const yellow = (s: string) => `\x1b[33m${s}\x1b[0m`;
const bold   = (s: string) => `\x1b[1m${s}\x1b[0m`;
const dim    = (s: string) => `\x1b[2m${s}\x1b[0m`;

// ─── Test runner ───────────────────────────────────────────────────────────
interface Result { name: string; ok: boolean; ms: number; detail?: string }
const results: Result[] = [];

async function test(name: string, fn: () => Promise<void>) {
  process.stdout.write(`  ${bold('·')} ${name} ... `);
  const t0 = Date.now();
  try {
    await fn();
    const ms = Date.now() - t0;
    console.log(green('PASS') + dim(` (${ms}ms)`));
    results.push({ name, ok: true, ms });
  } catch (err: any) {
    const ms = Date.now() - t0;
    const detail = err?.message ?? String(err);
    console.log(red('FAIL') + dim(` (${ms}ms)`) + '\n    ' + red(detail));
    results.push({ name, ok: false, ms, detail });
  }
}

function assert(cond: boolean, msg: string) {
  if (!cond) throw new Error(msg);
}

// ─── Build client (points to local proxy) ─────────────────────────────────
const client = new Anthropic({
  apiKey:  API_KEY,
  baseURL: BASE_URL,
});

// ─── Test suites ───────────────────────────────────────────────────────────

async function testHealth() {
  const res = await fetch(`${BASE_URL}/health`);
  assert(res.ok, `HTTP ${res.status}`);
  const body = await res.json() as any;
  assert(body.status === 'ok' || body.status === 'degraded', `unexpected status: ${body.status}`);
  assert(typeof body.servers === 'number', 'servers field missing');
  assert(typeof body.hasApiKey === 'boolean', 'hasApiKey field missing');
  if (body.status === 'degraded') {
    throw new Error(`Proxy degraded: servers=${body.servers} hasApiKey=${body.hasApiKey}. Is Antigravity running?`);
  }
}

async function testTokenCount() {
  // Claude Code calls this before every real message to validate model availability.
  const res = await fetch(`${BASE_URL}/v1/messages/count_tokens`, {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      'x-api-key': API_KEY,
      'anthropic-version': '2023-06-01',
    },
    body: JSON.stringify({ model: MODEL, messages: [{ role: 'user', content: 'hello' }] }),
  });
  assert(res.ok, `HTTP ${res.status}`);
  const body = await res.json() as any;
  assert(typeof body.input_tokens === 'number', `missing input_tokens: ${JSON.stringify(body)}`);
}

async function testNonStreaming() {
  const msg = await client.messages.create({
    model: MODEL,
    max_tokens: 256,
    messages: [{ role: 'user', content: 'Say exactly: PROXY_OK' }],
  });
  assert(msg.type === 'message', `unexpected type: ${msg.type}`);
  assert(msg.role === 'assistant', `unexpected role: ${msg.role}`);
  assert(msg.content.length > 0, 'empty content');
  const text = msg.content.filter(b => b.type === 'text').map(b => (b as any).text).join('');
  assert(text.length > 0, 'empty text content');
  console.log(dim(`\n    → response (${text.length} chars): "${text.slice(0, 80)}${text.length > 80 ? '…' : ''}"`));
}

async function testStreaming() {
  let fullText = '';
  let deltaCount = 0;
  let gotMessageStart = false;
  let gotMessageStop = false;

  const stream = await client.messages.stream({
    model: MODEL,
    max_tokens: 256,
    messages: [{ role: 'user', content: 'Count: 1, 2, 3. Just the numbers.' }],
  });

  for await (const event of stream) {
    if (event.type === 'message_start') gotMessageStart = true;
    if (event.type === 'message_stop') gotMessageStop = true;
    if (event.type === 'content_block_delta' && event.delta.type === 'text_delta') {
      fullText += event.delta.text;
      deltaCount++;
    }
  }

  assert(gotMessageStart, 'missing message_start event');
  assert(gotMessageStop, 'missing message_stop event');
  assert(deltaCount > 0, 'no content_block_delta events received — response was empty');
  assert(fullText.length > 0, 'empty streamed text');
  console.log(dim(`\n    → streamed (${deltaCount} deltas, ${fullText.length} chars): "${fullText.slice(0, 80)}${fullText.length > 80 ? '…' : ''}"`));
}

async function testValidationFastPath() {
  // Claude Code sends max_tokens=1 to validate the API key.
  // The proxy should fast-path this and return instantly (< 500ms).
  const t0 = Date.now();
  const msg = await client.messages.create({
    model: MODEL,
    max_tokens: 1,
    messages: [{ role: 'user', content: 'test' }],
  });
  const elapsed = Date.now() - t0;
  assert(msg.role === 'assistant', `unexpected role: ${msg.role}`);
  assert(elapsed < 500, `fast-path took too long: ${elapsed}ms (should be < 500ms)`);
}

async function testModels() {
  // GET /v1/models — OpenAI format
  const res = await fetch(`${BASE_URL}/v1/models`, {
    headers: { Authorization: `Bearer ${API_KEY}` },
  });
  assert(res.ok, `HTTP ${res.status}`);
  const body = await res.json() as any;
  assert(body.object === 'list', `unexpected object: ${body.object}`);
  assert(Array.isArray(body.data) && body.data.length > 0, 'empty models list');
}

// ─── Main ──────────────────────────────────────────────────────────────────

console.log(`\n${bold('open-antigravity smoke tests')}`);
console.log(dim(`  proxy: ${BASE_URL}  model: ${MODEL}\n`));

await test('health',              testHealth);
await test('token count stub',    testTokenCount);
await test('models list',         testModels);
await test('validation fast-path (max_tokens=1)', testValidationFastPath);
await test('non-streaming message', testNonStreaming);
if (!FAST) {
  await test('streaming message',   testStreaming);
} else {
  console.log(dim('  · streaming message ... SKIPPED (--fast)'));
}

// ─── Summary ───────────────────────────────────────────────────────────────
const passed = results.filter(r => r.ok).length;
const failed = results.filter(r => !r.ok).length;
const total  = results.length;

console.log('');
if (failed === 0) {
  console.log(green(bold(`✓ All ${total} tests passed`)));
} else {
  console.log(red(bold(`✗ ${failed}/${total} tests failed`)));
  process.exit(1);
}
