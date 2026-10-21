import fs from 'node:fs';
import path from 'node:path';

const AI_URL = (process.env.AI_SERVICE_URL || 'http://127.0.0.1:8090').replace(/\/$/, '');
const AI_TOKEN = process.env.AI_SERVICE_TOKEN || '';

function authHeaders() {
  if (!AI_TOKEN) throw new Error('AI_SERVICE_TOKEN is not configured on the Node server.');
  return { Authorization: `Bearer ${AI_TOKEN}` };
}

export async function enqueueFaceIndex({ eventId, mediaId, filePath, filename }) {
  const ext = path.extname(filename || filePath).toLowerCase();
  if (!['.jpg', '.jpeg', '.png', '.webp'].includes(ext)) return { skipped: true, reason: 'unsupported-image-format' };
  const bytes = fs.readFileSync(filePath);
  const form = new FormData();
  form.set('event_id', String(eventId));
  form.set('media_id', String(mediaId));
  form.set('file', new Blob([bytes]), path.basename(filename || filePath));
  const response = await fetch(`${AI_URL}/index`, { method: 'POST', headers: authHeaders(), body: form, signal: AbortSignal.timeout(30000) });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(result.detail || result.error || `AI index request failed (${response.status})`);
  return result;
}

export async function searchEventFaces({ eventId, selfieBuffer, filename = 'selfie.jpg', limit = 60 }) {
  const form = new FormData();
  form.set('event_id', String(eventId));
  form.set('limit', String(limit));
  form.set('selfie', new Blob([selfieBuffer]), path.basename(filename));
  const response = await fetch(`${AI_URL}/search`, { method: 'POST', headers: authHeaders(), body: form, signal: AbortSignal.timeout(60000) });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(result.detail || result.error || `AI face search failed (${response.status})`);
  return result;
}

export async function getFaceIndexStats() {
  const response = await fetch(`${AI_URL}/admin/stats`, { headers: authHeaders(), signal: AbortSignal.timeout(10000) });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(result.detail || `AI stats request failed (${response.status})`);
  return result;
}
