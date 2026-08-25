import { execSync } from 'child_process';
import { existsSync } from 'fs';

export interface LanguageServerInfo {
  pid: number;
  port: number;
  csrf: string;
  workspace?: string;
}

let _cachedServers: LanguageServerInfo[] = [];
let _cacheTime = 0;
const CACHE_TTL_MS = 3000;

/**
 * Decode Antigravity's workspace_id back to a real file:// URI.
 * Antigravity encodes workspace paths by replacing BOTH `/` and `-` with `_`.
 */
function decodeWorkspaceId(wsId: string): string | undefined {
  if (!wsId.startsWith('file_')) return wsId;

  const encoded = wsId.slice(5);
  const parts = encoded.split('_');
  let resolvedPath = '/';
  let i = 0;

  while (i < parts.length) {
    let found = false;
    for (let len = 1; len <= parts.length - i; len++) {
      const subparts = parts.slice(i, i + len);
      const candidates: string[] = [];
      if (len === 1) {
        candidates.push(subparts[0]);
      } else {
        candidates.push(subparts.join('-'));
        candidates.push(subparts.join('_'));
      }
      for (const candidate of candidates) {
        const testPath = resolvedPath + candidate;
        if (existsSync(testPath)) {
          resolvedPath = testPath + '/';
          i += len;
          found = true;
          break;
        }
      }
      if (found) break;
    }
    if (!found) {
      resolvedPath += parts.slice(i).join('/');
      break;
    }
  }

  return 'file://' + resolvedPath.replace(/\/$/, '');
}

/**
 * Discover all running Antigravity language_server instances.
 */
export function discoverLanguageServers(): LanguageServerInfo[] {
  if (Date.now() - _cacheTime < CACHE_TTL_MS && _cachedServers.length > 0) {
    return _cachedServers;
  }

  const servers: LanguageServerInfo[] = [];

  try {
    const psOutput = execSync('ps aux', { encoding: 'utf-8', timeout: 5000 });
    const psLines = psOutput.split('\n').filter(l => l.includes('language_server') && l.includes('--csrf_token'));

    let lsofOutput = '';
    try {
      lsofOutput = execSync('lsof -iTCP -sTCP:LISTEN -P -n 2>/dev/null', { encoding: 'utf-8', timeout: 10000 });
    } catch {}

    for (const line of psLines) {
      const pidMatch = line.match(/^\S+\s+(\d+)/);
      const csrfMatch = line.match(/--csrf_token[=\s]+(\S+)/);
      if (!pidMatch || !csrfMatch) continue;

      const pid = parseInt(pidMatch[1]);
      const csrf = csrfMatch[1];

      let port = 0;
      const pidRegex = new RegExp(`^language_\\S*\\s+${pid}\\s+.*:(\\d{4,5})\\s+\\(LISTEN\\)`, 'm');
      const portMatch = lsofOutput.match(pidRegex);
      if (portMatch) port = parseInt(portMatch[1]);

      if (port === 0) {
        try {
          const ssOutput = execSync('ss -tlpn', { encoding: 'utf-8', timeout: 5000 });
          for (const sline of ssOutput.split('\n')) {
            if (sline.includes(`pid=${pid},`) || sline.includes(`pid=${pid})`)) {
              const m = sline.match(/:(\d{4,5})\s+/);
              if (m) {
                port = parseInt(m[1]);
                break;
              }
            }
          }
        } catch {}
      }

      if (port === 0) continue;

      const wsMatch = line.match(/--workspace_id[=\s]+(\S+)/);
      const workspace = wsMatch?.[1] ? decodeWorkspaceId(wsMatch[1]) : undefined;

      servers.push({ pid, port, csrf, workspace });
    }
  } catch {}

  if (servers.length !== _cachedServers.length || servers.some((s, i) => s.port !== _cachedServers[i]?.port)) {
    console.log(`🔎 Discovered ${servers.length} server(s): ${servers.map(s => `port=${s.port} ws="${s.workspace}"`).join(' | ')}`);
  }

  _cachedServers = servers;
  _cacheTime = Date.now();
  return servers;
}

/**
 * Get first available server, or one matching a workspace path.
 */
export function getLanguageServer(workspacePath?: string): LanguageServerInfo | null {
  const servers = discoverLanguageServers();
  if (servers.length === 0) return null;

  if (workspacePath) {
    const exact = servers.find(s => s.workspace === workspacePath);
    if (exact) return exact;

    const partial = servers.find(s =>
      s.workspace?.includes(workspacePath) || workspacePath.includes(s.workspace || '\0')
    );
    if (partial) return partial;
  }

  return servers[0];
}
