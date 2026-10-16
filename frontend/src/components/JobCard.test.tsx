/** The card is where the dual-vector scores actually reach the user. */

import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { makeJob } from '../test/harness';
import { describeDeadline, JobCard } from './JobCard';

const noop = () => {};

describe('JobCard', () => {
  it('shows the priority, why, and what rules a posting out', () => {
    render(
      <JobCard
        job={makeJob({
          priority: 93,
          priority_source: 'review',
          priority_criteria: [
            { name: 'Feasibility', points: 40, max: 40 },
            { name: 'Organisation weight', points: 20, max: 20 },
          ],
          priority_adjustments: [{ name: 'Location', points: 5 }],
          priority_reasons: ['Fascia 1', 'Settore: spazio'],
          priority_flags: [
            { text: 'Non candidabile: cittadinanza o clearance USA', level: 'block' },
            { text: 'Lingua: francese', level: 'warn' },
          ],
          priority_summary: 'Molto allineata a IRIS².',
        })}
        onToggleSave={noop}
        onDismiss={noop}
      />,
    );
    const badge = screen.getByText(/^Priority 93/);
    expect(badge).toHaveAttribute('title', expect.stringContaining('Feasibility: 40/40'));
    expect(badge).toHaveAttribute('title', expect.stringContaining('Location: +5'));
    expect(badge).toHaveAttribute('title', expect.stringContaining('reviewed against your brief'));
    expect(screen.getByText('Fascia 1 · Settore: spazio')).toBeInTheDocument();
    expect(screen.getByText('Non candidabile: cittadinanza o clearance USA')).toHaveClass('text-red-700');
    expect(screen.getByText('Molto allineata a IRIS².')).toBeInTheDocument();
  });

  it('opens the review: what to check, and what to stress in the CV', async () => {
    render(
      <JobCard
        job={makeJob({
          priority: 80,
          priority_source: 'review',
          description: 'The advert.',
          alert_summary: '',
          priority_issues: ['Verificare la clearance SECRET'],
          priority_cv_tip: 'Lo stage ESA.',
          priority_facts: { Languages: 'English C1' },
        })}
        onToggleSave={noop}
        onDismiss={noop}
      />,
    );
    expect(screen.queryByText('Verificare la clearance SECRET')).not.toBeInTheDocument();
    await userEvent.click(screen.getByText('The advert.'));
    expect(screen.getByText('Verificare la clearance SECRET')).toBeInTheDocument();
    expect(screen.getByText('Lo stage ESA.')).toBeInTheDocument();
    expect(screen.getByText('English C1')).toBeInTheDocument();
  });

  it('marks a review made with an older prompt, and an excluded posting', () => {
    render(
      <JobCard
        job={makeJob({
          priority: 70,
          priority_source: 'review',
          priority_stale: true,
          priority_excluded: 'Serve un visto',
        })}
        onToggleSave={noop}
        onDismiss={noop}
      />,
    );
    expect(screen.getByLabelText('reviewed against an older brief')).toBeInTheDocument();
    expect(screen.getByText(/^Priority 70/)).toHaveAttribute(
      'title',
      expect.stringContaining('an older version of your brief'),
    );
    expect(screen.getByText('Excluded: Serve un visto')).toBeInTheDocument();
  });

  it('has no priority badge before the posting is ranked', () => {
    render(<JobCard job={makeJob({ priority: null })} onToggleSave={noop} onDismiss={noop} />);
    expect(screen.queryByText(/^Priority/)).not.toBeInTheDocument();
  });

  it('shows both fit vectors as separate figures', () => {
    render(
      <JobCard
        job={makeJob({ experience_fit_score: 88, interest_fit_score: 72 })}
        onToggleSave={noop}
        onDismiss={noop}
      />,
    );
    expect(screen.getByText('Exp 88%')).toBeInTheDocument();
    expect(screen.getByText('Pivot 72%')).toBeInTheDocument();
  });

  it('labels a pivot opportunity distinctly from a direct match', () => {
    const { rerender } = render(
      <JobCard job={makeJob({ category_type: 'Direct Match' })} onToggleSave={noop} onDismiss={noop} />,
    );
    expect(screen.getByText('Direct')).toBeInTheDocument();

    rerender(
      <JobCard
        job={makeJob({ category_type: 'Pivot / Growth Opportunity' })}
        onToggleSave={noop}
        onDismiss={noop}
      />,
    );
    expect(screen.getByText('Pivot')).toBeInTheDocument();
  });

  it('hides the match badge when the role is unrelated', () => {
    render(<JobCard job={makeJob({ category_type: 'Unrelated' })} onToggleSave={noop} onDismiss={noop} />);
    expect(screen.queryByText('Direct')).not.toBeInTheDocument();
    expect(screen.queryByText('Pivot')).not.toBeInTheDocument();
  });

  it('formats compensation in the posting currency', () => {
    render(
      <JobCard
        job={makeJob({ amount: 140000, currency: 'EUR', rate: 'Yearly' })}
        onToggleSave={noop}
        onDismiss={noop}
      />,
    );
    expect(screen.getByText(/140,000/)).toBeInTheDocument();
    expect(screen.getByText(/yearly/)).toBeInTheDocument();
  });

  it('says nothing about pay when none is published', () => {
    render(<JobCard job={makeJob({ amount: 0 })} onToggleSave={noop} onDismiss={noop} />);
    expect(screen.queryByText(/yearly/)).not.toBeInTheDocument();
  });

  it('falls back to USD formatting for a nonsense currency code', () => {
    // The currency comes from a language model, so it can be malformed;
    // Intl.NumberFormat throws on an invalid code.
    expect(() =>
      render(
        <JobCard job={makeJob({ currency: 'not-a-code' })} onToggleSave={noop} onDismiss={noop} />,
      ),
    ).not.toThrow();
  });

  it('reflects the saved state in the star control', async () => {
    const onToggleSave = vi.fn();
    const { rerender } = render(
      <JobCard job={makeJob({ saved: false })} onToggleSave={onToggleSave} onDismiss={noop} />,
    );

    const save = screen.getByRole('button', { name: /^Star / });
    expect(save).toHaveAttribute('aria-pressed', 'false');
    await userEvent.click(save);
    expect(onToggleSave).toHaveBeenCalledTimes(1);

    rerender(<JobCard job={makeJob({ saved: true })} onToggleSave={onToggleSave} onDismiss={noop} />);
    expect(screen.getByRole('button', { name: /^Unstar / })).toHaveAttribute('aria-pressed', 'true');
  });

  it('reports a dismissal', async () => {
    const onDismiss = vi.fn();
    render(<JobCard job={makeJob()} onToggleSave={noop} onDismiss={onDismiss} />);
    await userEvent.click(screen.getByRole('button', { name: /^Dismiss / }));
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it('reveals transferable skills and gaps only once expanded', async () => {
    render(<JobCard job={makeJob()} onToggleSave={noop} onDismiss={noop} />);
    expect(screen.queryByText('You already bring')).not.toBeInTheDocument();

    await userEvent.click(screen.getByText('Close to the work you already ship.'));
    expect(screen.getByText('You already bring')).toBeInTheDocument();
    expect(screen.getByText('Embedded C · Python')).toBeInTheDocument();
    expect(screen.getByText('RTOS internals')).toBeInTheDocument();
  });

  it('opens the posting in a new tab without leaking the referrer', () => {
    render(<JobCard job={makeJob()} onToggleSave={noop} onDismiss={noop} />);
    const apply = screen.getByRole('link', { name: /Open/ });
    expect(apply).toHaveAttribute('href', 'https://example.com/jobs/1');
    expect(apply).toHaveAttribute('target', '_blank');
    expect(apply.getAttribute('rel')).toContain('noopener');
  });

  it('renders a posting with no summary or skills', () => {
    render(
      <JobCard
        job={makeJob({ alert_summary: '', transferable_skills: [], skills_to_learn: [] })}
        onToggleSave={noop}
        onDismiss={noop}
      />,
    );
    expect(screen.getByRole('heading', { level: 3 })).toBeInTheDocument();
  });

  it('does not render posting text as markup', () => {
    // Titles and summaries come from third-party feeds and a language model.
    render(
      <JobCard
        job={makeJob({ title: 'Engineer <img src=x onerror=alert(1)>' })}
        onToggleSave={noop}
        onDismiss={noop}
      />,
    );
    expect(screen.getByRole('heading', { level: 3 }).querySelector('img')).toBeNull();
    expect(screen.getByText(/Engineer <img/)).toBeInTheDocument();
  });

  it('shows how close the deadline is', () => {
    // The card counts from today's local date, so the test must too: an
    // ISO (UTC) date is a day behind just after midnight in Europe.
    const day = new Date();
    day.setDate(day.getDate() + 3);
    const soon = [day.getFullYear(), day.getMonth() + 1, day.getDate()]
      .map((part, index) => String(part).padStart(index ? 2 : 4, '0'))
      .join('-');
    render(<JobCard job={makeJob({ deadline: soon })} onToggleSave={noop} onDismiss={noop} />);
    expect(screen.getByText(/Closes in 3 days/)).toBeInTheDocument();
  });

  it('hides fit scores the evaluator has not produced yet', () => {
    render(
      <JobCard
        job={makeJob({ scored: false, alert_summary: '', description: 'The advert itself.' })}
        onToggleSave={noop}
        onDismiss={noop}
      />,
    );
    expect(screen.queryByText(/^Exp /)).not.toBeInTheDocument();
    expect(screen.queryByText(/^Pivot /)).not.toBeInTheDocument();
    expect(screen.getByText('The advert itself.')).toBeInTheDocument();
  });

  it('marks a posting its site no longer lists', () => {
    render(<JobCard job={makeJob({ closed: true })} onToggleSave={noop} onDismiss={noop} />);
    expect(screen.getByText('No longer listed')).toBeInTheDocument();
  });

  it('offers to restore rather than dismiss a removed posting', async () => {
    const onDismiss = vi.fn();
    const job = makeJob({ dismissed: true });
    render(<JobCard job={job} onToggleSave={noop} onDismiss={onDismiss} removedView />);
    expect(screen.queryByText('Removed')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Dismiss / })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /^Restore / }));
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it('marks a removed posting outside the Removed view', () => {
    const job = makeJob({ saved: true, dismissed: true });
    render(<JobCard job={job} onToggleSave={noop} onDismiss={noop} />);
    expect(screen.getByText('Removed')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^Restore / })).toBeInTheDocument();
  });
});

describe('describeDeadline', () => {
  const now = Date.UTC(2026, 9, 2);

  it('is silent without a deadline', () => {
    expect(describeDeadline(null, now)).toBeNull();
  });

  it('counts down and gets louder as the date nears', () => {
    expect(describeDeadline('2026-10-02', now)?.label).toMatch(/^Closes today/);
    expect(describeDeadline('2026-10-03', now)?.label).toMatch(/^Closes tomorrow/);
    expect(describeDeadline('2026-10-07', now)?.tone).toMatch(/red/);
    expect(describeDeadline('2026-10-20', now)?.tone).toMatch(/amber/);
    expect(describeDeadline('2026-12-31', now)?.label).toMatch(/^Closes /);
  });

  it('says when a deadline has passed', () => {
    expect(describeDeadline('2026-09-30', now)?.label).toMatch(/^Closed /);
  });
});
