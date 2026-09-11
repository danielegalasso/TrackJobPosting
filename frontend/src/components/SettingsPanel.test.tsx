/** Settings: testing without saving, and how stored secrets are shown. */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '../lib/api';
import { renderWithQuery } from '../test/harness';
import type { AppConfig } from '../types';
import { SettingsPanel } from './SettingsPanel';

const MASK = '••••••••';

function config(overrides: Partial<AppConfig> = {}): AppConfig {
  return {
    openrouter: {
      api_key: '',
      model: 'google/gemini-2.5-flash',
      base_url: 'https://openrouter.ai/api/v1',
      max_concurrency: 4,
      temperature: 0.1,
      reasoning_effort: 'none',
      max_tokens: 10000,
      referer: 'http://localhost:8000',
      title: 'ACIDE-Watch Portal',
    },
    email: {
      enabled: false,
      smtp_server: '',
      smtp_port: 587,
      use_tls: true,
      security: 'starttls',
      sender_email: '',
      sender_password: '',
      sender_name: 'ACIDE-Watch',
    },
    scoring: { experience_threshold: 75, interest_threshold: 75 },
    spider: {
      enabled: false,
      interval_minutes: 360,
      request_delay_seconds: 1.5,
      max_jobs_per_source: 120,
      user_agent: 'ACIDE-Watch/2.0',
    },
    admin_email: '',
    resume_filename: '',
    interests: [],
    targets: [],
    source_types: ['ashby', 'greenhouse', 'lever'],
    has_openrouter_key: false,
    has_smtp_password: false,
    ...overrides,
  };
}

function stub(overrides: Partial<AppConfig> = {}) {
  vi.spyOn(api, 'config').mockResolvedValue(config(overrides));
  vi.spyOn(api, 'alerts').mockResolvedValue([]);
  vi.spyOn(api, 'models').mockResolvedValue([]);
}

describe('SettingsPanel', () => {
  beforeEach(() => vi.restoreAllMocks());

  it('tests OpenRouter with what is on screen, without saving', async () => {
    stub();
    const test = vi.spyOn(api, 'testOpenRouter').mockResolvedValue({ ok: true, detail: 'replied OK' });
    const save = vi.spyOn(api, 'saveConfig');
    renderWithQuery(<SettingsPanel />);
    await screen.findByText('OpenRouter');

    await userEvent.type(screen.getByLabelText('API key'), 'sk-typed-now');
    const section = screen.getByText('OpenRouter').closest('section')!;
    await userEvent.click(within(section).getByRole('button', { name: /Test connection/ }));

    await waitFor(() => expect(test).toHaveBeenCalled());
    // The typed key travels with the request…
    expect(test.mock.calls[0][0]?.openrouter.api_key).toBe('sk-typed-now');
    // …and nothing is written to disk.
    expect(save).not.toHaveBeenCalled();
  });

  it('tests SMTP with what is on screen, without saving', async () => {
    stub();
    const test = vi.spyOn(api, 'testSmtp').mockResolvedValue({ ok: true, detail: 'Connected' });
    const save = vi.spyOn(api, 'saveConfig');
    renderWithQuery(<SettingsPanel />);
    await screen.findByText('Email delivery');

    await userEvent.type(screen.getByLabelText('SMTP server'), 'smtp.gmail.com');
    await userEvent.type(screen.getByLabelText('Password'), 'abcd efgh ijkl mnop');

    const section = screen.getByText('Email delivery').closest('section')!;
    await userEvent.click(within(section).getByRole('button', { name: /Test connection/ }));

    await waitFor(() => expect(test).toHaveBeenCalled());
    expect(test.mock.calls[0][0]?.email.sender_password).toBe('abcd efgh ijkl mnop');
    expect(test.mock.calls[0][0]?.email.smtp_server).toBe('smtp.gmail.com');
    expect(save).not.toHaveBeenCalled();
  });

  it('shows a failing test without throwing the panel away', async () => {
    stub();
    vi.spyOn(api, 'testSmtp').mockResolvedValue({
      ok: false,
      detail: "could not resolve the SMTP server 'smpt.gmail.com' — did you mean smtp.gmail.com?",
    });
    renderWithQuery(<SettingsPanel />);
    await screen.findByText('Email delivery');

    const section = screen.getByText('Email delivery').closest('section')!;
    await userEvent.click(within(section).getByRole('button', { name: /Test connection/ }));
    expect(await screen.findByText(/did you mean smtp.gmail.com/)).toBeInTheDocument();
  });

  it('leaves a stored secret blank rather than showing bullets as a value', async () => {
    // The server returns a mask; rendering it as text looked like a typed
    // password of the wrong length.
    stub({ has_smtp_password: true, email: { ...config().email, sender_password: MASK } });
    renderWithQuery(<SettingsPanel />);
    await screen.findByText('Email delivery');

    const password = screen.getByLabelText('Password') as HTMLInputElement;
    expect(password.value).toBe('');
    expect(password.placeholder).toBe('Saved — type to replace');
    expect(screen.getByText('Saved on this host')).toBeInTheDocument();
  });

  it('says when nothing is saved yet', async () => {
    stub();
    renderWithQuery(<SettingsPanel />);
    await screen.findByText('OpenRouter');
    expect(screen.getAllByText('Nothing saved').length).toBeGreaterThan(0);
  });

  it('flags a secret that has been typed but not yet saved', async () => {
    stub();
    renderWithQuery(<SettingsPanel />);
    await screen.findByText('OpenRouter');

    await userEvent.type(screen.getByLabelText('API key'), 'sk-new');
    expect(await screen.findByText('Not saved yet')).toBeInTheDocument();
  });

  it('can forget a stored secret', async () => {
    stub({ has_openrouter_key: true, openrouter: { ...config().openrouter, api_key: MASK } });
    const clear = vi.spyOn(api, 'clearSecret').mockResolvedValue({ cleared: true });
    renderWithQuery(<SettingsPanel />);
    await screen.findByText('OpenRouter');

    await userEvent.click(screen.getAllByRole('button', { name: 'Forget' })[0]);
    await waitFor(() => expect(clear).toHaveBeenCalledWith('openrouter_api_key'));
  });

  it('follows a well-known port with the matching encryption mode', async () => {
    stub();
    const save = vi.spyOn(api, 'saveConfig').mockImplementation(async (c) => c as AppConfig);
    renderWithQuery(<SettingsPanel />);
    await screen.findByText('Email delivery');

    await userEvent.clear(screen.getByLabelText('Port'));
    await userEvent.type(screen.getByLabelText('Port'), '465');
    await userEvent.click(screen.getByRole('button', { name: /Save settings/ }));

    await waitFor(() => expect(save).toHaveBeenCalled());
    const sent = save.mock.calls[0][0] as AppConfig;
    expect(sent.email.smtp_port).toBe(465);
    expect(sent.email.security).toBe('ssl');
  });

  it('accepts a custom port without forcing a listed one', async () => {
    // The port used to be a three-option select, which could not express
    // a relay on e.g. 2525.
    stub();
    const save = vi.spyOn(api, 'saveConfig').mockImplementation(async (c) => c as AppConfig);
    renderWithQuery(<SettingsPanel />);
    await screen.findByText('Email delivery');

    await userEvent.clear(screen.getByLabelText('Port'));
    await userEvent.type(screen.getByLabelText('Port'), '2525');
    await userEvent.selectOptions(screen.getByLabelText('Encryption'), 'ssl');
    await userEvent.click(screen.getByRole('button', { name: /Save settings/ }));

    await waitFor(() => expect(save).toHaveBeenCalled());
    const sent = save.mock.calls[0][0] as AppConfig;
    expect(sent.email.smtp_port).toBe(2525);
    expect(sent.email.security).toBe('ssl');
  });
});
