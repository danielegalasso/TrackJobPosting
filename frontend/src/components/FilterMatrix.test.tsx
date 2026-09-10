/** Every control in the matrix must be wired, labelled and clearable. */

import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { EMPTY_FILTERS, type Filters, type JobFacets } from '../types';
import { FilterMatrix } from './FilterMatrix';

const facets: JobFacets = {
  categories: ['Cloud Security', 'Embedded / Systems'],
  companies: ['Acme', 'Globex'],
  seniorities: ['Junior', 'Senior'],
  currencies: ['USD', 'EUR'],
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
  it('gives every control an accessible name', () => {
    setup();
    for (const name of [
      /Search roles/,
      /^Location/,
      /Remote from/,
      /^Category/,
      /Years of experience/,
      /^Seniority/,
      /Date posted/,
      /^Company/,
      /^Rate/,
      /^Currency/,
      /Minimum amount/,
      /Match posting currency/,
      /Match type/,
      /Sort by/,
    ]) {
      expect(screen.getByLabelText(name)).toBeInTheDocument();
    }
  });

  it('reports the result count', () => {
    setup();
    expect(screen.getByText('42 open positions')).toBeInTheDocument();
  });

  it('uses the singular for one result', () => {
    render(
      <FilterMatrix
        filters={EMPTY_FILTERS}
        facets={facets}
        total={1}
        onChange={vi.fn()}
        onReset={vi.fn()}
        onOpenAlert={vi.fn()}
      />,
    );
    expect(screen.getByText('1 open position')).toBeInTheDocument();
  });

  it('emits a patch as the user types', async () => {
    const { onChange } = setup();
    await userEvent.type(screen.getByLabelText(/Search roles/), 'a');
    expect(onChange).toHaveBeenCalledWith({ search: 'a' });
  });

  it('populates the category options from live facets', () => {
    setup();
    const options = Array.from(
      screen.getByLabelText(/^Category/).querySelectorAll('option'),
    ).map((option) => option.textContent);
    expect(options).toEqual(['Categories ..', 'Cloud Security', 'Embedded / Systems']);
  });

  it('offers the full seniority ladder before any job is indexed', () => {
    render(
      <FilterMatrix
        filters={EMPTY_FILTERS}
        facets={{ categories: [], companies: [], seniorities: [], currencies: [] }}
        total={0}
        onChange={vi.fn()}
        onReset={vi.fn()}
        onOpenAlert={vi.fn()}
      />,
    );
    const options = Array.from(
      screen.getByLabelText(/^Seniority/).querySelectorAll('option'),
    ).map((option) => option.getAttribute('value'));
    expect(options).toContain('Staff');
    expect(options).toContain('Director');
  });

  it('emits the chosen category', async () => {
    const { onChange } = setup();
    await userEvent.selectOptions(screen.getByLabelText(/^Category/), 'Cloud Security');
    expect(onChange).toHaveBeenCalledWith({ category: 'Cloud Security' });
  });

  it('toggles match posting currency', async () => {
    const { onChange } = setup();
    await userEvent.click(screen.getByLabelText(/Match posting currency/));
    expect(onChange).toHaveBeenCalledWith({ match_posting_currency: true });
  });

  it('hides the clear button until something is filtered', () => {
    setup();
    expect(screen.queryByRole('button', { name: /Clear/ })).not.toBeInTheDocument();
  });

  it('counts only the controls that narrow the results', async () => {
    // Rate, currency and sort always hold a value; counting them would show
    // "Clear 3 filters" on an untouched page.
    setup({ rate: 'Monthly', currency: 'EUR', sort: 'recent' });
    expect(screen.queryByRole('button', { name: /Clear/ })).not.toBeInTheDocument();

    const { onReset } = setup({ search: 'embedded', seniority: 'Senior' });
    const clear = screen.getByRole('button', { name: /Clear 2 filters/ });
    await userEvent.click(clear);
    expect(onReset).toHaveBeenCalledTimes(1);
  });

  it('uses the singular for a single active filter', () => {
    setup({ search: 'embedded' });
    expect(screen.getByRole('button', { name: /Clear 1 filter$/ })).toBeInTheDocument();
  });

  it('opens the alert dialog from the primary call to action', async () => {
    const { onOpenAlert } = setup();
    await userEvent.click(screen.getByRole('button', { name: 'Alert' }));
    expect(onOpenAlert).toHaveBeenCalledTimes(1);
  });

  it('offers company suggestions without forcing a choice', async () => {
    const { onChange } = setup();
    const company = screen.getByLabelText(/^Company/);
    expect(company).toHaveAttribute('list', 'company-options');
    // A free-text value not present in the facets is still accepted.
    await userEvent.type(company, 'N');
    expect(onChange).toHaveBeenCalledWith({ company: 'N' });
  });
});
