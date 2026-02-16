import React from 'react';
import { DrawerState, StudioState } from '../../hooks/useLayoutPersistence';
import { useCanvas } from '../canvas/CanvasContext';
import { NotebookManager } from '../NotebookManager';
import { SourceUpload } from '../SourceUpload';
import { SourcesList } from '../SourcesList';
import { CollectorPanel } from '../CollectorPanel';
import { Studio } from '../Studio';

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
}

const DrawerSection: React.FC<DrawerSectionProps> = ({ title, icon, isOpen, onToggle, children, badge }) => (
  <div className="border-b dark:border-gray-700">
    <button
      onClick={onToggle}
      className="w-full flex items-center justify-between px-3 py-2 hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors"
    >
      <div className="flex items-center gap-2">
        <span className="text-gray-500 dark:text-gray-400">{icon}</span>
        <span className="text-xs font-semibold text-gray-700 dark:text-gray-300 uppercase tracking-wide">{title}</span>
        {badge !== undefined && badge > 0 && (
          <span className="px-1.5 py-0.5 text-[10px] font-medium bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300 rounded-full">
            {badge}
          </span>
        )}
      </div>
      <svg
        className={`w-4 h-4 text-gray-400 transition-transform duration-200 ${isOpen ? 'rotate-180' : ''}`}
        fill="none" stroke="currentColor" viewBox="0 0 24 24"
      >
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
      </svg>
    </button>
    {isOpen && (
      <div className="animate-in slide-in-from-top-1 duration-200">
        {children}
      </div>
    )}
  </div>
);

const WebResearchDrawerContent: React.FC<{ notebookId: string | null }> = ({ notebookId }) => {
  const ctx = useCanvas();
  const [query, setQuery] = React.useState('');

  const handleSearch = () => {
    if (!notebookId) return;
    ctx.openWebResearch(query || undefined);
  };

  return (
    <div className="px-3 py-2 space-y-2">
      <input
        type="text"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter') handleSearch(); }}
        placeholder="Search query or paste URL..."
        disabled={!notebookId}
        className="w-full px-2.5 py-1.5 text-xs border border-gray-300 dark:border-gray-600 rounded-md bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 placeholder-gray-400 focus:outline-none focus:ring-1 focus:ring-blue-500 disabled:opacity-50"
      />
      <div className="flex gap-2">
        <button
          onClick={() => handleSearch()}
          disabled={!notebookId}
          className="flex-1 flex items-center justify-center gap-1.5 px-2 py-1.5 bg-blue-600 hover:bg-blue-700 text-white rounded-md text-xs font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          🌐 Web Search
        </button>
        <button
          onClick={() => handleSearch()}
          disabled={!notebookId}
          className="flex-1 flex items-center justify-center gap-1.5 px-2 py-1.5 bg-gray-100 hover:bg-gray-200 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-700 dark:text-gray-300 rounded-md text-xs font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          🎯 Site Search
        </button>
      </div>
      {!notebookId && (
        <p className="text-[10px] text-gray-400 italic">Select a notebook first</p>
      )}
    </div>
  );
};

const STUDIO_TABS: { id: StudioState['activeTab']; icon: string; label: string }[] = [
  { id: 'documents', icon: '📄', label: 'Docs' },
  { id: 'audio', icon: '🎙️', label: 'Audio' },
  { id: 'quiz', icon: '🎯', label: 'Quiz' },
  { id: 'visual', icon: '🧠', label: 'Visual' },
  { id: 'writing', icon: '✍️', label: 'Write' },
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

  return (
    <div className="flex flex-col h-full w-full bg-white dark:bg-gray-800 overflow-hidden">
      {/* Drawers area — takes natural height, scrolls if needed, shrinks when Studio expands */}
      <div className="overflow-y-auto overflow-x-hidden flex-shrink">
      {/* Notebooks drawer */}
      <DrawerSection
        title="Notebooks"
        icon={
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" />
          </svg>
        }
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

      {/* Web Research drawer */}
      <DrawerSection
        title="Web Research"
        icon={
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
        }
        isOpen={drawers.webResearch}
        onToggle={() => toggleDrawer('webResearch')}
      >
        <WebResearchDrawerContent notebookId={selectedNotebookId} />
      </DrawerSection>

      {/* Sources drawer */}
      <DrawerSection
        title="Sources"
        icon={
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 21h10a2 2 0 002-2V9.414a1 1 0 00-.293-.707l-5.414-5.414A1 1 0 0012.586 3H7a2 2 0 00-2 2v14a2 2 0 002 2z" />
          </svg>
        }
        isOpen={drawers.sources}
        onToggle={() => toggleDrawer('sources')}
      >
        <SourceUpload
          notebookId={selectedNotebookId || ''}
          onUploadComplete={onUploadComplete}
        />
        <div className="max-h-[40vh] overflow-y-auto">
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

      {/* Collector drawer */}
      <DrawerSection
        title="Collector"
        icon={
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" />
          </svg>
        }
        isOpen={drawers.collector}
        onToggle={() => toggleDrawer('collector')}
      >
        <div className="max-h-[30vh] overflow-y-auto">
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

      {/* Studio — fills all remaining space below drawers, expands upward */}
      <div
        className={`flex-1 min-h-[36px] flex flex-col transition-all duration-300 ease-in-out overflow-hidden ${
          studio.expanded ? 'min-h-[45%]' : ''
        }`}
      >
        {/* Rainbow gradient accent line */}
        <div
          className="h-[3px] flex-shrink-0"
          style={{
            background: 'linear-gradient(90deg, rgba(244,114,182,0.25), rgba(251,146,60,0.2), rgba(250,204,21,0.15), rgba(74,222,128,0.2), rgba(96,165,250,0.25), rgba(167,139,250,0.25))',
          }}
        />

        {/* Studio header bar — always visible */}
        <button
          onClick={toggleStudio}
          className="w-full flex items-center justify-between px-3 h-9 bg-gray-50/80 dark:bg-gray-900/60 flex-shrink-0 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
        >
          <div className="flex items-center gap-1.5">
            <span className="text-[10px] font-semibold text-gray-400 uppercase tracking-wider">Studio</span>
            <div className="flex items-center gap-0.5 ml-1">
              {STUDIO_TABS.map(tab => (
                <span
                  key={tab.id}
                  onClick={(e) => { e.stopPropagation(); setStudioTab(tab.id); }}
                  className={`px-1.5 py-0.5 rounded text-[11px] cursor-pointer transition-colors ${
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

        {/* Studio content — visible when expanded */}
        {studio.expanded && (
          <div className="flex-1 min-h-0 overflow-hidden border-t dark:border-gray-700">
            <Studio
              notebookId={selectedNotebookId}
              initialVisualContent={visualContent}
              initialTab={studio.activeTab}
              onTabChange={(tab) => setStudioTab(tab)}
              hideHeader
            />
          </div>
        )}
      </div>
    </div>
  );
};
