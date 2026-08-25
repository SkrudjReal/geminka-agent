/**
 * Core converter: sends a message to Antigravity and waits for the AI response
 * by subscribing to the streaming state updates.
 *
 * This is the heart of the project — it bridges the gap between
 * Antigravity's async conversation model and the synchronous request-response
 * model of OpenAI/Anthropic APIs.
 *
 * Key feature: auto-approval of blocking NOTIFY_USER steps.
 * When Antigravity creates/modifies files, it may emit a blocking NOTIFY_USER
 * step that pauses the agent waiting for user approval. This converter
 * automatically detects and approves these steps so the agent continues.
 */

import { discoverLanguageServers, getLanguageServer } from './bridge/discovery.js';
import { getApiKey } from './bridge/statedb.js';
import * as grpc from './bridge/grpc.js';
import { resolveModelId } from './models.js';

/**
 * Extract text from Anthropic message content (handles both string and array formats).
 * Claude Code sends: [{type: 'text', text: '...'}, {type: 'tool_use', ...}]
 * Simple clients send: 'plain string'
 */
function extractText(content: any): string {
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) {
    return content
      .filter((block: any) => block.type === 'text')
      .map((block: any) => block.text || '')
      .join('\n');
  }
  return String(content || '');
}

/**
 * Extract text from system prompt (string or Anthropic content block array).
 */
function extractSystemText(system: any): string | undefined {
  if (!system) return undefined;
  if (typeof system === 'string') return system || undefined;
  if (Array.isArray(system)) {
    const text = system
      .filter((b: any) => b.type === 'text')
      .map((b: any) => b.text || '')
      .join('\n');
    return text || undefined;
  }
  return undefined;
}

/** Debug logging — set DEBUG=1 to enable. */
const DEBUG = !!process.env.DEBUG;
function dbg(...args: any[]) { if (DEBUG) console.log('[DBG]', ...args); }

/**
 * Extract the latest AI response text from a step list.
 * Tries several known Antigravity response field names.
 */
function extractContent(steps: any[]): string {
  for (let i = steps.length - 1; i >= 0; i--) {
    const step = steps[i];
    if (!step) continue;
    const pr = step.plannerResponse;
    if (pr) {
      const text = pr.modifiedResponse || pr.response || pr.text || pr.content || '';
      if (text) return text;
    }
    if (step.errorMessage) {
      return typeof step.errorMessage === 'string' ? step.errorMessage : JSON.stringify(step.errorMessage);
    }
    // Some agentic steps embed the response directly
    const text = step.response || step.text || step.content || '';
    if (text && typeof text === 'string') return text;
  }
  return '';
}

export interface CompletionRequest {
  messages: Array<{ role: string; content: string }>;
  model?: string;
  reasoning_effort?: string;
  system?: string;
  workspace?: string;       // file:///path or auto-detect
  conversationId?: string;  // reuse existing conversation
  maxWaitMs?: number;        // timeout (default 120s)
}

export interface CompletionResult {
  conversationId: string;
  content: string;
  model: string;
  stopReason: 'end_turn' | 'timeout' | 'error';
}

export interface StreamChunk {
  type: 'content_delta' | 'done' | 'error';
  text?: string;
  conversationId?: string;
  error?: string;
}

/**
 * Get a server connection for the given workspace.
 */
function getConnection(workspace?: string) {
  const srv = workspace ? getLanguageServer(workspace) : getLanguageServer();
  const apiKey = getApiKey();
  if (!srv || !apiKey) return null;
  return { ...srv, apiKey };
}

/**
 * Check if a NOTIFY_USER step is blocking and needs auto-approval.
 * Returns the artifact URI to approve, or null if not blocking.
 */
function getBlockingNotifyUri(steps: any[]): string | null {
  for (let i = steps.length - 1; i >= 0; i--) {
    const step = steps[i];
    if (!step) continue;
    const type = step.type || '';

    // If we hit a USER_INPUT after the last NOTIFY_USER, it means
    // the user already responded — no approval needed
    if (type === 'CORTEX_STEP_TYPE_USER_INPUT') return null;

    if (type === 'CORTEX_STEP_TYPE_NOTIFY_USER') {
      const nu = step.notifyUser || {};
      const blocked = nu.blockedOnUser ?? nu.isBlocking ?? false;
      if (!blocked) return null;

      // Get the first review path as the artifact URI
      const paths = nu.pathsToReview || nu.reviewAbsoluteUris || [];
      if (paths.length > 0) {
        // paths can be strings or objects with .uri
        const first = paths[0];
        return typeof first === 'string' ? first : (first?.uri || first?.absoluteUri || '');
      }
      // Blocking but no specific artifact — approve with empty URI
      return '';
    }
  }
  return null;
}

function buildFullPrompt(messages: Array<{ role: string; content: any }>, systemPrompt?: any): string {
  const parts: string[] = [];

  const sysText = extractSystemText(systemPrompt);
  if (sysText) {
    parts.push(sysText);
  }

  for (const m of messages) {
    const role = m.role || 'user';
    const content = extractText(m.content);
    if (!content || !content.trim()) continue;

    if (role === 'system') {
      parts.push(content);
    } else if (role === 'assistant') {
      parts.push(`[Columbina / Assistant]:\n${content}`);
    } else {
      parts.push(`[User]:\n${content}`);
    }
  }

  return parts.join('\n\n');
}

/**
 * Send a completion request and wait for the full response (non-streaming).
 */
export async function complete(req: CompletionRequest): Promise<CompletionResult> {
  const conn = getConnection(req.workspace);
  if (!conn) throw new Error('No Antigravity language_server found. Is Antigravity running?');

  const internalModel = resolveModelId(req.model, req.reasoning_effort);
  const maxWait = req.maxWaitMs || 120_000;
  console.log(`🧠 complete: model=${req.model} effort=${req.reasoning_effort || 'default'} → ${internalModel} maxWait=${maxWait}ms`);

  const promptText = buildFullPrompt(req.messages, req.system);

  // Create or reuse conversation
  let cascadeId = req.conversationId;
  if (!cascadeId) {
    const wsUri = conn.workspace || 'file:///tmp/antigravity-playground';
    const wsPath = wsUri.replace(/^file:\/\//, '');
    await grpc.addTrackedWorkspace(conn.port, conn.csrf, wsPath);
    const cascade = await grpc.startCascade(conn.port, conn.csrf, conn.apiKey, wsUri);
    cascadeId = cascade.cascadeId;
    if (!cascadeId) throw new Error('Failed to create conversation');
  }

  // Send the message
  await grpc.sendMessage(conn.port, conn.csrf, conn.apiKey, cascadeId, promptText, internalModel);

  // Wait for AI response via streaming (with auto-approval)
  const content = await waitForResponse(conn.port, conn.csrf, conn.apiKey, cascadeId, internalModel, maxWait);

  return {
    conversationId: cascadeId,
    content,
    model: req.model || 'gemini-3.7-flash',
    stopReason: 'end_turn',
  };
}

/**
 * Send a completion request and stream the response.
 * Yields chunks as they arrive.
 */
export async function* completeStream(req: CompletionRequest): AsyncGenerator<StreamChunk> {
  const conn = getConnection(req.workspace);
  if (!conn) {
    yield { type: 'error', error: 'No Antigravity language_server found. Is Antigravity running?' };
    return;
  }

  const internalModel = resolveModelId(req.model, req.reasoning_effort);
  const maxWait = req.maxWaitMs || 120_000;
  console.log(`🧠 completeStream: model=${req.model} effort=${req.reasoning_effort || 'default'} → ${internalModel}`);

  const isNewConversation = !req.conversationId;
  let promptText: string;

  if (isNewConversation) {
    promptText = buildFullPrompt(req.messages, req.system);
  } else {
    // In an existing conversation, Antigravity holds previous turns natively.
    const userMessages = req.messages.filter(m => m.role === 'user');
    const lastUserMsg = userMessages.pop();
    promptText = lastUserMsg ? extractText(lastUserMsg.content) : buildFullPrompt(req.messages, req.system);
  }

  if (!promptText.trim()) {
    yield { type: 'error', error: 'No message content provided' };
    return;
  }

  // Create or reuse conversation
  let cascadeId = req.conversationId;
  if (!cascadeId) {
    const wsUri = conn.workspace || 'file:///tmp/antigravity-playground';
    const wsPath = wsUri.replace(/^file:\/\//, '');
    await grpc.addTrackedWorkspace(conn.port, conn.csrf, wsPath);
    const cascade = await grpc.startCascade(conn.port, conn.csrf, conn.apiKey, wsUri);
    cascadeId = cascade.cascadeId;
    if (!cascadeId) {
      yield { type: 'error', error: 'Failed to create conversation' };
      return;
    }
  }

  yield { type: 'content_delta', text: '', conversationId: cascadeId };

  // Start streaming listener first to avoid race condition where fast responses complete before subscription
  const streamGen = streamResponse(conn.port, conn.csrf, conn.apiKey, cascadeId, internalModel, maxWait);

  // Send the message concurrently
  grpc.sendMessage(conn.port, conn.csrf, conn.apiKey, cascadeId, promptText, internalModel).catch(err => {
    console.error('Error sending cascade message:', err);
  });

  // Stream the response (with auto-approval)
  yield* streamGen;
}

/**
 * Wait for the AI response by subscribing to StreamAgentStateUpdates.
 * Auto-approves blocking NOTIFY_USER steps so the agent continues.
 * Returns the full response text when the agent goes idle.
 */
function waitForResponse(port: number, csrf: string, apiKey: string, cascadeId: string, model: string, maxWaitMs: number): Promise<string> {
  return new Promise((resolve, reject) => {
    let lastContent = '';
    let updateCount = 0;
    let autoApproving = false;
    const approvedUris = new Set<string>();

    const timeout = setTimeout(() => {
      abort();
      console.log(`⏱ waitForResponse timeout after ${maxWaitMs}ms — updates=${updateCount} lastContent=${lastContent.length}chars`);
      resolve(lastContent || '[Timeout: AI did not respond in time]');
    }, maxWaitMs);

    const abort = grpc.streamAgentState(
      port, csrf, cascadeId,
      (update) => {
        updateCount++;
        const status = update?.status || '';
        const stepsUpdate = update?.mainTrajectoryUpdate?.stepsUpdate;
        const steps: any[] = stepsUpdate?.steps || [];

        // Always log first update and status transitions
        if (updateCount === 1 || status.includes('IDLE') || status.includes('ERROR')) {
          const last = steps[steps.length - 1];
          console.log(`📡 wait #${updateCount} cascadeId=${cascadeId.slice(0,8)} status=${status} steps=${steps.length} lastStepType=${last?.type || '-'} hasPR=${!!last?.plannerResponse}`);
        }
        dbg(`update#${updateCount} status=${status} steps=${steps.length}`);
        if (DEBUG && steps.length) {
          const last = steps[steps.length - 1];
          dbg(`  last step type=${last?.type} hasPR=${!!last?.plannerResponse} keys=${Object.keys(last || {}).join(',')}`);
        }

        if (steps.length) {
          const content = extractContent(steps);
          if (content) lastContent = content;

          if (!autoApproving) {
            const blockingUri = getBlockingNotifyUri(steps);
            if (blockingUri !== null) {
              const approvalKey = `${steps.length}:${blockingUri}`;
              if (!approvedUris.has(approvalKey)) {
                autoApproving = true;
                approvedUris.add(approvalKey);
                console.log(`🤖 Auto-approving NOTIFY_USER (uri="${blockingUri || 'none'}")`);
                grpc.proceedArtifact(port, csrf, apiKey, cascadeId, blockingUri, model)
                  .then(() => { autoApproving = false; })
                  .catch(() => { autoApproving = false; });
              }
            }
          }
        }

        if (status === 'CASCADE_RUN_STATUS_IDLE' && lastContent && !autoApproving) {
          const stillBlocking = steps.length > 0 ? getBlockingNotifyUri(steps) : null;
          if (stillBlocking === null) {
            clearTimeout(timeout);
            abort();
            dbg(`resolved after ${updateCount} updates`);
            resolve(lastContent);
          }
        }
      },
      (err) => {
        clearTimeout(timeout);
        console.log(`❌ streamAgentState error after ${updateCount} updates: ${err.message}`);
        if (lastContent) resolve(lastContent);
        else reject(new Error(`Stream error: ${err.message}`));
      }
    );
  });
}

function extractContentAfter(steps: any[], startIndex: number): string {
  for (let i = steps.length - 1; i >= Math.max(0, startIndex); i--) {
    const step = steps[i];
    if (!step) continue;
    const pr = step.plannerResponse;
    if (pr) {
      const text = pr.modifiedResponse || pr.response || pr.text || pr.content || '';
      if (text) return text;
    }
  }
  return '';
}

/**
 * Stream the AI response as chunks.
 * Auto-approves blocking NOTIFY_USER steps.
 */
async function* streamResponse(port: number, csrf: string, apiKey: string, cascadeId: string, model: string, maxWaitMs: number): AsyncGenerator<StreamChunk> {
  type QueueItem = StreamChunk | null;
  const queue: QueueItem[] = [];
  let resolve: (() => void) | null = null;

  function push(item: QueueItem) {
    queue.push(item);
    if (resolve) { resolve(); resolve = null; }
  }

  function waitForItem(): Promise<void> {
    if (queue.length > 0) return Promise.resolve();
    return new Promise(r => { resolve = r; });
  }

  let initialStepCount = -1;
  let lastEmitted = '';
  let updateCount = 0;
  let sawRunning = false;
  let autoApproving = false;
  const approvedUris = new Set<string>();

  const timeout = setTimeout(() => {
    abort();
    console.log(`⏱ streamResponse timeout after ${maxWaitMs}ms — updates=${updateCount} lastEmitted=${lastEmitted.length}chars`);
    push({ type: 'done', conversationId: cascadeId });
    push(null);
  }, maxWaitMs);

  const abort = grpc.streamAgentState(
    port, csrf, cascadeId,
    (update) => {
      updateCount++;
      const status = update?.status || '';
      const stepsUpdate = update?.mainTrajectoryUpdate?.stepsUpdate;
      const steps: any[] = stepsUpdate?.steps || [];

      if (initialStepCount === -1) {
        initialStepCount = steps.length;
      }

      if (status.includes('RUNNING') || steps.length > initialStepCount) {
        sawRunning = true;
      }

      if (updateCount === 1 || status.includes('IDLE') || status.includes('ERROR')) {
        const last = steps[steps.length - 1];
        console.log(`📡 stream #${updateCount} cascadeId=${cascadeId.slice(0,8)} status=${status} steps=${steps.length} (init=${initialStepCount}) sawRunning=${sawRunning} lastStepType=${last?.type || '-'} hasPR=${!!last?.plannerResponse}`);
      }
      dbg(`stream update#${updateCount} status=${status} steps=${steps.length}`);

      // Only extract and emit content once the current turn is active
      if (sawRunning && steps.length > 0) {
        const currentText = extractContent(steps);
        if (currentText.length > lastEmitted.length) {
          const delta = currentText.slice(lastEmitted.length);
          lastEmitted = currentText;
          push({ type: 'content_delta', text: delta, conversationId: cascadeId });
        }

        if (!autoApproving) {
          const blockingUri = getBlockingNotifyUri(steps);
          if (blockingUri !== null) {
            const approvalKey = `${steps.length}:${blockingUri}`;
            if (!approvedUris.has(approvalKey)) {
              autoApproving = true;
              approvedUris.add(approvalKey);
              console.log(`🤖 Auto-approving NOTIFY_USER (uri="${blockingUri || 'none'}")`);
              grpc.proceedArtifact(port, csrf, apiKey, cascadeId, blockingUri, model)
                .then(() => { autoApproving = false; })
                .catch(() => { autoApproving = false; });
            }
          }
        }
      }

      // Finish only after the current turn has run and gone back to IDLE
      const isCurrentTurnFinished = (
        status === 'CASCADE_RUN_STATUS_IDLE'
        && sawRunning
        && !autoApproving
      );

      if (isCurrentTurnFinished) {
        const stillBlocking = steps.length > 0 ? getBlockingNotifyUri(steps) : null;
        if (stillBlocking === null) {
          clearTimeout(timeout);
          abort();
          dbg(`stream resolved after ${updateCount} updates`);
          push({ type: 'done', conversationId: cascadeId });
          push(null);
        }
      }
    },
    (err) => {
      clearTimeout(timeout);
      console.log(`❌ streamResponse error after ${updateCount} updates: ${err.message}`);
      push({ type: 'error', error: err.message });
      push(null);
    }
  );

  while (true) {
    await waitForItem();
    while (queue.length > 0) {
      const item = queue.shift()!;
      if (item === null) return;
      yield item;
    }
  }
}
