/** Dialog behaviour: escape, backdrop, focus restoration, scroll lock. */

import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { Modal } from './Modal';

describe('Modal', () => {
  it('closes on Escape', async () => {
    const onClose = vi.fn();
    render(<Modal title="Test" onClose={onClose}>body</Modal>);
    await userEvent.keyboard('{Escape}');
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('closes on a backdrop click but not on a click inside the panel', async () => {
    const onClose = vi.fn();
    const { container } = render(<Modal title="Test" onClose={onClose}>body</Modal>);

    await userEvent.click(screen.getByText('body'));
    expect(onClose).not.toHaveBeenCalled();

    await userEvent.click(container.firstElementChild as Element);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('closes from the explicit close control', async () => {
    const onClose = vi.fn();
    render(<Modal title="Test" onClose={onClose}>body</Modal>);
    await userEvent.click(screen.getByRole('button', { name: /Close dialog/ }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('locks page scrolling while open and restores it on close', () => {
    const { unmount } = render(<Modal title="Test" onClose={vi.fn()}>body</Modal>);
    expect(document.body.style.overflow).toBe('hidden');
    unmount();
    expect(document.body.style.overflow).not.toBe('hidden');
  });

  it('returns focus to whatever opened it', async () => {
    function Host() {
      const [open, setOpen] = useState(false);
      return (
        <>
          <button onClick={() => setOpen(true)}>Open</button>
          {open && <Modal title="Test" onClose={() => setOpen(false)}>body</Modal>}
        </>
      );
    }
    render(<Host />);
    const opener = screen.getByRole('button', { name: 'Open' });
    await userEvent.click(opener);
    await userEvent.keyboard('{Escape}');
    expect(document.activeElement).toBe(opener);
  });
});
