/** The model picker: filtering, keyboard use, and the reasoning warning. */

import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '../lib/api';
import { renderWithQuery } from '../test/harness';
import type { ModelInfo } from '../types';
import { ModelPicker } from './ModelPicker';

function model(id: string, overrides: Partial<ModelInfo> = {}): ModelInfo {
  return {
    id,
    name: id.replace('/', ': '),
    context_length: 128000,
    prompt_price: 0.0000002,
    completion_price: 0.0000012,
    supports_reasoning: false,
    ...overrides,
  };
}

const CATALOGUE = [
  model('openai/gpt-5.6-luna', { supports_reasoning: true, context_length: 400000 }),
  model('openai/gpt-5.6-mini'),
  model('google/gemini-2.5-flash', { context_length: 1048576 }),
  model('anthropic/claude-opus-5', { supports_reasoning: true }),
];

function open(props: Partial<Parameters<typeof ModelPicker>[0]> = {}) {
  const onChange = vi.fn();
  renderWithQuery(
    <ModelPicker
      value="google/gemini-2.5-flash"
      onChange={onChange}
      reasoningRequested={false}
      {...props}
    />,
  );
  return { onChange };
}

describe('ModelPicker', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, 'models').mockResolvedValue(CATALOGUE);
  });

  it('fetches the catalogue once and lists it on focus', async () => {
    open();
    await userEvent.click(screen.getByRole('combobox'));
    await waitFor(() => expect(screen.getAllByRole('option')).toHaveLength(4));
    expect(api.models).toHaveBeenCalledTimes(1);
  });

  it('filters by substring as the user types, without refetching', async () => {
    open();
    const input = screen.getByRole('combobox');
    await userEvent.click(input);
    await waitFor(() => expect(screen.getAllByRole('option')).toHaveLength(4));

    await userEvent.type(input, 'gpt');
    await waitFor(() => expect(screen.getAllByRole('option')).toHaveLength(2));
    // Filtering is local, so typing never costs a request.
    expect(api.models).toHaveBeenCalledTimes(1);
  });

  it('narrows on every term rather than widening', async () => {
    open();
    await userEvent.type(screen.getByRole('combobox'), 'gpt mini');
    await waitFor(() => expect(screen.getAllByRole('option')).toHaveLength(1));
    expect(screen.getByText('openai/gpt-5.6-mini')).toBeInTheDocument();
  });

  it('matches the display name, not just the id', async () => {
    open();
    await userEvent.type(screen.getByRole('combobox'), 'anthropic');
    await waitFor(() => expect(screen.getAllByRole('option')).toHaveLength(1));
  });

  it('is case-insensitive', async () => {
    open();
    await userEvent.type(screen.getByRole('combobox'), 'GEMINI');
    await waitFor(() => expect(screen.getAllByRole('option')).toHaveLength(1));
  });

  it('says so when nothing matches', async () => {
    open();
    await userEvent.type(screen.getByRole('combobox'), 'zzzz');
    expect(await screen.findByText(/No model matches/)).toBeInTheDocument();
  });

  it('reports the chosen id to the caller', async () => {
    const { onChange } = open();
    await userEvent.click(screen.getByRole('combobox'));
    await userEvent.click(await screen.findByText('anthropic/claude-opus-5'));
    expect(onChange).toHaveBeenCalledWith('anthropic/claude-opus-5');
  });

  it('selects with the keyboard', async () => {
    const { onChange } = open();
    const input = screen.getByRole('combobox');
    await userEvent.click(input);
    await waitFor(() => expect(screen.getAllByRole('option')).toHaveLength(4));

    await userEvent.keyboard('{ArrowDown}{Enter}');
    expect(onChange).toHaveBeenCalledWith('openai/gpt-5.6-mini');
  });

  it('closes on Escape without choosing anything', async () => {
    const { onChange } = open();
    await userEvent.click(screen.getByRole('combobox'));
    await waitFor(() => expect(screen.getAllByRole('option')).toHaveLength(4));

    await userEvent.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryAllByRole('option')).toHaveLength(0));
    expect(onChange).not.toHaveBeenCalled();
  });

  it('always shows what is currently saved', () => {
    open({ value: 'google/gemini-2.5-flash' });
    expect(screen.getByText('google/gemini-2.5-flash')).toBeInTheDocument();
  });

  it('warns when the chosen model cannot honour the requested effort', async () => {
    open({ value: 'google/gemini-2.5-flash', reasoningRequested: true });
    expect(await screen.findByRole('status')).toHaveTextContent(
      /does not advertise reasoning support/,
    );
  });

  it('stays quiet when the model does support reasoning', async () => {
    open({ value: 'anthropic/claude-opus-5', reasoningRequested: true });
    await waitFor(() => expect(screen.getByText('anthropic/claude-opus-5')).toBeInTheDocument());
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it('stays usable when the catalogue cannot be loaded', async () => {
    vi.spyOn(api, 'models').mockRejectedValue(new Error('OpenRouter unreachable'));
    open({ value: 'openai/gpt-5.6-luna' });
    expect(await screen.findByRole('alert')).toHaveTextContent('OpenRouter unreachable');
    // The saved id is still shown, so the setting is not lost.
    expect(screen.getByText('openai/gpt-5.6-luna')).toBeInTheDocument();
  });
});
