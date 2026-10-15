import { useCallback, useEffect, useState } from 'react';
import { API_BASE_URL, localFetch } from '../../services/api';

/**
 * LLM Locker → "Test" for an embedding model (2026-10-14).
 *
 * Compares a candidate embedder with the live one WITHOUT switching — switching means
 * re-indexing every notebook, so it should be the last step, not the way to find out.
 * The backend (services/embedding_trial.py) runs each model in its own prompt formats on
 * the Evaluator's labelled questions, hidden among passages from the user's notebooks
 * (read-only), and reports pure vector retrieval: the embedder alone.
 */

type Scores = { dims: number; hit_at_1: number; hit_at_5: number; mrr: number; mean_rank: number | null; missed: string[] };
type ModelResult = {
  model: string;
  weights_dtype: string;
  weights_gb: number | null;
  passages_per_sec: number | null;
  profile: { source: string; loader: string; pooling: string; query_template: string; document_template: string };
  full: Scores;
  dims_256?: Scores;
  without_prompts?: Scores;
};
type Job = {
  running?: boolean;
  stage?: string;
  error?: string | null;
  result?: { live: ModelResult; candidate: ModelResult; questions: number; passages: number; distractors: number; note: string } | null;
};

const pct = (v: number) => `${Math.round(v * 100)}%`;
const short = (id: string) => id.split('/').pop() ?? id;

function verdict(live: ModelResult, cand: ModelResult): string {
  const d = cand.full.mrr - live.full.mrr;
  const speed = live.passages_per_sec && cand.passages_per_sec ? cand.passages_per_sec / live.passages_per_sec : null;
  const quality = Math.abs(d) < 0.03 ? 'about as accurate as' : d > 0 ? 'more accurate than' : 'less accurate than';
  const fast = speed && speed > 1.2 ? `, ${speed.toFixed(1)}× faster` : speed && speed < 0.8 ? `, ${(1 / speed).toFixed(1)}× slower` : '';
  return `${short(cand.model)} is ${quality} your current model on these questions${fast}.`;
}

export function EmbeddingTrialPanel({ model, onClose }: { model: string; onClose: () => void }) {
  const [job, setJob] = useState<Job>({});
  const [withDistractors, setWithDistractors] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const poll = useCallback(async () => {
    try {
      const r = await localFetch(`${API_BASE_URL}/evaluator/embedding-trial`);
      if (r.ok) setJob(await r.json());
    } catch { /* next tick */ }
  }, []);

  useEffect(() => { void poll(); }, [poll]);
  useEffect(() => {
    if (!job.running) return;
    const t = setInterval(() => void poll(), 1500);
    return () => clearInterval(t);
  }, [job.running, poll]);

  const start = async () => {
    setError(null);
    const r = await localFetch(`${API_BASE_URL}/evaluator/embedding-trial`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model, with_distractors: withDistractors }),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) { setError(data?.detail ?? 'Could not start the test.'); return; }
    if (!data.started) setError(data.reason ?? 'A test is already running.');
    setJob(data.job ?? {});
  };

  const res = job.result && job.result.candidate?.model === model ? job.result : null;
  const rows: Array<[string, ModelResult | undefined, Scores | undefined, string]> = res ? [
    [`${short(res.live.model)} (current)`, res.live, res.live.full, ''],
    [short(res.candidate.model), res.candidate, res.candidate.full, 'its own prompts'],
    ...(res.candidate.dims_256 ? [[`  …at 256 dims`, res.candidate, res.candidate.dims_256, 'smaller vectors'] as [string, ModelResult, Scores, string]] : []),
    ...(res.candidate.without_prompts ? [[`  …without its prompts`, res.candidate, res.candidate.without_prompts, 'what skipping them costs'] as [string, ModelResult, Scores, string]] : []),
  ] : [];

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div className="w-full max-w-2xl rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 p-5 shadow-xl"
           onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start justify-between gap-3">
          <div>
            <h3 className="text-base font-semibold text-gray-900 dark:text-gray-100">Test {short(model)}</h3>
            <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
              Compares it with your current embedding model without switching — nothing is re-indexed and
              your notebooks are only read. Each model uses its own prompt formats.
            </p>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 text-lg leading-none">×</button>
        </div>

        {!job.running && (
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-2 text-xs text-gray-600 dark:text-gray-300">
              <input type="checkbox" checked={withDistractors} onChange={(e) => setWithDistractors(e.target.checked)} />
              Hide the answers among 600 passages from my notebooks (harder, more realistic)
            </label>
            <button onClick={() => void start()}
                    className="ml-auto rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-700">
              {res ? 'Run again' : 'Run test'}
            </button>
          </div>
        )}

        {job.running && (
          <p className="mt-4 text-sm text-gray-600 dark:text-gray-300">⏳ {job.stage}… (about two minutes)</p>
        )}
        {(error || job.error) && <p className="mt-3 text-sm text-red-600 dark:text-red-400">{error || job.error}</p>}

        {res && !job.running && (
          <div className="mt-4">
            <p className="text-sm font-medium text-gray-800 dark:text-gray-100">{verdict(res.live, res.candidate)}</p>
            <table className="mt-3 w-full text-xs">
              <thead className="text-gray-500 dark:text-gray-400">
                <tr className="text-left">
                  <th className="py-1 font-medium">Model</th>
                  <th className="py-1 font-medium" title="The right passage ranked first">Top 1</th>
                  <th className="py-1 font-medium" title="The right passage in the first five">Top 5</th>
                  <th className="py-1 font-medium" title="Mean reciprocal rank — 1.0 means always first">MRR</th>
                  <th className="py-1 font-medium">Dims</th>
                  <th className="py-1 font-medium">Speed</th>
                </tr>
              </thead>
              <tbody className="text-gray-800 dark:text-gray-200">
                {rows.map(([label, m, s, hint]) => s && m && (
                  <tr key={label} className="border-t border-gray-100 dark:border-gray-800">
                    <td className="py-1.5 whitespace-pre" title={hint}>{label}</td>
                    <td>{pct(s.hit_at_1)}</td>
                    <td>{pct(s.hit_at_5)}</td>
                    <td>{s.mrr.toFixed(3)}</td>
                    <td>{s.dims}</td>
                    <td>{label.startsWith('  ') ? '' : m.passages_per_sec ? `${m.passages_per_sec}/s` : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="mt-3 text-[11px] text-gray-500 dark:text-gray-400">
              {res.questions} questions · {res.passages} passages ({res.distractors} from your notebooks) ·
              weights {res.candidate.weights_dtype}{res.candidate.weights_gb ? `, ${res.candidate.weights_gb} GB` : ''} ·
              {' '}query <code>{res.candidate.profile.query_template}</code> · document <code>{res.candidate.profile.document_template}</code>
            </p>
            <p className="mt-1 text-[11px] text-gray-500 dark:text-gray-400">{res.note}</p>
          </div>
        )}
      </div>
    </div>
  );
}
