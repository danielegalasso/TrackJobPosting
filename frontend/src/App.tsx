import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useMemo, useState } from 'react';
import { AlertModal } from './components/AlertModal';
import { FilterMatrix } from './components/FilterMatrix';
import { Footer } from './components/Footer';
import { Header, type View } from './components/Header';
import { InspectorConsole } from './components/InspectorConsole';
import { JobGrid } from './components/JobGrid';
import { LegalModal } from './components/LegalModal';
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

  // Typing in a text control should not fire a request per keystroke.
  const debouncedSearch = useDebounced(filters.search);
  const debouncedLocation = useDebounced(filters.location);
  const debouncedRemote = useDebounced(filters.remote);
  const debouncedCompany = useDebounced(filters.company);
  const debouncedAmount = useDebounced(filters.min_amount);

  const effective: Filters = useMemo(
    () => ({
      ...filters,
      search: debouncedSearch,
      location: debouncedLocation,
      remote: debouncedRemote,
      company: debouncedCompany,
      min_amount: debouncedAmount,
      saved_only: view === 'saved',
    }),
    [
      filters,
      debouncedSearch,
      debouncedLocation,
      debouncedRemote,
      debouncedCompany,
      debouncedAmount,
      view,
    ],
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

  const { data: facets } = useQuery({ queryKey: ['facets'], queryFn: api.facets });
  const { data: status } = useQuery({
    queryKey: ['spider-status'],
    queryFn: api.spiderStatus,
    refetchInterval: 15000,
  });

  // The nav badge reflects every bookmark, not just the ones on screen.
  const { data: savedPage } = useQuery({
    queryKey: ['saved-count'],
    queryFn: () => api.jobs({ ...EMPTY_FILTERS, saved_only: true }, 0, 1),
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
      queryClient.setQueryData(jobsKey, (current: typeof previous) =>
        mapPages(current, (items) => items.filter((item) => item.id !== job.id)),
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
      ? 'Bookmark a role from the Search tab and it will appear here.'
      : (status?.targets.length ?? 0) === 0
        ? 'No career feeds are configured yet. Add one under Settings, then run the inspector.'
        : 'Try widening the filters, or run the inspector to index newly published roles.';

  return (
    <div className="flex min-h-screen flex-col bg-white">
      <Header
        view={view}
        onNavigate={setView}
        savedCount={savedCount}
        spiderRunning={Boolean(status?.running)}
      />

      {(view === 'search' || view === 'saved') && (
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
            />
          </main>
        </>
      )}

      {view === 'console' && (
        <main className="flex-1 pt-6">
          <InspectorConsole />
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
