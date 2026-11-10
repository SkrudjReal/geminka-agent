import { execFileSync } from 'child_process';
import { existsSync, readdirSync, readFileSync } from 'fs';
import { homedir } from 'os';
import path from 'path';

function getWslStateDbCandidates(): string[] {
  const usersRoot = '/mnt/c/Users';
  if (!existsSync(usersRoot)) return [];

  try {
    return readdirSync(usersRoot, { withFileTypes: true })
      .filter((entry) => entry.isDirectory())
      .flatMap((entry) => [
        path.join(usersRoot, entry.name, 'AppData/Roaming/Antigravity/User/globalStorage/state.vscdb'),
        path.join(usersRoot, entry.name, 'AppData/Roaming/Antigravity IDE/User/globalStorage/state.vscdb')
      ]);
  } catch {
    return [];
  }
}

function getCandidatePaths(): string[] {
  return [
    process.env.ANTIGRAVITY_STATE_DB,
    path.join(homedir(), '.config/Antigravity/User/globalStorage/state.vscdb'),
    path.join(homedir(), '.config/Antigravity IDE/User/globalStorage/state.vscdb'),
    path.join(homedir(), '.antigravity-ide-server/data/User/globalStorage/state.vscdb'),
    ...getWslStateDbCandidates(),
    path.join(homedir(), 'Library/Application Support/Antigravity/User/globalStorage/state.vscdb'),
    path.join(homedir(), 'Library/Application Support/Antigravity IDE/User/globalStorage/state.vscdb')
  ].filter((candidate): candidate is string => Boolean(candidate));
}

function queryDb(sql: string): string {
  for (const dbPath of getCandidatePaths()) {
    if (!existsSync(dbPath)) continue;
    try {
      const result = execFileSync('sqlite3', [dbPath, sql], {
        encoding: 'utf-8',
        timeout: 5000,
        stdio: ['ignore', 'pipe', 'ignore']
      }).trim();
      if (result) return result;
    } catch {
      continue;
    }
  }
  return '';
}

function getCliOAuthToken(): string {
  const tokenPaths = [
    path.join(homedir(), '.gemini/antigravity-cli/antigravity-oauth-token'),
    path.join(homedir(), '.antigravity/antigravity-oauth-token'),
  ];
  for (const tokenPath of tokenPaths) {
    if (existsSync(tokenPath)) {
      try {
        const data = JSON.parse(readFileSync(tokenPath, 'utf-8'));
        const token = data?.token?.access_token || data?.access_token;
        if (typeof token === 'string' && token.length > 20) {
          return token;
        }
      } catch {}
    }
  }
  return '';
}

function getOAuthTokenFromStateDb(): string {
  const raw = queryDb("SELECT value FROM ItemTable WHERE key='antigravityUnifiedStateSync.oauthToken';");
  if (!raw) return '';
  try {
    const b1 = Buffer.from(raw, 'base64').toString('utf-8');
    const matches = b1.match(/[A-Za-z0-9+/=]{40,}/g);
    if (matches) {
      for (const piece of matches) {
        try {
          const decoded = Buffer.from(piece, 'base64').toString('utf-8');
          const ym = decoded.match(/ya29\.[a-zA-Z0-9_\-]+/);
          if (ym) return ym[0];
        } catch {}
      }
    }
  } catch {}
  return '';
}

export function getApiKey(): string {
  if (process.env.ANTIGRAVITY_API_KEY) {
    return process.env.ANTIGRAVITY_API_KEY;
  }

  const raw = queryDb("SELECT value FROM ItemTable WHERE key='antigravityAuthStatus';");
  if (raw) {
    try {
      const key = JSON.parse(raw).apiKey;
      if (key) return key;
    } catch {}
  }

  const cliToken = getCliOAuthToken();
  if (cliToken) return cliToken;

  const dbOauthToken = getOAuthTokenFromStateDb();
  if (dbOauthToken) return dbOauthToken;

  return '';
}

export function getUserInfo(): { name: string; email: string; apiKey: string } {
  const apiKey = getApiKey();
  const raw = queryDb("SELECT value FROM ItemTable WHERE key='antigravityAuthStatus';");
  if (raw) {
    try {
      const data = JSON.parse(raw);
      return {
        name: data.name || '',
        email: data.email || '',
        apiKey: apiKey || data.apiKey || ''
      };
    } catch {}
  }
  return { name: '', email: '', apiKey };
}
