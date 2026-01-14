import React, { useState, useRef, useCallback, useEffect } from 'react';
import mermaid from 'mermaid';
import { visualService, Diagram } from '../services/visual';
import { Button } from './shared/Button';
import { LoadingSpinner } from './shared/LoadingSpinner';
import { MermaidRenderer } from './shared/MermaidRenderer';

// Helper to clean mermaid code and remove malformed statements
const cleanMermaidCode = (code: string): string => {
  if (!code) return code;
  return code
    .split('\n')
    .filter(line => {
      const trimmed = line.trim().toLowerCase();
      // Remove style lines that start with numbers (invalid syntax)
      if (trimmed.startsWith('style ')) {
        const afterStyle = trimmed.substring(6).trim();
        if (/^[\d,\s]+/.test(afterStyle)) {
          return false;
        }
      }
      return true;
    })
    .join('\n')
    .trim();
};

// Validate mermaid code by attempting to parse it
const validateMermaidCode = async (code: string): Promise<boolean> => {
  if (!code || code.trim().length < 10) return false;
  try {
    const cleanCode = cleanMermaidCode(code);
    await mermaid.parse(cleanCode);
    return true;
  } catch {
    return false;
  }
};

interface VisualPanelProps {
  notebookId: string;
  initialContent?: string; // For "Create Visual" from chat
}

type DiagramType = 'mindmap' | 'flowchart' | 'timeline' | 'classDiagram' | 'quadrant' | 'auto';

// Quick access diagram types - 6 options in 2 rows of 3
const QUICK_DIAGRAM_OPTIONS: { type: DiagramType; icon: string; label: string }[] = [
  { type: 'auto', icon: '✨', label: 'AI Visual' },
  { type: 'mindmap', icon: '🧠', label: 'Mindmap' },
  { type: 'flowchart', icon: '📊', label: 'Flow' },
  { type: 'timeline', icon: '📅', label: 'Timeline' },
  { type: 'classDiagram', icon: '🏗️', label: 'Hierarchy' },
  { type: 'quadrant', icon: '📈', label: 'Compare' },
];

// Helper to strip citation markers like [1], [2], etc.
const stripCitations = (text: string): string => {
  return text.replace(/\[\d+\]/g, '').replace(/\s+/g, ' ').trim();
};

// Advanced template categories (25 templates organized by purpose)
const ADVANCED_TEMPLATES = {
  'Context': [
    { id: 'key_stats', icon: '📊', label: 'Key Stats', desc: 'Highlight important metrics' },
    { id: 'overview', icon: '🗺️', label: 'Overview', desc: 'Big picture summary' },
    { id: 'anatomy', icon: '🔬', label: 'Anatomy', desc: 'Break down components' },
  ],
  'Mechanism': [
    { id: 'process_flow', icon: '➡️', label: 'Process Flow', desc: 'Step-by-step workflow' },
    { id: 'cycle', icon: '🔄', label: 'Cycle', desc: 'Repeating process' },
    { id: 'decision_tree', icon: '🌳', label: 'Decision Tree', desc: 'If-then logic' },
    { id: 'funnel', icon: '📉', label: 'Funnel', desc: 'Narrowing stages' },
  ],
  'Analysis': [
    { id: 'side_by_side', icon: '⚖️', label: 'Side by Side', desc: 'Compare options' },
    { id: 'pros_cons', icon: '👍👎', label: 'Pros/Cons', desc: 'Advantages vs disadvantages' },
    { id: 'matrix', icon: '📐', label: 'Matrix', desc: '2x2 comparison grid' },
    { id: 'ranking', icon: '🏆', label: 'Ranking', desc: 'Ordered list by criteria' },
  ],
  'Pattern': [
    { id: 'categories', icon: '📁', label: 'Categories', desc: 'Group related items' },
    { id: 'hierarchy', icon: '🏛️', label: 'Hierarchy', desc: 'Parent-child structure' },
    { id: 'timeline', icon: '📅', label: 'Timeline', desc: 'Chronological events' },
    { id: 'network', icon: '🕸️', label: 'Network', desc: 'Connected relationships' },
  ],
  'Persuade': [
    { id: 'key_takeaways', icon: '💡', label: 'Key Takeaways', desc: 'Main insights' },
    { id: 'action_plan', icon: '✅', label: 'Action Plan', desc: 'Next steps to take' },
    { id: 'roadmap', icon: '🛣️', label: 'Roadmap', desc: 'Future milestones' },
    { id: 'before_after', icon: '🔀', label: 'Before/After', desc: 'Transformation story' },
  ],
};

// Example prompts to show users what they can type
const EXAMPLE_PROMPTS = [
  'Compare AWS, Azure, and GCP on price and scalability',
  'Show the 5-step sales process with decision points',
  'Timeline of AI milestones from 2020 to 2025',
  'Breakdown of marketing budget by channel',
  'Pros and cons of remote vs hybrid work',
];

export const VisualPanel: React.FC<VisualPanelProps> = ({ notebookId, initialContent = '' }) => {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [diagrams, setDiagrams] = useState<Diagram[]>([]);
  const [keyPoints, setKeyPoints] = useState<string[]>([]);
  const [selectedDiagram, setSelectedDiagram] = useState<Diagram | null>(null);
  const [diagramType, setDiagramType] = useState<DiagramType>('auto');
  const [topic, setTopic] = useState(initialContent);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [selectedTemplate, setSelectedTemplate] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [showLightbox, setShowLightbox] = useState(false);
  const diagramRef = useRef<HTMLDivElement>(null);
  const lightboxRef = useRef<HTMLDivElement>(null);

  // Rotate through example prompts
  const [exampleIndex] = useState(() => Math.floor(Math.random() * EXAMPLE_PROMPTS.length));
  const placeholderText = `e.g., "${EXAMPLE_PROMPTS[exampleIndex]}"`;

  // Update topic when initialContent changes (from "Create Visual from this" in chat)
  // Strip citation markers to keep visual clean
  useEffect(() => {
    if (initialContent) {
      const cleanContent = stripCitations(initialContent);
      if (cleanContent !== topic) {
        setTopic(cleanContent);
      }
    }
  }, [initialContent]);

  const handleGenerate = async () => {
    setLoading(true);
    setError(null);
    try {
      // Use selected template or diagram type
      const templateToUse = selectedTemplate || diagramType;
      const result = await visualService.generateSummary(notebookId, [templateToUse], topic || undefined);
      
      // Validate diagrams before showing - filter out ones that won't render
      const rawDiagrams = result.diagrams || [];
      const validatedDiagrams: Diagram[] = [];
      
      for (const diagram of rawDiagrams) {
        const cleanedCode = cleanMermaidCode(diagram.code);
        const isValid = await validateMermaidCode(cleanedCode);
        if (isValid) {
          validatedDiagrams.push({ ...diagram, code: cleanedCode });
        } else {
          console.warn('[Visual] Filtered out invalid diagram:', diagram.diagram_type);
        }
      }
      
      setDiagrams(validatedDiagrams);
      setKeyPoints(result.key_points);
      if (validatedDiagrams.length > 0) {
        setSelectedDiagram(validatedDiagrams[0]);
      } else if (rawDiagrams.length > 0) {
        setError('Generated diagram had syntax errors. Try regenerating.');
      }
    } catch (err: any) {
      setError(err.message || 'Failed to generate visual summary');
    } finally {
      setLoading(false);
    }
  };

  const handleSmartGenerate = async () => {
    if (!topic.trim()) {
      setError('Please describe what you want to visualize');
      return;
    }
    setLoading(true);
    setError(null);
    try {
      // Call the smart endpoint that auto-picks the best template
      const result = await visualService.generateSmart(notebookId, topic);
      
      // Validate diagrams before showing - filter out ones that won't render
      const rawDiagrams = result.diagrams || [];
      const validatedDiagrams: Diagram[] = [];
      
      for (const diagram of rawDiagrams) {
        // Clean the code first
        const cleanedCode = cleanMermaidCode(diagram.code);
        const isValid = await validateMermaidCode(cleanedCode);
        if (isValid) {
          validatedDiagrams.push({ ...diagram, code: cleanedCode });
        } else {
          console.warn('[Visual] Filtered out invalid diagram:', diagram.diagram_type);
        }
      }
      
      setDiagrams(validatedDiagrams);
      setKeyPoints(result.key_points || []);
      if (validatedDiagrams.length > 0) {
        setSelectedDiagram(validatedDiagrams[0]);
      } else if (rawDiagrams.length > 0) {
        // All diagrams failed validation - show error
        setError('Generated diagrams had syntax errors. Try regenerating.');
      }
    } catch (err: any) {
      setError(err.message || 'Failed to generate visual');
    } finally {
      setLoading(false);
    }
  };

  const [copied, setCopied] = useState<string | null>(null);

  const copyCodeToClipboard = (code: string) => {
    navigator.clipboard.writeText(code);
    setCopied('code');
    setTimeout(() => setCopied(null), 2000);
  };

  const copyImageToClipboard = useCallback(async () => {
    if (!diagramRef.current) return;
    try {
      const svg = diagramRef.current.querySelector('svg');
      if (!svg) throw new Error('No SVG found');
      
      const canvas = document.createElement('canvas');
      const ctx = canvas.getContext('2d');
      const svgData = new XMLSerializer().serializeToString(svg);
      const svgBlob = new Blob([svgData], { type: 'image/svg+xml;charset=utf-8' });
      const url = URL.createObjectURL(svgBlob);
      
      const img = new Image();
      img.onload = async () => {
        canvas.width = img.width * 2;
        canvas.height = img.height * 2;
        ctx?.scale(2, 2);
        ctx?.drawImage(img, 0, 0);
        URL.revokeObjectURL(url);
        
        canvas.toBlob(async (blob) => {
          if (blob) {
            await navigator.clipboard.write([
              new ClipboardItem({ 'image/png': blob })
            ]);
            setCopied('image');
            setTimeout(() => setCopied(null), 2000);
          }
        }, 'image/png');
      };
      img.src = url;
    } catch (err) {
      console.error('Copy image failed:', err);
    }
  }, []);

  const exportToPNG = useCallback(async () => {
    if (!diagramRef.current) return;
    setExporting(true);
    try {
      const svg = diagramRef.current.querySelector('svg');
      if (!svg) throw new Error('No SVG found');
      
      // Create canvas and draw SVG
      const canvas = document.createElement('canvas');
      const ctx = canvas.getContext('2d');
      const svgData = new XMLSerializer().serializeToString(svg);
      const svgBlob = new Blob([svgData], { type: 'image/svg+xml;charset=utf-8' });
      const url = URL.createObjectURL(svgBlob);
      
      const img = new Image();
      img.onload = () => {
        canvas.width = img.width * 2; // 2x for better quality
        canvas.height = img.height * 2;
        ctx?.scale(2, 2);
        ctx?.drawImage(img, 0, 0);
        URL.revokeObjectURL(url);
        
        // Download
        const link = document.createElement('a');
        link.download = `${selectedDiagram?.title || 'visual'}.png`;
        link.href = canvas.toDataURL('image/png');
        link.click();
        setExporting(false);
      };
      img.src = url;
    } catch (err) {
      console.error('Export failed:', err);
      setExporting(false);
    }
  }, [selectedDiagram]);

  const exportToSVG = useCallback(() => {
    if (!diagramRef.current) return;
    const svg = diagramRef.current.querySelector('svg');
    if (!svg) return;
    
    const svgData = new XMLSerializer().serializeToString(svg);
    const blob = new Blob([svgData], { type: 'image/svg+xml;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    
    const link = document.createElement('a');
    link.download = `${selectedDiagram?.title || 'visual'}.svg`;
    link.href = url;
    link.click();
    URL.revokeObjectURL(url);
  }, [selectedDiagram]);

  const selectQuickType = (type: DiagramType) => {
    setDiagramType(type);
    setSelectedTemplate(null);
    setShowAdvanced(false);
  };

  const selectAdvancedTemplate = (templateId: string) => {
    setSelectedTemplate(templateId);
    setDiagramType('auto'); // Keep auto but with specific template override
  };

  return (
    <div className="space-y-4">
      {/* Topic/Description Input */}
      <div>
        <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
          Describe what you want to visualize
        </label>
        <textarea
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
          placeholder={placeholderText}
          rows={3}
          className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-md bg-white dark:bg-gray-700 text-gray-900 dark:text-white placeholder-gray-400 text-sm resize-none"
        />
      </div>

      {/* Quick Type Buttons - 6 options in 2 rows of 3 */}
      <div className="space-y-2">
        <label className="block text-xs font-medium text-gray-500 dark:text-gray-400">
          Visual Type
        </label>
        <div className="grid grid-cols-3 gap-2">
          {QUICK_DIAGRAM_OPTIONS.map((opt) => (
            <button
              key={opt.type}
              onClick={() => selectQuickType(opt.type)}
              className={`px-3 py-2 text-xs rounded-lg border transition-colors flex items-center justify-center gap-1.5 ${
                diagramType === opt.type && !selectedTemplate
                  ? opt.type === 'auto' 
                    ? 'border-purple-500 bg-purple-50 dark:bg-purple-900/30 text-purple-700 dark:text-purple-300'
                    : 'border-blue-500 bg-blue-50 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300'
                  : 'border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-800'
              }`}
            >
              {opt.icon} {opt.label}
            </button>
          ))}
        </div>
        
        {/* More Templates Toggle */}
        <button
          onClick={() => setShowAdvanced(!showAdvanced)}
          className="text-xs text-purple-600 dark:text-purple-400 hover:underline"
        >
          {showAdvanced ? '▲ Hide templates' : '▼ More templates...'}
        </button>
      </div>

      {/* Advanced Templates (Expandable) */}
      {showAdvanced && (
        <div className="border border-purple-200 dark:border-purple-800 rounded-lg p-3 bg-purple-50/50 dark:bg-purple-900/10">
          <p className="text-xs text-purple-600 dark:text-purple-400 mb-3">
            Choose a template or let AI pick the best one for your content
          </p>
          {Object.entries(ADVANCED_TEMPLATES).map(([category, templates]) => (
            <div key={category} className="mb-3 last:mb-0">
              <h5 className="text-xs font-semibold text-gray-500 dark:text-gray-400 mb-1.5 uppercase tracking-wide">
                {category}
              </h5>
              <div className="flex flex-wrap gap-1.5">
                {templates.map((t) => (
                  <button
                    key={t.id}
                    onClick={() => selectAdvancedTemplate(t.id)}
                    title={t.desc}
                    className={`px-2 py-1 text-xs rounded border transition-colors ${
                      selectedTemplate === t.id
                        ? 'border-purple-500 bg-purple-100 dark:bg-purple-900/40 text-purple-700 dark:text-purple-300'
                        : 'border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-400 hover:bg-white dark:hover:bg-gray-800'
                    }`}
                  >
                    {t.icon} {t.label}
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Single Generate Button */}
      <Button 
        onClick={diagramType === 'auto' && !selectedTemplate ? handleSmartGenerate : handleGenerate} 
        disabled={loading || !topic.trim()} 
        className="w-full"
      >
        {loading ? (
          <LoadingSpinner size="sm" />
        ) : diagramType === 'auto' && !selectedTemplate ? (
          '✨ Generate AI Visual'
        ) : (
          `Generate ${selectedTemplate || diagramType.charAt(0).toUpperCase() + diagramType.slice(1)}`
        )}
      </Button>

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
            <div className="flex gap-1 flex-wrap">
              <button
                onClick={copyImageToClipboard}
                className="px-2 py-1 text-xs rounded bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300 hover:bg-blue-200 dark:hover:bg-blue-800/40"
                title="Copy image to clipboard"
              >
                {copied === 'image' ? '✓ Copied!' : '📋 Copy'}
              </button>
              <button
                onClick={exportToPNG}
                disabled={exporting}
                className="px-2 py-1 text-xs rounded bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-300 hover:bg-green-200 dark:hover:bg-green-800/40 disabled:opacity-50"
                title="Download as PNG"
              >
                📷 PNG
              </button>
              <button
                onClick={exportToSVG}
                className="px-2 py-1 text-xs rounded bg-purple-100 dark:bg-purple-900/30 text-purple-700 dark:text-purple-300 hover:bg-purple-200 dark:hover:bg-purple-800/40"
                title="Download as SVG"
              >
                🎨 SVG
              </button>
              <button
                onClick={() => copyCodeToClipboard(selectedDiagram.code)}
                className="px-2 py-1 text-xs rounded bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-600"
                title="Copy Mermaid code"
              >
                {copied === 'code' ? '✓ Copied!' : '📝 Code'}
              </button>
            </div>
          </div>
          
          <p className="text-xs text-gray-500 dark:text-gray-400">
            {selectedDiagram.description}
          </p>

          {/* Rendered Diagram - Click to expand */}
          <div 
            ref={diagramRef}
            onClick={() => setShowLightbox(true)}
            className="cursor-zoom-in relative group"
            title="Click to view larger"
          >
            <MermaidRenderer code={selectedDiagram.code} className="border border-gray-200 dark:border-gray-700" />
            <div className="absolute inset-0 bg-black/0 group-hover:bg-black/5 dark:group-hover:bg-white/5 transition-colors flex items-center justify-center opacity-0 group-hover:opacity-100">
              <span className="bg-black/70 text-white text-xs px-2 py-1 rounded">
                🔍 Click to expand
              </span>
            </div>
          </div>
          
          {/* Regenerate hint */}
          <p className="text-xs text-gray-400 dark:text-gray-500 text-center mt-2">
            💡 Edit your description above and click Generate again to refine
          </p>

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

      {/* Visual Options Selector - Shows when we have multiple options */}
      {diagrams.length > 1 && (
        <div className="space-y-2">
          <label className="block text-xs font-medium text-gray-500 dark:text-gray-400">
            Choose a style ({diagrams.length} options)
          </label>
          <div className="grid grid-cols-3 gap-2">
            {diagrams.map((d, i) => (
              <button
                key={i}
                onClick={() => setSelectedDiagram(d)}
                className={`p-2 text-xs rounded-lg border-2 transition-all text-left ${
                  selectedDiagram === d
                    ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/30'
                    : 'border-gray-200 dark:border-gray-700 hover:border-gray-300 dark:hover:border-gray-600'
                }`}
              >
                <div className="font-medium text-gray-900 dark:text-white truncate">
                  {(d as any).template_name || d.diagram_type}
                </div>
                <div className="text-gray-500 dark:text-gray-400 text-[10px] mt-0.5">
                  {d.diagram_type}
                </div>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Lightbox Modal for full-size diagram view */}
      {showLightbox && selectedDiagram && (
        <div 
          className="fixed inset-0 z-50 bg-black/80 flex items-center justify-center p-4"
          onClick={() => setShowLightbox(false)}
        >
          <div 
            ref={lightboxRef}
            className="bg-white dark:bg-gray-900 rounded-xl max-w-[90vw] max-h-[90vh] overflow-auto p-6 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex justify-between items-start mb-4">
              <div>
                <h3 className="text-lg font-semibold text-gray-900 dark:text-white">
                  {selectedDiagram.title}
                </h3>
                <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
                  {selectedDiagram.description}
                </p>
              </div>
              <button
                onClick={() => setShowLightbox(false)}
                className="p-2 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg transition-colors"
                title="Close"
              >
                <svg className="w-5 h-5 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
            
            {/* Full-size diagram */}
            <div className="min-w-[600px]">
              <MermaidRenderer code={selectedDiagram.code} className="border border-gray-200 dark:border-gray-700" />
            </div>
            
            {/* Export buttons in lightbox */}
            <div className="flex gap-2 mt-4 justify-center">
              <button
                onClick={copyImageToClipboard}
                className="px-3 py-2 text-sm rounded-lg bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300 hover:bg-blue-200"
              >
                📋 Copy Image
              </button>
              <button
                onClick={exportToPNG}
                className="px-3 py-2 text-sm rounded-lg bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-300 hover:bg-green-200"
              >
                📷 Download PNG
              </button>
              <button
                onClick={exportToSVG}
                className="px-3 py-2 text-sm rounded-lg bg-purple-100 dark:bg-purple-900/30 text-purple-700 dark:text-purple-300 hover:bg-purple-200"
              >
                🎨 Download SVG
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
