/** The card is where the dual-vector scores actually reach the user. */

import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { makeJob } from '../test/harness';
import { JobCard } from './JobCard';

const noop = () => {};

describe('JobCard', () => {
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

  it('reflects the saved state in the bookmark control', async () => {
    const onToggleSave = vi.fn();
    const { rerender } = render(
      <JobCard job={makeJob({ saved: false })} onToggleSave={onToggleSave} onDismiss={noop} />,
    );

    const save = screen.getByRole('button', { name: /^Save / });
    expect(save).toHaveAttribute('aria-pressed', 'false');
    await userEvent.click(save);
    expect(onToggleSave).toHaveBeenCalledTimes(1);

    rerender(<JobCard job={makeJob({ saved: true })} onToggleSave={onToggleSave} onDismiss={noop} />);
    expect(screen.getByRole('button', { name: /^Remove /})).toHaveAttribute('aria-pressed', 'true');
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
    const apply = screen.getByRole('link', { name: /Apply/ });
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
});
