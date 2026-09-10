/** Query serialisation: the contract between the filter matrix and the API. */

import { describe, expect, it } from 'vitest';
import { EMPTY_FILTERS, type Filters } from '../types';
import { toQuery } from './api';

const parse = (filters: Partial<Filters>, offset = 0, limit = 60) =>
  Object.fromEntries(new URLSearchParams(toQuery({ ...EMPTY_FILTERS, ...filters }, offset, limit)));

describe('toQuery', () => {
  it('sends only paging when nothing is filtered', () => {
    expect(parse({})).toEqual({ sort: 'fit', limit: '60', offset: '0' });
  });

  it('omits empty controls rather than sending blanks', () => {
    const query = parse({ search: 'embedded', location: '', company: '' });
    expect(query.search).toBe('embedded');
    expect(query).not.toHaveProperty('location');
    expect(query).not.toHaveProperty('company');
  });

  it('carries every text and select control', () => {
    expect(
      parse({
        search: 'security',
        location: 'Rome',
        remote: 'Italy',
        category: 'Cloud Security',
        seniority: 'Senior',
        company: 'Globex',
        years_experience: '6-9',
        posted_within: 'week',
        category_type: 'Pivot / Growth Opportunity',
        sort: 'interest',
      }),
    ).toMatchObject({
      search: 'security',
      location: 'Rome',
      remote: 'Italy',
      category: 'Cloud Security',
      seniority: 'Senior',
      company: 'Globex',
      years_experience: '6-9',
      posted_within: 'week',
      category_type: 'Pivot / Growth Opportunity',
      sort: 'interest',
    });
  });

  it('sends rate and currency only alongside an amount', () => {
    // Rate and currency default to Yearly/USD, but on their own they are not
    // a filter — sending them would look like a compensation constraint.
    expect(parse({ rate: 'Monthly', currency: 'EUR' })).not.toHaveProperty('rate');

    const withAmount = parse({ rate: 'Monthly', currency: 'EUR', min_amount: '9000' });
    expect(withAmount).toMatchObject({ min_amount: '9000', rate: 'Monthly', currency: 'EUR' });
  });

  it('sends match_posting_currency only when an amount is set', () => {
    expect(parse({ match_posting_currency: true })).not.toHaveProperty('match_posting_currency');
    expect(parse({ match_posting_currency: true, min_amount: '150000' })).toMatchObject({
      match_posting_currency: 'true',
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
    const query = parse({ category_type: 'Pivot / Growth Opportunity', search: 'C++ & Rust' });
    expect(query.category_type).toBe('Pivot / Growth Opportunity');
    expect(query.search).toBe('C++ & Rust');
  });
});
