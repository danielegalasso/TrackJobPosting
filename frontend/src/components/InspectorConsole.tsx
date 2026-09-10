import { useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, Play, RefreshCw } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { api } from '../lib/api';
import type { LogLine } from '../types';

const LEVEL_STYLES: Record<LogLine['level'], string> = {
  info: 'text-gray-300',
  success: 'text-emerald-400',
  warn: 'text-amber-400',
  error: 'text-red-400',
};

const MAX_LINES = 500;

/** Live view of the inspector: status, controls and the streaming log. */
export function InspectorConsole() {
  const queryClient = useQueryClient();
  const [lines, setLines] = useState<LogLine[]>([]);
  const [streaming, setStreaming] = useState(false);
  const scroller = useRef<HTMLDivElement>(null);

  const { data: status, refetch } = useQuery({
    queryKey: ['spider-status'],
    queryFn: api.spiderStatus,
    refetchInterval: 5000,
  });

  useEffect(() => {
    // Server-sent events carry the inspector's console output. EventSource
    // reconnects on its own, so no retry loop is needed here.
    const source = new EventSource('/api/spider/logs');
    source.onopen = () => setStreaming(true);
    source.onerror = () => setStreaming(false);
    source.onmessage = (event) => {
      try {
        const line = JSON.parse(event.data) as LogLine;
        setLines((previous) => [...previous, line].slice(-MAX_LINES));
      } catch {
        /* ignore a malformed frame rather than break the stream */
      }
    };
    return () => source.close();
  }, []);

  useEffect(() => {
    const element = scroller.current;
    if (!element) return;
    // Only follow the tail if the reader has not scrolled up to read history.
    const atBottom = element.scrollHeight - element.scrollTop - element.clientHeight < 80;
    if (atBottom) element.scrollTop = element.scrollHeight;
  }, [lines]);

  const run = async () => {
    await api.runSpider();
    await refetch();
    // New postings land in the grid, so its cache is now stale.
    queryClient.invalidateQueries({ queryKey: ['jobs'] });
  };

  const lastRun = status?.last_run;

  return (
    <section className="mx-auto w-full max-w-7xl px-4 pb-16 sm:px-6">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-bold text-gray-900">Job inspector</h2>
          <p className="text-xs text-gray-500">
            Polls each configured career feed, scores what is new, then sends any alert digests.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => refetch()}
            className="flex items-center gap-1.5 rounded border border-gray-300 px-3 py-2 text-xs font-medium text-gray-600 transition hover:bg-gray-50"
          >
            <RefreshCw className="h-3.5 w-3.5" aria-hidden />
            <span>Refresh</span>
          </button>
          <button
            onClick={run}
            disabled={status?.running}
            className="flex items-center gap-1.5 rounded bg-brand px-4 py-2 text-xs font-semibold text-white transition hover:bg-brand-dark disabled:opacity-60"
          >
            <Play className="h-3.5 w-3.5" aria-hidden />
            <span>{status?.running ? 'Running…' : 'Run now'}</span>
          </button>
        </div>
      </div>

      <dl className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
        {[
          ['Sources polled', lastRun?.sources_polled ?? 0],
          ['Postings seen', lastRun?.postings_seen ?? 0],
          ['Newly scored', lastRun?.postings_scored ?? 0],
          ['Digests sent', lastRun?.alerts_sent ?? 0],
        ].map(([label, value]) => (
          <div key={String(label)} className="rounded-lg border border-gray-200 bg-white p-3">
            <dt className="text-[11px] uppercase tracking-wide text-gray-500">{label}</dt>
            <dd className="text-lg font-semibold text-gray-900">{value}</dd>
          </div>
        ))}
      </dl>

      <div className="mb-4 flex flex-wrap items-center gap-x-6 gap-y-1 text-xs text-gray-500">
        <span>
          Scheduled runs:{' '}
          <strong className="font-semibold text-gray-700">
            {status?.spider_enabled ? `every ${status.interval_minutes} min` : 'disabled'}
          </strong>
        </span>
        {status?.next_run_at && (
          <span>Next: {new Date(status.next_run_at).toLocaleString()}</span>
        )}
        <span>Targets configured: {status?.targets.length ?? 0}</span>
        <span className={streaming ? 'text-emerald-600' : 'text-gray-400'}>
          {streaming ? '● live' : '○ reconnecting'}
        </span>
      </div>

      {lastRun?.errors && lastRun.errors.length > 0 && (
        <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 p-4">
          <p className="mb-1 flex items-center gap-2 text-xs font-semibold text-amber-800">
            <AlertTriangle className="h-4 w-4" aria-hidden />
            {lastRun.errors.length} issue{lastRun.errors.length === 1 ? '' : 's'} in the last run
          </p>
          <ul className="ml-6 list-disc space-y-0.5 text-xs text-amber-700">
            {lastRun.errors.slice(0, 8).map((message, index) => (
              <li key={index}>{message}</li>
            ))}
          </ul>
        </div>
      )}

      <div
        ref={scroller}
        role="log"
        aria-label="Inspector output"
        className="h-96 overflow-y-auto rounded-lg bg-gray-900 p-4 font-mono text-xs leading-relaxed"
      >
        {lines.length === 0 ? (
          <p className="text-gray-500">Waiting for output — start a run to see live progress.</p>
        ) : (
          lines.map((line, index) => (
            <div key={index} className="flex gap-3">
              <span className="flex-shrink-0 text-gray-600">{line.ts.slice(11, 19)}</span>
              <span className={LEVEL_STYLES[line.level] ?? 'text-gray-300'}>{line.message}</span>
            </div>
          ))
        )}
      </div>
    </section>
  );
}
