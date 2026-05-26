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
