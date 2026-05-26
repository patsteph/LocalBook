/**
 * VisualPanel — left-nav Studio surface for visual generation.
 *
 * Topic-input surface only. Generation goes through the shared
 * useGenerateVisualToCanvas hook so behavior is identical to the
 * Studio bar (ChatActionBar) and canvas-overlay entry points.
 *
 * Per 2026-05-26 lock-step requirement: identical input MUST produce
 * identical output regardless of which UI surface kicked off generation.
 */
import React, { useState, useEffect } from 'react';
import { Button } from './shared/Button';
import { LoadingSpinner } from './shared/LoadingSpinner';
import { useGenerateVisualToCanvas } from '../hooks/useGenerateVisualToCanvas';

interface VisualPanelProps {
  notebookId: string;
  initialContent?: string;
  onVisualGenerated?: (code: string, title: string) => void;
}

const PLACEHOLDER_EXAMPLES = [
  'A three-tier microservices architecture for our e-commerce platform — show CDN, API gateway, services, and databases.',
  'Customer onboarding journey from lead to active user, with conversion rates and team owners per stage.',
  'Compare REST vs GraphQL vs gRPC across transport, schema, caching, and tooling.',
  'Q3 SaaS dashboard: ARR, NRR, customer count, gross margin, sales cycle, burn rate.',
  'Monolith to microservices transformation — before-state pain points and after-state wins.',
];

export const VisualPanel: React.FC<VisualPanelProps> = ({ notebookId, initialContent = '' }) => {
  const generateVisual = useGenerateVisualToCanvas();
  const [topic, setTopic] = useState(initialContent);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const placeholder = PLACEHOLDER_EXAMPLES[Math.floor(Math.random() * PLACEHOLDER_EXAMPLES.length)];

  useEffect(() => {
    if (initialContent && initialContent !== topic) setTopic(initialContent);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialContent]);

  const handleGenerate = async () => {
    if (!topic.trim()) {
      setError('Describe what you want to visualize first.');
      return;
    }
    setError(null);
    setLoading(true);
    // Note: onVisualGenerated callback is deprecated for v2 — the legacy
    // path triggered Studio → openPanel('visual-viewer', {content: ...})
    // which tried to render the (raw prompt) content as Mermaid and errored
    // with "No diagram type detected". The visual lives in the canvas now.
    const result = await generateVisual(notebookId, topic, {
      source: 'left_nav',
      onComplete: () => {
        setLoading(false);
      },
      onError: (msg) => {
        setLoading(false);
        setError(msg);
      },
    });
    if (!result.ok && result.error) {
      setLoading(false);
      setError(result.error);
    }
  };

  return (
    <div className="space-y-3">
      <div>
        <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
          Describe what you want to visualize
        </label>
        <textarea
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
          placeholder={placeholder}
          rows={4}
          className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white placeholder-gray-400 text-sm resize-none focus:outline-none focus:ring-2 focus:ring-indigo-400"
        />
        <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-1.5">
          The visual will appear in the canvas — expand, download, and rate it from there.
        </p>
      </div>

      <Button
        onClick={handleGenerate}
        disabled={loading || !topic.trim()}
        className="w-full"
      >
        {loading ? (
          <span className="inline-flex items-center gap-2">
            <LoadingSpinner size="sm" />
            Generating in canvas…
          </span>
        ) : (
          '✨ Generate visual'
        )}
      </Button>

      {error && (
        <div className="bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400 p-3 rounded-lg text-sm">
          {error}
        </div>
      )}
    </div>
  );
};
