/**
 * OpenAI-compatible API handler.
 * POST /v1/chat/completions
 * GET  /v1/models
 */

import { IncomingMessage, ServerResponse } from 'http';
import { complete, completeStream } from '../converter.js';
import { toOpenAIModelsResponse } from '../models.js';
import { v4 } from '../utils.js';

/**
 * POST /v1/chat/completions
 */
export async function handleChatCompletions(req: IncomingMessage, res: ServerResponse, body: any) {
  const {
    model,
    messages = [],
    stream = false,
    max_tokens,
  } = body;

  // Extract system message
  const systemMsg = messages.find((m: any) => m.role === 'system');
  const system = systemMsg?.content;

  // Extract workspace from header
  const workspace = req.headers['x-workspace'] as string | undefined;
  const conversationId = req.headers['x-conversation-id'] as string | undefined;

  const reasoning_effort = body.reasoning_effort || body.reasoning;

  const request = {
    messages: messages.filter((m: any) => m.role !== 'system'),
    model,
    reasoning_effort,
    system,
    workspace,
    conversationId,
    maxWaitMs: 120_000,
  };

  if (!stream) {
    // --- Non-streaming ---
    try {
      const result = await complete(request);
      const response = {
        id: `chatcmpl-${result.conversationId}`,
        object: 'chat.completion',
        created: Math.floor(Date.now() / 1000),
        model: result.model,
        choices: [{
          index: 0,
          message: { role: 'assistant', content: result.content },
          finish_reason: 'stop',
        }],
        usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 },
        // Extra: pass back conversation ID for reuse
        _conversation_id: result.conversationId,
      };
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify(response));
    } catch (err: any) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: { message: err.message, type: 'server_error' } }));
    }
  } else {
    // --- Streaming (SSE) ---
    res.writeHead(200, {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      'Connection': 'keep-alive',
    });

    try {
      const id = `chatcmpl-${v4()}`;
      for await (const chunk of completeStream(request)) {
        if (chunk.type === 'content_delta' && chunk.text) {
          const sseData = {
            id,
            object: 'chat.completion.chunk',
            created: Math.floor(Date.now() / 1000),
            model: model || 'claude-sonnet-4-20250514',
            choices: [{
              index: 0,
              delta: { content: chunk.text },
              finish_reason: null,
            }],
          };
          res.write(`data: ${JSON.stringify(sseData)}\n\n`);
        } else if (chunk.type === 'done') {
          const sseData = {
            id,
            object: 'chat.completion.chunk',
            created: Math.floor(Date.now() / 1000),
            model: model || 'claude-sonnet-4-20250514',
            choices: [{
              index: 0,
              delta: {},
              finish_reason: 'stop',
            }],
            _conversation_id: chunk.conversationId,
          };
          res.write(`data: ${JSON.stringify(sseData)}\n\n`);
          res.write('data: [DONE]\n\n');
        } else if (chunk.type === 'error') {
          res.write(`data: ${JSON.stringify({ error: { message: chunk.error } })}\n\n`);
        }
      }
    } catch (err: any) {
      res.write(`data: ${JSON.stringify({ error: { message: err.message } })}\n\n`);
    }
    res.end();
  }
}

/**
 * GET /v1/models
 */
export function handleModels(_req: IncomingMessage, res: ServerResponse) {
  res.writeHead(200, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify(toOpenAIModelsResponse()));
}
