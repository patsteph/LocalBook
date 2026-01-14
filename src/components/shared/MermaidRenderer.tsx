/**
 * MermaidRenderer - Renders Mermaid diagrams inline
 * 
 * Uses the mermaid library to render diagram code into SVG.
 * Supports dark mode and provides error handling for invalid diagrams.
 */

import React, { useEffect, useRef, useState } from 'react';
import mermaid from 'mermaid';

interface MermaidRendererProps {
  code: string;
  className?: string;
}

// Track if mermaid has been warmed up
let mermaidWarmedUp = false;

// Initialize mermaid with vibrant color theme
const initializeMermaid = (isDark: boolean) => {
  mermaid.initialize({
    startOnLoad: false,
    theme: 'base',
    securityLevel: 'loose',
    fontFamily: 'ui-sans-serif, system-ui, sans-serif',
    themeVariables: isDark ? {
      // Dark mode - vibrant colors on dark background
      primaryColor: '#6366f1',      // Indigo
      primaryTextColor: '#ffffff',
      primaryBorderColor: '#818cf8',
      secondaryColor: '#10b981',    // Emerald
      secondaryTextColor: '#ffffff',
      secondaryBorderColor: '#34d399',
      tertiaryColor: '#f59e0b',     // Amber
      tertiaryTextColor: '#000000',
      tertiaryBorderColor: '#fbbf24',
      lineColor: '#94a3b8',
      textColor: '#f1f5f9',
      mainBkg: '#1e293b',
      nodeBorder: '#475569',
      clusterBkg: '#334155',
      titleColor: '#f8fafc',
      edgeLabelBackground: '#1e293b',
    } : {
      // Light mode - vibrant colors on light background
      primaryColor: '#6366f1',      // Indigo
      primaryTextColor: '#ffffff',
      primaryBorderColor: '#4f46e5',
      secondaryColor: '#10b981',    // Emerald
      secondaryTextColor: '#ffffff',
      secondaryBorderColor: '#059669',
      tertiaryColor: '#f59e0b',     // Amber
      tertiaryTextColor: '#000000',
      tertiaryBorderColor: '#d97706',
      lineColor: '#64748b',
      textColor: '#1e293b',
      mainBkg: '#ffffff',
      nodeBorder: '#cbd5e1',
      clusterBkg: '#f1f5f9',
      titleColor: '#0f172a',
      edgeLabelBackground: '#ffffff',
    },
    flowchart: {
      useMaxWidth: true,
      htmlLabels: true,
      curve: 'basis',
      padding: 15,
      nodeSpacing: 50,
      rankSpacing: 50,
    },
    mindmap: {
      useMaxWidth: true,
      padding: 10,
    },
  });
};

/**
 * Prewarm the mermaid renderer by rendering a simple diagram.
 * Call this on app startup to avoid cold-start delays.
 */
export const prewarmMermaid = async (): Promise<boolean> => {
  if (mermaidWarmedUp) return true;
  
  try {
    const isDark = document.documentElement.classList.contains('dark');
    initializeMermaid(isDark);
    
    // Render a minimal diagram to warm up the parser and renderer
    const warmupCode = 'flowchart LR\n  A[Start] --> B[End]';
    await mermaid.render('mermaid-warmup', warmupCode);
    
    mermaidWarmedUp = true;
    console.log('[Mermaid] ✓ Renderer prewarmed');
    return true;
  } catch (err) {
    console.warn('[Mermaid] Prewarm failed:', err);
    return false;
  }
};

export const MermaidRenderer: React.FC<MermaidRendererProps> = ({ code, className = '' }) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [svgContent, setSvgContent] = useState<string>('');
  const [isRendering, setIsRendering] = useState(true);

  useEffect(() => {
    let timeoutId: NodeJS.Timeout;
    let isCancelled = false;

    const renderDiagram = async () => {
      if (!code) {
        setIsRendering(false);
        return;
      }

      setIsRendering(true);
      setError(null);

      // Timeout after 10 seconds
      timeoutId = setTimeout(() => {
        if (!isCancelled) {
          setError('Diagram rendering timed out. The diagram may be too complex.');
          setIsRendering(false);
        }
      }, 10000);

      try {
        // Detect dark mode
        const isDark = document.documentElement.classList.contains('dark');
        
        // Re-initialize if theme changed or not initialized
        initializeMermaid(isDark);

        // Generate unique ID for this diagram
        const id = `mermaid-${Math.random().toString(36).substring(2, 11)}`;

        // Clean the code - remove any leading/trailing whitespace and fix common issues
        let cleanCode = code.trim();
        
        // Fix common mermaid syntax issues
        cleanCode = cleanCode
          .replace(/\r\n/g, '\n')  // Normalize line endings
          .replace(/\t/g, '    ') // Replace tabs with spaces
          .replace(/```mermaid\s*/gi, '')  // Remove markdown code fence start
          .replace(/```\s*$/g, '');  // Remove markdown code fence end
        
        // Fix single-line code by adding line breaks
        // Check if code lacks newlines but has multiple statements
        if (!cleanCode.includes('\n') || cleanCode.split('\n').length < 3) {
          // Add newlines after diagram type declarations
          cleanCode = cleanCode
            .replace(/(flowchart\s+(?:LR|RL|TB|TD|BT))\s+/gi, '$1\n    ')
            .replace(/(graph\s+(?:LR|RL|TB|TD|BT))\s+/gi, '$1\n    ')
            .replace(/(mindmap)\s+/gi, '$1\n    ')
            .replace(/(timeline)\s+/gi, '$1\n    ')
            .replace(/(sequenceDiagram)\s+/gi, '$1\n    ')
            .replace(/(classDiagram)\s+/gi, '$1\n    ')
            .replace(/(pie)\s+/gi, '$1\n    ')
            .replace(/(quadrantChart)\s+/gi, '$1\n    ')
            // Add newlines before style statements
            .replace(/\s+(style\s+)/gi, '\n    $1')
            // Add newlines before subgraph
            .replace(/\s+(subgraph\s+)/gi, '\n    $1')
            .replace(/\s+(end)\s+/gi, '\n    $1\n    ')
            // Add newlines after arrow connections (but not in the middle of labels)
            .replace(/\]\s+([\w\d]+\[)/g, ']\n    $1')
            .replace(/\]\s+([\w\d]+\{)/g, ']\n    $1')
            .replace(/\]\s+([\w\d]+\()/g, ']\n    $1');
        }
        
        // Remove malformed style statements that cause parse errors
        // style with numeric IDs like "style 3,4,6" are invalid
        cleanCode = cleanCode
          .split('\n')
          .filter(line => {
            const trimmed = line.trim().toLowerCase();
            // Remove style lines that start with numbers or have comma-separated numbers
            if (trimmed.startsWith('style ')) {
              const afterStyle = trimmed.substring(6).trim();
              // Invalid if starts with number or has pattern like "3,4,6"
              if (/^[\d,\s]+/.test(afterStyle)) {
                return false;
              }
            }
            return true;
          })
          .join('\n');
        
        cleanCode = cleanCode.trim();

        // Render the diagram
        const { svg } = await mermaid.render(id, cleanCode);
        
        if (!isCancelled) {
          clearTimeout(timeoutId);
          setSvgContent(svg);
          setIsRendering(false);
        }
      } catch (err: any) {
        if (!isCancelled) {
          clearTimeout(timeoutId);
          console.error('Mermaid render error:', err);
          setError(err.message || 'Failed to render diagram');
          setIsRendering(false);
        }
      }
    };

    renderDiagram();

    return () => {
      isCancelled = true;
      if (timeoutId) clearTimeout(timeoutId);
    };
  }, [code]);

  // Re-render when theme changes
  useEffect(() => {
    const observer = new MutationObserver((mutations) => {
      mutations.forEach((mutation) => {
        if (mutation.attributeName === 'class') {
          // Theme changed, force re-render by clearing current SVG
          setSvgContent('');
        }
      });
    });

    observer.observe(document.documentElement, { attributes: true });

    return () => observer.disconnect();
  }, []);

  if (isRendering && !svgContent) {
    return (
      <div className={`flex items-center justify-center p-8 bg-gray-50 dark:bg-gray-800 rounded-lg ${className}`}>
        <div className="flex items-center gap-2 text-gray-500 dark:text-gray-400">
          <div className="animate-spin w-5 h-5 border-2 border-gray-300 dark:border-gray-600 border-t-blue-500 rounded-full" />
          <span className="text-sm">Rendering diagram...</span>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className={`bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg p-4 ${className}`}>
        <div className="flex items-start gap-2">
          <span className="text-red-500">⚠️</span>
          <div>
            <p className="text-sm font-medium text-red-700 dark:text-red-300">Failed to render diagram</p>
            <p className="text-xs text-red-600 dark:text-red-400 mt-1">{error}</p>
            <details className="mt-2">
              <summary className="text-xs text-red-500 cursor-pointer hover:underline">View raw code</summary>
              <pre className="mt-2 text-xs bg-gray-900 text-green-400 p-2 rounded overflow-x-auto">
                {code}
              </pre>
            </details>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div 
      ref={containerRef}
      className={`mermaid-container bg-white dark:bg-gray-800 rounded-lg p-4 overflow-x-auto ${className}`}
      dangerouslySetInnerHTML={{ __html: svgContent }}
    />
  );
};

export default MermaidRenderer;
