/** The live console: SSE lines, run controls and per-run counters. */

import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '../lib/api';
import { renderWithQuery } from '../test/harness';
import { MockEventSource } from '../test/setup';
import { InspectorConsole } from './InspectorConsole';

function stubStatus(overrides: Record<string, unknown> = {}) {
  return vi.spyOn(api, 'spiderStatus').mockResolvedValue({
    running: false,
    scheduler_active: true,
    spider_enabled: true,
    interval_minutes: 360,
    next_run_at: null,
    last_run: {
      started_at: '2026-09-10T00:00:00Z', finished_at: '2026-09-10T00:01:00Z',
      sources_polled: 3, postings_seen: 120, postings_new: 12, postings_scored: 12,
      alerts_sent: 1, errors: [], running: false,
    },
    targets: [{ company: 'Apple', source_type: 'greenhouse', board_token: 'apple', enabled: true }],
    ...overrides,
  } as Awaited<ReturnType<typeof api.spiderStatus>>);
}

describe('InspectorConsole', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    MockEventSource.instances.length = 0;
  });

  it('reports the counters from the last run', async () => {
    stubStatus();
    renderWithQuery(<InspectorConsole />);
    await waitFor(() => expect(screen.getByText('120')).toBeInTheDocument());
    expect(screen.getByText('Postings seen')).toBeInTheDocument();
    expect(screen.getByText('3')).toBeInTheDocument();
  });

  it('subscribes to the log stream on mount and closes it on unmount', async () => {
    stubStatus();
    const { unmount } = renderWithQuery(<InspectorConsole />);
    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1));
    expect(MockEventSource.instances[0].url).toBe('/api/spider/logs');

    unmount();
    expect(MockEventSource.instances[0].closed).toBe(true);
  });

  it('renders lines pushed down the stream', async () => {
    stubStatus();
    renderWithQuery(<InspectorConsole />);
    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1));

    const source = MockEventSource.instances[0];
    source.onopen?.();
    source.emit({ ts: '2026-09-10T10:00:00+00:00', level: 'success', message: 'MATCH Apple — Watch Software' });

    expect(await screen.findByText('MATCH Apple — Watch Software')).toBeInTheDocument();
    expect(screen.getByText('10:00:00')).toBeInTheDocument();
  });

  it('ignores a malformed frame rather than breaking the stream', async () => {
    stubStatus();
    renderWithQuery(<InspectorConsole />);
    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1));
    const source = MockEventSource.instances[0];

    source.onmessage?.({ data: 'not json' });
    source.emit({ ts: '2026-09-10T10:00:01+00:00', level: 'info', message: 'still alive' });
    expect(await screen.findByText('still alive')).toBeInTheDocument();
  });

  it('shows connection state so a dead stream is visible', async () => {
    stubStatus();
    renderWithQuery(<InspectorConsole />);
    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1));

    expect(screen.getByText(/reconnecting/)).toBeInTheDocument();
    MockEventSource.instances[0].onopen?.();
    expect(await screen.findByText(/live/)).toBeInTheDocument();
  });

  it('triggers a manual run', async () => {
    stubStatus();
    const run = vi.spyOn(api, 'runSpider').mockResolvedValue({ started: true, detail: 'inspection started' });
    renderWithQuery(<InspectorConsole />);

    await userEvent.click(await screen.findByRole('button', { name: /Run now/ }));
    expect(run).toHaveBeenCalledTimes(1);
  });

  it('disables the run control while a run is in flight', async () => {
    stubStatus({ running: true });
    renderWithQuery(<InspectorConsole />);
    await waitFor(() => expect(screen.getByRole('button', { name: /Running/ })).toBeDisabled());
  });

  it('surfaces per-source errors from the last run', async () => {
    stubStatus({
      last_run: {
        started_at: '2026-09-10T00:00:00Z', finished_at: '2026-09-10T00:01:00Z',
        sources_polled: 1, postings_seen: 10, postings_new: 0, postings_scored: 0,
        alerts_sent: 0, errors: ['Broken: board not found (404) — check the board token'],
        running: false,
      },
    });
    renderWithQuery(<InspectorConsole />);
    expect(await screen.findByText(/1 issue in the last run/)).toBeInTheDocument();
    expect(screen.getByText(/check the board token/)).toBeInTheDocument();
  });

  it('says scheduled runs are off when they are', async () => {
    stubStatus({ spider_enabled: false });
    renderWithQuery(<InspectorConsole />);
    expect(await screen.findByText('disabled')).toBeInTheDocument();
  });

  it('prompts for a run before any output exists', async () => {
    stubStatus();
    renderWithQuery(<InspectorConsole />);
    expect(await screen.findByText(/Waiting for output/)).toBeInTheDocument();
  });
});
