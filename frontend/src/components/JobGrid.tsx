import { Loader2, SearchX } from 'lucide-react';
import type { Job } from '../types';
import { JobCard } from './JobCard';

interface JobGridProps {
  jobs: Job[];
  total: number;
  loading: boolean;
  loadingMore: boolean;
  hasMore: boolean;
  error?: string;
  emptyHint: string;
  onLoadMore: () => void;
  onToggleSave: (job: Job) => void;
  onDismiss: (job: Job) => void;
}

function Skeleton() {
  return (
    <div className="animate-pulse rounded-lg border border-gray-200 bg-white p-5">
      <div className="mb-3 h-4 w-3/4 rounded bg-gray-200" />
      <div className="mb-2 h-3 w-1/3 rounded bg-gray-100" />
      <div className="mb-4 h-3 w-1/2 rounded bg-gray-100" />
      <div className="flex gap-2 border-t border-gray-100 pt-3">
        <div className="h-4 w-16 rounded bg-gray-100" />
        <div className="h-4 w-16 rounded bg-gray-100" />
      </div>
    </div>
  );
}

export function JobGrid({
  jobs,
  total,
  loading,
  loadingMore,
  hasMore,
  error,
  emptyHint,
  onLoadMore,
  onToggleSave,
  onDismiss,
}: JobGridProps) {
  if (error) {
    return (
      <div className="rounded-lg border border-red-200 bg-red-50 p-6 text-sm text-red-800">
        <p className="font-semibold">Could not load positions</p>
        <p className="mt-1">{error}</p>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="grid grid-cols-1 gap-5 md:grid-cols-2 lg:grid-cols-3">
        {Array.from({ length: 6 }, (_, index) => (
          <Skeleton key={index} />
        ))}
      </div>
    );
  }

  if (jobs.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-gray-300 bg-gray-50 p-12 text-center">
        <SearchX className="mx-auto mb-3 h-8 w-8 text-gray-400" aria-hidden />
        <p className="text-sm font-medium text-gray-700">No positions match these filters</p>
        <p className="mx-auto mt-1 max-w-md text-xs text-gray-500">{emptyHint}</p>
      </div>
    );
  }

  return (
    <>
      <div className="grid grid-cols-1 gap-5 md:grid-cols-2 lg:grid-cols-3">
        {jobs.map((job) => (
          <JobCard key={job.id} job={job} onToggleSave={onToggleSave} onDismiss={onDismiss} />
        ))}
      </div>

      {hasMore && (
        <div className="mt-8 flex justify-center">
          <button
            onClick={onLoadMore}
            disabled={loadingMore}
            className="flex items-center gap-2 rounded border border-gray-300 px-5 py-2.5 text-sm font-medium text-gray-700 transition hover:bg-gray-50 disabled:opacity-60"
          >
            {loadingMore && <Loader2 className="h-4 w-4 animate-spin" aria-hidden />}
            <span>
              Load more · showing {jobs.length} of {total.toLocaleString()}
            </span>
          </button>
        </div>
      )}
    </>
  );
}
