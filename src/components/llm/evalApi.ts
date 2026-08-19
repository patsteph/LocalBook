// Typed client for the LLM Evaluator + sidecar endpoints (backend/api/evaluator.py).
// Shapes mirror the Python `to_dict()` contracts. Used by the in-app "Labs (LLM)"
// Evaluator + History tabs — the React home for what used to live in the
// health-portal HTML page.
import { API_BASE_URL, localFetch } from '../../services/api';

// ── Hardware + combo (GET /evaluator/hardware) ───────────────────────────────
export interface HardwareInfo {
  chip: string;
  total_cores?: number;
  performance_cores?: number;
  efficiency_cores?: number;
  gpu_cores?: number;
  memory_gb: number;
  metal_support?: boolean;
  os_version?: string;
  ollama_version?: string;
  tier: string; // "entry" | "mid" | "high" | "ultra"
  fingerprint?: string;
}
export interface ComboInfo {
  name?: string;
  main_model: string;
  fast_model: string;
  embedding_model?: string;
  embedding_dim?: number;
  vision_model?: string;
  tts_engine?: string;
  // Wave 9.6 — which engine serves each role ("ollama" | "mlx"), for the ⚡ MLX badge.
  main_engine?: string;
  fast_engine?: string;
  vision_engine?: string;
  // Friendly short display names (raw *_model stays for history/matching).
  main_model_display?: string;
  fast_model_display?: string;
  vision_model_display?: string;
}
export interface HardwareResponse {
  hardware: HardwareInfo;
  combo: ComboInfo;
}

// ── Historical runs list (GET /evaluator/results) — top-8 by score ───────────
export interface RunSummary {
  run_id: string;
  timestamp: string;
  file?: string;
  combo?: string;
  main_model: string;
  fast_model: string;
  hardware?: string;
  overall_score: number;
  overall_grade: string;
  total_time_seconds: number;
}

// ── Live progress (GET /evaluator/status) ────────────────────────────────────
export interface EvalStatus {
  running: boolean;
  phase: number;
  phase_name: string;
  total_phases: number;
  progress_percent: number;
  current_test: string;
  elapsed_seconds: number;
  results_so_far: Record<string, { score: number; grade: string }>;
  error?: string;
}

// ── Full result (GET /evaluator/results/{id} | /latest) ──────────────────────
export interface TestResult {
  test_id?: string;
  test_name: string;
  overall_score: number;
  sub_scores?: Record<string, unknown>;
  passed?: boolean;
  skipped?: boolean;
  skip_reason?: string;
  provider?: string;
  total_time_ms?: number;
  tokens_per_second?: number;
}
export interface CategoryResult {
  category: string;
  display_name: string;
  tests: TestResult[];
  score: number;
  grade: string;
  passed?: boolean;
  verdict?: 'pass' | 'degraded' | 'fail' | 'not_applicable' | string;
  warnings?: string[];
  total_time_ms?: number;
  skipped?: boolean;
  skip_reason?: string;
}
export interface FeatureParity {
  category?: string;
  feature: string;
  verdict: 'pass' | 'degraded' | 'fail' | 'not_applicable' | string;
  icon?: string;
  score?: number | null;
  note?: string;
}
export interface ProviderUsed {
  provider: string;
  backend_url?: string;
  model?: string;
  model_display?: string;   // Wave 9.6 — friendly short name
}
export interface PreflightCheck {
  name: string;
  status: 'pass' | 'warn' | 'fail' | string;
  message?: string;
  details?: Record<string, unknown>;
}
export interface EvalResult {
  run_id: string;
  timestamp: string;
  combo?: ComboInfo;
  hardware?: HardwareInfo;
  overall_score: number;
  overall_grade: string;
  avg_tokens_per_sec?: number;
  avg_ttft_ms?: number;
  total_run_time_seconds?: number;
  categories?: Record<string, CategoryResult>;
  category_scores?: Record<string, number>;
  warnings?: string[];
  providers_used?: Record<string, ProviderUsed>;
  skipped_categories?: { category: string; display_name: string; reason: string }[];
  feature_parity?: FeatureParity[];
  production_readiness?: {
    counts: { pass: number; degraded: number; fail: number; not_applicable: number };
    headline: string; // "ready" | "viable" | "risky"
  };
  preflight?: { checks: PreflightCheck[]; blocking_failure?: string | null };
}

// ── Sidecar (llama-server) ───────────────────────────────────────────────────
export interface SidecarStatus {
  running: boolean;
  owned: boolean;
  healthy: boolean;
  pid?: number | null;
  uptime_seconds?: number;
  binary_path?: string;
  model_path?: string;
  model_exists?: boolean;
  port?: number;
  last_error?: string;
}

// ── Fetch helpers ────────────────────────────────────────────────────────────
async function getJSON<T>(path: string): Promise<T> {
  const res = await localFetch(`${API_BASE_URL}${path}`);
  if (!res.ok) throw new Error(`${path} → ${res.status}`);
  return res.json() as Promise<T>;
}
async function postJSON<T>(path: string): Promise<T> {
  const res = await localFetch(`${API_BASE_URL}${path}`, { method: 'POST' });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch { /* ignore */ }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

// ── Run comparison (2026-08-19) ──────────────────────────────────────────────
// The /results/compare endpoint has existed with NO frontend caller. It is the surface the
// MLX-vs-Ollama A/B is judged on, so it needs one.
export interface MetricDelta { a: number | null; b: number | null; delta: number | null; pct: number | null; }

export interface CompareSide {
  run_id: string;
  combo?: Record<string, unknown>;
  hardware?: Record<string, unknown>;
  overall_score: number;
  overall_grade: string;
  category_scores: Record<string, number>;
  timestamp: string;
  engines?: Record<string, string>;
  engine_fallbacks?: number;
  perf?: Record<string, number | null>;
  memory?: Record<string, number | null>;
}

export interface CompareValidity {
  comparable: boolean;
  problems: string[];
  same_engines: boolean;
  engine_diff: Record<string, { a?: string; b?: string }>;
  same_hardware: boolean;
}

export interface CompareResponse {
  run_a: CompareSide;
  run_b: CompareSide;
  differences: Record<string, { score_a: number; score_b: number; delta: number }>;
  perf_deltas: Record<string, MetricDelta>;
  memory_deltas: Record<string, MetricDelta>;
  validity: CompareValidity;
}

export const evalApi = {
  compare: (runA: string, runB: string) =>
    postJSON<CompareResponse>(
      `/evaluator/results/compare?run_a=${encodeURIComponent(runA)}&run_b=${encodeURIComponent(runB)}`),
  getHardware: () => getJSON<HardwareResponse>('/evaluator/hardware'),
  getResults: () => getJSON<{ runs: RunSummary[]; count: number }>('/evaluator/results'),
  getResult: (runId: string) => getJSON<{ result: EvalResult }>(`/evaluator/results/${runId}`),
  getLatest: () => getJSON<{ result: EvalResult | null }>('/evaluator/results/latest'),
  getStatus: () => getJSON<EvalStatus>('/evaluator/status'),
  run: () => postJSON<{ status: string; message: string }>('/evaluator/run'),
  getSidecar: () => getJSON<SidecarStatus>('/evaluator/sidecar/status'),
  startSidecar: () => postJSON<SidecarStatus & { status: string; message: string }>('/evaluator/sidecar/start'),
  stopSidecar: () => postJSON<{ status: string; message: string }>('/evaluator/sidecar/stop'),
};

// Score → letter-grade band (mirrors the portal's getGradeClass thresholds).
export function gradeBand(score: number): 'a' | 'b' | 'c' | 'd' | 'f' {
  if (score >= 90) return 'a';
  if (score >= 80) return 'b';
  if (score >= 70) return 'c';
  if (score >= 60) return 'd';
  return 'f';
}

// Tailwind classes for a grade badge (light + dark), replacing the portal's
// .grade-a/b/c/d CSS. Semantic palette consistent with the Locker chips.
export const GRADE_BADGE: Record<string, string> = {
  a: 'bg-emerald-100 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-300',
  b: 'bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300',
  c: 'bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300',
  d: 'bg-orange-100 dark:bg-orange-900/30 text-orange-700 dark:text-orange-300',
  f: 'bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-300',
};
