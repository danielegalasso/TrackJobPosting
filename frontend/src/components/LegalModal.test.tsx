/** The legal modal renders markdown into the DOM, so escaping matters. */

import { screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { renderWithQuery } from '../test/harness';
import { LegalModal } from './LegalModal';

function mockLegal(markdown: string) {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue({
    ok: true,
    text: async () => markdown,
  } as Response);
}

describe('LegalModal', () => {
  beforeEach(() => vi.restoreAllMocks());

  it('renders the document served by the backend', async () => {
    mockLegal('# Privacy Policy\n\nWe collect your **email address**.');
    renderWithQuery(<LegalModal document="privacy" onClose={vi.fn()} />);

    await waitFor(() => expect(screen.getByText(/We collect your/)).toBeInTheDocument());
    expect(screen.getByText('email address')).toBeInTheDocument();
  });

  it('renders headings, lists and rules', async () => {
    mockLegal('## Rights\n\n- Access\n- Erasure\n\n---\n\nContact us.');
    const { container } = renderWithQuery(<LegalModal document="privacy" onClose={vi.fn()} />);

    await waitFor(() => expect(screen.getByText('Rights')).toBeInTheDocument());
    expect(container.querySelectorAll('li')).toHaveLength(2);
    expect(container.querySelector('hr')).toBeInTheDocument();
  });

  it('escapes markup in the source instead of executing it', async () => {
    // The renderer writes into dangerouslySetInnerHTML; anything that reaches
    // it must be inert text, not live markup.
    mockLegal('# Policy\n\n<img src=x onerror="alert(1)"> and <script>alert(2)</script>');
    const { container } = renderWithQuery(<LegalModal document="terms" onClose={vi.fn()} />);

    await waitFor(() => expect(screen.getByText('Policy')).toBeInTheDocument());
    expect(container.querySelector('img')).toBeNull();
    expect(container.querySelector('script')).toBeNull();
    expect(container.textContent).toContain('<img src=x');
  });

  it('escapes markup that appears inside emphasis and code spans', async () => {
    mockLegal('Text **<b>bold</b>** and `<i>code</i>`.');
    const { container } = renderWithQuery(<LegalModal document="terms" onClose={vi.fn()} />);

    await waitFor(() => expect(container.textContent).toContain('bold'));
    expect(container.querySelector('b')).toBeNull();
    expect(container.querySelector('i')).toBeNull();
  });

  it('reports a document it cannot load rather than showing a blank panel', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({ ok: false, status: 404 } as Response);
    renderWithQuery(<LegalModal document="privacy" onClose={vi.fn()} />);
    await waitFor(() =>
      expect(screen.getByText(/could not be loaded/)).toBeInTheDocument(),
    );
  });

  it('is labelled as a dialog', async () => {
    mockLegal('# Terms');
    renderWithQuery(<LegalModal document="terms" onClose={vi.fn()} />);
    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    expect(dialog).toHaveAccessibleName('Terms and Conditions');
  });
});
