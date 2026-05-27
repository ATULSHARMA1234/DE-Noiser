/** Centralized API configuration for SemanticOS
 *  - In Docker (served via Nginx on port 80): use relative URLs, Nginx proxies /api/* → backend
 *  - In local dev (Next.js on port 3000): hit backend directly on port 8000
 */
const isDev = typeof window !== 'undefined' && window.location.port === '3000';
const protocol = typeof window !== 'undefined' && window.location.protocol === 'https:' ? 'wss' : 'ws';
export const API_BASE = isDev ? 'http://127.0.0.1:8000' : '/api';
export const WS_BASE = isDev ? 'ws://127.0.0.1:8000' : `${protocol}://${typeof window !== 'undefined' ? window.location.host : 'localhost'}`;

export async function apiFetch(path: string, options?: RequestInit) {
  const res = await fetch(`${API_BASE}${path}`, options);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || 'API request failed');
  }
  return res.json();
}

export async function apiPost(path: string, body: any) {
  return apiFetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export async function apiPut(path: string, body: any) {
  return apiFetch(path, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export async function apiDelete(path: string) {
  return apiFetch(path, { method: 'DELETE' });
}

export async function runAnalysis(body: any) {
  // Submit the job to Celery
  const initRes = await apiPost('/analyze', body);
  
  if (!initRes.task_id) {
      return initRes; // fallback in case it was synchronous
  }

  const taskId = initRes.task_id;
  
  // Poll until completion
  while (true) {
      await new Promise(r => setTimeout(r, 1000));
      const statusRes = await apiFetch(`/tasks/${taskId}`);
      
      if (statusRes.status === 'SUCCESS') {
          return statusRes.result;
      } else if (statusRes.status === 'FAILURE') {
          throw new Error('Analysis job failed on the backend.');
      }
      // If 'PENDING' or 'PROGRESS', just continue loop
  }
}
