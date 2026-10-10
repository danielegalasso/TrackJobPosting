/** Every control in the matrix must be wired, labelled and clearable. */

import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { EMPTY_FILTERS, type Filters, type JobFacets } from '../types';
import { FilterMatrix } from './FilterMatrix';

const facets: JobFacets = {
  categories: [],
  companies: ['Airbus', 'Leonardo', 'Thales'],
  seniorities: [],
  currencies: [],
  countries: [
    { code: 'EUROPE', name: 'Europe (all countries)', count: 900 },
    { code: 'FR', name: 'France', count: 400 },
    { code: 'IT', name: 'Italy', count: 300 },
    { code: 'TR', name: 'Türkiye', count: 4 },
  ],
};

function setup(filters: Partial<Filters> = {}) {
  const onChange = vi.fn();
  const onReset = vi.fn();
  const onOpenAlert = vi.fn();
  render(
    <FilterMatrix
      filters={{ ...EMPTY_FILTERS, ...filters }}
      facets={facets}
      total={42}
      onChange={onChange}
      onReset={onReset}
      onOpenAlert={onOpenAlert}
    />,
  );
  return { onChange, onReset, onOpenAlert };
}

describe('FilterMatrix', () => {
  it('has only the search, where, company, excluded and sort controls', () => {
    setup();
    for (const name of [/Search roles/, /^Where/, /^Company/, /Excluded postings/, /Sort by/]) {
      expect(screen.getByLabelText(name)).toBeInTheDocument();
    }
    for (const gone of [/Remote from/, /^Category/, /Seniority/, /Date posted/, /Minimum amount/, /Match type/]) {
      expect(screen.queryByLabelText(gone)).not.toBeInTheDocument();
    }
  });

  it('sorts by priority, deadline or publication only', () => {
    setup();
    const options = within(screen.getByLabelText('Sort by')).getAllByRole('option');
    expect(options.map((option) => option.getAttribute('value'))).toEqual(['priority', 'deadline', 'recent']);
  });

  it('picks Europe or a single country, searching as you type', async () => {
    const { onChange } = setup();
    const where = screen.getByLabelText('Where');
    await userEvent.click(where);
    const list = screen.getByRole('listbox', { name: 'Where' });
    expect(within(list).getAllByRole('option')[0]).toHaveTextContent('Europe (all countries)');

    await userEvent.type(where, 'turk'); // accents do not matter
    expect(within(list).getAllByRole('option').map((o) => o.textContent)).toEqual(['Türkiye4']);
    await userEvent.keyboard('{Enter}');
    expect(onChange).toHaveBeenCalledWith({ country: 'TR' });
  });

  it('shows the chosen country and clears it', async () => {
    const { onChange } = setup({ country: 'IT' });
    expect(screen.getByLabelText('Where')).toHaveValue('Italy');
    await userEvent.click(screen.getByRole('button', { name: 'Clear where' }));
    expect(onChange).toHaveBeenCalledWith({ country: '' });
  });

  it('lists every company to choose from', async () => {
    const { onChange } = setup();
    await userEvent.click(screen.getByLabelText('Company'));
    const list = screen.getByRole('listbox', { name: 'Company' });
    expect(within(list).getAllByRole('option')).toHaveLength(3);
    await userEvent.click(within(list).getByText('Leonardo'));
    expect(onChange).toHaveBeenCalledWith({ company: 'Leonardo' });
  });

  it('reports the result count', () => {
    setup();
    expect(screen.getByText('42 open positions')).toBeInTheDocument();
  });

  it('counts only the filters that narrow the result', async () => {
    setup({ sort: 'recent' });
    expect(screen.queryByRole('button', { name: /Clear/ })).not.toBeInTheDocument();
  });

  it('clears the active filters', async () => {
    const { onReset } = setup({ search: 'embedded', country: 'EUROPE' });
    await userEvent.click(screen.getByRole('button', { name: 'Clear 2 filters' }));
    expect(onReset).toHaveBeenCalled();
  });

  it('opens the alert dialog from the primary call to action', async () => {
    const { onOpenAlert } = setup();
    await userEvent.click(screen.getByRole('button', { name: 'Alert' }));
    expect(onOpenAlert).toHaveBeenCalledTimes(1);
  });
});
