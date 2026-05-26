/**
 * SVGRenderer - Renders SVG visuals inline
 *
 * Renders trusted-source SVG markup directly into the DOM. v2 visuals are
 * generated from pre-validated skeletons with XML-escaped slot values
 * (see backend/services/visual_slotfill.py:_apply_slot_fill), so we
 * intentionally skip DOMPurify sanitization here.
 *
 * Why: DOMPurify's SVG profile strips the HTML children of <foreignObject>
 * even when those tags are added to ADD_TAGS, because of the SVG↔XHTML
 * namespace transition. Stripping them produces "empty boxes" — the
 * structural SVG renders fine, but the wrapped text disappears.
 *
 * Trust boundary: the SVG markup must NEVER be produced from user-controlled
 * SVG/HTML source. Slot values from LLMs are always XML-escaped server-side
 * before they reach the skeleton, so text content is safe.
 */

import React, { useEffect, useRef } from 'react';

interface SVGRendererProps {
  svg: string;
  className?: string;
  title?: string;
}

export const SVGRenderer: React.FC<SVGRendererProps> = ({ svg, className = '', title }) => {
  // We render the SVG via direct DOM insertion (vs dangerouslySetInnerHTML) so
  // the browser parses it as proper SVG with namespace handling — which makes
  // <foreignObject> children render correctly in the WebView.
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ref.current) return;
    if (!svg || !svg.includes('<svg')) {
      ref.current.innerHTML = '';
      return;
    }
    // Parse as XML so foreignObject + xmlns transitions are respected.
    // (Setting innerHTML on an HTML element can sometimes mis-handle
    // namespaces; parsing as XML and inserting the resulting node tree
    // avoids that.)
    try {
      const doc = new DOMParser().parseFromString(svg, 'image/svg+xml');
      const root = doc.documentElement;
      // Detect parser errors (DOMParser emits a <parsererror> root on failure)
      const isError = root.nodeName === 'parsererror' || root.querySelector?.('parsererror');
      if (isError) {
        // Fall back to innerHTML — better partial render than nothing
        ref.current.innerHTML = svg;
        return;
      }
      // Adopt + replace existing children
      ref.current.innerHTML = '';
      ref.current.appendChild(document.importNode(root, true));
    } catch {
      ref.current.innerHTML = svg;
    }
  }, [svg]);

  if (!svg) {
    return (
      <div className={`flex items-center justify-center p-8 bg-gray-50 dark:bg-gray-800 rounded-lg ${className}`}>
        <span className="text-gray-500 dark:text-gray-400 text-sm">No visual to display</span>
      </div>
    );
  }

  // Basic validation - check if it looks like valid SVG
  if (!svg.includes('<svg') || !svg.includes('</svg>')) {
    return (
      <div className={`bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg p-4 ${className}`}>
        <div className="flex items-start gap-2">
          <span className="text-red-500">⚠️</span>
          <div>
            <p className="text-sm font-medium text-red-700 dark:text-red-300">Invalid SVG</p>
            <p className="text-xs text-red-600 dark:text-red-400 mt-1">The visual data is not valid SVG format.</p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div
      className={`svg-container bg-gray-800 rounded-lg overflow-hidden animate-fade-in ${className}`}
      style={{
        animation: 'fadeInScale 0.3s ease-out forwards',
      }}
    >
      {title && (
        <div className="px-4 py-2 border-b border-gray-700">
          <h3 className="text-sm font-medium text-gray-200">{title}</h3>
        </div>
      )}
      <div ref={ref} className="svg-content p-2" />
    </div>
  );
};

// Add CSS animation keyframes to document head (runs once)
if (typeof document !== 'undefined' && !document.getElementById('svg-animations')) {
  const style = document.createElement('style');
  style.id = 'svg-animations';
  style.textContent = `
    @keyframes fadeInScale {
      from {
        opacity: 0;
        transform: scale(0.98);
      }
      to {
        opacity: 1;
        transform: scale(1);
      }
    }
    
    .svg-container svg {
      width: 100%;
      height: auto;
      max-height: 500px;
    }
    
    .svg-container svg text {
      user-select: none;
    }
    
    /* Interactive hover effects for nodes */
    .svg-container svg rect[filter],
    .svg-container svg circle[filter] {
      transition: transform 0.15s ease, filter 0.15s ease;
      cursor: pointer;
    }
    
    .svg-container svg rect[filter]:hover,
    .svg-container svg circle[filter]:hover {
      filter: url(#shadow) brightness(1.1);
    }
  `;
  document.head.appendChild(style);
}

export default SVGRenderer;
