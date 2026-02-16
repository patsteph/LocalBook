import React from 'react';
import { Studio } from '../Studio';
import { StudioState } from '../../hooks/useLayoutPersistence';

interface StudioMiniPlayerProps {
  notebookId: string | null;
  studio: StudioState;
  toggleStudio: () => void;
  setStudioTab: (tab: StudioState['activeTab']) => void;
  visualContent: string;
}

const TABS: { id: StudioState['activeTab']; icon: string; label: string }[] = [
  { id: 'documents', icon: '📄', label: 'Docs' },
  { id: 'audio', icon: '🎙️', label: 'Audio' },
  { id: 'quiz', icon: '🎯', label: 'Quiz' },
  { id: 'visual', icon: '🧠', label: 'Visual' },
  { id: 'writing', icon: '✍️', label: 'Write' },
];

export const StudioMiniPlayer: React.FC<StudioMiniPlayerProps> = ({
  notebookId,
  studio,
  toggleStudio,
  setStudioTab,
  visualContent,
}) => {
  return (
    <div
      className={`z-40 transition-all duration-300 ease-in-out ${
        studio.expanded
          ? 'fixed bottom-14 right-4 w-[420px] h-[70vh] max-h-[700px]'
          : 'flex-shrink-0 flex justify-end px-4 py-1.5 bg-gray-50 dark:bg-gray-900 border-t dark:border-gray-700'
      }`}
    >
      {/* Expanded: full Studio interface */}
      {studio.expanded && (
        <div className="bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-xl shadow-2xl flex flex-col h-full overflow-hidden">
          {/* Header */}
          <div className="flex items-center justify-between px-3 py-2 border-b dark:border-gray-700 bg-gray-50 dark:bg-gray-900 rounded-t-xl flex-shrink-0">
            <span className="text-xs font-semibold text-gray-700 dark:text-gray-300">Studio</span>
            <button
              onClick={toggleStudio}
              className="p-1 rounded hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-400 hover:text-gray-600"
              title="Collapse Studio"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
              </svg>
            </button>
          </div>
          {/* Studio content */}
          <div className="flex-1 overflow-hidden">
            <Studio
              notebookId={notebookId}
              initialVisualContent={visualContent}
              initialTab={studio.activeTab}
              onTabChange={(tab) => setStudioTab(tab)}
            />
          </div>
        </div>
      )}

      {/* Collapsed: compact icon bar */}
      {!studio.expanded && (
        <div className="bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-full shadow-lg flex items-center gap-1 px-2 py-1.5">
          {TABS.map(tab => (
            <button
              key={tab.id}
              onClick={() => setStudioTab(tab.id)}
              className={`p-2 rounded-full transition-colors text-sm hover:bg-gray-100 dark:hover:bg-gray-700 ${
                studio.activeTab === tab.id ? 'bg-blue-50 dark:bg-blue-900/30' : ''
              }`}
              title={tab.label}
            >
              {tab.icon}
            </button>
          ))}
          <div className="w-px h-5 bg-gray-200 dark:bg-gray-700 mx-0.5" />
          <button
            onClick={toggleStudio}
            className="p-2 rounded-full hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-500 transition-colors"
            title="Expand Studio"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 15l7-7 7 7" />
            </svg>
          </button>
        </div>
      )}
    </div>
  );
};
