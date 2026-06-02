/**
 * StudioBar — the slim, always-visible Studio entry strip.
 *
 * Tier 5 (2026-06-02). Two variants, each tuned to its surface:
 *
 *  - `variant="bar"` (chat area): a PLAIN narrow strip sitting above the
 *    chat input. Centered text reads "Studio" with chevron-up indicators
 *    on either side. Matches the tested "Studio Actions ⌃" aesthetic
 *    from the original ChatActionBar — Studio complexity stays hidden
 *    behind a calm bar; clicking it pulls the unified drawer up from
 *    the bottom of the canvas (NOT a full-app overlay).
 *
 *  - `variant="leftnav"` (left rail bottom): rainbow gradient accent line
 *    on top (animates whenever a canvas item is generating/processing
 *    or generationStatus is generating). Below the rainbow, a thin
 *    "STUDIO" header strip with 5 type icons (Doc/Audio/Video/Visual/
 *    Quiz). Click any icon to open the drawer pre-selected to that type.
 *
 * Both bars open the same StudioDrawer, which is mounted inside the
 * canvas area (via CanvasPanel) so the drawer overlays only the canvas
 * — LeftNav and top nav stay visible/interactive.
 */
import React from 'react';
import { FileText, Mic, Video, Palette, Target, ChevronUp } from 'lucide-react';
import { useAppShell, useCanvasItems } from '../canvas/CanvasContext';

type StudioType = 'docs' | 'audio' | 'video' | 'visual' | 'quiz';

const TAB_ICONS: Array<{ id: StudioType; icon: React.ReactNode; label: string }> = [
  { id: 'docs',   icon: <FileText className="w-3 h-3" />, label: 'Docs' },
  { id: 'audio',  icon: <Mic className="w-3 h-3" />,      label: 'Audio' },
  { id: 'video',  icon: <Video className="w-3 h-3" />,    label: 'Video' },
  { id: 'visual', icon: <Palette className="w-3 h-3" />,  label: 'Visual' },
  { id: 'quiz',   icon: <Target className="w-3 h-3" />,   label: 'Quiz' },
];

interface StudioLauncherProps {
  variant?: 'bar' | 'leftnav';
}

export const StudioLauncher: React.FC<StudioLauncherProps> = ({ variant = 'bar' }) => {
  const { openStudio, selectedNotebookId, studioDrawerOpen, generationStatus } = useAppShell();
  const { canvasItems } = useCanvasItems();
  const disabled = !selectedNotebookId;
  const isLeftNav = variant === 'leftnav';

  // Rainbow accent — active when any canvas item is mid-flight OR an
  // app-level generation is happening. Same rule as the original Studio
  // bar so users see motion whenever work is observable, regardless of
  // which surface marked it.
  const hasActiveWork = canvasItems.some(
    item => item.status === 'generating' || item.status === 'processing'
  );
  const effectiveStatus = hasActiveWork ? 'generating' : generationStatus;

  const open = (type: StudioType = 'docs') => {
    if (disabled) return;
    openStudio(type);
  };

  // ─── Chat variant: plain narrow bar ──────────────────────────────────
  if (!isLeftNav) {
    return (
      <button
        onClick={() => open('docs')}
        disabled={disabled}
        className="w-full flex items-center justify-center gap-1.5 py-1 text-[10px] font-medium text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300 border-t border-gray-100 dark:border-gray-700/50 bg-white dark:bg-gray-800 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        title={disabled ? 'Select a notebook first' : 'Open Studio'}
      >
        <ChevronUp className="w-3 h-3" />
        <span>{studioDrawerOpen ? 'Hide Studio' : 'Studio'}</span>
        <ChevronUp className="w-3 h-3" />
      </button>
    );
  }

  // ─── LeftNav variant: rainbow + STUDIO header + tab icons ────────────
  return (
    <div className="flex-shrink-0 flex flex-col border-t border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800">
      {/* Rainbow gradient — animates during generation. Re-uses the
          .rainbow-line CSS that's been there from the original Studio bar. */}
      <div
        className={`h-[3px] flex-shrink-0 rainbow-line ${
          effectiveStatus === 'generating' ? 'rainbow-line--generating' :
          effectiveStatus === 'complete' ? 'rainbow-line--complete' :
          effectiveStatus === 'error' ? 'rainbow-line--error' : ''
        }`}
        style={{
          background: effectiveStatus === 'generating'
            ? 'linear-gradient(90deg, rgba(244,114,182,0.7), rgba(251,146,60,0.6), rgba(250,204,21,0.5), rgba(74,222,128,0.6), rgba(96,165,250,0.7), rgba(167,139,250,0.7))'
            : effectiveStatus === 'complete'
            ? 'linear-gradient(90deg, rgba(74,222,128,0.8), rgba(96,165,250,0.8), rgba(167,139,250,0.8))'
            : 'linear-gradient(90deg, rgba(244,114,182,0.25), rgba(251,146,60,0.2), rgba(250,204,21,0.15), rgba(74,222,128,0.2), rgba(96,165,250,0.25), rgba(167,139,250,0.25))',
        }}
      />
      <button
        onClick={() => open('docs')}
        disabled={disabled}
        className="w-full flex items-center justify-between px-3 h-11 hover:bg-gray-50 dark:hover:bg-gray-900/40 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        title={disabled ? 'Select a notebook first' : 'Open Studio'}
      >
        <div className="flex items-center gap-2">
          <span className="text-[11px] font-semibold text-gray-400 dark:text-gray-500 uppercase tracking-wider">
            Studio
          </span>
          <div className="flex items-center gap-0.5 ml-1">
            {TAB_ICONS.map(tab => (
              <span
                key={tab.id}
                onClick={(e) => {
                  e.stopPropagation();
                  e.preventDefault();
                  open(tab.id);
                }}
                role="button"
                tabIndex={disabled ? -1 : 0}
                onKeyDown={(e) => {
                  if ((e.key === 'Enter' || e.key === ' ') && !disabled) {
                    e.preventDefault();
                    open(tab.id);
                  }
                }}
                className={`px-1.5 py-0.5 rounded text-xs transition-colors text-gray-400 dark:text-gray-500 ${
                  disabled
                    ? 'cursor-not-allowed'
                    : 'cursor-pointer hover:text-gray-700 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800'
                }`}
                title={`Open Studio — ${tab.label}`}
              >
                {tab.icon}
              </span>
            ))}
          </div>
        </div>
        <ChevronUp className="w-3 h-3 text-gray-400 dark:text-gray-500 flex-shrink-0" />
      </button>
    </div>
  );
};
