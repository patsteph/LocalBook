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

// Initialize mermaid with default config
const initializeMermaid = (isDark: boolean) => {
  mermaid.initialize({
    startOnLoad: false,
    theme: isDark ? 'dark' : 'default',
    securityLevel: 'loose',
    fontFamily: 'ui-sans-serif, system-ui, sans-serif',
    flowchart: {
      useMaxWidth: true,
      htmlLabels: true,
      curve: 'basis',
    },
    mindmap: {
      useMaxWidth: true,
    },
  });
};

export const MermaidRenderer: React.FC<MermaidRendererProps> = ({ code, className = '' }) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [svgContent, setSvgContent] = useState<string>('');
  const [isRendering, setIsRendering] = useState(true);

  useEffect(() => {
    const renderDiagram = async () => {
      if (!code || !containerRef.current) return;

      setIsRendering(true);
      setError(null);

      try {
        // Detect dark mode
        const isDark = document.documentElement.classList.contains('dark');
        
        // Re-initialize if theme changed or not initialized
        initializeMermaid(isDark);

        // Generate unique ID for this diagram
        const id = `mermaid-${Math.random().toString(36).substring(2, 11)}`;

        // Clean the code - remove any leading/trailing whitespace
        const cleanCode = code.trim();

        // Render the diagram
        const { svg } = await mermaid.render(id, cleanCode);
        setSvgContent(svg);
      } catch (err: any) {
        console.error('Mermaid render error:', err);
        setError(err.message || 'Failed to render diagram');
      } finally {
        setIsRendering(false);
      }
    };

    renderDiagram();
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
