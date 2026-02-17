import { useState, useEffect, useCallback, useMemo } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { Group, Panel, Separator } from 'react-resizable-panels';
import { LeftNavColumn } from './components/layout/LeftNavColumn';
import { CanvasWorkspace } from './components/canvas/CanvasWorkspace';
import { CanvasProvider, CanvasContextValue } from './components/canvas/CanvasContext';
import { LayoutNode, PanelView, countLeaves, replaceLeaf, removeLeaf, findLeaf } from './components/canvas/types';
import { useCanvasLayout, useDrawerState, useStudioState } from './hooks/useLayoutPersistence';
import { ToastContainer, ToastMessage } from './components/shared/Toast';
import { Modal } from './components/shared/Modal';
import { Settings } from './components/Settings';
import { LLMSelector } from './components/LLMSelector';
import { EmbeddingSelector } from './components/EmbeddingSelector';
import { API_BASE_URL } from './services/api';
import { prewarmMermaid } from './components/shared/MermaidRenderer';

function App() {
  const [selectedNotebookId, setSelectedNotebookId] = useState<string | null>(null);
  const [selectedNotebookName, setSelectedNotebookName] = useState<string>('Notebook');
  const [backendReady, setBackendReady] = useState(false);
  const [backendError, setBackendError] = useState<string | null>(null);
  const [backendStatusMessage, setBackendStatusMessage] = useState<string>('Initializing backend services...');
  const [startupProgress, setStartupProgress] = useState(0);
  const [isUpgrade, setIsUpgrade] = useState(false);
  const [currentVersion, setCurrentVersion] = useState<string | null>(null);
  const [refreshSources, setRefreshSources] = useState(0);
  const [refreshNotebooks, setRefreshNotebooks] = useState(0);
  const [collectorRefreshKey, setCollectorRefreshKey] = useState(0);
  const [darkMode, setDarkMode] = useState(false);
  const [selectedLLMProvider, setSelectedLLMProvider] = useState<string>(() => {
    return localStorage.getItem('llmProvider') || 'ollama';
  });
  const [chatPrefillQuery, setChatPrefillQuery] = useState<string>('');
  const [selectedSourceId, setSelectedSourceId] = useState<string | null>(null);
  const [toasts, setToasts] = useState<ToastMessage[]>([]);
  const [visualContent, setVisualContent] = useState<string>('');
  const [morningBrief, setMorningBrief] = useState<any>(null);
  const [curatorBriefData, setCuratorBriefData] = useState<any>(null);
  const [showSettingsModal, setShowSettingsModal] = useState(false);
  const [showLLMModal, setShowLLMModal] = useState(false);
  const [showEmbeddingModal, setShowEmbeddingModal] = useState(false);

  // Canvas layout, drawer, and studio state (persisted to localStorage)
  const { layout, setLayout } = useCanvasLayout(selectedNotebookId);
  const { drawers, toggleDrawer } = useDrawerState();
  const { studio, toggleStudio, setStudioTab } = useStudioState();

  // Toast management
  const addToast = useCallback((toast: Omit<ToastMessage, 'id'>) => {
    const id = `toast-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
    setToasts((prev) => [...prev, { ...toast, id }]);
  }, []);

  const dismissToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  // === Canvas operations ===
  const findFirstLeafId = useCallback((node: LayoutNode): string => {
    if (node.type === 'leaf') return node.id;
    return findFirstLeafId(node.children[0]);
  }, []);

  const changePanelView = useCallback((panelId: string, view: PanelView) => {
    setLayout(prev => replaceLeaf(prev, panelId, { type: 'leaf', id: panelId, view }));
  }, [setLayout]);

  const openPanel = useCallback((view: PanelView, props?: Record<string, any>) => {
    setLayout(prev => {
      if (countLeaves(prev) < 4) {
        const mainId = findFirstLeafId(prev);
        const currentLeaf = findLeaf(prev, mainId);
        const newId = `panel-${Date.now()}`;
        return replaceLeaf(prev, mainId, {
          type: 'split',
          direction: 'vertical',
          sizes: [50, 50],
          children: [
            { type: 'leaf', id: mainId, view: currentLeaf?.view || 'chat' },
            { type: 'leaf', id: newId, view, props },
          ],
        });
      }
      const mainId = findFirstLeafId(prev);
      return replaceLeaf(prev, mainId, { type: 'leaf', id: mainId, view, props });
    });
  }, [setLayout, findFirstLeafId]);

  const closePanel = useCallback((panelId: string) => {
    setLayout(prev => removeLeaf(prev, panelId) || { type: 'leaf', id: 'main', view: 'chat' as PanelView });
  }, [setLayout]);

  const splitPanel = useCallback((panelId: string, direction: 'horizontal' | 'vertical', newView: PanelView) => {
    setLayout(prev => {
      if (countLeaves(prev) >= 4) return prev;
      const leaf = findLeaf(prev, panelId);
      if (!leaf) return prev;
      const newId = `panel-${Date.now()}`;
      return replaceLeaf(prev, panelId, {
        type: 'split',
        direction,
        sizes: [50, 50],
        children: [
          { type: 'leaf', id: panelId, view: leaf.view, props: leaf.props },
          { type: 'leaf', id: newId, view: newView },
        ],
      });
    });
  }, [setLayout]);

  const navigateToChat = useCallback(() => {
    setLayout(prev => {
      const hasChat = (node: LayoutNode): boolean => {
        if (node.type === 'leaf') return node.view === 'chat';
        return hasChat(node.children[0]) || hasChat(node.children[1]);
      };
      if (hasChat(prev)) return prev;
      const mainId = findFirstLeafId(prev);
      return replaceLeaf(prev, mainId, { type: 'leaf', id: mainId, view: 'chat' });
    });
  }, [setLayout, findFirstLeafId]);

  const openWebResearch = useCallback((query?: string) => {
    openPanel('web-research', query ? { initialQuery: query } : undefined);
  }, [openPanel]);

  const openSettings = useCallback(() => setShowSettingsModal(true), []);
  const openLLMSelector = useCallback(() => setShowLLMModal(true), []);
  const openEmbeddingSelector = useCallback(() => setShowEmbeddingModal(true), []);

  const toggleDarkMode = useCallback(() => {
    setDarkMode(prev => {
      const next = !prev;
      if (next) {
        document.documentElement.classList.add('dark');
        localStorage.setItem('theme', 'dark');
      } else {
        document.documentElement.classList.remove('dark');
        localStorage.setItem('theme', 'light');
      }
      return next;
    });
  }, []);

  // === Canvas context value ===
  const canvasContext: CanvasContextValue = useMemo(() => ({
    selectedNotebookId,
    selectedNotebookName,
    selectedSourceId,
    setSelectedSourceId,
    selectedLLMProvider,
    setSelectedLLMProvider,
    refreshSources,
    triggerSourcesRefresh: () => setRefreshSources(prev => prev + 1),
    refreshNotebooks,
    triggerNotebooksRefresh: () => setRefreshNotebooks(prev => prev + 1),
    collectorRefreshKey,
    addToast,
    openPanel,
    closePanel,
    splitPanel,
    changePanelView,
    layout,
    openWebResearch,
    openSettings,
    openLLMSelector,
    openEmbeddingSelector,
    chatPrefillQuery,
    setChatPrefillQuery,
    navigateToChat,
    darkMode,
    toggleDarkMode,
    morningBrief,
    setMorningBrief,
    curatorBriefData,
    setCuratorBriefData,
  }), [
    selectedNotebookId, selectedNotebookName, selectedSourceId, selectedLLMProvider,
    refreshSources, refreshNotebooks, collectorRefreshKey, addToast,
    openPanel, closePanel, splitPanel, changePanelView, layout,
    openWebResearch, openSettings, openLLMSelector, openEmbeddingSelector,
    chatPrefillQuery, navigateToChat, darkMode, toggleDarkMode,
    morningBrief, curatorBriefData,
  ]);

  // Fetch notebook name when selection changes
  useEffect(() => {
    if (!selectedNotebookId) {
      setSelectedNotebookName('Notebook');
      return;
    }
    fetch(`${API_BASE_URL}/notebooks/${selectedNotebookId}`)
      .then(res => res.ok ? res.json() : null)
      .then(data => {
        if (data?.title) setSelectedNotebookName(data.title);
      })
      .catch(() => {});
  }, [selectedNotebookId]);

  // WebSocket for background task notifications (source processing failures)
  useEffect(() => {
    if (!selectedNotebookId) return;

    const wsUrl = API_BASE_URL.replace('http', 'ws') + '/constellation/ws';
    const ws = new WebSocket(wsUrl);

    ws.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data);
        if (message.type === 'source_updated' && message.data?.notebook_id === selectedNotebookId) {
          // Refresh sources list
          setRefreshSources(prev => prev + 1);
          
          // Show toast for failures
          if (message.data.status === 'failed') {
            addToast({
              type: 'error',
              title: 'Failed to add source',
              message: message.data.title || message.data.error || 'Unknown error',
              duration: 8000,
            });
          }
        }
      } catch (e) {
        console.error('WebSocket message parse error:', e);
      }
    };

    ws.onerror = (e) => console.error('App WebSocket error:', e);

    return () => {
      ws.close();
    };
  }, [selectedNotebookId, addToast]);

  useEffect(() => {
    // Check for saved theme preference
    const savedTheme = localStorage.getItem('theme');
    if (savedTheme === 'dark') {
      setDarkMode(true);
      document.documentElement.classList.add('dark');
    }
  }, []);

  // Save LLM provider preference when it changes
  useEffect(() => {
    localStorage.setItem('llmProvider', selectedLLMProvider);
  }, [selectedLLMProvider]);

  // Secondary morning brief trigger — fires when app regains focus in the AM
  // Catches the case where app was left running overnight
  useEffect(() => {
    if (!backendReady) return;

    const handleVisibilityChange = () => {
      if (document.visibilityState !== 'visible') return;
      // Only trigger in the AM (before noon local time)
      const hour = new Date().getHours();
      if (hour >= 12) return;
      // Don't re-show if already visible
      if (morningBrief) return;

      fetch(`${API_BASE_URL}/curator/morning-brief/should-show`)
        .then(r => r.ok ? r.json() : null)
        .then(check => {
          if (check?.should_show) {
            const hoursAway = check.hours_away || 12;
            return fetch(`${API_BASE_URL}/curator/morning-brief?hours_away=${hoursAway}`)
              .then(r => r.ok ? r.json() : null);
          }
          return null;
        })
        .then(brief => {
          if (brief && (brief.notebooks?.length > 0 || brief.cross_notebook_insight || brief.narrative)) {
            setMorningBrief(brief);
            fetch(`${API_BASE_URL}/curator/morning-brief/mark-shown`, { method: 'POST' }).catch(() => {});
            fetch(`${API_BASE_URL}/curator/morning-brief/save`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(brief)
            }).catch(() => {});
          }
        })
        .catch(() => {});
    };

    document.addEventListener('visibilitychange', handleVisibilityChange);
    return () => document.removeEventListener('visibilitychange', handleVisibilityChange);
  }, [backendReady, morningBrief]);

  // Listen for "Create Visual from this" events from ChatInterface
  useEffect(() => {
    const handleOpenStudioVisual = (event: CustomEvent<{ content: string }>) => {
      setVisualContent(event.detail.content);
      setStudioTab('visual'); // Also expands the mini-player
    };

    window.addEventListener('openStudioVisual', handleOpenStudioVisual as EventListener);
    return () => {
      window.removeEventListener('openStudioVisual', handleOpenStudioVisual as EventListener);
    };
  }, [setStudioTab]);

  useEffect(() => {
    // Check if backend is ready
    const checkBackend = async () => {
      try {
        try {
          const status = await invoke<{ stage: string; message: string; last_error: string | null }>('get_backend_status');
          if (status?.message) {
            setBackendStatusMessage(status.message);
          }
          if (status?.stage === 'error' && status.last_error) {
            setBackendError(status.last_error);
          }
        } catch {
          // Ignore status errors (older builds/dev)
        }

        // Also try to get startup status from backend API for upgrade info
        let startupComplete = false;
        try {
          const response = await fetch(`${API_BASE_URL}/updates/startup-status`);
          if (response.ok) {
            const startupStatus = await response.json();
            setStartupProgress(startupStatus.progress);
            setIsUpgrade(startupStatus.is_upgrade);
            setCurrentVersion(startupStatus.current_version);
            if (startupStatus.message) {
              setBackendStatusMessage(startupStatus.message);
            }
            // Check if startup tasks are complete (status === 'ready')
            startupComplete = startupStatus.status === 'ready';
          }
        } catch {
          // Backend not ready yet
        }

        const ready = await invoke<boolean>('is_backend_ready');
        // Only mark as ready if BOTH Tauri says ready AND backend startup tasks are complete
        if (ready && startupComplete) {
          setBackendReady(true);
          // Prewarm mermaid renderer in background
          prewarmMermaid();
          // Fetch morning brief — check should-show first, then fetch if needed
          fetch(`${API_BASE_URL}/curator/morning-brief/should-show`)
            .then(r => r.ok ? r.json() : null)
            .then(check => {
              if (check?.should_show) {
                const hoursAway = check.hours_away || 12;
                return fetch(`${API_BASE_URL}/curator/morning-brief?hours_away=${hoursAway}`)
                  .then(r => r.ok ? r.json() : null);
              }
              return null;
            })
            .then(brief => {
              if (brief && (brief.notebooks?.length > 0 || brief.cross_notebook_insight || brief.narrative)) {
                setMorningBrief(brief);
                // Mark as shown and persist for recall
                fetch(`${API_BASE_URL}/curator/morning-brief/mark-shown`, { method: 'POST' }).catch(() => {});
                fetch(`${API_BASE_URL}/curator/morning-brief/save`, {
                  method: 'POST',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify(brief)
                }).catch(() => {});
              }
            })
            .catch(() => {});
        } else {
          // Keep checking every second
          setTimeout(checkBackend, 1000);
        }
      } catch (error) {
        console.error('Failed to check backend status:', error);
        setBackendError('Failed to connect to backend. Please ensure Ollama is running.');
        // Retry anyway
        setTimeout(checkBackend, 2000);
      }
    };

    checkBackend();
  }, []);

  const handleUploadComplete = () => {
    console.log('Upload completed - triggering sources and notebooks refresh');
    // Trigger sources list refresh and notebook count refresh
    setRefreshSources(prev => prev + 1);
    setRefreshNotebooks(prev => prev + 1);
  };


  // Show loading screen while backend starts
  if (!backendReady) {
    return (
      <div className="h-screen flex items-center justify-center bg-gray-50">
        <div className="text-center max-w-md">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600 mx-auto mb-4"></div>
          <h2 className="text-xl font-semibold text-gray-900 mb-2">
            {isUpgrade ? '⬆️ Upgrading LocalBook' : 'Starting LocalBook'}
          </h2>
          {currentVersion && (
            <p className="text-sm text-blue-600 mb-2">v{currentVersion}</p>
          )}
          <p className="text-gray-600 min-h-[24px] transition-all duration-200">{backendStatusMessage}</p>
          <div className="mt-4 w-full bg-gray-200 rounded-full h-2">
            <div 
              className="bg-blue-600 h-2 rounded-full transition-all duration-500 ease-out"
              style={{ width: `${Math.max(startupProgress, 5)}%` }}
            />
          </div>
          {isUpgrade && currentVersion && (
            <div className="mt-4 p-3 bg-blue-50 border border-blue-200 rounded-lg">
              <p className="text-sm text-blue-800">
                Upgrading to v{currentVersion} - please wait...
              </p>
            </div>
          )}
          {backendError && (
            <div className="mt-4 p-4 bg-yellow-50 border border-yellow-200 rounded-lg">
              <p className="text-sm text-yellow-800">{backendError}</p>
            </div>
          )}
        </div>
      </div>
    );
  }

  return (
    <CanvasProvider value={canvasContext}>
      <div className="h-screen flex flex-col bg-gray-50 dark:bg-gray-900">
        {/* Morning Brief — floats above canvas */}
        {morningBrief && (
          <button
            onClick={() => { setCuratorBriefData(morningBrief); changePanelView(findFirstLeafId(layout), 'curator'); setMorningBrief(null); }}
            className="mx-4 mt-2 mb-1 p-3 bg-gradient-to-r from-blue-50 to-indigo-50 dark:from-blue-900/20 dark:to-indigo-900/20 border border-blue-200 dark:border-blue-800/40 rounded-lg text-left hover:border-blue-400 dark:hover:border-blue-600 transition-colors flex-shrink-0"
          >
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="text-base">&#x2600;&#xFE0F;</span>
                <h4 className="text-xs font-semibold text-blue-800 dark:text-blue-200 uppercase tracking-wide">Morning Brief</h4>
                <span className="text-[10px] text-blue-500 dark:text-blue-400">Away {morningBrief.away_duration}</span>
              </div>
              <div className="flex items-center gap-3">
                <span
                  role="button"
                  onClick={(e) => { e.stopPropagation(); setCuratorBriefData(morningBrief); changePanelView(findFirstLeafId(layout), 'curator'); setMorningBrief(null); }}
                  className="text-[10px] text-blue-400 dark:text-blue-500 hover:text-blue-600 dark:hover:text-blue-300 transition-colors underline cursor-pointer"
                >
                  Read full brief &rarr;
                </span>
                <span
                  role="button"
                  onClick={(e) => { e.stopPropagation(); setMorningBrief(null); }}
                  className="text-blue-400 hover:text-blue-600 dark:hover:text-blue-300 text-xs p-1 -mr-1 rounded hover:bg-blue-500/10"
                >
                  &#x2715;
                </span>
              </div>
            </div>
            <div className="mt-1.5 text-xs text-blue-700 dark:text-blue-300">
              {morningBrief.narrative ? (
                <p className="line-clamp-2">{morningBrief.narrative.replace(/\*\*/g, '').replace(/^#+\s/gm, '').slice(0, 200)}</p>
              ) : (
                <div className="flex flex-wrap gap-x-4 gap-y-1">
                  {morningBrief.notebooks?.map((nb: any, i: number) => (
                    <span key={i}>
                      <span className="font-medium">{nb.name}</span>
                      {nb.items_added > 0 && `: ${nb.items_added} new`}
                      {nb.pending_approval > 0 && ` \u00B7 ${nb.pending_approval} pending`}
                    </span>
                  ))}
                </div>
              )}
            </div>
          </button>
        )}

        {/* Main content — resizable left nav + canvas */}
        <div className="flex-1 overflow-hidden">
          <Group orientation="horizontal" id="main-layout">
            <Panel id="left-nav" defaultSize="25%" minSize="20%" maxSize="30%">
              <LeftNavColumn
                selectedNotebookId={selectedNotebookId}
                onNotebookSelect={setSelectedNotebookId}
                refreshSources={refreshSources}
                refreshNotebooks={refreshNotebooks}
                collectorRefreshKey={collectorRefreshKey}
                onCollectorConfigured={() => setCollectorRefreshKey(k => k + 1)}
                onUploadComplete={handleUploadComplete}
                onSourcesChange={() => setRefreshNotebooks(prev => prev + 1)}
                selectedSourceId={selectedSourceId}
                onSourceSelect={(id) => setSelectedSourceId(prev => prev === id ? null : id)}
                drawers={drawers}
                toggleDrawer={toggleDrawer}
                selectedNotebookName={selectedNotebookName}
                studio={studio}
                toggleStudio={toggleStudio}
                setStudioTab={setStudioTab}
                visualContent={visualContent}
              />
            </Panel>
            <Separator>
              <div className="w-1.5 h-full cursor-col-resize hover:bg-blue-400/30 flex items-center justify-center transition-colors">
                <div className="w-0.5 h-8 rounded-full bg-gray-300 dark:bg-gray-600 group-hover:bg-blue-500 transition-colors" />
              </div>
            </Separator>
            <Panel id="canvas" defaultSize="75%" minSize="50%">
              <CanvasWorkspace layout={layout} />
            </Panel>
          </Group>
        </div>

        {/* Toast notifications */}
        <ToastContainer toasts={toasts} onDismiss={dismissToast} />

        {/* Settings Modal */}
        <Modal isOpen={showSettingsModal} onClose={() => setShowSettingsModal(false)} title="Settings" size="lg">
          <Settings />
        </Modal>

        {/* LLM Selector Modal */}
        <Modal isOpen={showLLMModal} onClose={() => setShowLLMModal(false)} title="Select AI Brain" size="lg">
          <LLMSelector selectedProvider={selectedLLMProvider} onProviderChange={setSelectedLLMProvider} />
        </Modal>

        {/* Embedding Selector Modal */}
        <Modal isOpen={showEmbeddingModal} onClose={() => setShowEmbeddingModal(false)} title="Select Embedding Model" size="lg">
          <EmbeddingSelector notebookId={selectedNotebookId} />
        </Modal>
      </div>
    </CanvasProvider>
  );
}

export default App;
