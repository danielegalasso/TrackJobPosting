import { Bell, Building2, MapPin, RotateCcw, Search } from 'lucide-react';
import { useMemo } from 'react';
import { EMPTY_FILTERS, type Filters, type JobFacets } from '../types';
import { SearchSelect } from './SearchSelect';

interface FilterMatrixProps {
  filters: Filters;
  facets?: JobFacets;
  total: number;
  onChange: (patch: Partial<Filters>) => void;
  onReset: () => void;
  onOpenAlert: () => void;
}

const SORTS = [
  { value: 'priority', label: 'Priority — read first' },
  { value: 'deadline', label: 'Deadline — soonest' },
  { value: 'recent', label: 'Published — newest' },
];

/** Postings the rules keep from the model's review: a visa, a senior role, ... */
const EXCLUSIONS = [
  { value: '', label: 'Hide excluded' },
  { value: 'include', label: 'Show excluded too' },
  { value: 'only', label: 'Only excluded' },
];

/** Count the controls that are actually narrowing the result set. */
function activeCount(filters: Filters): number {
  const ignored = new Set(['sort', 'origin', 'saved_only', 'dismissed_only']);
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

  const countries = useMemo(
    () =>
      (facets?.countries ?? []).map((item) => ({
        value: item.code,
        label: item.name,
        hint: item.count.toLocaleString(),
      })),
    [facets],
  );
  const companies = useMemo(
    () => (facets?.companies ?? []).map((name) => ({ value: name, label: name })),
    [facets],
  );

  return (
    <section className="mx-auto w-full max-w-7xl px-4 pb-4 pt-6 sm:px-6" aria-label="Filters">
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

        <SearchSelect
          label="Where"
          placeholder="Where ... (Europe, or a country)"
          icon={MapPin}
          options={countries}
          value={filters.country}
          onChange={(country) => onChange({ country })}
        />

        <SearchSelect
          label="Company"
          placeholder="Company ..."
          icon={Building2}
          options={companies}
          value={filters.company}
          onChange={(company) => onChange({ company })}
        />
      </div>

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

        <div className="ml-auto flex flex-wrap items-center gap-3">
          <label className="sr-only" htmlFor="filter-excluded">
            Excluded postings
          </label>
          <select
            id="filter-excluded"
            value={filters.excluded}
            onChange={(event) => onChange({ excluded: event.target.value })}
            title="Postings the rules keep from review: a visa, a senior or sales role, an eligibility blocker"
            className="control w-44 pr-3"
          >
            {EXCLUSIONS.map((item) => (
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
            className="control w-48 pr-3"
          >
            {SORTS.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </select>
        </div>
      </div>
    </section>
  );
}
