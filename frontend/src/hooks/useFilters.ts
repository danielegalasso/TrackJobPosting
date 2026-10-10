import { useCallback, useEffect, useState } from 'react';
import { EMPTY_FILTERS, type Filters } from '../types';

const STORAGE_KEY = 'acide-watch:filters';

/**
 * Filter state, persisted per browser so a reload does not throw away a
 * carefully built query. Storage can throw in a private window, so every
 * access is guarded.
 */
export function useFilters() {
  const [filters, setFilters] = useState<Filters>(() => {
    try {
      const stored = window.localStorage.getItem(STORAGE_KEY);
      if (!stored) return EMPTY_FILTERS;
      // Keep only the filters that still exist: one saved by an older version
      // (a min amount, a seniority) would narrow the grid with no control
      // left to clear it.
      const saved = JSON.parse(stored) as Record<string, unknown>;
      const known = Object.fromEntries(
        Object.entries(saved).filter(([key]) => key in EMPTY_FILTERS),
      ) as Partial<Filters>;
      // The old crawler's postings are gone: a stored "crawler" source would
      // show an empty grid.
      return { ...EMPTY_FILTERS, ...known, origin: 'extract' };
    } catch {
      return EMPTY_FILTERS;
    }
  });

  useEffect(() => {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(filters));
    } catch {
      /* private mode or blocked storage — the filters just will not persist */
    }
  }, [filters]);

  const update = useCallback((patch: Partial<Filters>) => {
    setFilters((current) => ({ ...current, ...patch }));
  }, []);

  const reset = useCallback(() => setFilters(EMPTY_FILTERS), []);

  return { filters, update, reset };
}

/** Delay a fast-changing value so typing does not fire a request per keystroke. */
export function useDebounced<T>(value: T, delay = 300): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delay);
    return () => window.clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}
