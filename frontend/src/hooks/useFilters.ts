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
      return stored ? { ...EMPTY_FILTERS, ...JSON.parse(stored) } : EMPTY_FILTERS;
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
