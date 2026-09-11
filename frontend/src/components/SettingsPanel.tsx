import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, Loader2, Plus, Trash2, Upload, X } from 'lucide-react';
import { useEffect, useState } from 'react';
import { api } from '../lib/api';
import {
  REASONING_EFFORTS,
  type AlertSubscription,
  type AppConfig,
  type HandshakeResult,
  type ReasoningEffort,
  type SmtpSecurity,
  type SpiderTarget,
} from '../types';
import { ModelPicker } from './ModelPicker';

/** Mirrors the probe the backend sends; shown so the test is not a black box. */
const PROBE_PROMPT = 'Rispondi solo con: OK';

/** What the server sends in place of a stored secret. */
const MASK = '••••••••';

/** Render only the filters a subscription actually set. */
function describeFilters(filters: Record<string, unknown>): string {
  const active = Object.entries(filters).filter(
    ([, value]) => value !== null && value !== undefined && value !== '' && value !== false,
  );
  if (active.length === 0) return 'All new matches';
  return active.map(([key, value]) => `${key.replace(/_/g, ' ')}: ${value}`).join(' · ');
}

function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <section className="rounded-lg border border-gray-200 bg-white p-5">
      <h3 className="text-sm font-bold text-gray-900">{title}</h3>
      {hint && <p className="mt-0.5 text-xs text-gray-500">{hint}</p>}
      <div className="mt-4 space-y-3">{children}</div>
    </section>
  );
}

function Field({
  label,
  children,
  hint,
  htmlFor,
}: {
  label: string;
  children: React.ReactNode;
  hint?: string;
  /**
   * Set for composite widgets. A wrapping `<label>` forwards every click
   * inside it to its control, so a combobox's own dropdown items would
   * refocus the input and reopen the list instead of selecting. Pointing at
   * the input by id keeps the label association without that capture.
   */
  htmlFor?: string;
}) {
  const caption = <span className="mb-1 block text-xs font-semibold text-gray-700">{label}</span>;

  // The hint lives outside the <label> so it does not become part of the
  // control's accessible name, which should be the label alone.
  return (
    <div className="block">
      {htmlFor ? (
        <>
          <label htmlFor={htmlFor}>{caption}</label>
          {children}
        </>
      ) : (
        <label className="block">
          {caption}
          {children}
        </label>
      )}
      {hint && <span className="mt-1 block text-[11px] text-gray-400">{hint}</span>}
    </div>
  );
}

/**
 * A password input that never displays a stored secret.
 *
 * The server returns a mask rather than the real value, so rendering that
 * mask as the field's text showed eight bullets regardless of the real
 * password's length and looked like a value that had been typed. The field
 * is blank instead, with an explicit "saved" state beside it.
 */
function SecretField({
  id,
  label,
  hint,
  placeholder,
  value,
  hasStored,
  onChange,
  onClear,
}: {
  id: string;
  label: string;
  hint?: string;
  placeholder?: string;
  value: string;
  hasStored: boolean;
  onChange: (value: string) => void;
  onClear: () => void;
}) {
  const stored = hasStored && (value === MASK || value === '');
  const typed = value !== MASK && value !== '';

  // htmlFor rather than a wrapping label: the status row below holds a
  // button, and a label would forward clicks on it to the input.
  return (
    <Field label={label} hint={hint} htmlFor={id}>
      <input
        id={id}
        type="password"
        autoComplete="new-password"
        value={value === MASK ? '' : value}
        placeholder={stored ? 'Saved — type to replace' : placeholder}
        onChange={(event) => onChange(event.target.value || MASK)}
        className="control pr-3 font-mono"
      />
      <span className="mt-1 flex items-center gap-2 text-[11px]">
        {typed ? (
          <span className="text-amber-700">Not saved yet</span>
        ) : stored ? (
          <>
            <span className="flex items-center gap-1 text-emerald-700">
              <Check className="h-3 w-3" aria-hidden />
              Saved on this host
            </span>
            <button type="button" onClick={onClear} className="text-red-600 hover:underline">
              Forget
            </button>
          </>
        ) : (
          <span className="text-gray-400">Nothing saved</span>
        )}
      </span>
    </Field>
  );
}

function Handshake({ result }: { result?: HandshakeResult }) {
  if (!result) return null;
  return (
    <p
      role="status"
      className={`mt-2 flex items-start gap-1.5 rounded p-2 text-xs ${
        result.ok ? 'bg-emerald-50 text-emerald-800' : 'bg-red-50 text-red-800'
      }`}
    >
      {result.ok ? (
        <Check className="mt-0.5 h-3.5 w-3.5 flex-shrink-0" aria-hidden />
      ) : (
        <X className="mt-0.5 h-3.5 w-3.5 flex-shrink-0" aria-hidden />
      )}
      <span>{result.detail}</span>
    </p>
  );
}

export function SettingsPanel() {
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ['config'], queryFn: api.config });
  const { data: alerts } = useQuery({ queryKey: ['alerts'], queryFn: api.alerts });

  const [draft, setDraft] = useState<AppConfig | null>(null);
  const [saved, setSaved] = useState(false);
  const [saveError, setSaveError] = useState('');
  const [openRouterTest, setOpenRouterTest] = useState<HandshakeResult>();
  const [testingOpenRouter, setTestingOpenRouter] = useState(false);
  const [smtpTest, setSmtpTest] = useState<HandshakeResult>();
  const [testingSmtp, setTestingSmtp] = useState(false);
  const [resumeNote, setResumeNote] = useState('');

  useEffect(() => {
    if (data) setDraft(structuredClone(data));
  }, [data]);

  const save = useMutation({
    mutationFn: (config: AppConfig) => api.saveConfig(config),
    onSuccess: (result) => {
      setDraft(structuredClone(result));
      setSaveError('');
      setSaved(true);
      queryClient.invalidateQueries({ queryKey: ['config'] });
      queryClient.invalidateQueries({ queryKey: ['spider-status'] });
      window.setTimeout(() => setSaved(false), 2500);
    },
    onError: (error: Error) => setSaveError(error.message),
  });

  if (isLoading || !draft) {
    return (
      <div className="mx-auto max-w-3xl px-4 py-12 text-center text-sm text-gray-500 sm:px-6">
        <Loader2 className="mx-auto mb-2 h-5 w-5 animate-spin" aria-hidden />
        Loading settings…
      </div>
    );
  }

  const patch = (updater: (next: AppConfig) => void) => {
    setDraft((current) => {
      if (!current) return current;
      const next = structuredClone(current);
      updater(next);
      return next;
    });
  };

  const updateTarget = <K extends keyof SpiderTarget>(
    index: number,
    field: K,
    value: SpiderTarget[K],
  ) =>
    patch((next) => {
      next.targets[index][field] = value;
    });

  const uploadResume = async (file: File) => {
    try {
      const result = await api.uploadResume(file);
      setResumeNote(`Stored ${result.filename} — ${result.characters.toLocaleString()} characters extracted.`);
      queryClient.invalidateQueries({ queryKey: ['config'] });
    } catch (error) {
      setResumeNote(error instanceof Error ? error.message : 'Upload failed');
    }
  };

  return (
    <div className="mx-auto w-full max-w-3xl space-y-4 px-4 pb-16 pt-6 sm:px-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <h2 className="text-lg font-bold text-gray-900">Settings</h2>
          <p className="text-xs text-gray-500">
            Stored in <code className="rounded bg-gray-100 px-1">data/setup.json</code> on this host.
            Secrets are shown masked and are never sent back to the browser.
          </p>
        </div>
        <button
          onClick={() => save.mutate(draft)}
          disabled={save.isPending}
          className="flex flex-shrink-0 items-center gap-2 whitespace-nowrap rounded bg-brand px-5 py-2 text-sm font-semibold text-white transition hover:bg-brand-dark disabled:opacity-60"
        >
          {save.isPending && <Loader2 className="h-4 w-4 animate-spin" aria-hidden />}
          <span>{saved ? 'Saved' : 'Save settings'}</span>
        </button>
      </div>

      {saveError && (
        <p role="alert" className="rounded bg-red-50 p-3 text-xs text-red-800">
          {saveError}
        </p>
      )}

      <Section title="Candidate profile" hint="What the evaluator scores each posting against.">
        <Field
          label="Curriculum Vitae"
          hint="PDF, Markdown or plain text. Replacing the file deletes the previous one immediately."
        >
          <div className="flex flex-wrap items-center gap-3">
            <label className="flex cursor-pointer items-center gap-2 rounded border border-gray-300 px-3 py-2 text-xs font-medium text-gray-700 transition hover:bg-gray-50">
              <Upload className="h-3.5 w-3.5" aria-hidden />
              <span>Choose file</span>
              <input
                type="file"
                accept=".pdf,.md,.markdown,.txt"
                className="hidden"
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) void uploadResume(file);
                }}
              />
            </label>
            {draft.resume_filename ? (
              <span className="flex items-center gap-2 text-xs text-gray-600">
                <code className="rounded bg-gray-100 px-1.5 py-0.5">{draft.resume_filename}</code>
                <button
                  onClick={async () => {
                    await api.deleteResume();
                    setResumeNote('CV erased from this host.');
                    queryClient.invalidateQueries({ queryKey: ['config'] });
                  }}
                  className="text-red-600 hover:underline"
                >
                  Erase
                </button>
              </span>
            ) : (
              <span className="text-xs text-gray-400">No CV uploaded</span>
            )}
          </div>
          {resumeNote && <p className="mt-2 text-[11px] text-gray-500">{resumeNote}</p>}
        </Field>

        <Field
          label="Pivot interests"
          hint="One per line. These drive the second fit vector — where you want to go, not where you have been."
        >
          <textarea
            rows={4}
            value={draft.interests.join('\n')}
            onChange={(event) =>
              patch((next) => {
                next.interests = event.target.value.split('\n').filter((line) => line.trim());
              })
            }
            className="control pr-3 font-mono text-xs"
            placeholder={'Cloud security and detection engineering\nPlatform engineering roles'}
          />
        </Field>

        <div className="grid grid-cols-2 gap-3">
          <Field label="Experience threshold" hint="Alert when experience fit reaches this.">
            <input
              type="number"
              min={0}
              max={100}
              value={draft.scoring.experience_threshold}
              onChange={(event) =>
                patch((next) => {
                  next.scoring.experience_threshold = Number(event.target.value);
                })
              }
              className="control pr-3"
            />
          </Field>
          <Field label="Pivot threshold" hint="…or when pivot fit reaches this.">
            <input
              type="number"
              min={0}
              max={100}
              value={draft.scoring.interest_threshold}
              onChange={(event) =>
                patch((next) => {
                  next.scoring.interest_threshold = Number(event.target.value);
                })
              }
              className="control pr-3"
            />
          </Field>
        </div>
      </Section>

      <Section title="OpenRouter" hint="The inference gateway that scores each posting.">
        <SecretField
          id="openrouter-api-key"
          label="API key"
          placeholder="sk-or-v1-…"
          value={draft.openrouter.api_key}
          hasStored={draft.has_openrouter_key}
          onChange={(value) =>
            patch((next) => {
              next.openrouter.api_key = value;
            })
          }
          onClear={async () => {
            await api.clearSecret('openrouter_api_key');
            queryClient.invalidateQueries({ queryKey: ['config'] });
          }}
        />
        <Field
          label="Model"
          htmlFor="model-search"
          hint="Type to filter OpenRouter's catalogue; ↑ ↓ and Enter also work."
        >
          <ModelPicker
            value={draft.openrouter.model}
            reasoningRequested={draft.openrouter.reasoning_effort !== 'none'}
            onChange={(model) =>
              patch((next) => {
                next.openrouter.model = model;
              })
            }
          />
        </Field>

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <Field label="Reasoning effort" htmlFor="reasoning-effort" hint="Sent as reasoning.effort.">
            <select
              id="reasoning-effort"
              value={draft.openrouter.reasoning_effort}
              onChange={(event) =>
                patch((next) => {
                  next.openrouter.reasoning_effort = event.target.value as ReasoningEffort;
                })
              }
              className="control pr-3"
            >
              {REASONING_EFFORTS.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label} — {item.hint}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Max tokens" hint="Thinking spends this budget before the answer starts.">
            <input
              type="number"
              min={256}
              max={200000}
              step={1000}
              value={draft.openrouter.max_tokens}
              onChange={(event) =>
                patch((next) => {
                  next.openrouter.max_tokens = Number(event.target.value);
                })
              }
              className="control pr-3"
            />
          </Field>
          <Field label="Concurrent evaluations" hint="Faster; costs the same per posting.">
            <input
              type="number"
              min={1}
              max={32}
              value={draft.openrouter.max_concurrency}
              onChange={(event) =>
                patch((next) => {
                  next.openrouter.max_concurrency = Number(event.target.value);
                })
              }
              className="control pr-3"
            />
          </Field>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <button
            onClick={async () => {
              setOpenRouterTest(undefined);
              setTestingOpenRouter(true);
              try {
                // The draft goes with the request, so what is on screen is
                // what gets tested — no save required.
                setOpenRouterTest(await api.testOpenRouter(draft));
              } catch (error) {
                setOpenRouterTest({
                  ok: false,
                  detail: error instanceof Error ? error.message : 'Test failed',
                });
              } finally {
                setTestingOpenRouter(false);
              }
            }}
            disabled={testingOpenRouter}
            className="flex items-center gap-2 rounded border border-gray-300 px-3 py-1.5 text-xs font-medium text-gray-700 transition hover:bg-gray-50 disabled:opacity-60"
          >
            {testingOpenRouter && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />}
            <span>Test connection</span>
          </button>
          <span className="text-[11px] text-gray-400">
            Sends “{PROBE_PROMPT}” with the settings above and expects <code>OK</code> back.
            No need to save first.
          </span>
        </div>
        <Handshake result={openRouterTest} />
      </Section>

      <Section title="Email delivery" hint="SMTP relay used for alert digests.">
        <label className="flex items-center gap-2 text-xs text-gray-700">
          <input
            type="checkbox"
            checked={draft.email.enabled}
            onChange={(event) =>
              patch((next) => {
                next.email.enabled = event.target.checked;
              })
            }
            className="rounded border-gray-300 text-brand focus:ring-brand"
          />
          <span className="font-semibold">Send alert digests</span>
        </label>
        <div className="grid grid-cols-2 gap-3">
          <Field label="SMTP server">
            <input
              type="text"
              value={draft.email.smtp_server}
              onChange={(event) =>
                patch((next) => {
                  next.email.smtp_server = event.target.value;
                })
              }
              className="control pr-3"
              placeholder="smtp.gmail.com"
            />
          </Field>
          <Field label="Port" hint="587 STARTTLS · 465 implicit TLS · 25 plaintext · anything else is fine.">
            <input
              type="number"
              min={1}
              max={65535}
              value={draft.email.smtp_port}
              onChange={(event) =>
                patch((next) => {
                  const port = Number(event.target.value);
                  next.email.smtp_port = port;
                  // Follow the port only while it matches a well-known one,
                  // so a custom port keeps whatever mode was chosen.
                  if (port === 465) next.email.security = 'ssl';
                  else if (port === 587) next.email.security = 'starttls';
                  else if (port === 25) next.email.security = 'none';
                })
              }
              className="control pr-3"
            />
          </Field>
          <Field
            label="Encryption"
            htmlFor="smtp-security"
            hint="Stated outright, so a relay on any port works."
          >
            <select
              id="smtp-security"
              value={draft.email.security}
              onChange={(event) =>
                patch((next) => {
                  next.email.security = event.target.value as SmtpSecurity;
                  next.email.use_tls = event.target.value !== 'none';
                })
              }
              className="control pr-3"
            >
              <option value="starttls">STARTTLS (Gmail, port 587)</option>
              <option value="ssl">Implicit TLS (port 465)</option>
              <option value="none">None (plaintext)</option>
            </select>
          </Field>
          <Field label="Sender address">
            <input
              type="email"
              value={draft.email.sender_email}
              onChange={(event) =>
                patch((next) => {
                  next.email.sender_email = event.target.value;
                })
              }
              className="control pr-3"
            />
          </Field>
          <SecretField
            id="smtp-password"
            label="Password"
            hint="Gmail: a 16-character app password, not your account password. Spaces are fine."
            value={draft.email.sender_password}
            hasStored={draft.has_smtp_password}
            onChange={(value) =>
              patch((next) => {
                next.email.sender_password = value;
              })
            }
            onClear={async () => {
              await api.clearSecret('smtp_password');
              queryClient.invalidateQueries({ queryKey: ['config'] });
            }}
          />
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <button
            onClick={async () => {
              setSmtpTest(undefined);
              setTestingSmtp(true);
              try {
                setSmtpTest(await api.testSmtp(draft));
              } catch (error) {
                setSmtpTest({
                  ok: false,
                  detail: error instanceof Error ? error.message : 'Test failed',
                });
              } finally {
                setTestingSmtp(false);
              }
            }}
            disabled={testingSmtp}
            className="flex items-center gap-2 rounded border border-gray-300 px-3 py-1.5 text-xs font-medium text-gray-700 transition hover:bg-gray-50 disabled:opacity-60"
          >
            {testingSmtp && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />}
            <span>Test connection</span>
          </button>
          <span className="text-[11px] text-gray-400">
            Logs in with the settings above without sending anything, or saving them.
          </span>
        </div>
        <Handshake result={smtpTest} />
      </Section>

      <Section
        title="Career feeds"
        hint="Each target is one company's public job board. Polling is rate limited and identifies itself by user agent."
      >
        <div className="grid grid-cols-2 gap-3">
          <Field label="Run automatically">
            <label className="flex items-center gap-2 pt-1 text-xs text-gray-700">
              <input
                type="checkbox"
                checked={draft.spider.enabled}
                onChange={(event) =>
                  patch((next) => {
                    next.spider.enabled = event.target.checked;
                  })
                }
                className="rounded border-gray-300 text-brand focus:ring-brand"
              />
              <span>Enable scheduled indexing</span>
            </label>
          </Field>
          <Field label="Interval (minutes)" hint="Minimum 15. Be a polite guest on someone else's server.">
            <input
              type="number"
              min={15}
              value={draft.spider.interval_minutes}
              onChange={(event) =>
                patch((next) => {
                  next.spider.interval_minutes = Number(event.target.value);
                })
              }
              className="control pr-3"
            />
          </Field>
        </div>

        <div className="space-y-2">
          {draft.targets.map((target, index) => (
            <div key={index} className="flex flex-wrap items-center gap-2 rounded border border-gray-200 p-2">
              <input
                type="checkbox"
                checked={target.enabled}
                onChange={(event) => updateTarget(index, 'enabled', event.target.checked)}
                aria-label={`Enable ${target.company || 'target'}`}
                className="rounded border-gray-300 text-brand focus:ring-brand"
              />
              <input
                type="text"
                value={target.company}
                onChange={(event) => updateTarget(index, 'company', event.target.value)}
                placeholder="Company"
                aria-label="Company name"
                className="control w-36 flex-shrink-0 pr-3"
              />
              <select
                value={target.source_type}
                onChange={(event) => updateTarget(index, 'source_type', event.target.value)}
                aria-label="Source type"
                className="control w-32 flex-shrink-0 pr-3"
              >
                {draft.source_types.map((item) => (
                  <option key={item} value={item}>
                    {item}
                  </option>
                ))}
              </select>
              <input
                type="text"
                value={target.board_token}
                onChange={(event) => updateTarget(index, 'board_token', event.target.value)}
                placeholder="board token"
                aria-label="Board token"
                className="control min-w-0 flex-1 pr-3 font-mono text-xs"
              />
              <button
                onClick={() =>
                  patch((next) => {
                    next.targets.splice(index, 1);
                  })
                }
                aria-label={`Remove ${target.company || 'target'}`}
                className="rounded p-1.5 text-gray-400 transition hover:bg-red-50 hover:text-red-600"
              >
                <Trash2 className="h-4 w-4" aria-hidden />
              </button>
            </div>
          ))}
        </div>

        <button
          onClick={() =>
            patch((next) => {
              next.targets.push({
                company: '',
                source_type: next.source_types[0] ?? 'greenhouse',
                board_token: '',
                enabled: true,
              });
            })
          }
          className="flex items-center gap-1.5 rounded border border-dashed border-gray-300 px-3 py-2 text-xs font-medium text-gray-600 transition hover:bg-gray-50"
        >
          <Plus className="h-3.5 w-3.5" aria-hidden />
          <span>Add career feed</span>
        </button>
        <p className="text-[11px] leading-relaxed text-gray-400">
          The board token is the company handle in its job-board URL — for example{' '}
          <code className="rounded bg-gray-100 px-1">stripe</code> in
          job-boards.greenhouse.io/stripe.
        </p>
      </Section>

      <Section title="Email alerts" hint="Subscriptions created from the Alert button.">
        {alerts && alerts.length > 0 ? (
          <ul className="divide-y divide-gray-100">
            {alerts.map((alert: AlertSubscription) => (
              <li key={alert.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                <div className="min-w-0">
                  <p className="truncate text-xs font-semibold text-gray-800">{alert.email}</p>
                  <p className="truncate text-[11px] text-gray-500">
                    {describeFilters(alert.filters)}
                    {alert.last_sent_at && ` · last sent ${alert.last_sent_at}`}
                  </p>
                </div>
                <div className="flex flex-shrink-0 items-center gap-3 text-xs">
                  <button
                    onClick={async () => {
                      await (alert.active ? api.pauseAlert(alert.id) : api.resumeAlert(alert.id));
                      queryClient.invalidateQueries({ queryKey: ['alerts'] });
                    }}
                    className="text-gray-600 hover:underline"
                  >
                    {alert.active ? 'Pause' : 'Resume'}
                  </button>
                  <button
                    onClick={async () => {
                      await api.deleteAlert(alert.id);
                      queryClient.invalidateQueries({ queryKey: ['alerts'] });
                    }}
                    className="text-red-600 hover:underline"
                  >
                    Delete
                  </button>
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-xs text-gray-400">No alerts yet.</p>
        )}
      </Section>
    </div>
  );
}
