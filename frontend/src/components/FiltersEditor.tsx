import { useQuery } from '@tanstack/react-query';
import { Save } from 'lucide-react';
import { useState } from 'react';
import { api } from '../lib/api';
import type { ExclusionFilters } from '../types';

const FIELDS: { key: keyof ExclusionFilters; label: string; placeholder: string }[] = [
  { key: 'title', label: 'Title must not contain', placeholder: 'Senior\nStage\nintern*\nWerkstudent' },
  { key: 'company', label: 'Company is not', placeholder: 'Acme Staffing' },
  { key: 'location', label: 'Location must not contain', placeholder: 'Remote - US' },
];

const toLines = (words: string[]) => words.join('\n');
const toWords = (text: string) =>
  text
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean);

interface FiltersEditorProps {
  onSaved: () => void;
}

/** The operator's own exclusions: words matched literally, never sent to the model. */
export function FiltersEditor({ onSaved }: FiltersEditorProps) {
  const { data, refetch } = useQuery({ queryKey: ['exclusion-filters'], queryFn: api.filters });
  const [draft, setDraft] = useState<Record<keyof ExclusionFilters, string> | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const saved = {
    title: toLines(data?.title ?? []),
    company: toLines(data?.company ?? []),
    location: toLines(data?.location ?? []),
  };
  const current = draft ?? saved;
  const dirty = draft !== null && FIELDS.some(({ key }) => draft[key] !== saved[key]);

  const save = async () => {
    setMessage(null);
    setSaving(true);
    try {
      const result = await api.saveFilters({
        title: toWords(current.title),
        company: toWords(current.company),
        location: toWords(current.location),
      });
      setDraft(null);
      await refetch();
      onSaved();
      setMessage(`Saved. ${result.excluded} open postings are now excluded by your filters.`);
    } catch (exc) {
      setMessage(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setSaving(false);
    }
  };

  const counts = Object.entries(data?.by_filter ?? {});

  return (
    <div className="rounded-lg border border-gray-200 p-4">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="text-sm font-bold text-gray-900">Your filters</h3>
          <p className="max-w-2xl text-xs text-gray-500">
            One word or phrase per line, matched as whole words, ignoring case and accents ("stage" does not
            catch "Backstage"). End a word with * to match its start ("intern*" catches "Internship"). A
            posting they catch is excluded like a senior or visa role: hidden from the grid and never scored
            by the model.
          </p>
        </div>
        <button
          onClick={save}
          disabled={!dirty || saving}
          className="flex items-center gap-1.5 rounded bg-brand px-3 py-2 text-xs font-semibold text-white hover:bg-brand-dark disabled:opacity-50"
        >
          <Save className="h-3.5 w-3.5" aria-hidden />
          <span>{saving ? 'Applying…' : 'Save filters'}</span>
        </button>
      </div>
      <div className="grid gap-3 sm:grid-cols-3">
        {FIELDS.map(({ key, label, placeholder }) => (
          <label key={key} className="text-xs font-medium text-gray-700">
            <span className="mb-1 block">{label}</span>
            <textarea
              aria-label={label}
              value={current[key]}
              placeholder={placeholder}
              onChange={(event) => setDraft({ ...current, [key]: event.target.value })}
              rows={6}
              className="control w-full font-mono text-xs leading-relaxed"
            />
          </label>
        ))}
      </div>
      {message && <p className="mt-2 text-xs text-gray-700">{message}</p>}
      {counts.length > 0 && (
        <p className="mt-2 text-xs text-gray-500">
          Open postings excluded: {counts.map(([reason, n]) => `${reason.replace(/^Filtro /, '')} ${n}`).join(' · ')}
        </p>
      )}
    </div>
  );
}
