import React, { useState } from 'react';
import { Button } from '../shared/Button';
import { Modal } from '../shared/Modal';
import { MermaidRenderer } from '../shared/MermaidRenderer';
import { exportService, SlideData } from '../../services/export';

type PptxTheme = 'light' | 'dark' | 'corporate' | 'academic';
type ModalView = 'formats' | 'slide-builder';

interface ExportModalProps {
  isOpen: boolean;
  onClose: () => void;
  onExport: (format: 'markdown' | 'html' | 'pdf' | 'pptx', pptxTheme?: PptxTheme) => void;
  exporting: boolean;
  notebookId: string | null;
}

const PPTX_THEMES: { id: PptxTheme; label: string; colors: string }[] = [
  { id: 'light', label: 'Light', colors: 'bg-white border-gray-300 text-gray-800' },
  { id: 'dark', label: 'Dark', colors: 'bg-gray-800 border-gray-600 text-gray-100' },
  { id: 'corporate', label: 'Corporate', colors: 'bg-blue-50 border-blue-300 text-blue-900' },
  { id: 'academic', label: 'Academic', colors: 'bg-amber-50 border-amber-300 text-amber-900' },
];

export const ExportModal: React.FC<ExportModalProps> = ({
  isOpen,
  onClose,
  onExport,
  exporting,
  notebookId,
}) => {
  const [pptxTheme, setPptxTheme] = useState<PptxTheme>('light');
  const [view, setView] = useState<ModalView>('formats');
  const [slides, setSlides] = useState<SlideData[]>([]);
  const [slideHistory, setSlideHistory] = useState<SlideData[][]>([]);
  const [revisionPrompt, setRevisionPrompt] = useState('');
  const [loading, setLoading] = useState(false);
  const [revising, setRevising] = useState(false);
  const [revisionCount, setRevisionCount] = useState(0);

  const handleGeneratePreview = async () => {
    if (!notebookId) return;
    setLoading(true);
    try {
      const result = await exportService.previewPptxSlides(notebookId, pptxTheme);
      setSlides(result.slides);
      setSlideHistory([]);
      setView('slide-builder');
      setRevisionCount(0);
    } catch (err) {
      console.error('Failed to generate preview:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleRevise = async () => {
    if (!notebookId || !revisionPrompt.trim() || slides.length === 0) return;
    setRevising(true);
    try {
      // Push current slides onto history stack before revision
      setSlideHistory(prev => [...prev, slides]);
      const result = await exportService.revisePptxSlides(notebookId, slides, revisionPrompt.trim(), pptxTheme);
      setSlides(result.slides);
      setRevisionPrompt('');
      setRevisionCount(c => c + 1);
    } catch (err) {
      console.error('Failed to revise:', err);
      // Pop the history entry we just pushed since revision failed
      setSlideHistory(prev => prev.slice(0, -1));
    } finally {
      setRevising(false);
    }
  };

  const handleUndo = () => {
    if (slideHistory.length === 0) return;
    const previous = slideHistory[slideHistory.length - 1];
    setSlideHistory(prev => prev.slice(0, -1));
    setSlides(previous);
    setRevisionCount(c => Math.max(0, c - 1));
  };

  const handleDownload = async () => {
    if (!notebookId || slides.length === 0) return;
    setLoading(true);
    try {
      const blob = await exportService.downloadPptxSlides(notebookId, slides, pptxTheme);
      await exportService.downloadBlob(blob, 'presentation.pptx');
      onClose();
    } catch (err) {
      console.error('Failed to download:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleClose = () => {
    setView('formats');
    setSlides([]);
    setSlideHistory([]);
    setRevisionPrompt('');
    setRevisionCount(0);
    onClose();
  };

  // Slide builder view
  if (view === 'slide-builder') {
    return (
      <Modal isOpen={isOpen} onClose={handleClose} title="Slide Deck Builder">
        <div className="p-4 max-h-[70vh] flex flex-col">
          {/* Theme row */}
          <div className="flex items-center gap-2 mb-3">
            <span className="text-xs text-gray-500 dark:text-gray-400">Theme:</span>
            {PPTX_THEMES.map((t) => (
              <button
                key={t.id}
                onClick={() => setPptxTheme(t.id)}
                className={`px-2 py-0.5 rounded-lg text-xs font-medium border transition-all ${t.colors} ${
                  pptxTheme === t.id ? 'ring-2 ring-blue-500 ring-offset-1' : 'opacity-60 hover:opacity-100'
                }`}
              >
                {t.label}
              </button>
            ))}
            <span className="ml-auto text-xs text-gray-400">{slides.length} slides{revisionCount > 0 ? ` · ${revisionCount} revision${revisionCount > 1 ? 's' : ''}` : ''}</span>
          </div>

          {/* Slide cards */}
          <div className="flex-1 overflow-y-auto space-y-2 mb-3 min-h-0">
            {slides.map((slide, i) => (
              <div
                key={i}
                className={`rounded-lg border p-3 ${
                  slide.slide_type === 'title' || slide.slide_type === 'thankyou'
                    ? 'bg-gradient-to-r from-blue-50 to-indigo-50 dark:from-blue-900/20 dark:to-indigo-900/20 border-blue-200 dark:border-blue-700'
                    : slide.slide_type === 'visual_overview'
                    ? 'bg-gradient-to-r from-amber-50 to-orange-50 dark:from-amber-900/20 dark:to-orange-900/20 border-amber-200 dark:border-amber-700'
                    : 'bg-white dark:bg-gray-800 border-gray-200 dark:border-gray-700'
                }`}
              >
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-xs font-mono text-gray-400 bg-gray-100 dark:bg-gray-700 rounded-lg px-1">{i + 1}</span>
                  {slide.slide_type === 'visual_overview' && <span className="text-xs text-amber-600 dark:text-amber-400">&#x1f4ca;</span>}
                  <h4 className="text-sm font-semibold text-gray-900 dark:text-white truncate">{slide.title}</h4>
                </div>
                {slide.slide_type === 'visual_overview' && slide.mermaid_code ? (
                  <div className="mt-2 rounded-lg border border-amber-200 dark:border-amber-700 overflow-hidden bg-white dark:bg-gray-900" style={{ maxHeight: '200px' }}>
                    <MermaidRenderer code={slide.mermaid_code} className="w-full" />
                  </div>
                ) : slide.slide_type === 'visual_overview' && slide.bullets.length > 0 ? (
                  <div className="flex flex-wrap gap-1.5 ml-5 mt-1">
                    {slide.bullets.map((b, bi) => (
                      <span key={bi} className="text-xs px-2 py-0.5 rounded-full bg-amber-100 dark:bg-amber-800/30 text-amber-700 dark:text-amber-300 border border-amber-200 dark:border-amber-700">{b}</span>
                    ))}
                  </div>
                ) : slide.bullets.length > 0 ? (
                  <ul className="space-y-0.5 ml-5">
                    {slide.bullets.map((b, bi) => (
                      <li key={bi} className="text-xs text-gray-600 dark:text-gray-400 truncate">• {b}</li>
                    ))}
                  </ul>
                ) : null}
              </div>
            ))}
          </div>

          {/* Revision prompt */}
          <div className="border-t border-gray-200 dark:border-gray-700 pt-3">
            <div className="flex gap-2">
              <input
                type="text"
                value={revisionPrompt}
                onChange={(e) => setRevisionPrompt(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter' && !revising) handleRevise(); }}
                placeholder="Revise slides... e.g. &quot;make the summary more concise&quot;"
                className="flex-1 px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white placeholder-gray-400 focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                disabled={revising}
              />
              <button
                onClick={handleRevise}
                disabled={revising || !revisionPrompt.trim()}
                className="px-3 py-2 text-sm font-medium bg-amber-500 hover:bg-amber-600 text-white rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1"
              >
                {revising ? (
                  <><div className="animate-spin rounded-full h-3 w-3 border-b-2 border-white"></div> Revising</>
                ) : (
                  <>&#x270F;&#xFE0F; Revise</>
                )}
              </button>
            </div>
            <div className="flex items-center justify-between mt-3">
              <div className="flex items-center gap-3">
                <button
                  onClick={() => { setView('formats'); setSlides([]); setSlideHistory([]); }}
                  className="text-sm text-gray-500 hover:text-gray-700 dark:hover:text-gray-300"
                >
                  ← Back to formats
                </button>
                {slideHistory.length > 0 && (
                  <button
                    onClick={handleUndo}
                    className="text-sm text-red-500 hover:text-red-700 dark:text-red-400 dark:hover:text-red-300 flex items-center gap-1"
                  >
                    ↩ Undo
                  </button>
                )}
              </div>
              <button
                onClick={handleDownload}
                disabled={loading || slides.length === 0}
                className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
              >
                {loading ? (
                  <><div className="animate-spin rounded-full h-3 w-3 border-b-2 border-white"></div> Downloading...</>
                ) : (
                  <>Download .pptx</>
                )}
              </button>
            </div>
          </div>
        </div>
      </Modal>
    );
  }

  // Format selection view (default)
  return (
    <Modal isOpen={isOpen} onClose={handleClose} title="Export Notebook">
      <div className="p-4">
        <p className="text-sm text-gray-600 dark:text-gray-400 mb-4">
          Choose the format to export your notebook:
        </p>
        <div className="space-y-3">
          <button
            onClick={(e) => { e.preventDefault(); e.stopPropagation(); onExport('markdown'); }}
            disabled={exporting}
            className="w-full flex items-center justify-between p-4 border border-gray-300 dark:border-gray-600 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <div className="flex items-center gap-3">
              <svg className="w-6 h-6 text-gray-600 dark:text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
              </svg>
              <div className="text-left">
                <div className="font-medium text-gray-900 dark:text-white">Markdown (.md)</div>
                <div className="text-sm text-gray-500 dark:text-gray-400">Plain text with formatting</div>
              </div>
            </div>
            <svg className="w-5 h-5 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
            </svg>
          </button>

          <button
            onClick={(e) => { e.preventDefault(); e.stopPropagation(); onExport('pdf'); }}
            disabled={exporting}
            className="w-full flex items-center justify-between p-4 border border-gray-300 dark:border-gray-600 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <div className="flex items-center gap-3">
              <svg className="w-6 h-6 text-gray-600 dark:text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 17h2a2 2 0 002-2v-4a2 2 0 00-2-2H5a2 2 0 00-2 2v4a2 2 0 002 2h2m2 4h6a2 2 0 002-2v-4a2 2 0 00-2-2H9a2 2 0 00-2 2v4a2 2 0 002 2zm8-12V5a2 2 0 00-2-2H9a2 2 0 00-2 2v4h10z" />
              </svg>
              <div className="text-left">
                <div className="font-medium text-gray-900 dark:text-white">PDF (.pdf)</div>
                <div className="text-sm text-gray-500 dark:text-gray-400">Opens browser print dialog</div>
              </div>
            </div>
            <svg className="w-5 h-5 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
            </svg>
          </button>

          {/* PPTX — opens Slide Deck Builder */}
          <div className="border border-gray-300 dark:border-gray-600 rounded-lg overflow-hidden">
            <button
              onClick={(e) => { e.preventDefault(); e.stopPropagation(); handleGeneratePreview(); }}
              disabled={exporting || loading || !notebookId}
              className="w-full flex items-center justify-between p-4 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <div className="flex items-center gap-3">
                <svg className="w-6 h-6 text-gray-600 dark:text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 12l3-3 3 3 4-4M8 21l4-4 4 4M3 4h18M4 4h16v12a1 1 0 01-1 1H5a1 1 0 01-1-1V4z" />
                </svg>
                <div className="text-left">
                  <div className="font-medium text-gray-900 dark:text-white">PowerPoint (.pptx)</div>
                  <div className="text-sm text-gray-500 dark:text-gray-400">AI slides with prompt-based revisions</div>
                </div>
              </div>
              {loading ? (
                <div className="animate-spin rounded-full h-5 w-5 border-b-2 border-blue-600"></div>
              ) : (
                <svg className="w-5 h-5 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                </svg>
              )}
            </button>
          </div>
        </div>

        {exporting && (
          <div className="mt-4 flex items-center justify-center gap-2 text-gray-600 dark:text-gray-400">
            <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-blue-600"></div>
            <span>Exporting...</span>
          </div>
        )}

        <div className="mt-6 flex justify-end">
          <Button variant="secondary" onClick={handleClose} disabled={exporting}>
            Cancel
          </Button>
        </div>
      </div>
    </Modal>
  );
};
