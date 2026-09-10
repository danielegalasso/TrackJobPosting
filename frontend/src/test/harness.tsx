import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, type RenderResult } from '@testing-library/react';
import type { ReactElement } from 'react';
import type { Job, JobPage } from '../types';

/** A fresh client per test: retries off so failures surface immediately. */
export function makeClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, staleTime: 0 },
      mutations: { retry: false },
    },
  });
}

export function renderWithQuery(ui: ReactElement, client = makeClient()): RenderResult {
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

export function makeJob(overrides: Partial<Job> = {}): Job {
  return {
    id: 'greenhouse-acme:1',
    title: 'Software Engineer, Watch Software',
    company: 'Acme',
    location: 'Cupertino, California, United States +2 more',
    seniority: 'Mid-Level',
    category: 'Embedded / Systems',
    years_experience_min: 4,
    date_posted: '2026-09-08',
    rate: 'Yearly',
    currency: 'USD',
    amount: 165000,
    experience_fit_score: 88,
    interest_fit_score: 72,
    category_type: 'Direct Match',
    transferable_skills: ['Embedded C', 'Python'],
    skills_to_learn: ['RTOS internals'],
    alert_summary: 'Close to the work you already ship.',
    apply_url: 'https://example.com/jobs/1',
    source_type: 'greenhouse',
    saved: false,
    dismissed: false,
    created_at: '2026-09-08T00:00:00Z',
    ...overrides,
  };
}

export function makePage(items: Job[], overrides: Partial<JobPage> = {}): JobPage {
  return {
    items,
    total: items.length,
    limit: 60,
    offset: 0,
    has_more: false,
    ...overrides,
  };
}
