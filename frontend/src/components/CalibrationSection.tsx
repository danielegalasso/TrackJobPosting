import { Sparkles, Trash2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import { api } from '../lib/api';
import type { CalibrationStatus } from '../types';

interface CalibrationSectionProps {
  calibration: CalibrationStatus;
  canRun: boolean;
  onAct: (action: () => Promise<unknown>) => Promise<void>;
}

/** What the review learned from the postings starred and removed. */
export function CalibrationSection({ calibration, canRun, onAct }: CalibrationSectionProps) {
  const { settings, current, pending, running } = calibration;
  const [every, setEvery] = useState(settings.every);
  useEffect(() => setEvery(settings.every), [settings.every]);

  const saveSettings = (next: Partial<CalibrationStatus['settings']>) =>
    onAct(() => api.saveCalibration({ ...settings, every, ...next }));

  return (
    <div className="rounded-lg border border-gray-200 p-4">
      <div className="mb-2 flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="text-sm font-bold text-gray-900">Learning from your stars and removals</h3>
          <p className="max-w-2xl text-xs text-gray-500">
            Every {settings.every} new stars or removals, the model compares them with the scores it gave and
            writes guidelines that sharpen your prompt; the postings it got most wrong are kept as examples.
            Both are added to the prompt after yours, which stays as you wrote it. Scores already given are
            not redone by themselves: rescore them below when you want.
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => onAct(api.calibrate)}
            disabled={running || !canRun}
            className="flex items-center gap-1.5 rounded border border-gray-300 px-3 py-2 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
          >
            <Sparkles className="h-3.5 w-3.5" aria-hidden />
            <span>{running ? 'Learning…' : 'Learn now'}</span>
          </button>
          {current && (
            <button
              onClick={() => onAct(api.resetCalibration)}
              disabled={running}
              title="Forget the guidelines: scoring goes back to your prompt alone"
              className="flex items-center gap-1.5 rounded border border-gray-300 px-3 py-2 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
            >
              <Trash2 className="h-3.5 w-3.5" aria-hidden />
              <span>Reset</span>
            </button>
          )}
        </div>
      </div>

      <div className="mb-3 flex flex-wrap items-center gap-4 text-xs text-gray-700">
        <label className="flex items-center gap-1.5">
          <input
            type="checkbox"
            checked={settings.auto}
            onChange={(event) => saveSettings({ auto: event.target.checked })}
          />
          <span>Learn by itself every</span>
          <input
            type="number"
            min={5}
            max={1000}
            value={every}
            onChange={(event) => setEvery(Number(event.target.value) || settings.every)}
            onBlur={() => every !== settings.every && saveSettings({ every })}
            className="control w-20"
            aria-label="Stars and removals between two updates"
          />
          <span>stars or removals</span>
        </label>
        <span className="text-gray-500">
          {pending}/{settings.every} since the last update
        </span>
      </div>

      <div className="mb-2 h-1.5 overflow-hidden rounded bg-gray-100">
        <div
          className="h-full bg-brand transition-all"
          style={{ width: `${Math.min(100, Math.round((pending / settings.every) * 100))}%` }}
        />
      </div>
      {calibration.error && <p className="mb-2 text-xs text-red-700">Last update failed: {calibration.error}</p>}

      {current ? (
        <div className="text-xs text-gray-700">
          <p className="mb-2 text-gray-500">
            Update {current.version}, {new Date(current.created_at).toLocaleString()} · from {current.starred}{' '}
            starred and {current.removed} removed · {current.model} · ${current.cost.toFixed(4)}
          </p>
          {current.notes && <p className="mb-2 italic">{current.notes}</p>}
          {current.guidelines.length > 0 ? (
            <ul className="mb-2 list-disc space-y-1 pl-5">
              {current.guidelines.map((guideline) => (
                <li key={guideline}>{guideline}</li>
              ))}
            </ul>
          ) : (
            <p className="mb-2 text-gray-500">No guidelines: the scores already agree with your choices.</p>
          )}
          <details>
            <summary className="cursor-pointer text-gray-600">Exactly what is added to the prompt</summary>
            <pre className="mt-2 whitespace-pre-wrap rounded bg-gray-50 p-3 text-gray-600">{calibration.section}</pre>
          </details>
        </div>
      ) : (
        <p className="text-xs text-gray-500">Nothing learned yet: scoring uses your prompt alone.</p>
      )}
    </div>
  );
}
