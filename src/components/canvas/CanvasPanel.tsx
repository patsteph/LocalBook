import React, { useState, useRef, useEffect } from 'react';
import { PanelView, VIEW_LABELS, VIEW_ICONS, countLeaves } from './types';
import { useCanvas } from './CanvasContext';
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
            <Settings onClose={() => ctx.closePanel(panelId)} />
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
      <div className="flex items-center justify-between px-2 py-1 bg-gray-50 dark:bg-gray-900 border-b dark:border-gray-700 flex-shrink-0 min-h-[32px]">
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
        </div>
      </div>

      {/* Panel content */}
      <div className="flex-1 overflow-hidden">
        {renderContent()}
      </div>
    </div>
  );
};
