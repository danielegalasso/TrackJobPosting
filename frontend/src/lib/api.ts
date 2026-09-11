/** Typed fetch helpers for the ACIDE-Watch API. */

import type {
  AlertSubscription,
  AppConfig,
  Filters,
  HandshakeResult,
  Job,
  JobFacets,
  JobPage,
  LogLine,
  ModelInfo,
  SpiderStatus,
} from '../types';

const BASE = '';

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
    this.name = 'ApiError';
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    headers: init?.body instanceof FormData ? undefined : { 'Content-Type': 'application/json' },
    ...init,
  });
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      if (typeof body.detail === 'string') detail = body.detail;
      else if (Array.isArray(body.detail)) detail = body.detail.map((e: { msg: string }) => e.msg).join('; ');
    } catch {
      /* the body was not JSON; the status line is all we have */
    }
    throw new ApiError(detail, response.status);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

/** Drop empty controls so the query string only carries active filters. */
export function toQuery(filters: Filters, offset = 0, limit = 60): string {
  const params = new URLSearchParams();
  const entries: [string, string | boolean][] = [
    ['search', filters.search],
    ['location', filters.location],
    ['remote', filters.remote],
    ['category', filters.category],
    ['years_experience', filters.years_experience],
    ['seniority', filters.seniority],
    ['posted_within', filters.posted_within],
    ['company', filters.company],
    ['category_type', filters.category_type],
    ['sort', filters.sort],
  ];
  for (const [key, value] of entries) {
    if (value) params.set(key, String(value));
  }
  if (filters.min_amount) {
    params.set('min_amount', filters.min_amount);
    params.set('rate', filters.rate);
    params.set('currency', filters.currency);
    if (filters.match_posting_currency) params.set('match_posting_currency', 'true');
  }
  if (filters.saved_only) params.set('saved_only', 'true');
  params.set('limit', String(limit));
  params.set('offset', String(offset));
  return params.toString();
}

export const api = {
  jobs: (filters: Filters, offset = 0, limit = 60) =>
    request<JobPage>(`/api/jobs?${toQuery(filters, offset, limit)}`),
  facets: () => request<JobFacets>('/api/jobs/facets'),
  bookmark: (id: string) =>
    request<Job>(`/api/jobs/${encodeURIComponent(id)}/bookmark`, { method: 'POST' }),
  dismiss: (id: string) =>
    request<Job>(`/api/jobs/${encodeURIComponent(id)}/dismiss`, { method: 'POST' }),

  createAlert: (email: string, filters: Record<string, unknown>) =>
    request<AlertSubscription>('/api/alerts', {
      method: 'POST',
      body: JSON.stringify({ email, filters }),
    }),
  alerts: () => request<AlertSubscription[]>('/api/alerts'),
  deleteAlert: (id: number) => request<void>(`/api/alerts/${id}`, { method: 'DELETE' }),
  pauseAlert: (id: number) => request<AlertSubscription>(`/api/alerts/${id}/pause`, { method: 'POST' }),
  resumeAlert: (id: number) => request<AlertSubscription>(`/api/alerts/${id}/resume`, { method: 'POST' }),
  sendAlertNow: (id: number) => request<HandshakeResult>(`/api/alerts/${id}/send-now`, { method: 'POST' }),

  config: () => request<AppConfig>('/api/config'),
  saveConfig: (config: Partial<AppConfig>) =>
    request<AppConfig>('/api/config', { method: 'PUT', body: JSON.stringify(config) }),
  uploadResume: (file: File) => {
    const form = new FormData();
    form.append('file', file);
    return request<{ filename: string; characters: number; preview: string }>(
      '/api/config/resume',
      { method: 'POST', body: form },
    );
  },
  deleteResume: () => request<void>('/api/config/resume', { method: 'DELETE' }),
  models: (q = '', refresh = false) => {
    const params = new URLSearchParams();
    if (q) params.set('q', q);
    if (refresh) params.set('refresh', 'true');
    const query = params.toString();
    return request<ModelInfo[]>(`/api/config/models${query ? `?${query}` : ''}`);
  },
  // The draft is posted so the buttons test what is on screen, not what
  // was last written to disk.
  testOpenRouter: (draft?: AppConfig) =>
    request<HandshakeResult>('/api/config/test/openrouter', {
      method: 'POST',
      body: JSON.stringify(draft ?? null),
    }),
  testSmtp: (draft?: AppConfig) =>
    request<HandshakeResult>('/api/config/test/smtp', {
      method: 'POST',
      body: JSON.stringify(draft ?? null),
    }),
  clearSecret: (name: 'openrouter_api_key' | 'smtp_password') =>
    request<{ cleared: boolean }>(`/api/config/secret/${name}`, { method: 'DELETE' }),

  spiderStatus: () => request<SpiderStatus>('/api/spider/status'),
  runSpider: () => request<{ started: boolean; detail: string }>('/api/spider/run', { method: 'POST' }),
  logHistory: () => request<LogLine[]>('/api/spider/logs/history'),

  legal: async (document: 'privacy' | 'terms') => {
    const response = await fetch(`/api/legal/${document}`);
    if (!response.ok) throw new ApiError('Document unavailable', response.status);
    return response.text();
  },
};
