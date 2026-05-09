/** Centralized API configuration for SemanticOS */
export const API_BASE = 'http://127.0.0.1:8000';
export const WS_BASE = 'ws://127.0.0.1:8000';

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
