import React, { useState } from 'react';
import { PanelView } from './types';
import { useAppShell } from './CanvasContext';
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

export const CanvasPanel: React.FC<CanvasPanelProps> = ({ panelId, view, panelProps }) => {
  const ctx = useAppShell();
  const [webSearchTab, setWebSearchTab] = useState<'web' | 'site'>('web');
  const [insightTab, setInsightTab] = useState<'themes' | 'journey'>('themes');

  const renderContent = () => {
    switch (view) {
      case 'chat':
        // Chat is always mounted below — this case just returns null
        return null;

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
        // These now render in the universal canvas workspace.
        // If somehow reached as standalone panels, redirect to canvas items + close.
        ctx.openPanel(view, panelProps);
        ctx.closePanel(panelId);
        return null;

      default:
        return <div className="flex items-center justify-center h-full text-gray-400">Select a view</div>;
    }
  };

  return (
    <div className="flex flex-col h-full bg-white dark:bg-gray-800 overflow-hidden">
      {/* Panel content — header now lives in unified top bar in App.tsx */}
      <div className="flex-1 overflow-hidden relative">
        {/* Chat is always mounted to preserve state; hidden when another view is active */}
        <div className={`absolute inset-0 ${view === 'chat' ? '' : 'invisible pointer-events-none'}`}>
          <div className="relative h-full">
            <ChatInterface
              notebookId={ctx.selectedNotebookId}
              llmProvider={ctx.selectedLLMProvider}
              onOpenWebSearch={(query) => ctx.openWebResearch(query)}
              prefillQuery={ctx.chatPrefillQuery}
            />
          </div>
        </div>
        {view !== 'chat' && renderContent()}
      </div>
    </div>
  );
};
