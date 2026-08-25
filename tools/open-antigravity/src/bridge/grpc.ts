import https from 'https';
import { IncomingMessage } from 'http';

const agent = new https.Agent({ rejectUnauthorized: false });

/**
 * Build a Connect streaming envelope: [flags:1][length:4 BE][payload]
 */
function buildConnectEnvelope(json: Record<string, any>): Buffer {
  const payload = Buffer.from(JSON.stringify(json), 'utf-8');
  const header = Buffer.alloc(5);
  header.writeUInt8(0x00, 0);
  header.writeUInt32BE(payload.length, 1);
  return Buffer.concat([header, payload]);
}

/**
 * Parse Connect streaming envelopes from a buffer.
 */
function parseConnectEnvelopes(buf: Buffer): { messages: any[]; remaining: Buffer } {
  const messages: any[] = [];
  let pos = 0;
  while (pos + 5 <= buf.length) {
    const length = buf.readUInt32BE(pos + 1);
    if (pos + 5 + length > buf.length) break;
    const payload = buf.subarray(pos + 5, pos + 5 + length);
    try {
      messages.push(JSON.parse(payload.toString('utf-8')));
    } catch {}
    pos += 5 + length;
  }
  return { messages, remaining: buf.subarray(pos) };
}

export interface GrpcCallOptions {
  port: number;
  csrf: string;
  method: string;
  body: Record<string, any>;
}

/**
 * Make a gRPC-Web call to the language_server.
 */
export function grpcCall(opts: GrpcCallOptions): Promise<any> {
  return new Promise((resolve, reject) => {
    const payload = JSON.stringify(opts.body);
    const req = https.request({
      hostname: '127.0.0.1',
      port: opts.port,
      path: `/exa.language_server_pb.LanguageServerService/${opts.method}`,
      method: 'POST',
      agent,
      headers: {
        'Content-Type': 'application/json',
        'connect-protocol-version': '1',
        'x-codeium-csrf-token': opts.csrf,
        'Content-Length': Buffer.byteLength(payload),
      },
    }, (res) => {
      let data = '';
      res.on('data', (chunk) => { data += chunk; });
      res.on('end', () => {
        try { resolve(JSON.parse(data)); }
        catch { resolve(data); }
      });
    });
    req.on('error', reject);
    req.write(payload);
    req.end();
  });
}

/**
 * Open a Connect streaming connection to StreamAgentStateUpdates.
 * Returns an abort function.
 */
export function streamAgentState(
  port: number,
  csrf: string,
  conversationId: string,
  onUpdate: (update: any) => void,
  onError?: (err: Error) => void,
): () => void {
  const body = buildConnectEnvelope({
    conversationId,
    subscriberId: `open-antigravity-${Date.now()}`,
  });

  let aborted = false;
  const req = https.request({
    hostname: '127.0.0.1',
    port,
    path: '/exa.language_server_pb.LanguageServerService/StreamAgentStateUpdates',
    method: 'POST',
    agent,
    headers: {
      'Content-Type': 'application/connect+json',
      'Connect-Protocol-Version': '1',
      'x-codeium-csrf-token': csrf,
      'Content-Length': body.length,
    },
  }, (res: IncomingMessage) => {
    let buffer = Buffer.alloc(0);

    res.on('data', (chunk: Buffer) => {
      if (aborted) return;
      buffer = Buffer.concat([buffer, chunk]);
      const { messages, remaining } = parseConnectEnvelopes(buffer);
      buffer = Buffer.from(remaining);
      for (const msg of messages) {
        const update = msg?.update;
        if (update) onUpdate(update);
        else if (msg?.error) onError?.(new Error(msg.error.message || 'stream error'));
      }
    });

    res.on('end', () => { if (!aborted) onError?.(new Error('stream ended')); });
    res.on('error', (err) => { if (!aborted) onError?.(err); });
  });

  req.on('error', (err) => { if (!aborted) onError?.(err); });
  req.write(body);
  req.end();

  return () => { aborted = true; req.destroy(); };
}

// --- Convenience wrappers ---

export function buildMetadata(apiKey: string) {
  return {
    ideName: 'antigravity',
    apiKey,
    locale: 'en',
    ideVersion: '1.20.6',
    extensionName: 'antigravity',
  };
}

export function buildCascadeConfig(model: string = 'MODEL_PLACEHOLDER_M26') {
  return {
    plannerConfig: {
      conversational: { plannerMode: 'CONVERSATIONAL_PLANNER_MODE_DEFAULT', agenticMode: true },
      toolConfig: {
        runCommand: { autoCommandConfig: { autoExecutionPolicy: 'CASCADE_COMMANDS_AUTO_EXECUTION_EAGER' } },
        notifyUser: { artifactReviewMode: 'ARTIFACT_REVIEW_MODE_ALWAYS' },
      },
      requestedModel: { model },
    },
  };
}

export async function startCascade(port: number, csrf: string, apiKey: string, workspaceUri: string) {
  return grpcCall({
    port, csrf,
    method: 'StartCascade',
    body: {
      metadata: buildMetadata(apiKey),
      source: 'CORTEX_TRAJECTORY_SOURCE_CASCADE_CLIENT',
      workspaceUris: [workspaceUri],
    },
  });
}

export async function sendMessage(port: number, csrf: string, apiKey: string, cascadeId: string, text: string, model?: string) {
  return grpcCall({
    port, csrf,
    method: 'SendUserCascadeMessage',
    body: {
      cascadeId,
      items: [{ text }],
      metadata: buildMetadata(apiKey),
      cascadeConfig: buildCascadeConfig(model),
    },
  });
}

export async function addTrackedWorkspace(port: number, csrf: string, workspacePath: string) {
  return grpcCall({
    port, csrf,
    method: 'AddTrackedWorkspace',
    body: { workspace: workspacePath },
  });
}

export async function getModelConfigs(port: number, csrf: string, apiKey: string) {
  return grpcCall({
    port, csrf,
    method: 'GetCascadeModelConfigData',
    body: { metadata: buildMetadata(apiKey) },
  });
}

export async function cancelCascade(port: number, csrf: string, apiKey: string, cascadeId: string) {
  return grpcCall({
    port, csrf,
    method: 'CancelCascadeInvocation',
    body: { cascadeId, metadata: buildMetadata(apiKey) },
  });
}

export async function proceedArtifact(port: number, csrf: string, apiKey: string, cascadeId: string, artifactUri: string, model?: string) {
  return grpcCall({
    port, csrf,
    method: 'SendUserCascadeMessage',
    body: {
      cascadeId,
      metadata: buildMetadata(apiKey),
      cascadeConfig: buildCascadeConfig(model),
      artifactComments: [{
        artifactUri,
        fullFile: {},
        approvalStatus: 'ARTIFACT_APPROVAL_STATUS_APPROVED',
      }],
    },
  });
}
