#!/usr/bin/env node
/**
 * Generate .env.production for Docker Compose from mizan-backend/.env (single source of truth).
 * Usage: node scripts/sync-env-production.mjs
 */
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const source = path.join(root, '.env');
const target = path.join(root, '.env.production');

if (!fs.existsSync(source)) {
  console.error(`Missing ${source}`);
  process.exit(1);
}

const lines = fs.readFileSync(source, 'utf8').split('\n');
const out = lines.map((line) => {
  const trimmed = line.trim();
  if (!trimmed || trimmed.startsWith('#') || !trimmed.includes('=')) {
    return line;
  }
  const key = trimmed.slice(0, trimmed.indexOf('=')).trim();
  const overrides = {
    DEBUG: 'False',
    SECURE_SSL_REDIRECT: 'True',
    PUBLIC_API_BASE_URL: 'https://api.heymizan.ai',
    FRONTEND_URL: 'https://app.heymizan.ai',
    MIYA_AGENT_API_BASE: 'https://api.heymizan.ai',
    CELERY_TASK_ALWAYS_EAGER: 'False',
    ALLOWED_HOSTS: '3.64.55.137,localhost,127.0.0.1,testserver,app.heymizan.ai,api.heymizan.ai',
    SQUARE_REDIRECT_URI: 'https://api.heymizan.ai/api/settings/square/oauth/callback/',
    GOOGLE_OAUTH_REDIRECT_URI:
      'https://api.heymizan.ai/api/integrations/google-calendar/callback/',
  };
  if (key in overrides) {
    return `${key}=${overrides[key]}`;
  }
  return line;
});

const header = [
  '# Auto-generated from .env — run: node scripts/sync-env-production.mjs',
  '# Docker Compose reads this file (env_file: .env.production)',
  '',
].join('\n');

fs.writeFileSync(target, header + out.join('\n'));
console.log(`Wrote ${target} from ${source}`);
