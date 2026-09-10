import { Bookmark, Building2, ExternalLink, MapPin, X } from 'lucide-react';
import { useState } from 'react';
import type { Job } from '../types';

interface JobCardProps {
  job: Job;
  onToggleSave: (job: Job) => void;
  onDismiss: (job: Job) => void;
}

function formatMoney(job: Job): string | null {
  if (!job.amount) return null;
  const formatted = new Intl.NumberFormat(undefined, {
    style: 'currency',
    currency: /^[A-Z]{3}$/.test(job.currency) ? job.currency : 'USD',
    maximumFractionDigits: 0,
  }).format(job.amount);
  return `${formatted} · ${job.rate.toLowerCase()}`;
}

function relativeDate(value: string | null): string | null {
  if (!value) return null;
  const posted = new Date(`${value}T00:00:00Z`).getTime();
  if (Number.isNaN(posted)) return null;
  const days = Math.floor((Date.now() - posted) / 86_400_000);
  if (days <= 0) return 'today';
  if (days === 1) return 'yesterday';
  if (days < 30) return `${days}d ago`;
  return `${Math.floor(days / 30)}mo ago`;
}

export function JobCard({ job, onToggleSave, onDismiss }: JobCardProps) {
  const [expanded, setExpanded] = useState(false);
  const money = formatMoney(job);
  const posted = relativeDate(job.date_posted);
  const isPivot = job.category_type === 'Pivot / Growth Opportunity';

  return (
    <article className="flex flex-col justify-between rounded-lg border border-gray-200 bg-white p-5 transition hover:shadow-md">
      <div>
        <div className="mb-3 flex items-start justify-between gap-2">
          <h3 className="text-base font-semibold leading-snug text-gray-900">
            <a
              href={job.apply_url}
              target="_blank"
              rel="noreferrer noopener"
              className="hover:text-brand"
            >
              {job.title}
            </a>
          </h3>
          <div className="flex flex-shrink-0 items-center">
            <button
              onClick={() => onToggleSave(job)}
              aria-pressed={job.saved}
              aria-label={job.saved ? `Remove ${job.title} from My Jobs` : `Save ${job.title}`}
              title={job.saved ? 'Remove from My Jobs' : 'Save to My Jobs'}
              className="rounded p-1 text-gray-400 transition hover:text-gray-600"
            >
              <Bookmark
                className={`h-4 w-4 ${job.saved ? 'fill-brand text-brand' : ''}`}
                aria-hidden
              />
            </button>
            <button
              onClick={() => onDismiss(job)}
              aria-label={`Dismiss ${job.title}`}
              title="Not for me — hide this posting"
              className="rounded p-1 text-gray-300 transition hover:text-gray-600"
            >
              <X className="h-4 w-4" aria-hidden />
            </button>
          </div>
        </div>

        <div className="mb-2 flex items-center gap-2 text-sm font-medium text-gray-700">
          <Building2 className="h-4 w-4 flex-shrink-0 text-gray-400" aria-hidden />
          <span className="truncate">{job.company}</span>
        </div>

        <div className="mb-3 flex items-center gap-2 text-xs text-gray-500">
          <MapPin className="h-4 w-4 flex-shrink-0 text-gray-400" aria-hidden />
          <span className="truncate" title={job.location}>
            {job.location}
          </span>
        </div>

        <div className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-gray-500">
          {money && <span className="font-medium text-gray-700">{money}</span>}
          {job.seniority && <span>{job.seniority}</span>}
          {posted && <span>{posted}</span>}
        </div>

        {job.alert_summary && (
          <button
            onClick={() => setExpanded((value) => !value)}
            className="mb-3 w-full text-left text-xs leading-relaxed text-gray-600 hover:text-gray-900"
            aria-expanded={expanded}
          >
            <span className={expanded ? '' : 'line-clamp-2'}>{job.alert_summary}</span>
          </button>
        )}

        {expanded && (job.transferable_skills.length > 0 || job.skills_to_learn.length > 0) && (
          <dl className="mb-3 space-y-2 rounded bg-gray-50 p-3 text-xs">
            {job.transferable_skills.length > 0 && (
              <div>
                <dt className="font-semibold text-gray-700">You already bring</dt>
                <dd className="text-gray-600">{job.transferable_skills.join(' · ')}</dd>
              </div>
            )}
            {job.skills_to_learn.length > 0 && (
              <div>
                <dt className="font-semibold text-gray-700">Gaps to close</dt>
                <dd className="text-gray-600">{job.skills_to_learn.join(' · ')}</dd>
              </div>
            )}
          </dl>
        )}
      </div>

      <div className="flex items-center justify-between border-t border-gray-100 pt-3 text-xs">
        <div className="flex flex-wrap items-center gap-1.5">
          <span
            className="chip bg-emerald-50 text-emerald-700"
            title="How much of this role you can already do"
          >
            Exp {job.experience_fit_score}%
          </span>
          <span
            className="chip bg-blue-50 text-blue-700"
            title="How strongly this role advances your stated pivot interests"
          >
            Pivot {job.interest_fit_score}%
          </span>
          {job.category_type !== 'Unrelated' && (
            <span className={`chip ${isPivot ? 'bg-amber-50 text-amber-700' : 'bg-gray-100 text-gray-600'}`}>
              {isPivot ? 'Pivot' : 'Direct'}
            </span>
          )}
        </div>
        <a
          href={job.apply_url}
          target="_blank"
          rel="noreferrer noopener"
          className="flex flex-shrink-0 items-center gap-1 font-semibold text-brand hover:underline"
        >
          <span>Apply</span>
          <ExternalLink className="h-3 w-3" aria-hidden />
        </a>
      </div>
    </article>
  );
}
