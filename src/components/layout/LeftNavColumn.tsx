import React from 'react';
import { DrawerState } from '../../hooks/useLayoutPersistence';
import { useCanvas } from '../canvas/CanvasContext';
import { NotebookManager } from '../NotebookManager';
import { SourceUpload } from '../SourceUpload';
import { SourcesList } from '../SourcesList';
import { CollectorPanel } from '../CollectorPanel';

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

  return (
    <div className="flex flex-col h-full bg-white dark:bg-gray-800 overflow-hidden">
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
        {/* Web Research Button */}
        <div className="px-4 py-3 border-b dark:border-gray-700">
          <button
            onClick={() => ctx.openWebResearch()}
            disabled={!selectedNotebookId}
            title="Search the web or paste URLs to add to your research"
            className="w-full flex items-center justify-center gap-2 px-3 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-md text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
            </svg>
            <span>Web Research</span>
          </button>
        </div>
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

      {/* Spacer fills remaining height */}
      <div className="flex-1" />
    </div>
  );
};
