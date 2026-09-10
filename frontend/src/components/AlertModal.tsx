import { Loader2 } from 'lucide-react';
import { useState } from 'react';
import { api } from '../lib/api';
import type { Filters } from '../types';
import { Modal } from './Modal';

interface AlertModalProps {
  filters: Filters;
  onClose: () => void;
}

/** The filter fields that actually travel with a saved alert. */
function alertPayload(filters: Filters): Record<string, unknown> {
  const payload: Record<string, unknown> = {};
  const keys: (keyof Filters)[] = [
    'search',
    'location',
    'remote',
    'category',
    'seniority',
    'company',
    'years_experience',
    'posted_within',
  ];
  for (const key of keys) {
    const value = filters[key];
    if (value) payload[key] = value;
  }
  if (filters.min_amount) {
    payload.min_amount = Number(filters.min_amount);
    payload.rate = filters.rate;
    payload.currency = filters.currency;
    payload.match_posting_currency = filters.match_posting_currency;
  }
  return payload;
}

function summarise(filters: Filters): [string, string][] {
  return [
    ['Keyword', filters.search || 'All'],
    ['Location', filters.location || filters.remote || 'Anywhere'],
    ['Category', filters.category || 'All'],
    ['Seniority', filters.seniority || 'All'],
    ['Company', filters.company || 'All'],
    [
      'Minimum pay',
      filters.min_amount
        ? `${filters.currency} ${Number(filters.min_amount).toLocaleString()} ${filters.rate.toLowerCase()}`
        : 'Any',
    ],
  ];
}

export function AlertModal({ filters, onClose }: AlertModalProps) {
  const [email, setEmail] = useState('');
  const [state, setState] = useState<'idle' | 'saving' | 'saved'>('idle');
  const [error, setError] = useState('');

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError('');
    setState('saving');
    try {
      await api.createAlert(email.trim(), alertPayload(filters));
      setState('saved');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not save the alert');
      setState('idle');
    }
  };

  if (state === 'saved') {
    return (
      <Modal title="Alert created" onClose={onClose}>
        <p className="text-sm text-gray-700">
          New roles matching these filters will be emailed to{' '}
          <strong className="font-semibold">{email}</strong> after each indexing run.
        </p>
        <p className="mt-3 text-xs text-gray-500">
          Only postings that clear your fit thresholds are sent, and nothing is ever sent twice.
          Every digest carries a one-click unsubscribe link.
        </p>
        <button
          onClick={onClose}
          className="mt-5 w-full rounded bg-brand py-2 text-sm font-semibold text-white transition hover:bg-brand-dark"
        >
          Done
        </button>
      </Modal>
    );
  }

  return (
    <Modal title="Create job alert" onClose={onClose}>
      <form onSubmit={submit}>
        <p className="mb-4 text-xs text-gray-500">
          Get an email whenever a newly indexed role matches these filters and clears your
          dual-vector fit thresholds.
        </p>

        <label className="mb-1 block text-xs font-semibold text-gray-700" htmlFor="alert-email">
          Your email
        </label>
        <input
          id="alert-email"
          type="email"
          required
          autoFocus
          placeholder="name@domain.com"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          className="control mb-4 pr-3"
        />

        <dl className="mb-5 space-y-1 rounded bg-gray-50 p-3 text-xs">
          {summarise(filters).map(([label, value]) => (
            <div key={label} className="flex justify-between gap-4">
              <dt className="font-semibold text-gray-700">{label}</dt>
              <dd className="truncate text-gray-600">{value}</dd>
            </div>
          ))}
        </dl>

        {error && (
          <p role="alert" className="mb-3 rounded bg-red-50 p-2 text-xs text-red-700">
            {error}
          </p>
        )}

        <button
          type="submit"
          disabled={state === 'saving' || !email.trim()}
          className="flex w-full items-center justify-center gap-2 rounded bg-brand py-2 text-sm font-semibold text-white transition hover:bg-brand-dark disabled:opacity-60"
        >
          {state === 'saving' && <Loader2 className="h-4 w-4 animate-spin" aria-hidden />}
          <span>Save alert</span>
        </button>

        <p className="mt-3 text-[11px] leading-relaxed text-gray-400">
          Your address is stored on this host and used only to send these digests. See the Privacy
          Policy linked in the footer.
        </p>
      </form>
    </Modal>
  );
}
