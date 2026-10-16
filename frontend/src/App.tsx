import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useMemo, useState } from 'react';
import { AlertModal } from './components/AlertModal';
import { FilterMatrix } from './components/FilterMatrix';
import { Footer } from './components/Footer';
import { Header, type View } from './components/Header';
import { JobGrid } from './components/JobGrid';
import { LegalModal } from './components/LegalModal';
import { SearchPanel } from './components/SearchPanel';
import { SettingsPanel } from './components/SettingsPanel';
import { useDebounced, useFilters } from './hooks/useFilters';
import { api } from './lib/api';
import { EMPTY_FILTERS, type Filters, type Job, type JobPage } from './types';

const PAGE_SIZE = 60;

/** Apply a change to every page currently held for this filter set. */
function mapPages(
  data: { pages: JobPage[]; pageParams: unknown[] } | undefined,
  transform: (items: Job[]) => Job[],
) {
  if (!data) return data;
  return {
    ...data,
    pages: data.pages.map((page) => ({ ...page, items: transform(page.items) })),
  };
}

export default function App() {
  const queryClient = useQueryClient();
  const { filters, update, reset } = useFilters();
  const [view, setView] = useState<View>('search');
  const [alertOpen, setAlertOpen] = useState(false);
  const [legal, setLegal] = useState<'privacy' | 'terms' | null>(null);

  // Typing in the search box should not fire a request per keystroke; the
  // other controls pick from a list, so they apply at once.
  const debouncedSearch = useDebounced(filters.search);

  const effective: Filters = useMemo(
    () => ({
      ...filters,
      search: debouncedSearch,
      saved_only: view === 'saved',
      dismissed_only: view === 'removed',
    }),
    [filters, debouncedSearch, view],
  );

  // An infinite query keeps every loaded page in the cache under the filter
  // key, so returning to a previous filter set restores the whole list rather
  // than replaying it from a side effect.
  const jobsKey = useMemo(() => ['jobs', effective] as const, [effective]);
  const {
    data,
    isLoading,
    isFetchingNextPage,
    hasNextPage,
    fetchNextPage,
    error,
  } = useInfiniteQuery({
    queryKey: jobsKey,
    queryFn: ({ pageParam }) => api.jobs(effective, pageParam, PAGE_SIZE),
    initialPageParam: 0,
    getNextPageParam: (lastPage) =>
      lastPage.has_more ? lastPage.offset + lastPage.limit : undefined,
  });

  const jobs = useMemo(() => data?.pages.flatMap((page) => page.items) ?? [], [data]);
  const total = data?.pages[0]?.total ?? 0;

  const { data: facets } = useQuery({
    queryKey: ['facets', filters.origin],
    queryFn: () => api.facets(filters.origin),
  });
  const { data: status } = useQuery({
    queryKey: ['search-status'],
    queryFn: api.searchStatus,
    refetchInterval: 15000,
  });

  // The nav badge reflects every bookmark, not just the ones on screen.
  const { data: savedPage } = useQuery({
    queryKey: ['saved-count'],
    queryFn: () => api.jobs({ ...EMPTY_FILTERS, origin: '', saved_only: true }, 0, 1),
  });

  const refreshLists = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ['jobs'] });
    queryClient.invalidateQueries({ queryKey: ['saved-count'] });
  }, [queryClient]);

  const bookmark = useMutation({
    mutationFn: (job: Job) => api.bookmark(job.id),
    // Optimistic: a bookmark should feel instant. The list re-syncs after.
    onMutate: async (job) => {
      await queryClient.cancelQueries({ queryKey: jobsKey });
      const previous = queryClient.getQueryData<{ pages: JobPage[]; pageParams: unknown[] }>(jobsKey);
      queryClient.setQueryData(jobsKey, (current: typeof previous) =>
        mapPages(current, (items) =>
          items.map((item) => (item.id === job.id ? { ...item, saved: !item.saved } : item)),
        ),
      );
      return { previous };
    },
    onError: (_error, _job, context) => {
      if (context?.previous) queryClient.setQueryData(jobsKey, context.previous);
    },
    onSettled: refreshLists,
  });

  const dismiss = useMutation({
    mutationFn: (job: Job) => api.dismiss(job.id),
    onMutate: async (job) => {
      await queryClient.cancelQueries({ queryKey: jobsKey });
      const previous = queryClient.getQueryData<{ pages: JobPage[]; pageParams: unknown[] }>(jobsKey);
      // Starred keeps a removed posting, so there it only changes state;
      // elsewhere removing or restoring moves it out of the list.
      queryClient.setQueryData(jobsKey, (current: typeof previous) =>
        mapPages(current, (items) =>
          view === 'saved'
            ? items.map((item) =>
                item.id === job.id ? { ...item, dismissed: !item.dismissed } : item,
              )
            : items.filter((item) => item.id !== job.id),
        ),
      );
      return { previous };
    },
    onError: (_error, _job, context) => {
      if (context?.previous) queryClient.setQueryData(jobsKey, context.previous);
    },
    onSettled: refreshLists,
  });

  const savedCount = savedPage?.total ?? 0;

  const emptyHint =
    view === 'saved'
      ? 'Star a role from the Search tab and it will appear here.'
      : view === 'removed'
        ? 'Postings you remove with ✕ land here, where they can be put back.'
        : 'Try widening the filters, or open Run the Search to read the career sites again.';

  return (
    <div className="flex min-h-screen flex-col bg-white">
      <Header
        view={view}
        onNavigate={setView}
        savedCount={savedCount}
        searchRunning={Boolean(status?.search.running || status?.review.running)}
      />

      {(view === 'search' || view === 'saved' || view === 'removed') && (
        <>
          <FilterMatrix
            filters={filters}
            facets={facets}
            total={total}
            onChange={update}
            onReset={reset}
            onOpenAlert={() => setAlertOpen(true)}
          />
          <main className="mx-auto w-full max-w-7xl flex-1 px-4 pb-16 sm:px-6">
            <JobGrid
              jobs={jobs}
              total={total}
              loading={isLoading}
              loadingMore={isFetchingNextPage}
              hasMore={Boolean(hasNextPage)}
              error={error instanceof Error ? error.message : undefined}
              emptyHint={emptyHint}
              onLoadMore={() => void fetchNextPage()}
              onToggleSave={(job) => bookmark.mutate(job)}
              onDismiss={(job) => dismiss.mutate(job)}
              removedView={view === 'removed'}
            />
          </main>
        </>
      )}

      {view === 'console' && (
        <main className="flex-1 pt-6">
          <SearchPanel />
        </main>
      )}

      {view === 'settings' && (
        <main className="flex-1">
          <SettingsPanel />
        </main>
      )}

      <Footer onOpenLegal={setLegal} />

      {alertOpen && <AlertModal filters={filters} onClose={() => setAlertOpen(false)} />}
      {legal && <LegalModal document={legal} onClose={() => setLegal(null)} />}
    </div>
  );
}
