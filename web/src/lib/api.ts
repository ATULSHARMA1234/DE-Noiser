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

/**
 * The session lives in httpOnly cookies the browser sends automatically, so
 * there is no token here to read — which is the point: an XSS cannot exfiltrate
 * a credential it cannot see. Tokens are never written to localStorage.
 *
 * `memoryToken` is the fallback for split-origin development, where the browser
 * declines to send a SameSite cookie from :3000 to :8000. It lives for the
 * lifetime of the tab and is never persisted, so it is not an XSS-durable
 * credential either.
 */
let memoryToken: string | null = null;

export function setSessionToken(token: string | null) {
 memoryToken = token;
}

/** Read the non-httpOnly CSRF cookie so it can be echoed in a header. */
function readCsrfToken(): string | null {
 if (typeof document === 'undefined') return null;
 const match = document.cookie.match(/(?:^|;\s*)sos_csrf=([^;]+)/);
 return match ? decodeURIComponent(match[1]) : null;
}

const UNSAFE_METHODS = ['POST', 'PUT', 'PATCH', 'DELETE'];

/** A single in-flight refresh, so a burst of 401s produces one refresh call. */
let refreshInFlight: Promise<boolean> | null = null;

async function refreshSession(): Promise<boolean> {
 if (!refreshInFlight) {
  refreshInFlight = (async () => {
   try {
    const headers = new Headers({ 'Content-Type': 'application/json' });
    const csrf = readCsrfToken();
    if (csrf) headers.set('X-CSRF-Token', csrf);
    const res = await fetch(`${API_BASE}/auth/refresh`, {
     method: 'POST',
     headers,
     credentials: 'include',
     // The refresh token normally rides in its own httpOnly cookie; the body is
     // only used by non-browser clients.
     body: JSON.stringify({}),
    });
    if (!res.ok) return false;
    const data = await res.json().catch(() => null);
    if (data?.access_token) memoryToken = data.access_token;
    if (data?.user && typeof window !== 'undefined') {
     localStorage.setItem('user', JSON.stringify(data.user));
    }
    return true;
   } catch {
    return false;
   } finally {
    // Cleared on the next tick so concurrent callers all observe this result.
    setTimeout(() => { refreshInFlight = null; }, 0);
   }
  })();
 }
 return refreshInFlight;
}

function endSession() {
 memoryToken = null;
 if (typeof window !== 'undefined') {
  localStorage.removeItem('user');
  // Legacy key from when tokens were persisted; removed so an upgraded tab
  // does not keep a stale credential lying around.
  localStorage.removeItem('token');
  if (window.location.pathname !== '/login') window.location.href = '/login';
 }
}

async function rawFetch(path: string, options: RequestInit): Promise<Response> {
 const headers = new Headers(options.headers || {});

 if (memoryToken && !headers.has('Authorization')) {
  headers.set('Authorization', `Bearer ${memoryToken}`);
 }

 const method = (options.method || 'GET').toUpperCase();
 if (UNSAFE_METHODS.includes(method) && !headers.has('X-CSRF-Token')) {
  const csrf = readCsrfToken();
  if (csrf) headers.set('X-CSRF-Token', csrf);
 }

 // Auto-set Content-Type for JSON string bodies (prevents 422 errors
 // when callers forget the header with body: JSON.stringify(...))
 if (options.body && typeof options.body === 'string' && !headers.has('Content-Type')) {
  headers.set('Content-Type', 'application/json');
 }

 return fetch(`${API_BASE}${path}`, {
  ...options,
  headers,
  // Required for the session cookies to be sent at all.
  credentials: 'include',
 });
}

export async function apiFetch(path: string, options: RequestInit = {}) {
 let res = await rawFetch(path, options);

 // A 401 usually just means the 30-minute access token aged out mid-session.
 // Refresh once and replay before concluding the session is over — previously
 // this went straight to /login, so every operator was thrown out of the app
 // twice an hour, including in the middle of an incident.
 if (res.status === 401 && path !== '/auth/refresh') {
  const refreshed = await refreshSession();
  if (refreshed) {
   res = await rawFetch(path, options);
  }
  if (res.status === 401) {
   endSession();
   const err = await res.json().catch(() => ({ detail: 'Session expired. Please log in again.' }));
   throw new Error(err.detail || 'Unauthorized');
  }
 }

 if (!res.ok) {
  const err = await res.json().catch(() => ({ detail: res.statusText }));
  // FastAPI validation errors return detail as an array of objects
  let message = 'API request failed';
  if (typeof err.detail === 'string') {
   message = err.detail;
  } else if (Array.isArray(err.detail)) {
   message = err.detail.map((d: any) => d.msg || JSON.stringify(d)).join('; ');
  } else if (err.detail) {
   message = JSON.stringify(err.detail);
  }
  throw new Error(message);
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

export async function apiPatch(path: string, body: any, options: RequestInit = {}) {
 return apiFetch(path, {
 method: 'PATCH',
 headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
 body: JSON.stringify(body),
 ...options,
 });
}

export async function apiDelete(path: string, options: RequestInit = {}) {
 return apiFetch(path, { method: 'DELETE', ...options });
}

const MAX_POLL_MS = 5 * 60 * 1000; // 5 minute hard timeout
const STUCK_PENDING_MS = 30 * 1000; // If still PENDING after 30s, no worker is running
const POLL_INTERVAL_MS = 2000;

/**
 * Poll a Celery task to completion.
 *
 * Split out of runAnalysis so a task can be followed without having submitted
 * it in this page load: the analysis keeps running on the worker across a hard
 * reload, and only the browser's poll loop was lost.
 *
 * `startTime` is the moment the job was *submitted*, not the moment we started
 * watching — otherwise a resumed task gets a fresh five-minute budget and a
 * timer that restarts from zero.
 */
export async function pollTask(taskId: string, startTime: number = Date.now()) {
 let lastNonPending = false; // Track if task ever left PENDING

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

export async function runAnalysis(body: any, onSubmitted?: (taskId: string) => void) {
 // Submit the job to Celery
 const initRes = await apiPost('/analyze', body);

 if (!initRes.task_id) {
 return initRes; // fallback in case it was synchronous
 }

 // Hand the server-side id back so the caller can persist it and reattach after
 // a reload instead of orphaning a job that is still running on the worker.
 onSubmitted?.(initRes.task_id);

 return pollTask(initRes.task_id);
}
