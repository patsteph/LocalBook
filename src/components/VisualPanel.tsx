import React, { useState, useRef, useCallback, useEffect } from 'react';
import mermaid from 'mermaid';
import { visualService, Diagram } from '../services/visual';
import { Button } from './shared/Button';
import { LoadingSpinner } from './shared/LoadingSpinner';
import { MermaidRenderer } from './shared/MermaidRenderer';
import { SVGRenderer } from './shared/SVGRenderer';
import { BookmarkButton } from './shared/BookmarkButton';

// Phase 4: Refinement Chat Component
interface RefinementChatProps {
  notebookId: string;
  currentCode: string;
  colorTheme: string;
  onRefined: (newCode: string) => void;
}

const QUICK_REFINEMENTS = [
  { label: '✂️ Simpler', instruction: 'make it simpler with fewer nodes' },
  { label: '📝 More Detail', instruction: 'add more detail and sub-items' },
  { label: '↔️ Horizontal', instruction: 'change to horizontal layout' },
  { label: '↕️ Vertical', instruction: 'change to vertical layout' },
];

const RefinementChat: React.FC<RefinementChatProps> = ({ notebookId, currentCode, colorTheme, onRefined }) => {
  const [refineInput, setRefineInput] = useState('');
  const [refining, setRefining] = useState(false);
  const [lastChange, setLastChange] = useState<string | null>(null);

  const handleRefine = async (instruction: string) => {
    if (!instruction.trim()) return;
    setRefining(true);
    setLastChange(null);
    try {
      const result = await visualService.refineVisual(notebookId, currentCode, instruction, colorTheme);
      if (result.success && result.code !== currentCode) {
        onRefined(result.code);
        setLastChange(result.changes_made);
        setRefineInput('');
      }
    } catch (err) {
      console.error('Refinement failed:', err);
    } finally {
      setRefining(false);
    }
  };

  return (
    <div className="mt-3 space-y-2">
      <div className="flex gap-1 flex-wrap">
        {QUICK_REFINEMENTS.map((r) => (
          <button
            key={r.label}
            onClick={() => handleRefine(r.instruction)}
            disabled={refining}
            className="px-2 py-1 text-xs rounded bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-600 disabled:opacity-50"
          >
            {r.label}
          </button>
        ))}
      </div>
      <div className="flex gap-2">
        <input
          type="text"
          value={refineInput}
          onChange={(e) => setRefineInput(e.target.value)}
          placeholder="Refine: 'focus on X', 'add connections'..."
          className="flex-1 px-2 py-1 text-xs border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
          onKeyDown={(e) => e.key === 'Enter' && handleRefine(refineInput)}
        />
        <button
          onClick={() => handleRefine(refineInput)}
          disabled={refining || !refineInput.trim()}
          className="px-3 py-1 text-xs rounded bg-purple-500 text-white hover:bg-purple-600 disabled:opacity-50"
        >
          {refining ? '...' : '✨'}
        </button>
      </div>
      {lastChange && (
        <p className="text-xs text-green-600 dark:text-green-400">✓ {lastChange}</p>
      )}
    </div>
  );
};

// Helper to clean mermaid code and remove malformed statements
const cleanMermaidCode = (code: string): string => {
  if (!code) return code;
  return code
    .split('\n')
    .map(line => {
      // Fix spacing issues: "Branch1 ((text))" -> "Branch1((text))"
      // Also handles "Item (text)" -> "Item(text)" for mindmap nodes
      return line.replace(/(\w)\s+\(\(/g, '$1((')  // Fix double parens
                 .replace(/(\w)\s+\(/g, '$1(');     // Fix single parens
    })
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
  'Feynman Learning': [
    { id: 'feynman_progression', icon: '🎓', label: 'Learning Path', desc: '4-level Feynman progression' },
    { id: 'feynman_knowledge_map', icon: '🧠', label: 'Knowledge Map', desc: 'Concepts & connections' },
    { id: 'feynman_misconceptions', icon: '❌➡️✅', label: 'Misconceptions', desc: 'Common mistakes vs reality' },
  ],
};

// Color themes for diagrams - Napkin.ai style palettes
type ColorTheme = 'auto' | 'vibrant' | 'ocean' | 'sunset' | 'forest' | 'monochrome' | 'pastel';

const COLOR_THEMES: { id: ColorTheme; icon: string; label: string; colors: string[] }[] = [
  { id: 'auto', icon: '✨', label: 'Auto', colors: ['#ef4444', '#f97316', '#eab308', '#22c55e', '#3b82f6', '#8b5cf6'] },
  { id: 'vibrant', icon: '🌈', label: 'Vibrant', colors: ['#ef4444', '#f97316', '#eab308', '#22c55e', '#3b82f6', '#8b5cf6'] },
  { id: 'ocean', icon: '🌊', label: 'Ocean', colors: ['#0ea5e9', '#06b6d4', '#14b8a6', '#0d9488', '#0891b2'] },
  { id: 'sunset', icon: '🌅', label: 'Sunset', colors: ['#f97316', '#fb923c', '#fbbf24', '#f59e0b', '#dc2626'] },
  { id: 'forest', icon: '🌲', label: 'Forest', colors: ['#22c55e', '#16a34a', '#15803d', '#84cc16', '#65a30d'] },
  { id: 'monochrome', icon: '⬛', label: 'Mono', colors: ['#1f2937', '#374151', '#4b5563', '#6b7280', '#9ca3af'] },
  { id: 'pastel', icon: '🎀', label: 'Pastel', colors: ['#fecaca', '#fed7aa', '#fef08a', '#bbf7d0', '#bfdbfe', '#ddd6fe'] },
];

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
  const [loadingAlternatives, setLoadingAlternatives] = useState(false);
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
  const [colorTheme, setColorTheme] = useState<ColorTheme>('auto');
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
      const result = await visualService.generateSummary(notebookId, [templateToUse], topic || undefined, colorTheme);
      
      // Validate diagrams before showing - filter out ones that won't render
      const rawDiagrams = result.diagrams || [];
      const validatedDiagrams: Diagram[] = [];
      
      for (const diagram of rawDiagrams) {
        // SVG diagrams don't need Mermaid validation
        if (diagram.svg || diagram.render_type === 'svg') {
          validatedDiagrams.push(diagram);
        } else if (diagram.code) {
          // Validate Mermaid code
          const cleanedCode = cleanMermaidCode(diagram.code);
          const isValid = await validateMermaidCode(cleanedCode);
          if (isValid) {
            validatedDiagrams.push({ ...diagram, code: cleanedCode });
          } else {
            console.warn('[Visual] Filtered out invalid diagram:', diagram.diagram_type);
          }
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
    setDiagrams([]);
    setSelectedDiagram(null);
    
    try {
      // Use streaming endpoint - primary appears first, alternatives follow
      await visualService.generateSmartStream(
        notebookId,
        topic,
        colorTheme,
        // onPrimary - show immediately, then start loading alternatives
        async (diagram) => {
          // SVG diagrams don't need Mermaid validation
          if (diagram.svg || diagram.render_type === 'svg') {
            setDiagrams([diagram]);
            setSelectedDiagram(diagram);
            setLoading(false);
            setLoadingAlternatives(true); // Start loading alternatives indicator
            return;
          }
          
          // Mermaid diagrams need validation
          const cleanedCode = cleanMermaidCode(diagram.code || '');
          const isValid = await validateMermaidCode(cleanedCode);
          if (isValid) {
            const validDiagram = { ...diagram, code: cleanedCode };
            setDiagrams([validDiagram]);
            setSelectedDiagram(validDiagram);
            setLoading(false);
          } else {
            console.warn('[Visual] Primary diagram failed validation:', cleanedCode.substring(0, 200));
            const rawDiagram = { ...diagram, code: cleanedCode };
            setDiagrams([rawDiagram]);
            setSelectedDiagram(rawDiagram);
            setLoading(false);
          }
        },
        // onAlternative - add to list
        async (diagram) => {
          // SVG diagrams don't need validation
          if (diagram.svg || diagram.render_type === 'svg') {
            setDiagrams(prev => [...prev, diagram]);
            return;
          }
          
          const cleanedCode = cleanMermaidCode(diagram.code || '');
          const isValid = await validateMermaidCode(cleanedCode);
          if (isValid) {
            setDiagrams(prev => [...prev, { ...diagram, code: cleanedCode }]);
          }
        },
        // onDone
        () => {
          setLoading(false);
          setLoadingAlternatives(false);
        },
        // onError
        (errorMsg) => {
          setError(errorMsg);
          setLoading(false);
        }
      );
    } catch (err: any) {
      setError(err.message || 'Failed to generate visual');
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
          className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white placeholder-gray-400 text-sm resize-none"
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
        
        {/* Color Theme Selector */}
        <div className="flex items-center gap-1 mt-2">
          <span className="text-xs text-gray-500 dark:text-gray-400 mr-1">Theme:</span>
          {COLOR_THEMES.map((theme) => (
            <button
              key={theme.id}
              onClick={() => setColorTheme(theme.id)}
              title={theme.label}
              className={`w-6 h-6 rounded-full flex items-center justify-center text-xs transition-all ${
                colorTheme === theme.id
                  ? 'ring-2 ring-offset-1 ring-purple-500 scale-110'
                  : 'hover:scale-105 opacity-70 hover:opacity-100'
              }`}
              style={{
                background: theme.id === 'auto' 
                  ? 'linear-gradient(135deg, #3b82f6, #22c55e, #f59e0b)' 
                  : `linear-gradient(135deg, ${theme.colors[0]}, ${theme.colors[Math.floor(theme.colors.length/2)]})`
              }}
            >
              <span className="sr-only">{theme.label}</span>
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
                onClick={() => copyCodeToClipboard(selectedDiagram.code || selectedDiagram.svg || '')}
                className="px-2 py-1 text-xs rounded bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-600"
                title="Copy code"
              >
                {copied === 'code' ? '✓ Copied!' : '📝 Code'}
              </button>
              <BookmarkButton
                notebookId={notebookId}
                type="visual"
                title={selectedDiagram.title || 'Visual'}
                content={{
                  type: selectedDiagram.svg ? 'svg' : 'mermaid',
                  code: selectedDiagram.svg || selectedDiagram.code || '',
                  description: selectedDiagram.description,
                }}
              />
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
            {/* Use SVGRenderer for SVG visuals, MermaidRenderer for legacy Mermaid code */}
            {selectedDiagram.svg ? (
              <SVGRenderer svg={selectedDiagram.svg} className="border border-gray-200 dark:border-gray-700 rounded-lg" />
            ) : (
              <MermaidRenderer code={selectedDiagram.code || ''} className="border border-gray-200 dark:border-gray-700" />
            )}
            <div className="absolute inset-0 bg-black/0 group-hover:bg-black/5 dark:group-hover:bg-white/5 transition-colors flex items-center justify-center opacity-0 group-hover:opacity-100">
              <span className="bg-black/70 text-white text-xs px-2 py-1 rounded">
                🔍 Click to expand
              </span>
            </div>
          </div>
          
          {/* Phase 4: Refinement Chat - only for Mermaid diagrams */}
          {selectedDiagram.code && !selectedDiagram.svg && (
            <RefinementChat 
              notebookId={notebookId}
              currentCode={selectedDiagram.code}
              colorTheme={colorTheme}
              onRefined={(newCode) => {
                setSelectedDiagram({ ...selectedDiagram, code: newCode });
              }}
            />
          )}

          {/* Code Block (collapsible) - shows Mermaid code or SVG code */}
          <details className="mt-2">
            <summary className="text-xs text-gray-500 dark:text-gray-400 cursor-pointer hover:text-gray-700 dark:hover:text-gray-300">
              📝 View {selectedDiagram.svg ? 'SVG' : 'Mermaid'} code
            </summary>
            <div className="bg-gray-900 rounded-lg p-3 overflow-x-auto mt-2">
              <pre className="text-xs text-green-400 font-mono whitespace-pre-wrap max-h-60 overflow-y-auto">
                {selectedDiagram.code || selectedDiagram.svg || ''}
              </pre>
            </div>
          </details>
        </div>
      )}

      {/* Visual Options Selector - Shows when we have options or loading alternatives */}
      {(diagrams.length > 1 || loadingAlternatives) && (
        <div className="space-y-2">
          <label className="block text-xs font-medium text-gray-500 dark:text-gray-400">
            {loadingAlternatives ? (
              <span className="flex items-center gap-2">
                <span className="animate-pulse">●</span>
                Loading alternative styles...
              </span>
            ) : (
              `Choose a style (${diagrams.length} options)`
            )}
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
                  {i === 0 ? '⭐ Recommended' : d.diagram_type}
                </div>
              </button>
            ))}
            {loadingAlternatives && (
              <>
                <div className="p-2 text-xs rounded-lg border-2 border-dashed border-gray-300 dark:border-gray-600 animate-pulse">
                  <div className="h-4 bg-gray-200 dark:bg-gray-700 rounded w-3/4 mb-1"></div>
                  <div className="h-3 bg-gray-100 dark:bg-gray-800 rounded w-1/2"></div>
                </div>
                <div className="p-2 text-xs rounded-lg border-2 border-dashed border-gray-300 dark:border-gray-600 animate-pulse">
                  <div className="h-4 bg-gray-200 dark:bg-gray-700 rounded w-3/4 mb-1"></div>
                  <div className="h-3 bg-gray-100 dark:bg-gray-800 rounded w-1/2"></div>
                </div>
              </>
            )}
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
                <h3 className="text-base font-semibold text-gray-900 dark:text-white">
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
              {selectedDiagram.svg ? (
                <SVGRenderer svg={selectedDiagram.svg} className="border border-gray-200 dark:border-gray-700 rounded-lg" />
              ) : (
                <MermaidRenderer code={selectedDiagram.code || ''} className="border border-gray-200 dark:border-gray-700" />
              )}
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
