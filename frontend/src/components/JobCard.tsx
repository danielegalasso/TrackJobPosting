import { Building2, CalendarClock, ExternalLink, MapPin, Star, Undo2, X } from 'lucide-react';
import { useState } from 'react';
import type { Job, PriorityFlag } from '../types';

interface JobCardProps {
  job: Job;
  onToggleSave: (job: Job) => void;
  onDismiss: (job: Job) => void;
  /** Shown in the Removed view, where every card is removed: no chip says so. */
  removedView?: boolean;
}

const DAY = 86_400_000;

function formatMoney(job: Job): string | null {
  if (!job.amount) return null;
  const formatted = new Intl.NumberFormat(undefined, {
    style: 'currency',
    currency: /^[A-Z]{3}$/.test(job.currency) ? job.currency : 'USD',
    maximumFractionDigits: 0,
  }).format(job.amount);
  return `${formatted} · ${job.rate.toLowerCase()}`;
}

/** Midnight UTC of an ISO date, or NaN. */
function dayOf(value: string): number {
  return new Date(`${value}T00:00:00Z`).getTime();
}

function today(): number {
  const now = new Date();
  return Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
}

function shortDate(value: string): string {
  return new Date(`${value}T00:00:00Z`).toLocaleDateString(undefined, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    timeZone: 'UTC',
  });
}

function relativeDate(value: string | null): string | null {
  if (!value) return null;
  const posted = dayOf(value);
  if (Number.isNaN(posted)) return null;
  const days = Math.floor((Date.now() - posted) / DAY);
  if (days <= 0) return 'today';
  if (days === 1) return 'yesterday';
  if (days < 30) return `${days}d ago`;
  return `${Math.floor(days / 30)}mo ago`;
}

interface DeadlineInfo {
  label: string;
  tone: string;
}

/** "Closes in 3 days" and a colour that gets louder as the date nears. */
export function describeDeadline(value: string | null, now = today()): DeadlineInfo | null {
  if (!value) return null;
  const due = dayOf(value);
  if (Number.isNaN(due)) return null;
  const days = Math.round((due - now) / DAY);
  const date = shortDate(value);
  if (days < 0) return { label: `Closed ${date}`, tone: 'text-gray-400' };
  if (days === 0) return { label: `Closes today · ${date}`, tone: 'text-red-700 font-semibold' };
  if (days === 1) return { label: `Closes tomorrow · ${date}`, tone: 'text-red-700 font-semibold' };
  if (days <= 7) return { label: `Closes in ${days} days · ${date}`, tone: 'text-red-700 font-medium' };
  if (days <= 21) return { label: `Closes in ${days} days · ${date}`, tone: 'text-amber-700' };
  return { label: `Closes ${date}`, tone: 'text-gray-600' };
}

const FLAG_TONES: Record<PriorityFlag['level'], string> = {
  block: 'bg-red-50 text-red-700',
  warn: 'bg-amber-50 text-amber-800',
  info: 'bg-gray-100 text-gray-600',
};

/** Louder the higher it is: the point of the number is where to look first. */
export function priorityTone(score: number): string {
  if (score >= 80) return 'bg-brand text-white';
  if (score >= 65) return 'bg-emerald-100 text-emerald-800';
  if (score >= 45) return 'bg-gray-100 text-gray-700';
  return 'bg-gray-50 text-gray-400';
}

function signed(points: number): string {
  return points > 0 ? `+${points}` : String(points);
}

/** The score's make-up, criterion by criterion, for the chip's tooltip. */
export function priorityTitle(job: Job): string {
  const source =
    job.priority_source === 'review'
      ? `reviewed against ${job.priority_stale ? 'an older version of ' : ''}your brief${
          job.priority_model ? ` by ${job.priority_model}` : ''
        }`
      : 'by rules';
  const criteria = job.priority_criteria.map((c) => `${c.name}: ${c.points}/${c.max}`);
  const adjustments = job.priority_adjustments.map((a) => `${a.name}: ${signed(a.points)}`);
  return [`Priority ${job.priority}, ${source}`, ...criteria, ...adjustments].join('\n');
}

export function JobCard({ job, onToggleSave, onDismiss, removedView = false }: JobCardProps) {
  const [expanded, setExpanded] = useState(false);
  const money = formatMoney(job);
  const posted = relativeDate(job.date_posted);
  const deadline = describeDeadline(job.deadline);
  const isPivot = job.category_type === 'Pivot / Growth Opportunity';
  // An evaluator summary when there is one; otherwise the advert itself.
  const summary = job.alert_summary || job.description;

  return (
    <article
      className={`flex flex-col justify-between rounded-lg border bg-white p-5 transition hover:shadow-md ${
        job.saved ? 'border-amber-300' : 'border-gray-200'
      } ${job.closed ? 'opacity-70' : ''}`}
    >
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
              aria-label={job.saved ? `Unstar ${job.title}` : `Star ${job.title}`}
              title={job.saved ? 'Remove the star' : 'Star — keep it in Starred'}
              className="rounded p-1 text-gray-400 transition hover:text-amber-500"
            >
              <Star
                className={`h-4 w-4 ${job.saved ? 'fill-amber-400 text-amber-500' : ''}`}
                aria-hidden
              />
            </button>
            {job.dismissed ? (
              <button
                onClick={() => onDismiss(job)}
                aria-label={`Restore ${job.title}`}
                title="Put it back in the list"
                className="rounded p-1 text-gray-400 transition hover:text-gray-700"
              >
                <Undo2 className="h-4 w-4" aria-hidden />
              </button>
            ) : (
              <button
                onClick={() => onDismiss(job)}
                aria-label={`Dismiss ${job.title}`}
                title="Not interesting — remove it from the list"
                className="rounded p-1 text-gray-300 transition hover:text-gray-600"
              >
                <X className="h-4 w-4" aria-hidden />
              </button>
            )}
          </div>
        </div>

        <div className="mb-2 flex items-center gap-2 text-sm font-medium text-gray-700">
          <Building2 className="h-4 w-4 flex-shrink-0 text-gray-400" aria-hidden />
          <span className="truncate">{job.company}</span>
          {job.dismissed && !removedView && (
            <span className="chip bg-gray-100 text-gray-600" title="You removed this posting">
              Removed
            </span>
          )}
          {job.closed && (
            <span
              className="chip bg-gray-100 text-gray-600"
              title="The site no longer lists this posting"
            >
              No longer listed
            </span>
          )}
          {job.priority_excluded && (
            <span
              className="chip bg-gray-100 text-gray-600"
              title="The rules keep this posting from the model's review"
            >
              Excluded: {job.priority_excluded}
            </span>
          )}
        </div>

        <div className="mb-3 flex items-center gap-2 text-xs text-gray-500">
          <MapPin className="h-4 w-4 flex-shrink-0 text-gray-400" aria-hidden />
          <span className="truncate" title={job.location}>
            {job.location}
          </span>
        </div>

        {deadline && (
          <div className={`mb-2 flex items-center gap-2 text-xs ${deadline.tone}`}>
            <CalendarClock className="h-4 w-4 flex-shrink-0" aria-hidden />
            <span>{deadline.label}</span>
          </div>
        )}

        {job.priority_reasons.length > 0 && (
          <p className="mb-2 text-xs text-gray-500">{job.priority_reasons.join(' · ')}</p>
        )}

        {job.priority_flags.length > 0 && (
          <ul className="mb-3 flex flex-wrap gap-1.5" aria-label="Things to check">
            {job.priority_flags.map((flag) => (
              <li key={flag.text} className={`chip ${FLAG_TONES[flag.level]}`}>
                {flag.text}
              </li>
            ))}
          </ul>
        )}

        {job.priority_summary && (
          <p className="mb-2 text-xs font-medium leading-relaxed text-gray-800">
            {job.priority_summary}
          </p>
        )}

        <div className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-gray-500">
          {money && <span className="font-medium text-gray-700">{money}</span>}
          {job.seniority && <span>{job.seniority}</span>}
          {posted && (
            <span title={job.date_posted ? `Published ${shortDate(job.date_posted)}` : undefined}>
              Published {posted}
            </span>
          )}
        </div>

        {summary && (
          <button
            onClick={() => setExpanded((value) => !value)}
            className="mb-3 w-full text-left text-xs leading-relaxed text-gray-600 hover:text-gray-900"
            aria-expanded={expanded}
          >
            <span
              className={
                expanded ? 'block max-h-96 overflow-y-auto whitespace-pre-line' : 'line-clamp-3'
              }
            >
              {summary}
            </span>
          </button>
        )}

        {expanded && (job.priority_issues.length > 0 || job.priority_cv_tip || Object.keys(job.priority_facts).length > 0) && (
          <dl className="mb-3 space-y-2 rounded bg-gray-50 p-3 text-xs">
            {Object.keys(job.priority_facts).length > 0 && (
              <div className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5">
                {Object.entries(job.priority_facts).map(([label, value]) => (
                  <div key={label} className="contents">
                    <dt className="font-semibold text-gray-700">{label}</dt>
                    <dd className="text-gray-600">{value}</dd>
                  </div>
                ))}
              </div>
            )}
            {job.priority_issues.length > 0 && (
              <div>
                <dt className="font-semibold text-gray-700">Da verificare</dt>
                <dd>
                  <ul className="list-disc space-y-0.5 pl-4 text-gray-600">
                    {job.priority_issues.map((issue) => (
                      <li key={issue}>{issue}</li>
                    ))}
                  </ul>
                </dd>
              </div>
            )}
            {job.priority_cv_tip && (
              <div>
                <dt className="font-semibold text-gray-700">Nel CV, metti in evidenza</dt>
                <dd className="text-gray-600">{job.priority_cv_tip}</dd>
              </div>
            )}
          </dl>
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
          {job.priority !== null && (
            <span
              className={`chip font-semibold ${priorityTone(job.priority)}`}
              title={priorityTitle(job)}
            >
              Priority {job.priority}
              {job.priority_source === 'review' &&
                (job.priority_stale ? (
                  <span aria-label="reviewed against an older brief"> ↻</span>
                ) : (
                  <span aria-label="reviewed"> ✓</span>
                ))}
            </span>
          )}
          {job.scored ? (
            <>
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
                <span
                  className={`chip ${isPivot ? 'bg-amber-50 text-amber-700' : 'bg-gray-100 text-gray-600'}`}
                >
                  {isPivot ? 'Pivot' : 'Direct'}
                </span>
              )}
            </>
          ) : (
            job.category && job.category !== 'General' && (
              <span className="chip max-w-[14rem] truncate bg-gray-100 text-gray-600" title={job.category}>
                {job.category}
              </span>
            )
          )}
        </div>
        <a
          href={job.apply_url}
          target="_blank"
          rel="noreferrer noopener"
          className="flex flex-shrink-0 items-center gap-1 font-semibold text-brand hover:underline"
        >
          <span>Open</span>
          <ExternalLink className="h-3 w-3" aria-hidden />
        </a>
      </div>
    </article>
  );
}
