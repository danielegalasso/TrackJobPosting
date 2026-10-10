/** Typed fetch helpers for the findmyjob.top API. */

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
  Brief,
  ReviewCounts,
  ReviewEstimate,
  ReviewMode,
  ScoringSettings,
  SearchStatus,
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
  const entries: [string, string][] = [
    ['search', filters.search],
    ['country', filters.country],
    ['company', filters.company],
    ['sort', filters.sort],
    ['origin', filters.origin],
    ['excluded', filters.excluded],
  ];
  for (const [key, value] of entries) {
    if (value) params.set(key, value);
  }
  if (filters.saved_only) params.set('saved_only', 'true');
  if (filters.dismissed_only) params.set('dismissed_only', 'true');
  params.set('limit', String(limit));
  params.set('offset', String(offset));
  return params.toString();
}

export const api = {
  jobs: (filters: Filters, offset = 0, limit = 60) =>
    request<JobPage>(`/api/jobs?${toQuery(filters, offset, limit)}`),
  facets: (origin = '') =>
    request<JobFacets>(`/api/jobs/facets${origin ? `?origin=${encodeURIComponent(origin)}` : ''}`),
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

  searchStatus: () => request<SearchStatus>('/api/search/status'),
  runSearch: (jobs?: number) =>
    request<{ started: boolean }>(`/api/search/run${jobs ? `?jobs=${jobs}` : ''}`, { method: 'POST' }),
  stopSearch: () => request<{ stopping: boolean }>('/api/search/stop', { method: 'POST' }),
  brief: () => request<Brief>('/api/search/brief'),
  saveBrief: (text: string) =>
    request<{ digest: string; counts: ReviewCounts }>('/api/search/brief', {
      method: 'PUT',
      body: JSON.stringify({ text }),
    }),
  saveScoring: (scoring: Omit<ScoringSettings, 'has_key'>) =>
    request<ScoringSettings>('/api/search/scoring', { method: 'PUT', body: JSON.stringify(scoring) }),
  estimate: (mode: ReviewMode, limit?: number) =>
    request<ReviewEstimate>(
      `/api/search/estimate?mode=${mode}${limit ? `&limit=${limit}` : ''}`,
    ),
  review: (mode: ReviewMode, limit?: number) =>
    request<{ started: boolean }>('/api/search/review', {
      method: 'POST',
      body: JSON.stringify({ mode, limit: limit ?? null }),
    }),
  stopReview: () => request<{ stopping: boolean }>('/api/search/review/stop', { method: 'POST' }),

  spiderStatus: () => request<SpiderStatus>('/api/spider/status'),
  runSpider: () => request<{ started: boolean; detail: string }>('/api/spider/run', { method: 'POST' }),
  logHistory: () => request<LogLine[]>('/api/spider/logs/history'),

  legal: async (document: 'privacy' | 'terms') => {
    const response = await fetch(`/api/legal/${document}`);
    if (!response.ok) throw new ApiError('Document unavailable', response.status);
    return response.text();
  },
};
