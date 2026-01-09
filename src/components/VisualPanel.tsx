import React, { useState } from 'react';
import { visualService, Diagram } from '../services/visual';
import { Button } from './shared/Button';
import { LoadingSpinner } from './shared/LoadingSpinner';
import { MermaidRenderer } from './shared/MermaidRenderer';

interface VisualPanelProps {
  notebookId: string;
}

type DiagramType = 'mindmap' | 'flowchart' | 'timeline' | 'classDiagram' | 'quadrant';

const DIAGRAM_OPTIONS: { type: DiagramType; icon: string; label: string; description: string }[] = [
  { type: 'mindmap', icon: '🧠', label: 'Mindmap', description: 'Concept relationships' },
  { type: 'flowchart', icon: '📊', label: 'Flowchart', description: 'Process flow' },
  { type: 'timeline', icon: '📅', label: 'Timeline', description: 'Chronological events' },
  { type: 'classDiagram', icon: '🏗️', label: 'Hierarchy', description: 'Concept structure' },
  { type: 'quadrant', icon: '📈', label: 'Quadrant', description: 'Compare & contrast' },
];

export const VisualPanel: React.FC<VisualPanelProps> = ({ notebookId }) => {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [diagrams, setDiagrams] = useState<Diagram[]>([]);
  const [keyPoints, setKeyPoints] = useState<string[]>([]);
  const [selectedDiagram, setSelectedDiagram] = useState<Diagram | null>(null);
  const [diagramType, setDiagramType] = useState<DiagramType>('mindmap');
  const [topic, setTopic] = useState('');

  const handleGenerate = async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await visualService.generateSummary(notebookId, [diagramType], topic || undefined);
      setDiagrams(result.diagrams);
      setKeyPoints(result.key_points);
      if (result.diagrams.length > 0) {
        setSelectedDiagram(result.diagrams[0]);
      }
    } catch (err: any) {
      setError(err.message || 'Failed to generate visual summary');
    } finally {
      setLoading(false);
    }
  };

  const copyToClipboard = (code: string) => {
    navigator.clipboard.writeText(code);
  };

  return (
    <div className="space-y-4">
      {/* Controls */}
      <div className="space-y-3">
        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
            Diagram Type
          </label>
          <div className="grid grid-cols-2 gap-2">
            {DIAGRAM_OPTIONS.map((opt) => (
              <button
                key={opt.type}
                onClick={() => setDiagramType(opt.type)}
                className={`px-2 py-2 text-xs rounded-md border text-left ${
                  diagramType === opt.type
                    ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/20 text-blue-700 dark:text-blue-300'
                    : 'border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800'
                }`}
              >
                <span className="text-base">{opt.icon}</span>
                <span className="ml-1 font-medium">{opt.label}</span>
                <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">{opt.description}</p>
              </button>
            ))}
          </div>
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
            Topic <span className="text-gray-400 font-normal">(optional)</span>
          </label>
          <input
            type="text"
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
            placeholder="e.g., Neural Networks, API Design..."
            className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-md bg-white dark:bg-gray-700 text-gray-900 dark:text-white placeholder-gray-400 text-sm"
          />
          <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">Focus diagram on a specific concept</p>
        </div>

        <Button onClick={handleGenerate} disabled={loading} className="w-full">
          {loading ? <LoadingSpinner size="sm" /> : '✨ Generate Visual Summary'}
        </Button>
      </div>

      {error && (
        <div className="bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400 p-3 rounded-lg text-sm">
          {error}
        </div>
      )}

      {/* Key Points */}
      {keyPoints.length > 0 && (
        <div className="bg-gray-50 dark:bg-gray-800 rounded-lg p-3">
          <h4 className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">Key Points</h4>
          <ul className="space-y-1">
            {keyPoints.map((point, i) => (
              <li key={i} className="text-sm text-gray-600 dark:text-gray-400 flex gap-2">
                <span className="text-blue-500">•</span>
                {point}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Diagram Display */}
      {selectedDiagram && (
        <div className="space-y-2">
          <div className="flex justify-between items-center">
            <h4 className="text-sm font-medium text-gray-700 dark:text-gray-300">
              {selectedDiagram.title}
            </h4>
            <button
              onClick={() => copyToClipboard(selectedDiagram.code)}
              className="text-xs text-blue-600 hover:text-blue-700"
            >
              Copy Mermaid Code
            </button>
          </div>
          
          <p className="text-xs text-gray-500 dark:text-gray-400">
            {selectedDiagram.description}
          </p>

          {/* Rendered Diagram */}
          <MermaidRenderer code={selectedDiagram.code} className="border border-gray-200 dark:border-gray-700" />

          {/* Mermaid Code Block (collapsible) */}
          <details className="mt-2">
            <summary className="text-xs text-gray-500 dark:text-gray-400 cursor-pointer hover:text-gray-700 dark:hover:text-gray-300">
              📝 View Mermaid code
            </summary>
            <div className="bg-gray-900 rounded-lg p-3 overflow-x-auto mt-2">
              <pre className="text-xs text-green-400 font-mono whitespace-pre-wrap">
                {selectedDiagram.code}
              </pre>
            </div>
          </details>
        </div>
      )}

      {/* Multiple Diagrams */}
      {diagrams.length > 1 && (
        <div className="flex gap-2 flex-wrap">
          {diagrams.map((d, i) => (
            <button
              key={i}
              onClick={() => setSelectedDiagram(d)}
              className={`px-2 py-1 text-xs rounded ${
                selectedDiagram === d
                  ? 'bg-blue-500 text-white'
                  : 'bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-300'
              }`}
            >
              {d.diagram_type}
            </button>
          ))}
        </div>
      )}
    </div>
  );
};
