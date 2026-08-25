import { execSync } from 'child_process';
import { existsSync } from 'fs';
import { homedir } from 'os';
import path from 'path';

const CANDIDATE_PATHS = [
  '/mnt/c/Users/velunae/AppData/Roaming/Antigravity/User/globalStorage/state.vscdb',
  path.join(homedir(), '.config/Antigravity/User/globalStorage/state.vscdb'),
  path.join(homedir(), '.antigravity-ide-server/data/User/globalStorage/state.vscdb'),
  path.join(homedir(), 'Library/Application Support/Antigravity/User/globalStorage/state.vscdb')
];

function getStateDbPath(): string {
  for (const p of CANDIDATE_PATHS) {
    if (existsSync(p)) return p;
  }
  return CANDIDATE_PATHS[0];
}

function queryDb(sql: string): string {
  try {
    const dbPath = getStateDbPath();
    return execSync(`sqlite3 "${dbPath}" "${sql}"`, {
      encoding: 'utf-8',
      timeout: 5000
    }).trim();
  } catch {
    return '';
  }
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
