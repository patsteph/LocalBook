import React, { useState, useEffect } from 'react';
import { onEvent } from '../../lib/events';
import { Target, Globe, BookOpen, Search, FileBox, Archive, ChevronDown } from 'lucide-react';
import { DrawerState } from '../../hooks/useLayoutPersistence';
import { useCanvas } from '../canvas/CanvasContext';
import { NotebookManager } from '../NotebookManager';
import { SourceUpload } from '../SourceUpload';
import { SourcesList } from '../SourcesList';
import { CollectorPanel } from '../CollectorPanel';
import { Modal } from '../shared/Modal';
import { WebSearchResults } from '../WebSearchResults';
import { SiteSearch } from '../SiteSearch';
import { StudioLauncher } from '../studio/StudioLauncher';

interface LeftNavColumnProps {
  selectedNotebookId: string | null;
  onNotebookSelect: (id: string | null) => void;
  refreshSources: number;
  refreshNotebooks: number;
  collectorRefreshKey: number;
  onCollectorConfigured: () => void;
  onUploadComplete: () => void;
  onSourcesChange: () => void;
  selectedSourceId: string | null;
  onSourceSelect: (id: string | null) => void;
  drawers: DrawerState;
  toggleDrawer: (drawer: keyof DrawerState) => void;
  selectedNotebookName: string;
  /** Reserved for future direct-launch from this surface (currently the
   *  StudioLauncher component reaches openStudio via context). */
  onOpenStudio?: (type?: 'docs' | 'audio' | 'video' | 'visual' | 'quiz' | 'cards') => void;
}

interface DrawerSectionProps {
  title: string;
  icon: React.ReactNode;
  isOpen: boolean;
  onToggle: () => void;
  children: React.ReactNode;
  badge?: number;
  flexible?: boolean;
}

// L4 (2026-06-09): unified scroll for the left nav. The previous design
// had "flexible" drawers (Sources, Collector) claim flex-1 + their own
// inner scroll container. When multiple drawers were open, fixed-size
// drawers (Notebooks, Web Research) pushed the flexible ones down with
// no way to scroll the *whole* nav as a single column.
//
// Now: every drawer renders at its natural content size, and the outer
// drawers container scrolls. The `flexible` prop is still accepted for
// backward compat but is no longer load-bearing — sticky section
// headers (a future polish) would let us re-introduce per-drawer
// behavior without breaking the unified scroll.
const DrawerSection: React.FC<DrawerSectionProps> = ({ title, icon, isOpen, onToggle, children, badge }) => (
  <div className="border-t border-gray-200 dark:border-gray-700 flex-shrink-0">
    <button
      onClick={onToggle}
      className="w-full flex items-center justify-between px-3 py-1.5 hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors sticky top-0 bg-white dark:bg-gray-800 z-10"
    >
      <div className="flex items-center gap-1.5">
        <span className="text-gray-400 dark:text-gray-500">{icon}</span>
        <span className="text-[11px] font-semibold text-gray-600 dark:text-gray-400 uppercase tracking-wide">{title}</span>
        {badge !== undefined && badge > 0 && (
          <span className="px-1.5 py-0.5 text-[10px] font-medium bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300 rounded-full">
            {badge}
          </span>
        )}
      </div>
      <ChevronDown
        className={`w-3.5 h-3.5 text-gray-400 transition-transform duration-200 ${isOpen ? 'rotate-180' : ''}`}
      />
    </button>
    {isOpen && (
      <div className="animate-slide-down">
        {children}
      </div>
    )}
  </div>
);

const WebResearchDrawerContent: React.FC<{ notebookId: string | null; onOpenModal: (tab: 'web' | 'site') => void }> = ({ notebookId, onOpenModal }) => {
  return (
    <div className="px-3 py-2 space-y-2">
      <div className="flex gap-2">
        <button
          onClick={() => onOpenModal('web')}
          disabled={!notebookId}
          className="flex-1 flex items-center justify-center gap-1.5 px-2 py-1.5 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-xs font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          <Globe className="w-3.5 h-3.5" /> Web Search
        </button>
        <button
          onClick={() => onOpenModal('site')}
          disabled={!notebookId}
          className="flex-1 flex items-center justify-center gap-1.5 px-2 py-1.5 bg-gray-100 hover:bg-gray-200 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-700 dark:text-gray-300 rounded-lg text-xs font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          <Target className="w-3.5 h-3.5" /> Site Search
        </button>
      </div>
      {!notebookId && (
        <p className="text-xs text-gray-400 italic">Select a notebook first</p>
      )}
    </div>
  );
};

export const LeftNavColumn: React.FC<LeftNavColumnProps> = ({
  selectedNotebookId,
  onNotebookSelect,
  refreshSources,
  refreshNotebooks,
  collectorRefreshKey,
  onCollectorConfigured,
  onUploadComplete,
  onSourcesChange,
  selectedSourceId,
  onSourceSelect,
  drawers,
  toggleDrawer,
  selectedNotebookName,
}) => {
  const ctx = useCanvas();
  const [webResearchModal, setWebResearchModal] = useState<'web' | 'site' | null>(null);
  const [webResearchInitialQuery, setWebResearchInitialQuery] = useState<string>('');

  // Bug fix (2026-06-01): chat-triggered "Yes, search the web" used to swap
  // the canvas panel for a web-research view. Now both the chat path and the
  // LeftNav drawer open the SAME modal via this event listener so behavior is
  // consistent across entry points.
  useEffect(() => {
    return onEvent('lb:openWebResearch', (detail) => {
      setWebResearchInitialQuery(detail.query || '');
      setWebResearchModal(detail.tab || 'web');
    });
  }, []);

  return (
    <div className="flex flex-col h-full w-full bg-white dark:bg-gray-800 overflow-hidden">
      {/* Drawers area — L4 (2026-06-09): switched from per-section
          internal scroll to single outer scroll so users with multiple
          drawers open can scroll the whole nav as one column instead
          of being trapped at the bottom of a non-scrolling notebook
          list. Drawer headers stick to the top while content scrolls. */}
      <div className="flex-1 flex flex-col overflow-y-auto min-h-0">
      {/* Notebooks drawer */}
      <DrawerSection
        title="Notebooks"
        icon={<BookOpen className="w-3.5 h-3.5" />}
        isOpen={drawers.notebooks}
        onToggle={() => toggleDrawer('notebooks')}
      >
        <NotebookManager
          onNotebookSelect={onNotebookSelect}
          selectedNotebookId={selectedNotebookId}
          refreshTrigger={refreshNotebooks}
          onCollectorConfigured={onCollectorConfigured}
          onNewNote={() => {
            if (!selectedNotebookId) return;
            ctx.clearCanvas();
            ctx.addCanvasItem({
              type: 'note',
              title: '',
              content: '',
              collapsed: false,
              metadata: { notebookId: selectedNotebookId },
            });
            ctx.navigateToChat();
          }}
        />
      </DrawerSection>

      {/* Web Research drawer */}
      <DrawerSection
        title="Web Research"
        icon={<Search className="w-3.5 h-3.5" />}
        isOpen={drawers.webResearch}
        onToggle={() => toggleDrawer('webResearch')}
      >
        <WebResearchDrawerContent notebookId={selectedNotebookId} onOpenModal={(tab) => setWebResearchModal(tab)} />
      </DrawerSection>

      {/* Sources drawer */}
      <DrawerSection
        title="Sources"
        icon={<FileBox className="w-3.5 h-3.5" />}
        isOpen={drawers.sources}
        onToggle={() => toggleDrawer('sources')}
        flexible
      >
        <SourceUpload
          notebookId={selectedNotebookId || ''}
          onUploadComplete={onUploadComplete}
        />
        <div>
          <SourcesList
            key={`${selectedNotebookId}-${refreshSources}`}
            notebookId={selectedNotebookId}
            onSourcesChange={onSourcesChange}
            selectedSourceId={selectedSourceId}
            onSourceSelect={(sourceId) => {
              onSourceSelect(selectedSourceId === sourceId ? null : sourceId);
            }}
          />
        </div>
      </DrawerSection>

      {/* Note editor now lives in the universal canvas */}

      {/* Collector drawer */}
      <DrawerSection
        title="Collector"
        icon={<Archive className="w-3.5 h-3.5" />}
        isOpen={drawers.collector}
        onToggle={() => toggleDrawer('collector')}
        flexible
      >
        <div>
          <CollectorPanel
            notebookId={selectedNotebookId}
            notebookName={selectedNotebookName}
            refreshKey={collectorRefreshKey}
            onSourcesRefresh={() => {
              ctx.triggerSourcesRefresh();
              ctx.triggerNotebooksRefresh();
            }}
          />
        </div>
      </DrawerSection>

      </div>

      {/* Studio launcher — 5 chips at the bottom of the column. Opens
          the unified StudioDrawer with the chosen type preselected. */}
      <StudioLauncher variant="leftnav" />

      {/* Web Research Modal Popup */}
      <Modal
        isOpen={webResearchModal !== null}
        onClose={() => setWebResearchModal(null)}
        title="Web Research"
        size="lg"
      >
        <div className="p-4">
          {/* Tab switcher */}
          <div className="flex gap-2 mb-4 border-b border-gray-200 dark:border-gray-700 pb-2">
            <button
              onClick={() => setWebResearchModal('web')}
              className={`px-3 py-1.5 text-sm font-medium rounded-t transition-colors ${
                webResearchModal === 'web'
                  ? 'text-blue-600 border-b-2 border-blue-600 dark:text-blue-400 dark:border-blue-400'
                  : 'text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200'
              }`}
            >
              <Globe className="w-3.5 h-3.5 inline-block mr-1" />Web Search
            </button>
            <button
              onClick={() => setWebResearchModal('site')}
              className={`px-3 py-1.5 text-sm font-medium rounded-t transition-colors ${
                webResearchModal === 'site'
                  ? 'text-blue-600 border-b-2 border-blue-600 dark:text-blue-400 dark:border-blue-400'
                  : 'text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200'
              }`}
            >
              <Target className="w-3.5 h-3.5 inline-block mr-1" />Site Search
            </button>
          </div>
          {/* Tab content */}
          {webResearchModal === 'web' && selectedNotebookId && (
            <WebSearchResults
              notebookId={selectedNotebookId}
              initialQuery={webResearchInitialQuery}
              onSourceAdded={() => { ctx.triggerSourcesRefresh(); ctx.triggerNotebooksRefresh(); }}
            />
          )}
          {webResearchModal === 'site' && selectedNotebookId && (
            <SiteSearch
              notebookId={selectedNotebookId}
              onSourceAdded={() => { ctx.triggerSourcesRefresh(); ctx.triggerNotebooksRefresh(); }}
            />
          )}
        </div>
      </Modal>
    </div>
  );
};
