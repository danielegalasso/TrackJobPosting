/** Creating an alert from the current filter state. */

import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '../lib/api';
import { renderWithQuery } from '../test/harness';
import { EMPTY_FILTERS, type Filters } from '../types';
import { AlertModal } from './AlertModal';

function open(filters: Partial<Filters> = {}) {
  const onClose = vi.fn();
  renderWithQuery(<AlertModal filters={{ ...EMPTY_FILTERS, ...filters }} onClose={onClose} />);
  return { onClose };
}

describe('AlertModal', () => {
  beforeEach(() => vi.restoreAllMocks());

  it('summarises what will be watched before saving', () => {
    open({ search: 'embedded', country: 'IT', company: 'Leonardo' });
    expect(screen.getByText('embedded')).toBeInTheDocument();
    expect(screen.getByText('Italy')).toBeInTheDocument();
    expect(screen.getByText('Leonardo')).toBeInTheDocument();
  });

  it('says "All" and "Anywhere" for filters the user left open', () => {
    open();
    expect(screen.getAllByText('All').length).toBe(2);
    expect(screen.getByText('Anywhere')).toBeInTheDocument();
  });

  it('will not submit without an email', () => {
    open();
    expect(screen.getByRole('button', { name: /Save alert/ })).toBeDisabled();
  });

  it('sends only the filters that are actually set', async () => {
    const create = vi.spyOn(api, 'createAlert').mockResolvedValue({
      id: 1, email: 'a@b.c', filters: {}, active: true, created_at: null, last_sent_at: null,
    });
    open({ search: 'embedded', country: 'EUROPE' });

    await userEvent.type(screen.getByLabelText(/Your email/), 'candidate@example.com');
    await userEvent.click(screen.getByRole('button', { name: /Save alert/ }));

    await waitFor(() => expect(create).toHaveBeenCalled());
    const [email, filters] = create.mock.calls[0];
    expect(email).toBe('candidate@example.com');
    expect(filters).toEqual({ search: 'embedded', country: 'EUROPE' });
    // Empty controls must not travel as blanks.
    expect(filters).not.toHaveProperty('company');
  });

  it('confirms the address the digests will go to', async () => {
    vi.spyOn(api, 'createAlert').mockResolvedValue({
      id: 1, email: 'a@b.c', filters: {}, active: true, created_at: null, last_sent_at: null,
    });
    open();
    await userEvent.type(screen.getByLabelText(/Your email/), 'candidate@example.com');
    await userEvent.click(screen.getByRole('button', { name: /Save alert/ }));

    expect(await screen.findByText('Alert created')).toBeInTheDocument();
    expect(screen.getByText('candidate@example.com')).toBeInTheDocument();
  });

  it('keeps the form open and shows why when the server rejects it', async () => {
    vi.spyOn(api, 'createAlert').mockRejectedValue(new Error('value is not a valid email address'));
    open();
    await userEvent.type(screen.getByLabelText(/Your email/), 'candidate@example.com');
    await userEvent.click(screen.getByRole('button', { name: /Save alert/ }));

    expect(await screen.findByRole('alert')).toHaveTextContent('not a valid email address');
    expect(screen.getByRole('button', { name: /Save alert/ })).toBeEnabled();
  });
});
