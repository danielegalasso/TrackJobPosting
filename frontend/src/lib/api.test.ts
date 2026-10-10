/** Query serialisation: the contract between the filter matrix and the API. */

import { describe, expect, it } from 'vitest';
import { EMPTY_FILTERS, type Filters } from '../types';
import { toQuery } from './api';

const parse = (filters: Partial<Filters>, offset = 0, limit = 60) =>
  Object.fromEntries(new URLSearchParams(toQuery({ ...EMPTY_FILTERS, ...filters }, offset, limit)));

describe('toQuery', () => {
  it('sends only paging when nothing is filtered', () => {
    expect(parse({})).toEqual({ sort: 'priority', origin: 'extract', limit: '60', offset: '0' });
  });

  it('asks for every source when no origin is chosen', () => {
    expect(parse({ origin: '' })).not.toHaveProperty('origin');
  });

  it('asks for removed postings only in the Removed view', () => {
    expect(parse({})).not.toHaveProperty('dismissed_only');
    expect(parse({ dismissed_only: true })).toMatchObject({ dismissed_only: 'true' });
  });

  it('omits empty controls rather than sending blanks', () => {
    const query = parse({ search: 'embedded', country: '', company: '' });
    expect(query.search).toBe('embedded');
    expect(query).not.toHaveProperty('country');
    expect(query).not.toHaveProperty('company');
  });

  it('carries the search, the country and the company', () => {
    expect(parse({ search: 'security', country: 'EUROPE', company: 'Thales', excluded: 'include' })).toMatchObject({
      search: 'security',
      country: 'EUROPE',
      company: 'Thales',
      excluded: 'include',
    });
  });

  it('sends saved_only only when true', () => {
    expect(parse({ saved_only: false })).not.toHaveProperty('saved_only');
    expect(parse({ saved_only: true }).saved_only).toBe('true');
  });

  it('passes paging through', () => {
    expect(parse({}, 120, 30)).toMatchObject({ offset: '120', limit: '30' });
  });

  it('escapes values that would otherwise break the query string', () => {
    const query = parse({ company: 'Thales Alenia Space & Co', search: 'C++ & Rust' });
    expect(query.company).toBe('Thales Alenia Space & Co');
    expect(query.search).toBe('C++ & Rust');
  });
});
