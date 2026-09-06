import { execFileSync } from 'child_process';
import { existsSync, readdirSync } from 'fs';
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

export function getApiKey(): string {
  const raw = queryDb("SELECT value FROM ItemTable WHERE key='antigravityAuthStatus';");
  if (!raw) return '';
  try { return JSON.parse(raw).apiKey || ''; } catch { return ''; }
}

export function getUserInfo(): { name: string; email: string; apiKey: string } {
  const raw = queryDb("SELECT value FROM ItemTable WHERE key='antigravityAuthStatus';");
  if (!raw) return { name: '', email: '', apiKey: '' };
  try {
    const data = JSON.parse(raw);
    return { name: data.name || '', email: data.email || '', apiKey: data.apiKey || '' };
  } catch { return { name: '', email: '', apiKey: '' }; }
}
