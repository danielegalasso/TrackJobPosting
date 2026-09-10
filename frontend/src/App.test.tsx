/** App wiring: filters drive queries, and the grid reflects mutations. */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import { api } from './lib/api';
import { makeJob, makePage, renderWithQuery } from './test/harness';
import type { Filters } from './types';

const WATCH = makeJob({ id: 'g:1', title: 'Watch Software Engineer', company: 'Apple' });
const CLOUD = makeJob({
  id: 'g:2',
  title: 'Cloud Security Engineer',
  company: 'Globex',
  category: 'Cloud Security',
  category_type: 'Pivot / Growth Opportunity',
  experience_fit_score: 35,
  interest_fit_score: 96,
});

/**
 * A fake backend that holds state, so a refetch after a mutation returns what
 * the mutation did — exactly like the real server. A stateless stub would
 * silently revert every optimistic update and hide the behaviour under test.
 */
function stubBackend(initial = [WATCH, CLOUD]) {
  let store = initial.map((job) => ({ ...job }));

  const jobsSpy = vi.spyOn(api, 'jobs').mockImplementation(async (filters: Filters) => {
    let matching = store.filter((job) => !job.dismissed);
    if (filters.saved_only) matching = matching.filter((job) => job.saved);
    if (filters.search) {
      matching = matching.filter((job) =>
        job.title.toLowerCase().includes(filters.search.toLowerCase()),
      );
    }
    if (filters.category) matching = matching.filter((job) => job.category === filters.category);
    return makePage(matching);
  });

  vi.spyOn(api, 'bookmark').mockImplementation(async (id: string) => {
    store = store.map((job) => (job.id === id ? { ...job, saved: !job.saved } : job));
    return store.find((job) => job.id === id)!;
  });
  vi.spyOn(api, 'dismiss').mockImplementation(async (id: string) => {
    store = store.map((job) => (job.id === id ? { ...job, dismissed: true } : job));
    return store.find((job) => job.id === id)!;
  });
  vi.spyOn(api, 'facets').mockResolvedValue({
    categories: ['Cloud Security', 'Embedded / Systems'],
    companies: ['Apple', 'Globex'],
    seniorities: ['Mid-Level', 'Senior'],
    currencies: ['USD'],
  });
  vi.spyOn(api, 'spiderStatus').mockResolvedValue({
    running: false,
    scheduler_active: true,
    spider_enabled: false,
    interval_minutes: 360,
    next_run_at: null,
    last_run: {
      started_at: '2026-09-10T00:00:00Z', finished_at: null, sources_polled: 1,
      postings_seen: 2, postings_new: 2, postings_scored: 2, alerts_sent: 0,
      errors: [], running: false,
    },
    targets: [{ company: 'Apple', source_type: 'greenhouse', board_token: 'apple', enabled: true }],
  });
  return jobsSpy;
}

const cards = () => screen.queryAllByRole('article');

describe('App', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
  });

  it('renders the indexed positions', async () => {
    stubBackend();
    renderWithQuery(<App />);
    await waitFor(() => expect(cards()).toHaveLength(2));
    expect(screen.getByText('Watch Software Engineer')).toBeInTheDocument();
  });

  it('narrows the grid as the search filter is applied', async () => {
    stubBackend();
    renderWithQuery(<App />);
    await waitFor(() => expect(cards()).toHaveLength(2));

    await userEvent.type(screen.getByLabelText(/Search roles/), 'Cloud');
    await waitFor(() => expect(cards()).toHaveLength(1), { timeout: 3000 });
    expect(screen.getByText('Cloud Security Engineer')).toBeInTheDocument();
  });

  it('restores the full list when the filters are cleared', async () => {
    // Regression guard: the grid was previously populated by a side effect
    // inside the query function, which does not re-run on a cache hit — so
    // returning to an earlier filter left stale results on screen.
    stubBackend();
    renderWithQuery(<App />);
    await waitFor(() => expect(cards()).toHaveLength(2));

    await userEvent.type(screen.getByLabelText(/Search roles/), 'Cloud');
    await waitFor(() => expect(cards()).toHaveLength(1), { timeout: 3000 });

    await userEvent.click(screen.getByRole('button', { name: /Clear 1 filter/ }));
    await waitFor(() => expect(cards()).toHaveLength(2), { timeout: 3000 });
  });

  it('debounces typing into a single request per pause', async () => {
    const jobsSpy = stubBackend();
    renderWithQuery(<App />);
    await waitFor(() => expect(cards()).toHaveLength(2));
    jobsSpy.mockClear();

    await userEvent.type(screen.getByLabelText(/Search roles/), 'Cloud');
    await waitFor(() => expect(cards()).toHaveLength(1), { timeout: 3000 });
    // Five keystrokes must not become five round trips.
    expect(jobsSpy.mock.calls.length).toBeLessThan(3);
  });

  it('bookmarks optimistically and keeps the badge in step', async () => {
    stubBackend();
    renderWithQuery(<App />);
    await waitFor(() => expect(cards()).toHaveLength(2));

    const card = screen.getByText('Watch Software Engineer').closest('article')!;
    await userEvent.click(within(card).getByRole('button', { name: /^Save / }));

    await waitFor(() =>
      expect(within(card).getByRole('button', { name: /^Remove / })).toBeInTheDocument(),
    );
    expect(api.bookmark).toHaveBeenCalledWith('g:1');
  });

  it('puts a bookmark back if the server rejects it', async () => {
    stubBackend();
    vi.spyOn(api, 'bookmark').mockRejectedValue(new Error('offline'));
    renderWithQuery(<App />);
    await waitFor(() => expect(cards()).toHaveLength(2));

    const card = screen.getByText('Watch Software Engineer').closest('article')!;
    await userEvent.click(within(card).getByRole('button', { name: /^Save / }));

    await waitFor(() =>
      expect(within(card).getByRole('button', { name: /^Save / })).toBeInTheDocument(),
    );
  });

  it('removes a dismissed posting from the grid at once', async () => {
    stubBackend();
    renderWithQuery(<App />);
    await waitFor(() => expect(cards()).toHaveLength(2));

    const card = screen.getByText('Cloud Security Engineer').closest('article')!;
    await userEvent.click(within(card).getByRole('button', { name: /^Dismiss / }));
    await waitFor(() => expect(screen.queryByText('Cloud Security Engineer')).not.toBeInTheDocument());
  });

  it('shows only bookmarks under My Jobs', async () => {
    stubBackend([{ ...WATCH, saved: true }, CLOUD]);
    renderWithQuery(<App />);
    await waitFor(() => expect(cards()).toHaveLength(2));

    await userEvent.click(within(screen.getByRole('banner')).getByRole('button', { name: /My Jobs/ }));
    await waitFor(() => expect(cards()).toHaveLength(1));
    expect(screen.getByText('Watch Software Engineer')).toBeInTheDocument();
  });

  it('tells the user to configure a feed when nothing is indexed', async () => {
    stubBackend([]);
    renderWithQuery(<App />);
    await waitFor(() =>
      expect(screen.getByText(/No positions match these filters/)).toBeInTheDocument(),
    );
    expect(screen.getByText(/run the inspector/)).toBeInTheDocument();
  });

  it('surfaces a failing backend instead of an empty grid', async () => {
    vi.spyOn(api, 'jobs').mockRejectedValue(new Error('Request failed (500)'));
    vi.spyOn(api, 'facets').mockResolvedValue({
      categories: [], companies: [], seniorities: [], currencies: [],
    });
    vi.spyOn(api, 'spiderStatus').mockRejectedValue(new Error('offline'));
    renderWithQuery(<App />);
    await waitFor(() =>
      expect(screen.getByText(/Could not load positions/)).toBeInTheDocument(),
    );
    expect(screen.getByText('Request failed (500)')).toBeInTheDocument();
  });

  it('remembers filters across a reload', async () => {
    stubBackend();
    const { unmount } = renderWithQuery(<App />);
    await waitFor(() => expect(cards()).toHaveLength(2));
    await userEvent.type(screen.getByLabelText(/Search roles/), 'Cloud');
    await waitFor(() => expect(cards()).toHaveLength(1), { timeout: 3000 });
    unmount();

    renderWithQuery(<App />);
    await waitFor(() =>
      expect(screen.getByLabelText(/Search roles/)).toHaveValue('Cloud'),
    );
  });

  it('opens the alert dialog carrying the live filters', async () => {
    stubBackend();
    renderWithQuery(<App />);
    await waitFor(() => expect(cards()).toHaveLength(2));

    await userEvent.type(screen.getByLabelText(/Search roles/), 'Cloud');
    await userEvent.click(screen.getByRole('button', { name: 'Alert' }));

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('Cloud')).toBeInTheDocument();
  });
});
