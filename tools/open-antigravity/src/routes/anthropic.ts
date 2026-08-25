/**
 * Anthropic-compatible API handler.
 * POST /v1/messages
 */

import { IncomingMessage, ServerResponse } from 'http';
import { complete, completeStream } from '../converter.js';
import { v4 } from '../utils.js';

/**
 * POST /v1/messages
 */
export async function handleMessages(req: IncomingMessage, res: ServerResponse, body: any) {
  const {
    model,
    messages = [],
    system,
    stream = false,
    max_tokens,
    metadata,
  } = body;

  // --- Fast-path for validation/preflight requests ---
  // Claude Code sends these to validate API keys and check quotas:
  //   rJq: {max_tokens:1, messages:[{role:'user',content:'test'}]}
  //   g79: {max_tokens:1, messages:[{role:'user',content:'quota'}]}
  // Return an instant stub response instead of creating a full cascade.
  if (max_tokens != null && max_tokens <= 1) {
    const lastMsg = messages[messages.length - 1]?.content || '';
    console.log(`⚡ Fast-path validation: max_tokens=${max_tokens}, msg="${String(lastMsg).slice(0, 20)}"`);
    const stubResponse = {
      id: `msg_${v4()}`,
      type: 'message',
      role: 'assistant',
      content: [{ type: 'text', text: 'ok' }],
      model: model || 'claude-sonnet-4-20250514',
      stop_reason: 'end_turn',
      stop_sequence: null,
      usage: { input_tokens: 10, output_tokens: 1 },
    };
    if (stream) {
      res.writeHead(200, {
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-cache',
        'Connection': 'keep-alive',
      });
      res.write(`event: message_start\ndata: ${JSON.stringify({ type: 'message_start', message: { ...stubResponse, content: [], stop_reason: null } })}\n\n`);
      res.write(`event: content_block_start\ndata: ${JSON.stringify({ type: 'content_block_start', index: 0, content_block: { type: 'text', text: '' } })}\n\n`);
      res.write(`event: content_block_delta\ndata: ${JSON.stringify({ type: 'content_block_delta', index: 0, delta: { type: 'text_delta', text: 'ok' } })}\n\n`);
      res.write(`event: content_block_stop\ndata: ${JSON.stringify({ type: 'content_block_stop', index: 0 })}\n\n`);
      res.write(`event: message_delta\ndata: ${JSON.stringify({ type: 'message_delta', delta: { stop_reason: 'end_turn', stop_sequence: null }, usage: { output_tokens: 1 } })}\n\n`);
      res.write(`event: message_stop\ndata: ${JSON.stringify({ type: 'message_stop' })}\n\n`);
      res.end();
    } else {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify(stubResponse));
    }
    return;
  }

  // Extract workspace from header
  const workspace = req.headers['x-workspace'] as string | undefined;
  const conversationId = req.headers['x-conversation-id'] as string | undefined;

  const request = {
    messages,
    model,
    system: typeof system === 'string' ? system : undefined,
    workspace,
    conversationId,
    maxWaitMs: 120_000,
  };

  if (!stream) {
    // --- Non-streaming ---
    try {
      const result = await complete(request);
      const response = {
        id: `msg_${result.conversationId}`,
        type: 'message',
        role: 'assistant',
        content: [{
          type: 'text',
          text: result.content,
        }],
        model: result.model,
        stop_reason: 'end_turn',
        stop_sequence: null,
        usage: { input_tokens: Math.ceil(JSON.stringify(messages).length / 4), output_tokens: Math.ceil(result.content.length / 4) },
        _conversation_id: result.conversationId,
      };
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify(response));
    } catch (err: any) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({
        type: 'error',
        error: { type: 'api_error', message: err.message },
      }));
    }
  } else {
    // --- Streaming (SSE) ---
    res.writeHead(200, {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      'Connection': 'keep-alive',
    });

    try {
      const msgId = `msg_${v4()}`;
      let contentBlockStarted = false;

      // message_start
      res.write(`event: message_start\ndata: ${JSON.stringify({
        type: 'message_start',
        message: {
          id: msgId,
          type: 'message',
          role: 'assistant',
          content: [],
          model: model || 'claude-sonnet-4-20250514',
          stop_reason: null,
          stop_sequence: null,
          usage: { input_tokens: Math.ceil(JSON.stringify(messages).length / 4), output_tokens: 0 },
        },
      })}\n\n`);

      for await (const chunk of completeStream(request)) {
        if (chunk.type === 'content_delta') {
          if (!contentBlockStarted) {
            // content_block_start
            res.write(`event: content_block_start\ndata: ${JSON.stringify({
              type: 'content_block_start',
              index: 0,
              content_block: { type: 'text', text: '' },
            })}\n\n`);
            contentBlockStarted = true;
          }

          if (chunk.text) {
            // content_block_delta
            res.write(`event: content_block_delta\ndata: ${JSON.stringify({
              type: 'content_block_delta',
              index: 0,
              delta: { type: 'text_delta', text: chunk.text },
            })}\n\n`);
          }
        } else if (chunk.type === 'done') {
          if (contentBlockStarted) {
            // content_block_stop
            res.write(`event: content_block_stop\ndata: ${JSON.stringify({
              type: 'content_block_stop',
              index: 0,
            })}\n\n`);
          }

          // message_delta
          res.write(`event: message_delta\ndata: ${JSON.stringify({
            type: 'message_delta',
            delta: { stop_reason: 'end_turn', stop_sequence: null },
            usage: { output_tokens: 10 },
          })}\n\n`);

          // message_stop
          res.write(`event: message_stop\ndata: ${JSON.stringify({
            type: 'message_stop',
          })}\n\n`);
        } else if (chunk.type === 'error') {
          res.write(`event: error\ndata: ${JSON.stringify({
            type: 'error',
            error: { type: 'api_error', message: chunk.error },
          })}\n\n`);
        }
      }
    } catch (err: any) {
      res.write(`event: error\ndata: ${JSON.stringify({
        type: 'error',
        error: { type: 'api_error', message: err.message },
      })}\n\n`);
    }
    res.end();
  }
}
