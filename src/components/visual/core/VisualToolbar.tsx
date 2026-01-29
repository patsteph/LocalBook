/**
 * VisualToolbar.tsx - Action toolbar for visuals
 * 
 * Provides consistent actions across all visual surfaces:
 * - Save to Findings
 * - Open in Studio (full editing)
 * - Export (PNG/SVG)
 * - Regenerate
 * - Color palette selection
 */

import React, { useState } from 'react';

// Color palette options
export type ColorPaletteId = 'vibrant' | 'ocean' | 'sunset' | 'forest' | 'monochrome' | 'pastel';

export const COLOR_PALETTES: { id: ColorPaletteId; icon: string; label: string; colors: string[] }[] = [
  { id: 'vibrant', icon: '🌈', label: 'Vibrant', colors: ['#ef4444', '#f97316', '#eab308', '#22c55e', '#3b82f6', '#8b5cf6'] },
  { id: 'ocean', icon: '🌊', label: 'Ocean', colors: ['#0ea5e9', '#06b6d4', '#14b8a6', '#0d9488', '#0891b2'] },
  { id: 'sunset', icon: '🌅', label: 'Sunset', colors: ['#f97316', '#fb923c', '#fbbf24', '#f59e0b', '#dc2626'] },
  { id: 'forest', icon: '🌲', label: 'Forest', colors: ['#22c55e', '#16a34a', '#15803d', '#84cc16', '#65a30d'] },
  { id: 'monochrome', icon: '⬛', label: 'Mono', colors: ['#1f2937', '#374151', '#4b5563', '#6b7280', '#9ca3af'] },
  { id: 'pastel', icon: '🎀', label: 'Pastel', colors: ['#fecaca', '#fed7aa', '#fef08a', '#bbf7d0', '#bfdbfe', '#ddd6fe'] },
];

export interface VisualToolbarProps {
  onSave?: () => void;
  onOpenStudio?: () => void;
  onExport?: (format: 'png' | 'svg') => void;
  onRegenerate?: () => void;
  onPaletteChange?: (palette: ColorPaletteId) => void;
  currentPalette?: ColorPaletteId;
  compact?: boolean;
  className?: string;
  saved?: boolean;
}

export const VisualToolbar: React.FC<VisualToolbarProps> = ({
  onSave,
  onOpenStudio,
  onExport,
  onRegenerate,
  onPaletteChange,
  currentPalette,
  compact = false,
  className = '',
  saved = false,
}) => {
  const [showExportMenu, setShowExportMenu] = useState(false);
  const [showPaletteMenu, setShowPaletteMenu] = useState(false);

  const buttonBase = compact
    ? 'p-1.5 rounded hover:bg-gray-700 transition-colors'
    : 'p-2 rounded-lg hover:bg-gray-700 transition-colors flex items-center gap-1.5';

  const iconSize = compact ? 'w-4 h-4' : 'w-4 h-4';
  const textClass = compact ? 'hidden' : 'text-xs';

  return (
    <div className={`flex items-center gap-1 ${className}`}>
      {/* Save to Findings */}
      {onSave && (
        <button
          onClick={onSave}
          className={`${buttonBase} ${saved ? 'text-amber-400' : 'text-gray-400 hover:text-white'}`}
          title={saved ? 'Saved to Findings' : 'Save to Findings'}
        >
          <svg className={iconSize} fill={saved ? 'currentColor' : 'none'} stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 5a2 2 0 012-2h10a2 2 0 012 2v16l-7-3.5L5 21V5z" />
          </svg>
          <span className={textClass}>{saved ? 'Saved' : 'Save'}</span>
        </button>
      )}

      {/* Open in Studio */}
      {onOpenStudio && (
        <button
          onClick={onOpenStudio}
          className={`${buttonBase} text-gray-400 hover:text-white`}
          title="Edit in Studio"
        >
          <svg className={iconSize} fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
          </svg>
          <span className={textClass}>Studio</span>
        </button>
      )}

      {/* Export */}
      {onExport && (
        <div className="relative">
          <button
            onClick={() => setShowExportMenu(!showExportMenu)}
            className={`${buttonBase} text-gray-400 hover:text-white`}
            title="Export"
          >
            <svg className={iconSize} fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" />
            </svg>
            <span className={textClass}>Export</span>
          </button>
          
          {showExportMenu && (
            <div className="absolute right-0 top-full mt-1 bg-gray-800 border border-gray-700 rounded-lg shadow-lg z-10 min-w-[100px]">
              <button
                onClick={() => { onExport('png'); setShowExportMenu(false); }}
                className="w-full px-3 py-2 text-left text-sm text-gray-300 hover:bg-gray-700 rounded-t-lg"
              >
                PNG Image
              </button>
              <button
                onClick={() => { onExport('svg'); setShowExportMenu(false); }}
                className="w-full px-3 py-2 text-left text-sm text-gray-300 hover:bg-gray-700 rounded-b-lg"
              >
                SVG Vector
              </button>
            </div>
          )}
        </div>
      )}

      {/* Color Palette */}
      {onPaletteChange && (
        <div className="relative">
          <button
            onClick={() => setShowPaletteMenu(!showPaletteMenu)}
            className={`${buttonBase} text-purple-400 hover:text-purple-300`}
            title="Change Colors"
          >
            <svg className={iconSize} fill="none" stroke="url(#paletteGradient)" viewBox="0 0 24 24">
              <defs>
                <linearGradient id="paletteGradient" x1="0%" y1="0%" x2="100%" y2="100%">
                  <stop offset="0%" stopColor="#f97316" />
                  <stop offset="50%" stopColor="#22c55e" />
                  <stop offset="100%" stopColor="#3b82f6" />
                </linearGradient>
              </defs>
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 21a4 4 0 01-4-4V5a2 2 0 012-2h4a2 2 0 012 2v12a4 4 0 01-4 4zm0 0h12a2 2 0 002-2v-4a2 2 0 00-2-2h-2.343M11 7.343l1.657-1.657a2 2 0 012.828 0l2.829 2.829a2 2 0 010 2.828l-8.486 8.485M7 17h.01" />
            </svg>
            <span className={textClass}>Colors</span>
          </button>
          
          {showPaletteMenu && (
            <div className="absolute right-0 top-full mt-1 bg-gray-800 border border-gray-700 rounded-lg shadow-lg z-10 p-2 min-w-[140px]">
              <div className="text-xs text-gray-400 px-2 pb-1 mb-1 border-b border-gray-700">Color Theme</div>
              {COLOR_PALETTES.map((p) => (
                <button
                  key={p.id}
                  onClick={() => { onPaletteChange(p.id); setShowPaletteMenu(false); }}
                  className={`w-full px-2 py-1.5 text-left text-sm rounded flex items-center gap-2 ${
                    currentPalette === p.id 
                      ? 'bg-purple-600/30 text-purple-300' 
                      : 'text-gray-300 hover:bg-gray-700'
                  }`}
                >
                  <span>{p.icon}</span>
                  <span>{p.label}</span>
                  <div className="flex gap-0.5 ml-auto">
                    {p.colors.slice(0, 3).map((c, i) => (
                      <div key={i} className="w-2 h-2 rounded-full" style={{ backgroundColor: c }} />
                    ))}
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Regenerate */}
      {onRegenerate && (
        <button
          onClick={onRegenerate}
          className={`${buttonBase} text-gray-400 hover:text-white`}
          title="Regenerate"
        >
          <svg className={iconSize} fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
          </svg>
          <span className={textClass}>Refresh</span>
        </button>
      )}
    </div>
  );
};

export default VisualToolbar;
