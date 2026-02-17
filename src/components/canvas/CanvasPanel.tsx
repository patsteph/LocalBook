import React, { useState, useRef, useEffect } from 'react';
import { PanelView, VIEW_LABELS, VIEW_ICONS, countLeaves, findFirstLeafId } from './types';
import { useCanvas } from './CanvasContext';
import { openUrl } from '@tauri-apps/plugin-opener';
import { API_BASE_URL } from '../../services/api';
import { ChatInterface } from '../ChatInterface';
import { Timeline } from '../Timeline';
import { Constellation3D } from '../Constellation3D';
import { ThemesPanel } from '../ThemesPanel';
import { ExplorationPanel } from '../ExplorationPanel';
import { FindingsPanel } from '../FindingsPanel';
import { CuratorPanel } from '../CuratorPanel';
import { Settings } from '../Settings';
import { LLMSelector } from '../LLMSelector';
import { EmbeddingSelector } from '../EmbeddingSelector';
import { WebSearchResults } from '../WebSearchResults';
import { SiteSearch } from '../SiteSearch';

interface CanvasPanelProps {
  panelId: string;
  view: PanelView;
  panelProps?: Record<string, any>;
}

const MAIN_VIEWS: PanelView[] = ['chat', 'constellation', 'timeline', 'findings', 'curator'];

export const CanvasPanel: React.FC<CanvasPanelProps> = ({ panelId, view, panelProps }) => {
  const ctx = useCanvas();
  const [showViewMenu, setShowViewMenu] = useState(false);
  const [webSearchTab, setWebSearchTab] = useState<'web' | 'site'>('web');
  const [insightTab, setInsightTab] = useState<'themes' | 'journey'>('themes');
  const menuRef = useRef<HTMLDivElement>(null);
  const leafCount = countLeaves(ctx.layout);
  const canSplit = leafCount < 4;
  const isFirstLeaf = findFirstLeafId(ctx.layout) === panelId;

  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setShowViewMenu(false);
      }
    };
    if (showViewMenu) document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, [showViewMenu]);

  const renderContent = () => {
    switch (view) {
      case 'chat':
        return (
          <ChatInterface
            notebookId={ctx.selectedNotebookId}
            llmProvider={ctx.selectedLLMProvider}
            onOpenWebSearch={(query) => ctx.openWebResearch(query)}
            prefillQuery={ctx.chatPrefillQuery}
          />
        );

      case 'constellation':
        return (
          <div className="flex h-full">
            <div className="w-72 border-r border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 flex-shrink-0 overflow-hidden flex flex-col">
              <div className="flex border-b border-gray-200 dark:border-gray-700">
                <button
                  onClick={() => setInsightTab('themes')}
                  className={`flex-1 px-3 py-2 text-xs font-medium transition-colors ${
                    insightTab === 'themes'
                      ? 'text-purple-600 dark:text-purple-400 border-b-2 border-purple-600'
                      : 'text-gray-500 dark:text-gray-400 hover:text-gray-700'
                  }`}
                >
                  🎯 Themes
                </button>
                <button
                  onClick={() => setInsightTab('journey')}
                  className={`flex-1 px-3 py-2 text-xs font-medium transition-colors ${
                    insightTab === 'journey'
                      ? 'text-blue-600 dark:text-blue-400 border-b-2 border-blue-600'
                      : 'text-gray-500 dark:text-gray-400 hover:text-gray-700'
                  }`}
                >
                  🧭 Journey
                </button>
              </div>
              <div className="flex-1 overflow-hidden">
                {insightTab === 'themes' ? (
                  <ThemesPanel
                    notebookId={ctx.selectedNotebookId}
                    onConceptClick={(concept, relatedConcepts) => {
                      const query = relatedConcepts && relatedConcepts.length > 0
                        ? `Tell me about ${concept} and how it relates to ${relatedConcepts.join(', ')}`
                        : `Tell me about ${concept}`;
                      ctx.setChatPrefillQuery(query);
                      ctx.navigateToChat();
                    }}
                  />
                ) : (
                  <ExplorationPanel
                    notebookId={ctx.selectedNotebookId}
                    onQueryClick={(query) => {
                      ctx.setChatPrefillQuery(query);
                      ctx.navigateToChat();
                    }}
                    onTopicClick={(topic) => {
                      ctx.setChatPrefillQuery(`Tell me more about ${topic}`);
                      ctx.navigateToChat();
                    }}
                  />
                )}
              </div>
            </div>
            <div className="flex-1 relative">
              <Constellation3D
                notebookId={ctx.selectedNotebookId}
                selectedSourceId={ctx.selectedSourceId}
                rightSidebarCollapsed={true}
              />
            </div>
          </div>
        );

      case 'timeline':
        return <Timeline notebookId={ctx.selectedNotebookId} sourcesRefreshTrigger={ctx.refreshSources} />;

      case 'findings':
        return <FindingsPanel notebookId={ctx.selectedNotebookId} />;

      case 'curator':
        return <CuratorPanel notebookId={ctx.selectedNotebookId} morningBrief={ctx.curatorBriefData} />;

      case 'settings':
        return (
          <div className="p-6 overflow-y-auto h-full">
            <Settings />
          </div>
        );

      case 'llm-selector':
        return (
          <div className="p-6 overflow-y-auto h-full">
            <LLMSelector
              selectedProvider={ctx.selectedLLMProvider}
              onProviderChange={(provider) => {
                ctx.setSelectedLLMProvider(provider);
                ctx.closePanel(panelId);
              }}
            />
          </div>
        );

      case 'embedding-selector':
        return (
          <div className="p-6 overflow-y-auto h-full">
            <EmbeddingSelector
              notebookId={ctx.selectedNotebookId}
              onModelChange={() => ctx.triggerSourcesRefresh()}
            />
          </div>
        );

      case 'web-research':
        return (
          <div className="flex flex-col h-full">
            <div className="flex border-b border-gray-200 dark:border-gray-700">
              <button
                onClick={() => setWebSearchTab('web')}
                className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                  webSearchTab === 'web'
                    ? 'border-blue-500 text-blue-600 dark:text-blue-400'
                    : 'border-transparent text-gray-500 hover:text-gray-700 dark:text-gray-400'
                }`}
              >
                🌐 Web Search
              </button>
              <button
                onClick={() => setWebSearchTab('site')}
                className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                  webSearchTab === 'site'
                    ? 'border-blue-500 text-blue-600 dark:text-blue-400'
                    : 'border-transparent text-gray-500 hover:text-gray-700 dark:text-gray-400'
                }`}
              >
                🎯 Site Search
              </button>
            </div>
            <div className="flex-1 overflow-hidden">
              {ctx.selectedNotebookId && (
                webSearchTab === 'web' ? (
                  <WebSearchResults
                    notebookId={ctx.selectedNotebookId}
                    onSourceAdded={() => { ctx.triggerSourcesRefresh(); ctx.triggerNotebooksRefresh(); }}
                    initialQuery={panelProps?.initialQuery || ''}
                  />
                ) : (
                  <SiteSearch
                    notebookId={ctx.selectedNotebookId}
                    onSourceAdded={() => { ctx.triggerSourcesRefresh(); ctx.triggerNotebooksRefresh(); }}
                    initialQuery={panelProps?.initialQuery || ''}
                  />
                )
              )}
            </div>
          </div>
        );

      case 'content-viewer':
      case 'quiz-viewer':
      case 'visual-viewer':
        return (
          <div className="p-6 overflow-y-auto h-full">
            <div className="prose dark:prose-invert max-w-none" dangerouslySetInnerHTML={{ __html: panelProps?.content || '' }} />
          </div>
        );

      default:
        return <div className="flex items-center justify-center h-full text-gray-400">Select a view</div>;
    }
  };

  return (
    <div className="flex flex-col h-full bg-white dark:bg-gray-800 overflow-hidden">
      {/* Panel header bar */}
      <div className="flex items-center justify-between px-2 py-1.5 bg-gray-50 dark:bg-gray-900 border-b dark:border-gray-700 flex-shrink-0">
        <div className="relative" ref={menuRef}>
          <button
            onClick={() => setShowViewMenu(!showViewMenu)}
            className="flex items-center gap-1.5 px-2 py-0.5 text-xs font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-700 rounded transition-colors"
          >
            <span>{VIEW_ICONS[view]}</span>
            <span>{VIEW_LABELS[view]}</span>
            <svg className="w-3 h-3 opacity-50" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
            </svg>
          </button>

          {showViewMenu && (
            <div className="absolute top-full left-0 mt-1 w-48 bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-lg shadow-lg z-50 py-1">
              <div className="px-2 py-1 text-[10px] font-semibold text-gray-400 uppercase tracking-wider">Views</div>
              {MAIN_VIEWS.map(v => (
                <button
                  key={v}
                  onClick={() => { ctx.changePanelView(panelId, v); setShowViewMenu(false); }}
                  className={`w-full text-left px-3 py-1.5 text-xs flex items-center gap-2 hover:bg-gray-100 dark:hover:bg-gray-700 ${
                    v === view ? 'text-blue-600 dark:text-blue-400 font-medium' : 'text-gray-700 dark:text-gray-300'
                  }`}
                >
                  <span>{VIEW_ICONS[v]}</span> {VIEW_LABELS[v]}
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="flex items-center gap-0.5">
          {canSplit && (
            <>
              <button
                onClick={() => ctx.splitPanel(panelId, 'vertical', 'chat')}
                className="p-1 rounded hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
                title="Split right"
              >
                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 3v18m12-18H3" />
                </svg>
              </button>
              <button
                onClick={() => ctx.splitPanel(panelId, 'horizontal', 'chat')}
                className="p-1 rounded hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
                title="Split down"
              >
                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 12h18M3 3v18" />
                </svg>
              </button>
            </>
          )}
          {leafCount > 1 && (
            <button
              onClick={() => ctx.closePanel(panelId)}
              className="p-1 rounded hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
              title="Close panel"
            >
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          )}

          {/* Global utility icons — only on first panel */}
          {isFirstLeaf && (
            <>
              <div className="w-px h-5 bg-gray-200 dark:bg-gray-700 mx-1.5" />
              <button
                onClick={ctx.openLLMSelector}
                className="p-1.5 rounded hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors"
                title="Select AI Brain"
              >
                <svg className="w-5 h-5 text-purple-600 dark:text-purple-400" fill="currentColor" viewBox="0 0 512 512">
                  <path d="M184 0c30.9 0 56 25.1 56 56V456c0 30.9-25.1 56-56 56c-28.9 0-52.7-21.9-55.7-50.1c-5.2 1.4-10.7 2.1-16.3 2.1c-35.3 0-64-28.7-64-64c0-7.4 1.3-14.6 3.6-21.2C21.4 367.4 0 338.2 0 304c0-31.9 18.7-59.5 45.8-72.3C37.1 220.8 32 207 32 192c0-30.7 21.6-56.3 50.4-62.6C80.8 123.9 80 118 80 112c0-29.9 20.6-55.1 48.3-62.1C131.3 21.9 155.1 0 184 0zM328 0c28.9 0 52.6 21.9 55.7 49.9c27.8 7 48.3 32.1 48.3 62.1c0 6-.8 11.9-2.4 17.4c28.8 6.2 50.4 31.9 50.4 62.6c0 15-5.1 28.8-13.8 39.7C493.3 244.5 512 272.1 512 304c0 34.2-21.4 63.4-51.6 74.8c2.3 6.6 3.6 13.8 3.6 21.2c0 35.3-28.7 64-64 64c-5.6 0-11.1-.7-16.3-2.1c-3 28.2-26.8 50.1-55.7 50.1c-30.9 0-56-25.1-56-56V56c0-30.9 25.1-56 56-56z"/>
                </svg>
              </button>
              <button
                onClick={ctx.openEmbeddingSelector}
                className="p-1.5 rounded hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors"
                title="Select Embedding Model"
              >
                <svg className="w-5 h-5 text-blue-600 dark:text-blue-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
                </svg>
              </button>
              <button
                onClick={() => openUrl(`${API_BASE_URL}/health/portal`)}
                className="p-1.5 rounded hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors"
                title="System Health"
              >
                <svg className="w-5 h-5 text-green-600 dark:text-green-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
                </svg>
              </button>
              <button
                onClick={ctx.openSettings}
                className="p-1.5 rounded hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors"
                title="Settings"
              >
                <svg className="w-5 h-5 text-gray-700 dark:text-gray-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                </svg>
              </button>
              <button
                onClick={ctx.toggleDarkMode}
                className="p-1.5 rounded hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors"
                title={ctx.darkMode ? 'Switch to light mode' : 'Switch to dark mode'}
              >
                {ctx.darkMode ? (
                  <svg className="w-5 h-5 text-yellow-500" fill="currentColor" viewBox="0 0 20 20">
                    <path fillRule="evenodd" d="M10 2a1 1 0 011 1v1a1 1 0 11-2 0V3a1 1 0 011-1zm4 8a4 4 0 11-8 0 4 4 0 018 0zm-.464 4.95l.707.707a1 1 0 001.414-1.414l-.707-.707a1 1 0 00-1.414 1.414zm2.12-10.607a1 1 0 010 1.414l-.706.707a1 1 0 11-1.414-1.414l.707-.707a1 1 0 011.414 0zM17 11a1 1 0 100-2h-1a1 1 0 100 2h1zm-7 4a1 1 0 011 1v1a1 1 0 11-2 0v-1a1 1 0 011-1zM5.05 6.464A1 1 0 106.465 5.05l-.708-.707a1 1 0 00-1.414 1.414l.707.707zm1.414 8.486l-.707.707a1 1 0 01-1.414-1.414l.707-.707a1 1 0 011.414 1.414zM4 11a1 1 0 100-2H3a1 1 0 000 2h1z" clipRule="evenodd" />
                  </svg>
                ) : (
                  <svg className="w-5 h-5 text-gray-700" fill="currentColor" viewBox="0 0 20 20">
                    <path d="M17.293 13.293A8 8 0 016.707 2.707a8.001 8.001 0 1010.586 10.586z" />
                  </svg>
                )}
              </button>
            </>
          )}
        </div>
      </div>

      {/* Panel content */}
      <div className="flex-1 overflow-hidden">
        {renderContent()}
      </div>
    </div>
  );
};
