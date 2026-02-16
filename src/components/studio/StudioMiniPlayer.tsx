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
      className={`flex-shrink-0 border-t dark:border-gray-700 bg-white dark:bg-gray-800 transition-all duration-300 ease-in-out overflow-hidden ${
        studio.expanded ? 'h-[45vh] max-h-[500px]' : ''
      }`}
    >
      {/* Bottom bar: always visible — tab icons + expand/collapse toggle */}
      <div className="flex items-center justify-between px-3 h-9 bg-gray-50 dark:bg-gray-900 border-b dark:border-gray-700 flex-shrink-0">
        <div className="flex items-center gap-0.5">
          <span className="text-[10px] font-semibold text-gray-400 uppercase tracking-wider mr-2">Studio</span>
          {TABS.map(tab => (
            <button
              key={tab.id}
              onClick={() => setStudioTab(tab.id)}
              className={`px-2 py-1 rounded transition-colors text-xs hover:bg-gray-200 dark:hover:bg-gray-700 ${
                studio.activeTab === tab.id
                  ? 'bg-blue-50 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400'
                  : 'text-gray-500 dark:text-gray-400'
              }`}
              title={tab.label}
            >
              <span className="mr-1">{tab.icon}</span>
              <span className="hidden sm:inline">{tab.label}</span>
            </button>
          ))}
        </div>
        <button
          onClick={toggleStudio}
          className="p-1 rounded hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-400 hover:text-gray-600 transition-colors"
          title={studio.expanded ? 'Collapse Studio' : 'Expand Studio'}
        >
          <svg className={`w-4 h-4 transition-transform duration-200 ${studio.expanded ? 'rotate-180' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 15l7-7 7 7" />
          </svg>
        </button>
      </div>

      {/* Expanded: full Studio content accordion */}
      {studio.expanded && (
        <div className="flex-1 overflow-hidden" style={{ height: 'calc(100% - 36px)' }}>
          <Studio
            notebookId={notebookId}
            initialVisualContent={visualContent}
            initialTab={studio.activeTab}
            onTabChange={(tab) => setStudioTab(tab)}
          />
        </div>
      )}
    </div>
  );
};
