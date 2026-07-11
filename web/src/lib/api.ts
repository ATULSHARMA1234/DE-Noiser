/** Centralized API configuration for SemanticOS
 * - In Docker (served via Nginx on port 80): use relative URLs, Nginx proxies /api/* → backend
 * - In local dev (Next.js on port 3000): hit backend directly on port 8000
 */
const isDev = typeof window !== 'undefined' && window.location.port === '3000';
const protocol = typeof window !== 'undefined' && window.location.protocol === 'https:' ? 'wss' : 'ws';

// When the frontend is hosted separately from the backend (e.g. the UI on Vercel
// and the API on a VM), set NEXT_PUBLIC_API_BASE / NEXT_PUBLIC_WS_BASE to the
// backend's public URL. When unset, fall back to same-origin (`/api` behind the
// nginx proxy) for the all-in-one docker-compose deployment, and to localhost in
// local dev.
const ENV_API_BASE = process.env.NEXT_PUBLIC_API_BASE;
const ENV_WS_BASE = process.env.NEXT_PUBLIC_WS_BASE;

export const API_BASE = ENV_API_BASE || (isDev ? 'http://127.0.0.1:8000' : '/api');
export const WS_BASE = ENV_WS_BASE || (isDev ? 'ws://127.0.0.1:8000' : `${protocol}://${typeof window !== 'undefined' ? window.location.host : 'localhost'}`);

export async function apiFetch(path: string, options: RequestInit = {}) {
 const headers = new Headers(options.headers || {});
 if (typeof window !== 'undefined') {
 const token = localStorage.getItem('token');
 if (token && !headers.has('Authorization')) {
 headers.set('Authorization', `Bearer ${token}`);
 }
 }

 const res = await fetch(`${API_BASE}${path}`, {
 ...options,
 headers,
 });

 if (res.status === 401) {
 if (typeof window !== 'undefined' && window.location.pathname !== '/login') {
 localStorage.removeItem('token');
 localStorage.removeItem('user');
 window.location.href = '/login';
 }
 const err = await res.json().catch(() => ({ detail: 'Session expired. Please log in again.' }));
 throw new Error(err.detail || 'Unauthorized');
 }

 if (!res.ok) {
 const err = await res.json().catch(() => ({ detail: res.statusText }));
 throw new Error(err.detail || 'API request failed');
 }
 return res.json();
}

export async function apiPost(path: string, body: any, options: RequestInit = {}) {
 return apiFetch(path, {
 method: 'POST',
 headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
 body: JSON.stringify(body),
 ...options,
 });
}

export async function apiPut(path: string, body: any, options: RequestInit = {}) {
 return apiFetch(path, {
 method: 'PUT',
 headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
 body: JSON.stringify(body),
 ...options,
 });
}

export async function apiDelete(path: string, options: RequestInit = {}) {
 return apiFetch(path, { method: 'DELETE', ...options });
}

export async function runAnalysis(body: any) {
 // Submit the job to Celery
 const initRes = await apiPost('/analyze', body);
 
 if (!initRes.task_id) {
 return initRes; // fallback in case it was synchronous
 }

 const taskId = initRes.task_id;
 
 const MAX_POLL_MS = 5 * 60 * 1000; // 5 minute hard timeout
 const STUCK_PENDING_MS = 30 * 1000; // If still PENDING after 30s, no worker is running
 const POLL_INTERVAL_MS = 2000;
 const startTime = Date.now();
 let lastNonPending = false; // Track if task ever left PENDING
 
 // Poll until completion (with timeout)
 while (true) {
 await new Promise(r => setTimeout(r, POLL_INTERVAL_MS));
 
 const elapsed = Date.now() - startTime;
 if (elapsed > MAX_POLL_MS) {
   throw new Error('Analysis timed out after 5 minutes. The backend may be overloaded.');
 }

 const statusRes = await apiFetch(`/tasks/${taskId}`);
 
 if (statusRes.status === 'SUCCESS') {
   return statusRes.result;
 } else if (statusRes.status === 'FAILURE') {
   throw new Error(statusRes.error || 'Analysis job failed on the backend.');
 } else if (statusRes.status === 'PROGRESS') {
   lastNonPending = true;
   // Task is actively being processed, keep waiting
 } else if (statusRes.status === 'PENDING') {
   // If still PENDING after 30s, no Celery worker is consuming the queue
   if (!lastNonPending && elapsed > STUCK_PENDING_MS) {
     throw new Error(
       'Analysis task is stuck in queue — no Celery worker is running. ' +
       'Start a worker with: celery -A denoiser.workers.analysis_worker worker'
     );
   }
 }
 }
}
