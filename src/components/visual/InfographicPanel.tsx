/**
 * InfographicPanel — self-contained Studio surface for the next-gen
 * infographic feature (L1 annotated charts + L2 structured diagrams).
 *
 * Deliberately self-contained: it does NOT go through the canvas generation
 * hook (owned by the Journey-Canvas agent) — it calls `POST /visual/infographic`
 * directly and renders the returned `json:infographic` artifact inline via the
 * registry. Mount it wherever `VisualPanel` mounts (LeftNavColumn / Studio).
 */
import { useState } from 'react';
import { api } from '../../services/api';
import { Button } from '../shared/Button';
import { LoadingSpinner } from '../shared/LoadingSpinner';
import { ArtifactRender } from '../artifact/RendererRegistry';
import type { Artifact } from '../../types/artifact';
import { InfographicLanePicker, type InfographicLane } from './InfographicLanePicker';

interface Props {
  notebookId: string;
  initialContent?: string;
}

interface InfographicResponse {
  artifact: Artifact;
  lane: string;
  routing?: { mode?: string; confidence?: number; stage?: string };
}

export const InfographicPanel = ({ notebookId, initialContent = '' }: Props) => {
  const [topic, setTopic] = useState(initialContent);
  const [lane, setLane] = useState<InfographicLane>('auto');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<InfographicResponse | null>(null);

  const handleGenerate = async () => {
    if (!topic.trim() && !notebookId) {
      setError('Describe what you want, or open a notebook with sources.');
      return;
    }
    setError(null);
    setLoading(true);
    setResult(null);
    try {
      const { data } = await api.post<InfographicResponse>('/visual/infographic', {
        notebook_id: notebookId,
        topic,
        lane,
        include_sources: true,
      });
      setResult(data);
    } catch (e: any) {
      setError(e?.response?.data?.detail || e?.message || 'Generation failed.');
    } finally {
      setLoading(false);
    }
  };

  const routing = result?.routing;

  return (
    <div className="space-y-3">
      <div>
        <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
          Describe the infographic
        </label>
        <textarea
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
          placeholder="Compare runtime retrieval vs compile-time RAG; or chart token growth across agent-loop iterations."
          rows={3}
          className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white placeholder-gray-400 text-sm resize-none focus:outline-none focus:ring-2 focus:ring-[#e0503a]/40"
        />
      </div>

      <div>
        <span className="block text-[11px] font-medium text-gray-500 dark:text-gray-400 mb-1">Lane</span>
        <InfographicLanePicker value={lane} onChange={setLane} />
      </div>

      <Button onClick={handleGenerate} disabled={loading} className="w-full">
        {loading ? (
          <span className="inline-flex items-center gap-2">
            <LoadingSpinner size="sm" />
            Generating…
          </span>
        ) : (
          'Generate infographic'
        )}
      </Button>

      {error && (
        <div className="bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400 p-3 rounded-lg text-sm">
          {error}
        </div>
      )}

      {result?.artifact && (
        <div className="space-y-1.5">
          <div className="text-[11px] text-gray-500 dark:text-gray-400">
            Lane {result.lane}
            {routing?.mode === 'auto' && typeof routing.confidence === 'number'
              ? ` · auto (${Math.round(routing.confidence * 100)}%${routing.stage ? `, stage ${routing.stage}` : ''})`
              : ' · override'}
          </div>
          <ArtifactRender artifact={result.artifact} context="canvas-full" />
        </div>
      )}
    </div>
  );
};

export default InfographicPanel;
