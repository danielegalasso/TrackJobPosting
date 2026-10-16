/** Run the Search: the run, its results by employer, scoring and the prompt. */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '../lib/api';
import { makeSearchStatus, renderWithQuery } from '../test/harness';
import { MockEventSource } from '../test/setup';
import { SearchPanel } from './SearchPanel';

function stub(status = makeSearchStatus()) {
  vi.spyOn(api, 'searchStatus').mockResolvedValue(status);
  vi.spyOn(api, 'brief').mockResolvedValue({
    text: 'You are my career advisor.',
    instructions: 'You receive ONE job offer, not a list.',
    digest: 'abc',
  });
  vi.spyOn(api, 'models').mockResolvedValue([]);
  vi.spyOn(api, 'filters').mockResolvedValue({
    title: ['Senior'],
    company: [],
    location: [],
    excluded: 12,
    by_filter: { 'Filtro titolo: Senior': 12 },
  });
}

describe('SearchPanel', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    MockEventSource.instances.length = 0;
  });

  it('starts a search', async () => {
    stub();
    const run = vi.spyOn(api, 'runSearch').mockResolvedValue({ started: true });
    renderWithQuery(<SearchPanel />);
    await userEvent.click(await screen.findByRole('button', { name: 'Run the Search' }));
    expect(run).toHaveBeenCalled();
  });

  it('shows what each employer gained and lost, and the sites not read', async () => {
    const status = makeSearchStatus();
    status.search = {
      ...status.search,
      phase: 'done',
      sites_total: 3,
      sites: [
        { name: 'Airbus', status: 'OK', counts: '10/10', new: 2, gone: 1, seconds: 9 },
        { name: 'Thales', status: 'FAILED', counts: '-', new: null, gone: null, seconds: 1 },
      ],
      changes: [{ company: 'Airbus', new: 2, gone: 1, new_excluded: 1 }],
      new_total: 2,
      gone_total: 1,
    };
    stub(status);
    renderWithQuery(<SearchPanel />);
    const table = await screen.findByRole('table');
    const row = within(table).getByText('Airbus').closest('tr')!;
    expect(within(row).getByText('+2')).toBeInTheDocument();
    expect(within(row).getByText('−1')).toBeInTheDocument();
    expect(screen.getByText(/Thales \(FAILED\)/)).toBeInTheDocument();
  });

  it('asks before scoring, with the count and the cost', async () => {
    stub();
    const save = vi.spyOn(api, 'saveScoring');
    vi.spyOn(api, 'estimate').mockResolvedValue({
      count: 30,
      model: 'z-ai/glm-5.3-flash',
      per_posting: 0.0007,
      source: 'history',
      samples: 100,
      total: 0.021,
    });
    const review = vi.spyOn(api, 'review').mockResolvedValue({ started: true });
    renderWithQuery(<SearchPanel />);

    await userEvent.type(await screen.findByLabelText(/At most/), '30');
    await userEvent.click(screen.getByRole('button', { name: /Rescore with the current prompt/ }));
    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent('30 postings');
    expect(dialog).toHaveTextContent('$0.02');
    expect(api.estimate).toHaveBeenCalledWith('stale', 30);
    expect(review).not.toHaveBeenCalled();
    expect(save).not.toHaveBeenCalled(); // nothing changed in the settings

    await userEvent.click(within(dialog).getByRole('button', { name: 'Start' }));
    expect(review).toHaveBeenCalledWith('stale', 30);
  });

  it('saves the prompt', async () => {
    stub();
    const save = vi
      .spyOn(api, 'saveBrief')
      .mockResolvedValue({ digest: 'def', counts: makeSearchStatus().counts });
    renderWithQuery(<SearchPanel />);
    const editor = await screen.findByLabelText('Ranking prompt');
    await waitFor(() => expect(editor).toHaveValue('You are my career advisor.'));
    await userEvent.type(editor, ' Score hard.');
    await userEvent.click(screen.getByRole('button', { name: 'Save prompt' }));
    expect(save).toHaveBeenCalledWith('You are my career advisor. Score hard.');
    expect(await screen.findByText(/now marked ↻/)).toBeInTheDocument();
  });

  it('saves your filters, one word per line', async () => {
    stub();
    const save = vi.spyOn(api, 'saveFilters').mockResolvedValue({
      title: ['Senior', 'Stage'],
      company: [],
      location: [],
      excluded: 30,
      by_filter: {},
    });
    renderWithQuery(<SearchPanel />);
    const title = await screen.findByLabelText('Title must not contain');
    await waitFor(() => expect(title).toHaveValue('Senior'));
    expect(screen.getByText(/titolo: Senior 12/)).toBeInTheDocument();
    await userEvent.type(title, '{enter}Stage{enter}');
    await userEvent.click(screen.getByRole('button', { name: 'Save filters' }));
    expect(save).toHaveBeenCalledWith({ title: ['Senior', 'Stage'], company: [], location: [] });
    expect(await screen.findByText(/30 open postings are now excluded/)).toBeInTheDocument();
  });

  it('shows what was learned from stars and removals, and learns on request', async () => {
    const status = makeSearchStatus();
    status.calibration = {
      ...status.calibration,
      pending: 12,
      current: {
        version: 2,
        created_at: '2026-10-16T10:00:00+00:00',
        model: 'z-ai/glm-5.3-flash',
        cost: 0.004,
        feedback_total: 80,
        starred: 10,
        removed: 70,
        guidelines: ['Abbassa i ruoli GRC di 15 punti.'],
        notes: 'Meno GRC.',
        examples: [],
      },
      section: '## Calibration from my feedback',
    };
    stub(status);
    const calibrate = vi.spyOn(api, 'calibrate').mockResolvedValue({ started: true });
    renderWithQuery(<SearchPanel />);
    expect(await screen.findByText('Abbassa i ruoli GRC di 15 punti.')).toBeInTheDocument();
    expect(screen.getByText('12/50 since the last update')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Learn now' }));
    expect(calibrate).toHaveBeenCalled();
  });

  it('streams the console', async () => {
    stub();
    renderWithQuery(<SearchPanel />);
    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1));
    MockEventSource.instances[0].emit({ ts: '2026-10-05T10:00:00+00:00', level: 'success', message: '[1/2] Airbus OK' });
    expect(await screen.findByText('[1/2] Airbus OK')).toHaveClass('text-emerald-400');
  });
});
