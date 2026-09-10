import {
  Bell,
  Briefcase,
  Building2,
  Calendar,
  MapPin,
  RotateCcw,
  Search,
  Tag,
} from 'lucide-react';
import { EMPTY_FILTERS, type Filters, type JobFacets } from '../types';

interface FilterMatrixProps {
  filters: Filters;
  facets?: JobFacets;
  total: number;
  onChange: (patch: Partial<Filters>) => void;
  onReset: () => void;
  onOpenAlert: () => void;
}

const YEARS = [
  { value: '0-2', label: '0 - 2 years' },
  { value: '3-5', label: '3 - 5 years' },
  { value: '6-9', label: '6 - 9 years' },
  { value: '10+', label: '10+ years' },
];

const WHEN = [
  { value: 'today', label: 'Past 24 hours' },
  { value: '3days', label: 'Past 3 days' },
  { value: 'week', label: 'Past week' },
  { value: 'month', label: 'Past month' },
];

const RATES = ['Hourly', 'Daily', 'Monthly', 'Yearly'];
const CURRENCIES = ['USD', 'EUR', 'GBP', 'CHF', 'CAD', 'AUD', 'SEK', 'PLN', 'INR', 'JPY', 'SGD'];

const SORTS = [
  { value: 'fit', label: 'Best fit' },
  { value: 'recent', label: 'Most recent' },
  { value: 'experience', label: 'Experience fit' },
  { value: 'interest', label: 'Pivot fit' },
  { value: 'compensation', label: 'Compensation' },
];

const MATCH_TYPES = [
  { value: '', label: 'All matches' },
  { value: 'Direct Match', label: 'Direct matches' },
  { value: 'Pivot / Growth Opportunity', label: 'Pivot opportunities' },
];

/** Count the controls that are actually narrowing the result set. */
function activeCount(filters: Filters): number {
  const ignored = new Set(['rate', 'currency', 'sort', 'match_posting_currency', 'saved_only']);
  return Object.entries(filters).filter(([key, value]) => {
    if (ignored.has(key)) return false;
    return value !== '' && value !== false && value !== EMPTY_FILTERS[key as keyof Filters];
  }).length;
}

export function FilterMatrix({
  filters,
  facets,
  total,
  onChange,
  onReset,
  onOpenAlert,
}: FilterMatrixProps) {
  const active = activeCount(filters);

  return (
    <section className="mx-auto w-full max-w-7xl px-4 pb-4 pt-6 sm:px-6" aria-label="Filters">
      {/* Row 1 — what, where, remote from */}
      <div className="mb-3 grid grid-cols-1 gap-3 md:grid-cols-3">
        <div className="relative">
          <label className="sr-only" htmlFor="filter-search">
            Search roles
          </label>
          <input
            id="filter-search"
            type="search"
            placeholder="What ..."
            value={filters.search}
            onChange={(event) => onChange({ search: event.target.value })}
            className="control"
          />
          <Search className="control-icon" aria-hidden />
        </div>

        <div className="relative">
          <label className="sr-only" htmlFor="filter-location">
            Location
          </label>
          <input
            id="filter-location"
            type="text"
            placeholder="Where ..."
            value={filters.location}
            onChange={(event) => onChange({ location: event.target.value })}
            className="control"
          />
          <MapPin className="control-icon" aria-hidden />
        </div>

        <div className="relative">
          <label className="sr-only" htmlFor="filter-remote">
            Remote from
          </label>
          <input
            id="filter-remote"
            type="text"
            placeholder="Remote from ..."
            value={filters.remote}
            onChange={(event) => onChange({ remote: event.target.value })}
            className="control"
          />
          <MapPin className="control-icon" aria-hidden />
        </div>
      </div>

      {/* Row 2 — category, experience, seniority, when, company */}
      <div className="mb-3 grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-5">
        <div className="relative">
          <label className="sr-only" htmlFor="filter-category">
            Category
          </label>
          <select
            id="filter-category"
            value={filters.category}
            onChange={(event) => onChange({ category: event.target.value })}
            className="control appearance-none"
          >
            <option value="">Categories ..</option>
            {facets?.categories.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
          <Tag className="control-icon" aria-hidden />
        </div>

        <div className="relative">
          <label className="sr-only" htmlFor="filter-years">
            Years of experience
          </label>
          <select
            id="filter-years"
            value={filters.years_experience}
            onChange={(event) => onChange({ years_experience: event.target.value })}
            className="control appearance-none"
          >
            <option value="">Years of experience ..</option>
            {YEARS.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </select>
          <Briefcase className="control-icon" aria-hidden />
        </div>

        <div className="relative">
          <label className="sr-only" htmlFor="filter-seniority">
            Seniority
          </label>
          <select
            id="filter-seniority"
            value={filters.seniority}
            onChange={(event) => onChange({ seniority: event.target.value })}
            className="control appearance-none"
          >
            <option value="">Seniority ..</option>
            {(facets?.seniorities.length
              ? facets.seniorities
              : ['Intern', 'Junior', 'Mid-Level', 'Senior', 'Staff', 'Manager', 'Director']
            ).map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
          <Briefcase className="control-icon" aria-hidden />
        </div>

        <div className="relative">
          <label className="sr-only" htmlFor="filter-when">
            Date posted
          </label>
          <select
            id="filter-when"
            value={filters.posted_within}
            onChange={(event) => onChange({ posted_within: event.target.value })}
            className="control appearance-none"
          >
            <option value="">When ...</option>
            {WHEN.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </select>
          <Calendar className="control-icon" aria-hidden />
        </div>

        <div className="relative">
          <label className="sr-only" htmlFor="filter-company">
            Company
          </label>
          <input
            id="filter-company"
            list="company-options"
            type="text"
            placeholder="Company ..."
            value={filters.company}
            onChange={(event) => onChange({ company: event.target.value })}
            className="control"
          />
          <datalist id="company-options">
            {facets?.companies.map((item) => (
              <option key={item} value={item} />
            ))}
          </datalist>
          <Building2 className="control-icon" aria-hidden />
        </div>
      </div>

      {/* Row 3 — compensation */}
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <label className="sr-only" htmlFor="filter-rate">
          Rate
        </label>
        <select
          id="filter-rate"
          value={filters.rate}
          onChange={(event) => onChange({ rate: event.target.value })}
          className="control w-32 pr-3"
        >
          {RATES.map((item) => (
            <option key={item} value={item}>
              {item}
            </option>
          ))}
        </select>

        <label className="sr-only" htmlFor="filter-currency">
          Currency
        </label>
        <select
          id="filter-currency"
          value={filters.currency}
          onChange={(event) => onChange({ currency: event.target.value })}
          className="control w-28 pr-3"
        >
          {CURRENCIES.map((item) => (
            <option key={item} value={item}>
              {item}
            </option>
          ))}
        </select>

        <label className="sr-only" htmlFor="filter-amount">
          Minimum amount
        </label>
        <input
          id="filter-amount"
          type="number"
          min={0}
          step={1000}
          placeholder="Amount"
          value={filters.min_amount}
          onChange={(event) => onChange({ min_amount: event.target.value })}
          className="control w-36 pr-3"
        />

        <label className="flex cursor-pointer select-none items-center gap-2 text-xs text-gray-600">
          <input
            type="checkbox"
            checked={filters.match_posting_currency}
            onChange={(event) => onChange({ match_posting_currency: event.target.checked })}
            className="rounded border-gray-300 text-brand focus:ring-brand"
          />
          <span>Match posting currency</span>
        </label>

        <div className="ml-auto flex items-center gap-3">
          <label className="sr-only" htmlFor="filter-type">
            Match type
          </label>
          <select
            id="filter-type"
            value={filters.category_type}
            onChange={(event) => onChange({ category_type: event.target.value })}
            className="control w-44 pr-3"
          >
            {MATCH_TYPES.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </select>

          <label className="sr-only" htmlFor="filter-sort">
            Sort by
          </label>
          <select
            id="filter-sort"
            value={filters.sort}
            onChange={(event) => onChange({ sort: event.target.value })}
            className="control w-40 pr-3"
          >
            {SORTS.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Action bar */}
      <div className="mb-6 flex flex-wrap items-center gap-3">
        <button
          onClick={onOpenAlert}
          className="flex items-center gap-1.5 rounded bg-alert px-4 py-2 text-xs font-semibold text-black shadow-sm transition hover:bg-amber-600"
        >
          <span>Alert</span>
          <Bell className="h-3.5 w-3.5" aria-hidden />
        </button>

        {active > 0 && (
          <button
            onClick={onReset}
            className="flex items-center gap-1.5 rounded border border-gray-300 px-3 py-2 text-xs font-medium text-gray-600 transition hover:bg-gray-50"
          >
            <RotateCcw className="h-3.5 w-3.5" aria-hidden />
            <span>
              Clear {active} filter{active === 1 ? '' : 's'}
            </span>
          </button>
        )}

        <span className="text-xs text-gray-500" aria-live="polite">
          {total.toLocaleString()} open position{total === 1 ? '' : 's'}
        </span>
      </div>
    </section>
  );
}
