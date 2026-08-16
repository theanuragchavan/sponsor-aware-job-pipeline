/**
 * Typed client over the FastAPI surface.
 *
 * Hand-written rather than generated: `openapi-typescript` plus fetch is the
 * plan for the full route set, but the shapes below are the only ones the
 * review flow touches and a 60-line file beats a build step for now.
 *
 * The important part is `ApiError`. Every failure from the API arrives in one
 * envelope carrying the validations that were run, so the UI has exactly one
 * error renderer and a refused action can show *which check said no*.
 */

export type Validation = {
  rule: string;
  result: "pass" | "fail";
  detail: string;
  overridable?: boolean;
  overridden?: boolean;
  override_reason?: string;
};

export type Suggestion = {
  register_name: string;
  rating: string;
  why: string;
  rejected: boolean;
};

export type ReviewItem = {
  key: string;
  company: string;
  rows: number;
  tiers: string[];
  tech_titles: string[];
  priority: boolean;
  suggestions: Suggestion[];
};

export type Change = {
  entity: string;
  title: string;
  field: string;
  before: string;
  after: string;
};

export type ActionResponse = {
  applied: boolean;
  replayed: boolean;
  validations: Validation[];
  changes: Change[];
  effects: {
    company?: string;
    register_name?: string;
    rating?: string;
    rows_changed?: number;
    shortlist_delta?: number;
    job_ids?: string[];
  };
  log_entry_id: string | null;
};

export type Summary = {
  tracked: number;
  ready_to_apply: number;
  applied: number;
  awaiting_reply: number;
  filtered_out: number;
  filtered_no_sponsor: number;
  needs_a_name_decision: number;
};

export type Job = {
  id: string;
  title: string;
  company: string;
  location: string;
  tier: string;
  status: string;
  source: string;
  posted_date: string;
  age_days: number | null;
  salary_max: string;
  sponsor_match: string;
  sponsor_rating: string;
  redirect_url: string;
  score: number;
  eligible: boolean;
  disqualified_reason: string | null;
  date_applied?: string;
  applied_via?: string;
};

export type JobPage = { total: number; items: Job[] };

export type Meta = {
  mode: "local" | "demo";
  actor: { id: string; display_name: string };
  dataset: { rows: number; companies: number; aliases: number };
  register: { entries: number; route: string };
  capabilities: { liveness: boolean; writeback: boolean };
};

export type LogEntry = {
  seq: number;
  id: string;
  ts: string;
  type: string;
  actor: { id: string; display_name: string };
  entity: { kind: string; id: string };
  input: Record<string, unknown>;
  validations: Validation[];
  rationale: string;
  effects: Record<string, unknown>;
};

export class ApiError extends Error {
  code: string;
  validations: Validation[];
  retryable: boolean;

  constructor(code: string, message: string, validations: Validation[],
              retryable: boolean) {
    super(message);
    this.code = code;
    this.validations = validations;
    this.retryable = retryable;
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    let body: any = null;
    try { body = await res.json(); } catch { /* non-JSON error page */ }
    const err = body?.error;
    throw new ApiError(
      err?.code ?? "http_error",
      err?.message ?? `${res.status} ${res.statusText}`,
      err?.validations ?? [],
      err?.retryable ?? false,
    );
  }
  return res.json() as Promise<T>;
}

const post = <T>(path: string, body: unknown) =>
  call<T>(path, { method: "POST", body: JSON.stringify(body) });

export type ResolveInput = {
  company: string;
  register_name: string;
  rationale: string;
  source_rule?: string;
  action_id?: string;
  supersede?: boolean;
  acknowledge_agency?: boolean;
};

export type LogApplicationInput = {
  job_id: string;
  applied_via?: string;
  date_applied?: string;
  notes?: string;
  override_reason?: string;
  action_id?: string;
};

export const api = {
  meta: () => call<Meta>("/meta"),
  summary: () => call<Summary>("/summary"),
  jobs: (q: string) => call<JobPage>(`/jobs?${q}`),
  logApplication: (input: LogApplicationInput) =>
    post<ActionResponse>("/actions/log-application", input),
  review: () => call<ReviewItem[]>("/review"),
  audit: (limit = 50) => call<LogEntry[]>(`/audit?limit=${limit}`),
  verify: () => call<{
    chain_ok: boolean; count: number; projection_matches_log: boolean;
    unattributed_aliases: string[];
  }>("/audit/verify"),
  previewResolve: (input: ResolveInput) =>
    post<ActionResponse>("/actions/resolve-company/preview", input),
  resolve: (input: ResolveInput) =>
    post<ActionResponse>("/actions/resolve-company", input),
};
