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
  /** False until the evaluator has judged it; the fit scores mean nothing before. */
  scored: boolean;
  /** Last day to apply (ISO date), when the source states one. */
  deadline: string | null;
  /** The advert as plain text, when the source carries it. */
  description: string;
  /** The source no longer lists this posting. */
  closed: boolean;
  created_at: string | null;
  /** Which to read first: the model's review where there is one, the rules otherwise. */
  priority: number | null;
  priority_source: 'review' | 'rules' | '';
  /** The review was made against a brief that has changed since. */
  priority_stale: boolean;
  priority_model: string;
  priority_tier: number | null;
  /** Points per criterion: the brief's own criteria when reviewed, the rules' otherwise. */
  priority_criteria: PriorityCriterion[];
  /** Bonuses and maluses the brief defines (location, salary, ...). */
  priority_adjustments: { name: string; points: number }[];
  priority_reasons: string[];
  /** "block": cannot apply; "warn": check first; "info": worth knowing. */
  priority_flags: PriorityFlag[];
  priority_summary: string;
  priority_issues: string[];
  priority_cv_tip: string;
  /** What the review read in the offer: deadline, location, languages, clearance. */
  priority_facts: Record<string, string>;
  /** Why the rules keep it from review (a visa, a senior role, ...), or "". */
  priority_excluded: string;
}

export interface PriorityCriterion {
  name: string;
  points: number;
  max: number;
}

export interface PriorityFlag {
  text: string;
  level: 'block' | 'warn' | 'info';
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
  /** "EUROPE" first, then each country, most postings first. */
  countries: { code: string; name: string; count: number }[];
}

/** The filter matrix state, kept in one object so it round-trips to alerts. */
export interface Filters {
  search: string;
  /** An ISO country code, "EUROPE" for any European country, or '' for anywhere. */
  country: string;
  company: string;
  sort: string;
  /** 'extract' (the per-site extractors), 'crawler', or '' for both. */
  origin: string;
  /** Postings the rules keep from review: '' hides them, 'include', 'only'. */
  excluded: string;
  saved_only: boolean;
  dismissed_only: boolean;
}

export const EMPTY_FILTERS: Filters = {
  search: '',
  country: '',
  company: '',
  sort: 'priority',
  origin: 'extract',
  excluded: '',
  saved_only: false,
  dismissed_only: false,
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
    security: SmtpSecurity;
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
    search_terms: string[];
    user_agent: string;
  };
  admin_email: string;
  resume_filename: string;
  interests: string[];
  targets: SpiderTarget[];
  source_types: string[];
  /** Server-derived: a secret exists on disk, though it is never sent here. */
  has_openrouter_key: boolean;
  has_smtp_password: boolean;
}

/** `none` means "omit the reasoning block", for models with no thinking mode. */
export type ReasoningEffort = 'none' | 'minimal' | 'low' | 'medium' | 'high' | 'xhigh' | 'max';

export const REASONING_EFFORTS: { value: ReasoningEffort; label: string; hint: string }[] = [
  { value: 'none', label: 'None', hint: 'No reasoning block sent' },
  { value: 'minimal', label: 'Minimal', hint: 'Fastest, cheapest' },
  { value: 'low', label: 'Low', hint: 'Light deliberation' },
  { value: 'medium', label: 'Medium', hint: 'Balanced' },
  { value: 'high', label: 'High', hint: 'Slower, more thorough' },
  { value: 'xhigh', label: 'Extra high', hint: "OpenRouter's highest documented level" },
  { value: 'max', label: 'Max', hint: 'Largest budget, highest cost' },
];

/** One entry of OpenRouter's catalogue, as the picker shows it. */
/** How the SMTP connection is encrypted, stated rather than inferred. */
export type SmtpSecurity = 'starttls' | 'ssl' | 'none';

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

/** What a review run covers: never-reviewed postings, those reviewed against
 *  an older brief (and the never-reviewed), those reviewed before the latest
 *  calibration, or every one again. */
export type ReviewMode = 'new' | 'stale' | 'calibration' | 'all';

export interface ReviewCounts {
  open: number;
  excluded: number;
  never: number;
  stale: number;
  current: number;
  /** Scored with this prompt, but before the latest calibration. */
  older_calibration: number;
  excluded_by_reason: Record<string, number>;
}

export interface ScoringSettings {
  model: string;
  reasoning_effort: ReasoningEffort;
  max_concurrency: number;
  has_key: boolean;
}

export interface SiteResult {
  name: string;
  status: 'OK' | 'CHECK' | 'INCOMPLETE' | 'EMPTY' | 'FAILED';
  counts: string;
  new: number | null;
  gone: number | null;
  seconds: number;
}

export interface CompanyChange {
  company: string;
  new: number;
  gone: number;
  new_excluded: number;
}

export interface SearchRun {
  running: boolean;
  phase: 'idle' | 'extracting' | 'importing' | 'ranking' | 'reviewing' | 'done' | 'failed' | 'stopped';
  started_at: string;
  finished_at: string;
  sites_total: number;
  sites: SiteResult[];
  changes: CompanyChange[];
  new_total: number;
  gone_total: number;
  reviewed: number;
  review_cost: number;
  error: string;
}

export interface ReviewRun {
  mode: string;
  model: string;
  effort: string;
  total: number;
  reviewed: number;
  failed: number;
  cost: number;
  calibration: number;
  prompt_tokens: number;
  cached_tokens: number;
  errors: string[];
  stopped: string;
  running: boolean;
  started_at: string;
  finished_at: string;
}

export interface SearchStatus {
  search: SearchRun;
  review: ReviewRun;
  counts: ReviewCounts;
  scoring: ScoringSettings;
  brief_present: boolean;
  calibration: CalibrationStatus;
}

/** Words a posting's title, company or location must not have. */
export interface ExclusionFilters {
  title: string[];
  company: string[];
  location: string[];
}

export interface ExclusionFiltersState extends ExclusionFilters {
  excluded: number;
  by_filter: Record<string, number>;
}

export interface CalibrationExample {
  action: 'starred' | 'removed';
  score: number;
  company: string;
  title: string;
  location: string;
}

export interface Calibration {
  version: number;
  created_at: string;
  model: string;
  cost: number;
  feedback_total: number;
  starred: number;
  removed: number;
  guidelines: string[];
  notes: string;
  examples: CalibrationExample[];
}

/** What the review learned from the postings starred and removed. */
export interface CalibrationStatus {
  settings: { auto: boolean; every: number };
  current: Calibration | null;
  /** Stars and removals since the latest calibration. */
  pending: number;
  running: boolean;
  error: string;
  finished_at: string;
  /** The text added to the review prompt. */
  section: string;
}

export interface Brief {
  text: string;
  instructions: string;
  digest: string;
}

export interface ReviewEstimate {
  count: number;
  model: string;
  per_posting: number | null;
  source: 'history' | 'list price' | 'unknown';
  samples: number;
  total: number | null;
}
