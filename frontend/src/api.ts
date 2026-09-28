import type { AgentReply, HealthResponse } from './types';

const BASE = import.meta.env.VITE_API_BASE ?? '/api';

export class ApiError extends Error {
  // Declared explicitly rather than as a constructor parameter property:
  // the project builds with `erasableSyntaxOnly`, which disallows that shorthand.
  readonly status?: number;

  constructor(message: string, status?: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      headers: { 'Content-Type': 'application/json' },
      ...init,
    });
  } catch {
    throw new ApiError(
      'Cannot reach the backend. Start it with: uvicorn app.main:app --port 8000',
    );
  }

  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      if (body?.detail) detail = String(body.detail);
    } catch {
      /* keep the status-based message */
    }
    throw new ApiError(detail, response.status);
  }
  return response.json() as Promise<T>;
}

export const api = {
  health: () => request<HealthResponse>('/health'),

  chat: (message: string, sessionId: string | null) =>
    request<AgentReply>('/chat', {
      method: 'POST',
      body: JSON.stringify({ message, session_id: sessionId }),
    }),

  resetSession: (sessionId: string) =>
    request<unknown>(`/sessions/${sessionId}`, { method: 'DELETE' }),
};
