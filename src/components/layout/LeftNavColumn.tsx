import React, { useState } from 'react';
import { FileText, Mic, Video, Target, Brain, PenTool, Globe, BookOpen, Search, FileBox, Archive, ChevronDown } from 'lucide-react';
import { DrawerState, StudioState } from '../../hooks/useLayoutPersistence';
import { useCanvas } from '../canvas/CanvasContext';
import { NotebookManager } from '../NotebookManager';
import { SourceUpload } from '../SourceUpload';
import { SourcesList } from '../SourcesList';
import { CollectorPanel } from '../CollectorPanel';
import { CollectionTombstone } from '../collector/CollectionTombstone';
import { Studio } from '../Studio';
import { Modal } from '../shared/Modal';
import { WebSearchResults } from '../WebSearchResults';
import { SiteSearch } from '../SiteSearch';

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
  studio: StudioState;
  toggleStudio: () => void;
  setStudioTab: (tab: StudioState['activeTab']) => void;
  visualContent: string;
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

const DrawerSection: React.FC<DrawerSectionProps> = ({ title, icon, isOpen, onToggle, children, badge, flexible }) => (
  <div className={`border-t border-gray-200 dark:border-gray-700 ${
    flexible && isOpen ? 'flex-1 min-h-0 flex flex-col overflow-hidden' : 'flex-shrink-0'
  }`}>
    <button
      onClick={onToggle}
      className="w-full flex items-center justify-between px-3 py-1.5 hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors flex-shrink-0"
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
      <div className={`animate-slide-down ${
        flexible ? 'flex-1 min-h-0 overflow-y-auto' : ''
      }`}>
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

const studioIconClass = 'w-3 h-3';
const STUDIO_TABS: { id: StudioState['activeTab']; icon: React.ReactNode; label: string }[] = [
  { id: 'documents', icon: <FileText className={studioIconClass} />, label: 'Docs' },
  { id: 'audio', icon: <Mic className={studioIconClass} />, label: 'Audio' },
  { id: 'video', icon: <Video className={studioIconClass} />, label: 'Video' },
  { id: 'visual', icon: <Brain className={studioIconClass} />, label: 'Visual' },
  { id: 'quiz', icon: <Target className={studioIconClass} />, label: 'Quiz' },
  { id: 'writing', icon: <PenTool className={studioIconClass} />, label: 'Write' },
];

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
  studio,
  toggleStudio,
  setStudioTab,
  visualContent,
}) => {
  const ctx = useCanvas();
  const [webResearchModal, setWebResearchModal] = useState<'web' | 'site' | null>(null);

  return (
    <div className="flex flex-col h-full w-full bg-white dark:bg-gray-800 overflow-hidden">
      {/* Drawers area — fills remaining space, scrolls when content exceeds available space */}
      <div className={`min-h-0 ${studio.expanded ? 'overflow-y-auto flex-shrink' : 'flex-1 flex flex-col overflow-hidden'}`}>
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
        />
      </DrawerSection>

      {/* Collection tombstone — surfaces pending items and stagnation status */}
      {selectedNotebookId && (
        <CollectionTombstone
          notebookId={selectedNotebookId}
          onOpenCollector={() => {
            if (!drawers.collector) toggleDrawer('collector');
          }}
        />
      )}

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
        <div className="border-t border-gray-100 dark:border-gray-700/50" />
        {selectedNotebookId && (
          <button
            onClick={() => {
              // Open a fresh note in the universal canvas
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
            className="w-full flex items-center gap-2.5 px-3 py-2 text-sm font-semibold text-blue-600 dark:text-blue-400 hover:bg-blue-50 dark:hover:bg-blue-900/20 transition-colors"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
            </svg>
            New Note
          </button>
        )}
        <div className="border-t border-gray-100 dark:border-gray-700/50" />
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

      {/* Studio — anchored to absolute bottom of column, expands upward */}
      <div
        className={`flex-shrink-0 flex flex-col transition-all duration-300 ease-in-out overflow-hidden ${
          studio.expanded ? 'flex-1 min-h-[45%]' : ''
        }`}
      >

        {/* Rainbow gradient accent line — responds to generation activity.
            Derives state from BOTH explicit generationStatus AND canvas items
            that are actively generating/processing (covers background tasks
            like video and audio that process after the API call returns). */}
        {(() => {
          const hasActiveWork = ctx.canvasItems.some(
            item => item.status === 'generating' || item.status === 'processing'
          );
          const effectiveStatus = hasActiveWork ? 'generating' : ctx.generationStatus;
          return (
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
          );
        })()}

        {/* Studio header bar — at bottom when collapsed, at top when expanded */}
        <button
          onClick={toggleStudio}
          className="w-full flex items-center justify-between px-3 h-11 bg-gray-50/80 dark:bg-gray-900/60 flex-shrink-0 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
        >
          <div className="flex items-center gap-1.5">
            <span className="text-xs font-semibold text-gray-400 uppercase tracking-wider">Studio</span>
            <div className="flex items-center gap-0.5 ml-1">
              {STUDIO_TABS.map(tab => (
                <span
                  key={tab.id}
                  onClick={(e) => { e.stopPropagation(); setStudioTab(tab.id); }}
                  className={`px-1.5 py-0.5 rounded-lg text-xs cursor-pointer transition-colors ${
                    studio.activeTab === tab.id
                      ? 'bg-blue-50 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400'
                      : 'text-gray-400 hover:text-gray-600 dark:hover:text-gray-300'
                  }`}
                  title={tab.label}
                >
                  {tab.icon}
                </span>
              ))}
            </div>
          </div>
          <svg
            className={`w-3.5 h-3.5 text-gray-400 transition-transform duration-200 ${studio.expanded ? '' : 'rotate-180'}`}
            fill="none" stroke="currentColor" viewBox="0 0 24 24"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
          </svg>
        </button>

        {/* Studio content — visible when expanded, BELOW the header */}
        {studio.expanded && (
          <div className="flex-1 min-h-0 overflow-hidden">
            <Studio
              notebookId={selectedNotebookId}
              initialVisualContent={visualContent}
              initialTab={studio.activeTab}
              onTabChange={(tab) => setStudioTab(tab)}
              hideHeader
              onContentGenerated={(content, skillName) => {
                ctx.openPanel('content-viewer', { content, title: skillName });
              }}
              onQuizGenerated={(quizHtml, topic) => {
                ctx.openPanel('quiz-viewer', { content: quizHtml, title: topic });
              }}
              onVisualGenerated={(mermaidCode, title) => {
                ctx.openPanel('visual-viewer', { content: mermaidCode, title });
              }}
              onGenerationStatus={ctx.setGenerationStatus}
            />
          </div>
        )}
      </div>

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
