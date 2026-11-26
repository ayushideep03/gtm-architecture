// Thin typed client for the Oxygen API. All data shown in the dashboard comes from here.

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`/api/v1${path}`, {
    method,
    headers: body === undefined ? undefined : { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (res.status === 401) window.dispatchEvent(new Event("oxygen:unauthorized"));
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j);
    } catch {
      /* keep status text */
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

export const api = {
  get: <T,>(path: string) => request<T>("GET", path),
  post: <T,>(path: string, body: unknown = {}) => request<T>("POST", path, body),
  put: <T,>(path: string, body: unknown = {}) => request<T>("PUT", path, body),
  patch: <T,>(path: string, body: unknown = {}) => request<T>("PATCH", path, body),
};

/** Build a query string from defined, non-empty values. */
export function qs(params: Record<string, string | number | boolean | null | undefined>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : "";
}

export type Json = Record<string, any>;

export interface Overview {
  kpis: Record<string, number>;
  lead_status: Record<string, number>;
  engine: Json;
  autonomy: { paused: boolean };
}

export interface Prospect {
  id: string;
  status: string;
  score: number | null;
  source: string | null;
  sample: boolean;
  name: string | null;
  email: string | null;
  title: string | null;
  company: string | null;
  domain: string | null;
  industry: string | null;
  employees: number | null;
  last_decision: { action: string; status: string; engine: string; confidence: number | null; at: string } | null;
  next_work: { kind: string; due_at: string; reason: string } | null;
  pending_approvals: number;
  updated_at: string;
}

export interface Decision {
  id: string;
  lead_id: string;
  name?: string | null;
  company?: string | null;
  engine: string;
  model: string | null;
  action: string;
  capability: string | null;
  channel: string | null;
  reasoning: string | null;
  confidence: number | null;
  uncertainty: string | null;
  risks: string[] | null;
  expected_outcome: string | null;
  evidence_refs: string[] | null;
  parameters: Json | null;
  guardrails: Json | null;
  status: string;
  requires_approval: boolean;
  details: Json | null;
  task_id: string | null;
  task_status: string | null;
  task_result: Json | null;
  provider: string | null;
  created_at: string;
}

export interface Approval {
  id: string;
  task_id: string;
  lead_id: string | null;
  capability: string;
  status: string;
  original_payload: Json;
  final_payload: Json | null;
  edited: boolean;
  reasoning: string | null;
  evidence: Json | Json[] | null;
  reviewer_note: string | null;
  requested_at: string;
  expires_at: string | null;
  decided_at: string | null;
  decided_by: string | null;
  prospect: { name: string | null; email: string | null; title: string | null; company: string | null };
}

export interface ActivityEvent {
  id: string;
  type: string;
  source: string | null;
  at: string;
  lead_id: string | null;
  prospect: string | null;
  company: string | null;
  payload: Json;
}
