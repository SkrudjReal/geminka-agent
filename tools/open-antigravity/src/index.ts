/**
 * open-antigravity — Expose Antigravity as OpenAI & Anthropic compatible API.
 *
 * Endpoints:
 *   POST /v1/chat/completions   (OpenAI format)
 *   POST /v1/messages           (Anthropic format)
 *   GET  /v1/models             (OpenAI format)
 *   GET  /health                (health check)
 */

import { createServer, IncomingMessage, ServerResponse } from 'http';
import { handleChatCompletions, handleModels } from './routes/openai.js';
import { handleMessages } from './routes/anthropic.js';
import { discoverLanguageServers } from './bridge/discovery.js';
import { getApiKey } from './bridge/statedb.js';

const PORT = parseInt(process.env.PORT || '4000');
const HOST = process.env.HOST || '0.0.0.0';

// --- Request body parser ---
function parseBody(req: IncomingMessage): Promise<any> {
  return new Promise((resolve, reject) => {
    let data = '';
    req.on('data', chunk => { data += chunk; });
    req.on('end', () => {
      if (!data) return resolve({});
      try { resolve(JSON.parse(data)); }
      catch { reject(new Error('Invalid JSON')); }
    });
    req.on('error', reject);
  });
}

// --- CORS headers ---
function setCorsHeaders(res: ServerResponse) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Authorization, x-api-key, x-workspace, x-conversation-id, anthropic-version');
}

// --- Router ---
const server = createServer(async (req: IncomingMessage, res: ServerResponse) => {
  setCorsHeaders(res);

  // Handle preflight
  if (req.method === 'OPTIONS') {
    res.writeHead(204);
    res.end();
    return;
  }

  const rawUrl = req.url || '';
  // Strip query string for route matching
  const url = rawUrl.split('?')[0];

  // Debug logging for all requests
  console.log(`📥 ${req.method} ${rawUrl} [${Object.entries(req.headers).filter(([k]) => k.startsWith('x-') || k === 'anthropic-version' || k === 'content-type' || k === 'authorization').map(([k,v]) => `${k}=${v}`).join(', ')}]`);

  try {
    // Health check
    if (url === '/health' && req.method === 'GET') {
      const servers = discoverLanguageServers();
      const apiKey = getApiKey();
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({
        status: servers.length > 0 && apiKey ? 'ok' : 'degraded',
        servers: servers.length,
        hasApiKey: !!apiKey,
      }));
      return;
    }

    // OpenAI: POST /v1/chat/completions
    if (url === '/v1/chat/completions' && req.method === 'POST') {
      const body = await parseBody(req);
      await handleChatCompletions(req, res, body);
      return;
    }

    // OpenAI: GET /v1/models
    if (url === '/v1/models' && req.method === 'GET') {
      handleModels(req, res);
      return;
    }

    // Anthropic: POST /v1/messages
    if (url === '/v1/messages' && req.method === 'POST') {
      const body = await parseBody(req);
      console.log(`  ↳ model=${body.model} max_tokens=${body.max_tokens} stream=${body.stream} msgs=${body.messages?.length} tools=${body.tools?.length ?? 0} sysLen=${typeof body.system === 'string' ? body.system.length : 0}`);
      await handleMessages(req, res, body);
      return;
    }

    // Anthropic: POST /v1/messages/count_tokens
    // Claude Code calls this to validate model availability before sending messages.
    // Return a stub response with an estimated token count.
    if (url === '/v1/messages/count_tokens' && req.method === 'POST') {
      const body = await parseBody(req);
      const inputTokens = JSON.stringify(body.messages || []).length / 4; // rough estimate
      console.log(`📊 count_tokens stub: model=${body.model}, estimated=${Math.ceil(inputTokens)}`);
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ input_tokens: Math.ceil(inputTokens) }));
      return;
    }

    // Anthropic: POST /v1/messages/batches (stub — not supported but return valid error)
    if (url.startsWith('/v1/messages/batches') && req.method === 'POST') {
      res.writeHead(501, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({
        type: 'error',
        error: { type: 'not_supported', message: 'Batch API is not supported by this proxy.' },
      }));
      return;
    }

    // 404
    console.log(`⚠️  404 Not Found: ${req.method} ${rawUrl}`);
    res.writeHead(404, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ error: { message: 'Not Found', type: 'not_found' } }));

  } catch (err: any) {
    console.error('❌ Request error:', err.message);
    if (!res.headersSent) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
    }
    res.end(JSON.stringify({ error: { message: err.message, type: 'server_error' } }));
  }
});

server.listen(PORT, HOST, () => {
  console.log(`
╔═══════════════════════════════════════════════════╗
║          open-antigravity  v0.1.0                 ║
║   Antigravity → OpenAI / Anthropic API Proxy      ║
╠═══════════════════════════════════════════════════╣
║                                                   ║
║  OpenAI format:                                   ║
║    POST http://${HOST}:${PORT}/v1/chat/completions${' '.repeat(Math.max(0, 12 - String(PORT).length))}║
║    GET  http://${HOST}:${PORT}/v1/models${' '.repeat(Math.max(0, 22 - String(PORT).length))}║
║                                                   ║
║  Anthropic format:                                ║
║    POST http://${HOST}:${PORT}/v1/messages${' '.repeat(Math.max(0, 20 - String(PORT).length))}║
║                                                   ║
║  Health: http://${HOST}:${PORT}/health${' '.repeat(Math.max(0, 22 - String(PORT).length))}║
╚═══════════════════════════════════════════════════╝
`);

  // Check status
  const servers = discoverLanguageServers();
  const apiKey = getApiKey();
  if (servers.length === 0) {
    console.log('⚠️  No Antigravity language_server found. Is Antigravity running?');
  } else {
    console.log(`✅ Found ${servers.length} server(s):`);
    servers.forEach(s => console.log(`   port=${s.port}  workspace="${s.workspace}"`));
  }
  if (!apiKey) {
    console.log('⚠️  No API key found in state.vscdb');
  } else {
    console.log(`✅ API key loaded (${apiKey.slice(0, 8)}...)`);
  }
  console.log('');
});
