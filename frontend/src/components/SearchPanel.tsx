import { useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, Play, RotateCcw, Save, Square } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { api } from '../lib/api';
import {
  REASONING_EFFORTS,
  type LogLine,
  type ReasoningEffort,
  type ReviewEstimate,
  type ReviewMode,
  type SearchRun,
} from '../types';
import { ModelPicker } from './ModelPicker';

const LEVEL_STYLES: Record<LogLine['level'], string> = {
  info: 'text-gray-300',
  success: 'text-emerald-400',
  warn: 'text-amber-400',
  error: 'text-red-400',
};

const MAX_LINES = 500;

const PHASES: Record<SearchRun['phase'], string> = {
  idle: 'Not run yet',
  extracting: 'Reading the career sites…',
  importing: 'Importing what changed…',
  ranking: 'Ranking by rules…',
  reviewing: 'Scoring the new postings…',
  done: 'Done',
  failed: 'Failed',
  stopped: 'Stopped',
};

const MODE_LABELS: Record<ReviewMode, string> = {
  new: 'Score the never-scored',
  stale: 'Rescore with the current prompt',
  all: 'Rescore everything again',
};

function dollars(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined) return 'unknown';
  return `$${value.toFixed(value < 0.01 && value > 0 ? 5 : digits)}`;
}

/** The streamed console: the extractors' lines and the review's. */
function useLogStream() {
  const [lines, setLines] = useState<LogLine[]>([]);
  const [streaming, setStreaming] = useState(false);
  useEffect(() => {
    // EventSource reconnects on its own, so no retry loop is needed here.
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
  return { lines, streaming };
}

/** Run the Search: extract every site, import, rank, and score against the brief. */
export function SearchPanel() {
  const queryClient = useQueryClient();
  const { lines, streaming } = useLogStream();
  const scroller = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);

  const { data: status, refetch } = useQuery({
    queryKey: ['search-status'],
    queryFn: api.searchStatus,
    refetchInterval: (query) =>
      query.state.data?.search.running || query.state.data?.review.running ? 2000 : 10000,
  });
  const search = status?.search;
  const review = status?.review;
  const busy = Boolean(search?.running || review?.running);

  // New postings and scores change the grid: refresh it when a run ends.
  const wasBusy = useRef(false);
  useEffect(() => {
    if (wasBusy.current && !busy) {
      queryClient.invalidateQueries({ queryKey: ['jobs'] });
      queryClient.invalidateQueries({ queryKey: ['facets'] });
    }
    wasBusy.current = busy;
  }, [busy, queryClient]);

  useEffect(() => {
    const element = scroller.current;
    if (!element) return;
    // Only follow the tail if the reader has not scrolled up to read history.
    const atBottom = element.scrollHeight - element.scrollTop - element.clientHeight < 80;
    if (atBottom) element.scrollTop = element.scrollHeight;
  }, [lines]);

  const act = async (action: () => Promise<unknown>) => {
    setError(null);
    try {
      await action();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
    await refetch();
  };

  const done = search?.sites.length ?? 0;
  const total = search?.sites_total ?? 0;
  const failing = search?.sites.filter((site) => site.status !== 'OK' && site.status !== 'CHECK') ?? [];
  const changed = search?.changes.filter((change) => change.new || change.gone) ?? [];

  return (
    <section className="mx-auto w-full max-w-7xl space-y-6 px-4 pb-16 sm:px-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-bold text-gray-900">Run the Search</h2>
          <p className="max-w-2xl text-xs text-gray-500">
            Reads every career site, adds the new postings, takes out those no longer listed, and has
            the model score the new ones against your prompt. Postings the rules exclude (a visa, a
            senior or sales role, an eligibility blocker) keep their rules score and are not sent.
          </p>
        </div>
        {search?.running ? (
          <button
            onClick={() => act(api.stopSearch)}
            className="flex items-center gap-1.5 rounded border border-red-300 px-4 py-2 text-xs font-semibold text-red-700 transition hover:bg-red-50"
          >
            <Square className="h-3.5 w-3.5" aria-hidden />
            <span>Stop</span>
          </button>
        ) : (
          <button
            onClick={() => act(() => api.runSearch())}
            disabled={busy}
            className="flex items-center gap-1.5 rounded bg-brand px-4 py-2 text-xs font-semibold text-white transition hover:bg-brand-dark disabled:opacity-60"
          >
            <Play className="h-3.5 w-3.5" aria-hidden />
            <span>Run the Search</span>
          </button>
        )}
      </div>

      {error && (
        <p role="alert" className="rounded border border-red-200 bg-red-50 p-3 text-xs text-red-700">
          {error}
        </p>
      )}

      {search && search.phase !== 'idle' && (
        <div className="rounded-lg border border-gray-200 p-4">
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-xs">
            <span className="font-semibold text-gray-800">{PHASES[search.phase]}</span>
            <span className="text-gray-500">
              {done}/{total} sites
              {search.finished_at && ` · finished ${new Date(search.finished_at).toLocaleString()}`}
            </span>
          </div>
          <div className="mb-3 h-1.5 overflow-hidden rounded bg-gray-100">
            <div
              className="h-full bg-brand transition-all"
              style={{ width: `${total ? Math.round((done / total) * 100) : 0}%` }}
            />
          </div>
          {search.error && <p className="mb-2 text-xs text-red-700">{search.error}</p>}
          <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {[
              ['New postings', search.new_total],
              ['No longer listed', search.gone_total],
              ['Scored this run', search.reviewed],
              ['Scoring cost', dollars(search.review_cost, 4)],
            ].map(([label, value]) => (
              <div key={String(label)} className="rounded border border-gray-100 bg-gray-50 p-2">
                <dt className="text-[11px] uppercase tracking-wide text-gray-500">{label}</dt>
                <dd className="text-lg font-semibold text-gray-900">{value}</dd>
              </div>
            ))}
          </dl>

          {changed.length > 0 && (
            <table className="mt-4 w-full text-left text-xs">
              <caption className="mb-1 text-left font-semibold text-gray-700">By employer</caption>
              <thead className="text-gray-500">
                <tr>
                  <th className="py-1 font-medium">Employer</th>
                  <th className="py-1 text-right font-medium">New</th>
                  <th className="py-1 text-right font-medium" title="Of the new ones, excluded by the rules">
                    of which excluded
                  </th>
                  <th className="py-1 text-right font-medium">Gone</th>
                </tr>
              </thead>
              <tbody>
                {changed.map((change) => (
                  <tr key={change.company} className="border-t border-gray-100">
                    <td className="py-1 text-gray-800">{change.company}</td>
                    <td className="py-1 text-right font-semibold text-emerald-700">
                      {change.new ? `+${change.new}` : ''}
                    </td>
                    <td className="py-1 text-right text-gray-400">{change.new_excluded || ''}</td>
                    <td className="py-1 text-right text-gray-500">{change.gone ? `−${change.gone}` : ''}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {failing.length > 0 && (
            <div className="mt-4 rounded border border-amber-200 bg-amber-50 p-3">
              <p className="mb-1 flex items-center gap-2 text-xs font-semibold text-amber-800">
                <AlertTriangle className="h-4 w-4" aria-hidden />
                {failing.length} site{failing.length === 1 ? '' : 's'} not read in full: their previous
                postings are kept
              </p>
              <p className="text-xs text-amber-700">
                {failing.map((site) => `${site.name} (${site.status})`).join(' · ')}
              </p>
            </div>
          )}
        </div>
      )}

      {status && <ScoringSection status={status} onAct={act} />}

      <BriefEditor busy={Boolean(review?.running)} onSaved={() => refetch()} />

      <div>
        <div className="mb-1 flex items-center justify-between text-xs text-gray-500">
          <span className="font-semibold text-gray-700">Console</span>
          <span className={streaming ? 'text-emerald-600' : 'text-gray-400'}>
            {streaming ? '● live' : '○ reconnecting'}
          </span>
        </div>
        <div
          ref={scroller}
          role="log"
          aria-label="Search output"
          className="h-80 overflow-y-auto rounded-lg bg-gray-900 p-4 font-mono text-xs leading-relaxed"
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
      </div>
    </section>
  );
}

interface ScoringSectionProps {
  status: NonNullable<Awaited<ReturnType<typeof api.searchStatus>>>;
  onAct: (action: () => Promise<unknown>) => Promise<void>;
}

/** Which model scores, how hard it thinks, and the rescoring controls. */
function ScoringSection({ status, onAct }: ScoringSectionProps) {
  const { counts, review, scoring, search } = status;
  const [model, setModel] = useState(scoring.model);
  const [effort, setEffort] = useState<ReasoningEffort>(scoring.reasoning_effort);
  const [concurrency, setConcurrency] = useState(scoring.max_concurrency);
  const [limit, setLimit] = useState('');
  const [pending, setPending] = useState<{ mode: ReviewMode; estimate: ReviewEstimate } | null>(null);

  // Follow the saved settings when they change elsewhere (Settings tab).
  useEffect(() => {
    setModel(scoring.model);
    setEffort(scoring.reasoning_effort);
    setConcurrency(scoring.max_concurrency);
  }, [scoring.model, scoring.reasoning_effort, scoring.max_concurrency]);

  const dirty =
    model !== scoring.model ||
    effort !== scoring.reasoning_effort ||
    concurrency !== scoring.max_concurrency;
  const busy = review.running || search.running;
  const limitValue = Number.parseInt(limit, 10) > 0 ? Number.parseInt(limit, 10) : undefined;

  const ask = (mode: ReviewMode) =>
    onAct(async () => {
      if (dirty) await api.saveScoring({ model, reasoning_effort: effort, max_concurrency: concurrency });
      setPending({ mode, estimate: await api.estimate(mode, limitValue) });
    });

  const start = () =>
    onAct(async () => {
      if (!pending) return;
      await api.review(pending.mode, limitValue);
      setPending(null);
    });

  const reasons = Object.entries(counts.excluded_by_reason);

  return (
    <div className="rounded-lg border border-gray-200 p-4">
      <h3 className="mb-1 text-sm font-bold text-gray-900">Scoring with the model</h3>
      <p className="mb-3 text-xs text-gray-500">
        Of {counts.open} open postings, {counts.excluded} are excluded by the rules
        {reasons.length > 0 && ` (${reasons.map(([reason, n]) => `${reason}: ${n}`).join(', ')})`}.
      </p>
      <dl className="mb-4 grid grid-cols-3 gap-3">
        {[
          ['Scored with this prompt', counts.current, 'text-emerald-700'],
          ['Scored with an older prompt', counts.stale, 'text-amber-700'],
          ['Never scored', counts.never, 'text-gray-900'],
        ].map(([label, value, tone]) => (
          <div key={String(label)} className="rounded border border-gray-100 bg-gray-50 p-2">
            <dt className="text-[11px] uppercase tracking-wide text-gray-500">{label}</dt>
            <dd className={`text-lg font-semibold ${tone}`}>{value}</dd>
          </div>
        ))}
      </dl>

      <div className="mb-4 grid gap-3 sm:grid-cols-[1fr_auto_auto]">
        <div>
          <span className="mb-1 block text-xs font-medium text-gray-700">Model</span>
          <ModelPicker value={model} onChange={setModel} reasoningRequested={effort !== 'none'} />
        </div>
        <label className="text-xs font-medium text-gray-700">
          <span className="mb-1 block">Reasoning effort</span>
          <select
            value={effort}
            onChange={(event) => setEffort(event.target.value as ReasoningEffort)}
            className="control w-40 pr-3"
          >
            {REASONING_EFFORTS.map((item) => (
              <option key={item.value} value={item.value} title={item.hint}>
                {item.label}
              </option>
            ))}
          </select>
        </label>
        <label className="text-xs font-medium text-gray-700">
          <span className="mb-1 block">Parallel calls</span>
          <input
            type="number"
            min={1}
            max={32}
            value={concurrency}
            onChange={(event) => setConcurrency(Number(event.target.value) || 1)}
            className="control w-24"
          />
        </label>
      </div>

      <div className="flex flex-wrap items-end gap-2">
        <label className="text-xs font-medium text-gray-700">
          <span className="mb-1 block">At most (best by rules first)</span>
          <input
            type="number"
            min={1}
            placeholder="all"
            value={limit}
            onChange={(event) => setLimit(event.target.value)}
            className="control w-28"
          />
        </label>
        {(['new', 'stale', 'all'] as ReviewMode[]).map((mode) => (
          <button
            key={mode}
            onClick={() => ask(mode)}
            disabled={busy || !scoring.has_key || !status.brief_present}
            className="flex items-center gap-1.5 rounded border border-gray-300 px-3 py-2 text-xs font-medium text-gray-700 transition hover:bg-gray-50 disabled:opacity-50"
          >
            <RotateCcw className="h-3.5 w-3.5" aria-hidden />
            <span>{MODE_LABELS[mode]}</span>
          </button>
        ))}
        {review.running && (
          <button
            onClick={() => onAct(api.stopReview)}
            className="flex items-center gap-1.5 rounded border border-red-300 px-3 py-2 text-xs font-semibold text-red-700 hover:bg-red-50"
          >
            <Square className="h-3.5 w-3.5" aria-hidden />
            <span>Stop scoring</span>
          </button>
        )}
      </div>
      {!scoring.has_key && (
        <p className="mt-2 text-xs text-amber-700">Add an OpenRouter key under Settings to score.</p>
      )}

      {pending && (
        <div role="dialog" aria-label="Confirm scoring" className="mt-4 rounded border border-brand/40 bg-emerald-50 p-3 text-xs">
          <p className="mb-2 text-gray-800">
            <strong>{MODE_LABELS[pending.mode]}</strong>: {pending.estimate.count} postings with{' '}
            {pending.estimate.model}
            {effort !== 'none' && `, reasoning ${effort}`}. About{' '}
            <strong>{dollars(pending.estimate.total)}</strong> ({dollars(pending.estimate.per_posting)} each,{' '}
            {pending.estimate.source === 'history'
              ? `from the last ${pending.estimate.samples} reviews with this model`
              : pending.estimate.source === 'list price'
                ? 'from the list price, for a typical review'
                : 'no price known for this model'}
            ).
          </p>
          <div className="flex gap-2">
            <button
              onClick={start}
              disabled={pending.estimate.count === 0}
              className="rounded bg-brand px-3 py-1.5 font-semibold text-white hover:bg-brand-dark disabled:opacity-50"
            >
              Start
            </button>
            <button onClick={() => setPending(null)} className="rounded border border-gray-300 px-3 py-1.5 text-gray-700">
              Cancel
            </button>
          </div>
        </div>
      )}

      {(review.running || review.finished_at) && review.total > 0 && (
        <div className="mt-4 text-xs text-gray-600">
          <div className="mb-1 flex justify-between">
            <span>
              {review.running ? 'Scoring' : 'Last scoring'}: {review.reviewed + review.failed}/{review.total}
              {review.failed > 0 && `, ${review.failed} failed`} · {review.model}
              {review.effort !== 'none' && ` (${review.effort})`}
            </span>
            <span className="font-semibold text-gray-800">{dollars(review.cost, 4)}</span>
          </div>
          <div className="h-1.5 overflow-hidden rounded bg-gray-100">
            <div
              className="h-full bg-brand transition-all"
              style={{ width: `${Math.round(((review.reviewed + review.failed) / review.total) * 100)}%` }}
            />
          </div>
          {review.stopped && <p className="mt-1 text-amber-700">{review.stopped}</p>}
        </div>
      )}
    </div>
  );
}

interface BriefEditorProps {
  busy: boolean;
  onSaved: () => void;
}

/** The prompt sent with every posting: the operator's, editable. */
function BriefEditor({ busy, onSaved }: BriefEditorProps) {
  const { data: brief, refetch } = useQuery({ queryKey: ['brief'], queryFn: api.brief });
  const [text, setText] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const draft = text ?? brief?.text ?? '';
  const dirty = text !== null && text !== brief?.text;

  const save = async () => {
    setMessage(null);
    try {
      const result = await api.saveBrief(draft);
      setText(null);
      await refetch();
      onSaved();
      setMessage(
        `Saved. ${result.counts.stale + result.counts.current} postings scored with the previous prompt are ` +
          'now marked ↻: "Rescore with the current prompt" scores them again.',
      );
    } catch (exc) {
      setMessage(exc instanceof Error ? exc.message : String(exc));
    }
  };

  return (
    <div className="rounded-lg border border-gray-200 p-4">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="text-sm font-bold text-gray-900">Ranking prompt</h3>
          <p className="text-xs text-gray-500">
            Who you are, what you want and how to score. Sent with every posting; saved in
            data/ranking-brief.md.
          </p>
        </div>
        <button
          onClick={save}
          disabled={!dirty || busy}
          title={busy ? 'Stop the scoring in progress first' : undefined}
          className="flex items-center gap-1.5 rounded bg-brand px-3 py-2 text-xs font-semibold text-white hover:bg-brand-dark disabled:opacity-50"
        >
          <Save className="h-3.5 w-3.5" aria-hidden />
          <span>Save prompt</span>
        </button>
      </div>
      <textarea
        aria-label="Ranking prompt"
        value={draft}
        onChange={(event) => setText(event.target.value)}
        rows={16}
        className="control w-full font-mono text-xs leading-relaxed"
      />
      {message && <p className="mt-2 text-xs text-gray-700">{message}</p>}
      {brief?.instructions && (
        <details className="mt-3 text-xs">
          <summary className="cursor-pointer text-gray-600">
            Fixed instructions added after your prompt (what the model must answer)
          </summary>
          <pre className="mt-2 whitespace-pre-wrap rounded bg-gray-50 p-3 text-gray-600">{brief.instructions}</pre>
        </details>
      )}
    </div>
  );
}
