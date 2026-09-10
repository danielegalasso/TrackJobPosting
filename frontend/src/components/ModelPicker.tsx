import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, Brain, Check, Loader2, RefreshCw, Search } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../lib/api';
import type { ModelInfo } from '../types';

interface ModelPickerProps {
  value: string;
  onChange: (modelId: string) => void;
  /** Warn when the chosen model cannot honour the configured effort. */
  reasoningRequested: boolean;
}

/** OpenRouter quotes per-token USD; per-million is the readable unit. */
function perMillion(price: number | null): string | null {
  if (price === null || Number.isNaN(price)) return null;
  if (price === 0) return 'free';
  const value = price * 1_000_000;
  return `$${value < 1 ? value.toFixed(3) : value.toFixed(2)}/M`;
}

function contextLabel(tokens: number | null): string | null {
  if (!tokens) return null;
  if (tokens >= 1_000_000) return `${(tokens / 1_000_000).toFixed(1).replace(/\.0$/, '')}M ctx`;
  if (tokens >= 1000) return `${Math.round(tokens / 1000)}K ctx`;
  return `${tokens} ctx`;
}

export function ModelPicker({ value, onChange, reasoningRequested }: ModelPickerProps) {
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const [highlighted, setHighlighted] = useState(0);
  const container = useRef<HTMLDivElement>(null);
  const listbox = useRef<HTMLUListElement>(null);

  // The whole catalogue is fetched once and filtered in the browser, so
  // typing is instant and does not put a request on every keystroke.
  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ['models'],
    queryFn: () => api.models(),
    staleTime: 10 * 60 * 1000,
  });

  const models = useMemo(() => data ?? [], [data]);

  const matches = useMemo(() => {
    const terms = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
    if (terms.length === 0) return models;
    return models.filter((model) => {
      const haystack = `${model.id} ${model.name}`.toLowerCase();
      return terms.every((term) => haystack.includes(term));
    });
  }, [models, query]);

  const selected = models.find((model) => model.id === value);

  useEffect(() => setHighlighted(0), [query]);

  // Close when focus or a click leaves the combobox.
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      if (!container.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onPointerDown);
    return () => document.removeEventListener('mousedown', onPointerDown);
  }, [open]);

  // Keep the highlighted row in view during keyboard navigation.
  useEffect(() => {
    if (!open) return;
    listbox.current
      ?.querySelector(`[data-index="${highlighted}"]`)
      ?.scrollIntoView({ block: 'nearest' });
  }, [highlighted, open]);

  const choose = (model: ModelInfo) => {
    onChange(model.id);
    setQuery('');
    setOpen(false);
  };

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      if (!open) {
        setOpen(true);
        return;
      }
      const step = event.key === 'ArrowDown' ? 1 : -1;
      setHighlighted((current) => {
        if (matches.length === 0) return 0;
        return (current + step + matches.length) % matches.length;
      });
    } else if (event.key === 'Enter') {
      if (open && matches[highlighted]) {
        event.preventDefault();
        choose(matches[highlighted]);
      }
    } else if (event.key === 'Escape') {
      if (open) {
        event.stopPropagation();
        setOpen(false);
      }
    }
  };

  const missingReasoning = reasoningRequested && selected && !selected.supports_reasoning;

  return (
    <div ref={container} className="relative">
      <div className="relative">
        <input
          id="model-search"
          type="text"
          role="combobox"
          aria-expanded={open}
          aria-controls="model-listbox"
          aria-autocomplete="list"
          autoComplete="off"
          placeholder={value || 'Search models …'}
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={onKeyDown}
          className="control pr-16 font-mono text-xs"
        />
        <Search className="pointer-events-none absolute right-9 top-2.5 h-4 w-4 text-gray-400" aria-hidden />
        <button
          type="button"
          onClick={() => refetch()}
          title="Refresh the model list from OpenRouter"
          aria-label="Refresh the model list"
          className="absolute right-2 top-2 rounded p-0.5 text-gray-400 transition hover:text-gray-700"
        >
          <RefreshCw className={`h-4 w-4 ${isFetching ? 'animate-spin' : ''}`} aria-hidden />
        </button>
      </div>

      {/* What is actually saved, shown whether or not the list is open. */}
      <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-gray-500">
        <span>
          Selected: <code className="rounded bg-gray-100 px-1 font-mono">{value || 'none'}</code>
        </span>
        {selected?.supports_reasoning && (
          <span className="inline-flex items-center gap-1 text-emerald-700">
            <Brain className="h-3 w-3" aria-hidden />
            reasoning
          </span>
        )}
        {contextLabel(selected?.context_length ?? null) && (
          <span>{contextLabel(selected?.context_length ?? null)}</span>
        )}
      </p>

      {missingReasoning && (
        <p
          role="status"
          className="mt-1 flex items-start gap-1.5 rounded bg-amber-50 p-2 text-[11px] text-amber-800"
        >
          <AlertTriangle className="mt-0.5 h-3 w-3 flex-shrink-0" aria-hidden />
          <span>
            This model does not advertise reasoning support — the effort setting will be ignored.
          </span>
        </p>
      )}

      {error && (
        <p role="alert" className="mt-1 rounded bg-red-50 p-2 text-[11px] text-red-700">
          Could not load the model list: {error instanceof Error ? error.message : 'unknown error'}.
          You can still type an id above and save it.
        </p>
      )}

      {open && (
        <ul
          ref={listbox}
          id="model-listbox"
          role="listbox"
          aria-label="OpenRouter models"
          className="absolute z-20 mt-1 max-h-72 w-full overflow-y-auto rounded-md border border-gray-200 bg-white shadow-lg"
        >
          {isLoading && (
            <li className="flex items-center gap-2 px-3 py-2 text-xs text-gray-500">
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
              Loading catalogue …
            </li>
          )}

          {!isLoading && matches.length === 0 && (
            <li className="px-3 py-2 text-xs text-gray-500">
              No model matches “{query}”.
            </li>
          )}

          {matches.slice(0, 80).map((model, index) => (
            <li key={model.id} role="option" aria-selected={model.id === value} data-index={index}>
              <button
                type="button"
                onMouseEnter={() => setHighlighted(index)}
                onClick={() => choose(model)}
                className={`flex w-full items-start justify-between gap-3 px-3 py-2 text-left transition ${
                  index === highlighted ? 'bg-emerald-50' : ''
                }`}
              >
                <span className="min-w-0">
                  <span className="flex items-center gap-1.5">
                    <code className="truncate font-mono text-xs text-gray-900">{model.id}</code>
                    {model.supports_reasoning && (
                      <Brain className="h-3 w-3 flex-shrink-0 text-emerald-600" aria-label="supports reasoning" />
                    )}
                  </span>
                  <span className="block truncate text-[11px] text-gray-500">{model.name}</span>
                </span>
                <span className="flex flex-shrink-0 items-center gap-2 text-[11px] text-gray-400">
                  {contextLabel(model.context_length)}
                  {perMillion(model.prompt_price) && <span>{perMillion(model.prompt_price)}</span>}
                  {model.id === value && <Check className="h-3.5 w-3.5 text-emerald-600" aria-hidden />}
                </span>
              </button>
            </li>
          ))}

          {matches.length > 80 && (
            <li className="border-t border-gray-100 px-3 py-2 text-[11px] text-gray-400">
              {matches.length - 80} more — keep typing to narrow the list.
            </li>
          )}
        </ul>
      )}
    </div>
  );
}
