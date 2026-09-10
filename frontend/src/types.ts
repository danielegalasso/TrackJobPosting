/** Shapes mirroring the backend's Pydantic models. */

export interface Job {
  id: string;
  title: string;
  company: string;
  location: string;
  seniority: string;
  category: string;
  years_experience_min: number;
  date_posted: string | null;
  rate: string;
  currency: string;
  amount: number;
  experience_fit_score: number;
  interest_fit_score: number;
  category_type: 'Direct Match' | 'Pivot / Growth Opportunity' | 'Unrelated';
  transferable_skills: string[];
  skills_to_learn: string[];
  alert_summary: string;
  apply_url: string;
  source_type: string;
  saved: boolean;
  dismissed: boolean;
  created_at: string | null;
}

export interface JobPage {
  items: Job[];
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
}

export interface JobFacets {
  categories: string[];
  companies: string[];
  seniorities: string[];
  currencies: string[];
}

/** The filter matrix state, kept in one object so it round-trips to alerts. */
export interface Filters {
  search: string;
  location: string;
  remote: string;
  category: string;
  years_experience: string;
  seniority: string;
  posted_within: string;
  company: string;
  rate: string;
  currency: string;
  min_amount: string;
  match_posting_currency: boolean;
  category_type: string;
  sort: string;
  saved_only: boolean;
}

export const EMPTY_FILTERS: Filters = {
  search: '',
  location: '',
  remote: '',
  category: '',
  years_experience: '',
  seniority: '',
  posted_within: '',
  company: '',
  rate: 'Yearly',
  currency: 'USD',
  min_amount: '',
  match_posting_currency: false,
  category_type: '',
  sort: 'fit',
  saved_only: false,
};

export interface AlertSubscription {
  id: number;
  email: string;
  filters: Record<string, unknown>;
  active: boolean;
  created_at: string | null;
  last_sent_at: string | null;
}

export interface SpiderTarget {
  company: string;
  source_type: string;
  board_token: string;
  enabled: boolean;
}

export interface SpiderStatus {
  running: boolean;
  scheduler_active: boolean;
  spider_enabled: boolean;
  interval_minutes: number;
  next_run_at: string | null;
  last_run: {
    started_at: string;
    finished_at: string | null;
    sources_polled: number;
    postings_seen: number;
    postings_new: number;
    postings_scored: number;
    alerts_sent: number;
    errors: string[];
    running: boolean;
  };
  targets: SpiderTarget[];
}

export interface LogLine {
  ts: string;
  level: 'info' | 'warn' | 'error' | 'success';
  message: string;
}

export interface AppConfig {
  openrouter: {
    api_key: string;
    model: string;
    base_url: string;
    max_concurrency: number;
    temperature: number;
    reasoning_effort: ReasoningEffort;
    max_tokens: number;
    referer: string;
    title: string;
  };
  email: {
    enabled: boolean;
    smtp_server: string;
    smtp_port: number;
    use_tls: boolean;
    sender_email: string;
    sender_password: string;
    sender_name: string;
  };
  scoring: { experience_threshold: number; interest_threshold: number };
  spider: {
    enabled: boolean;
    interval_minutes: number;
    request_delay_seconds: number;
    max_jobs_per_source: number;
    user_agent: string;
  };
  admin_email: string;
  resume_filename: string;
  interests: string[];
  targets: SpiderTarget[];
  source_types: string[];
}

/** `none` means "omit the reasoning block", for models with no thinking mode. */
export type ReasoningEffort = 'none' | 'minimal' | 'low' | 'medium' | 'high' | 'max';

export const REASONING_EFFORTS: { value: ReasoningEffort; label: string; hint: string }[] = [
  { value: 'none', label: 'None', hint: 'No reasoning block sent' },
  { value: 'minimal', label: 'Minimal', hint: 'Fastest, cheapest' },
  { value: 'low', label: 'Low', hint: 'Light deliberation' },
  { value: 'medium', label: 'Medium', hint: 'Balanced' },
  { value: 'high', label: 'High', hint: 'Slower, more thorough' },
  { value: 'max', label: 'Max', hint: 'Largest budget, highest cost' },
];

/** One entry of OpenRouter's catalogue, as the picker shows it. */
export interface ModelInfo {
  id: string;
  name: string;
  context_length: number | null;
  prompt_price: number | null;
  completion_price: number | null;
  supports_reasoning: boolean;
}

export interface HandshakeResult {
  ok: boolean;
  detail: string;
}
