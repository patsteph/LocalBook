import { useState, useEffect, useCallback, useMemo } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { Group, Panel, Separator } from 'react-resizable-panels';
import { openUrl } from '@tauri-apps/plugin-opener';
import { LeftNavColumn } from './components/layout/LeftNavColumn';
import { CanvasWorkspace } from './components/canvas/CanvasWorkspace';
import { CanvasProvider, CanvasContextValue } from './components/canvas/CanvasContext';
import { LayoutNode, PanelView, countLeaves, replaceLeaf, removeLeaf, findLeaf } from './components/canvas/types';
import { StudioMiniPlayer } from './components/studio/StudioMiniPlayer';
import { useCanvasLayout, useDrawerState, useStudioState } from './hooks/useLayoutPersistence';
import { ToastContainer, ToastMessage } from './components/shared/Toast';
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

  const openSettings = useCallback(() => openPanel('settings'), [openPanel]);
  const openLLMSelector = useCallback(() => openPanel('llm-selector'), [openPanel]);
  const openEmbeddingSelector = useCallback(() => openPanel('embedding-selector'), [openPanel]);

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
        {/* Header */}
        <div className="bg-white dark:bg-gray-800 border-b dark:border-gray-700 px-6 py-3 flex justify-between items-center flex-shrink-0">
          <div>
            <h1 className="text-xl font-bold text-gray-900 dark:text-white">LocalBook</h1>
          </div>
          <div className="flex items-center gap-1">
            <button
              onClick={openLLMSelector}
              className="p-2 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
              title="Select AI Brain"
            >
              <svg className="w-5 h-5 text-purple-600 dark:text-purple-400" fill="currentColor" viewBox="0 0 512 512">
                <path d="M184 0c30.9 0 56 25.1 56 56V456c0 30.9-25.1 56-56 56c-28.9 0-52.7-21.9-55.7-50.1c-5.2 1.4-10.7 2.1-16.3 2.1c-35.3 0-64-28.7-64-64c0-7.4 1.3-14.6 3.6-21.2C21.4 367.4 0 338.2 0 304c0-31.9 18.7-59.5 45.8-72.3C37.1 220.8 32 207 32 192c0-30.7 21.6-56.3 50.4-62.6C80.8 123.9 80 118 80 112c0-29.9 20.6-55.1 48.3-62.1C131.3 21.9 155.1 0 184 0zM328 0c28.9 0 52.6 21.9 55.7 49.9c27.8 7 48.3 32.1 48.3 62.1c0 6-.8 11.9-2.4 17.4c28.8 6.2 50.4 31.9 50.4 62.6c0 15-5.1 28.8-13.8 39.7C493.3 244.5 512 272.1 512 304c0 34.2-21.4 63.4-51.6 74.8c2.3 6.6 3.6 13.8 3.6 21.2c0 35.3-28.7 64-64 64c-5.6 0-11.1-.7-16.3-2.1c-3 28.2-26.8 50.1-55.7 50.1c-30.9 0-56-25.1-56-56V56c0-30.9 25.1-56 56-56z"/>
              </svg>
            </button>
            <button
              onClick={openEmbeddingSelector}
              className="p-2 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
              title="Select Embedding Model"
            >
              <svg className="w-5 h-5 text-blue-600 dark:text-blue-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
              </svg>
            </button>
            <button
              onClick={() => openUrl(`${API_BASE_URL}/health/portal`)}
              className="p-2 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
              title="System Health"
            >
              <svg className="w-5 h-5 text-green-600 dark:text-green-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
              </svg>
            </button>
            <button
              onClick={openSettings}
              className="p-2 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
              title="Settings"
            >
              <svg className="w-5 h-5 text-gray-700 dark:text-gray-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
              </svg>
            </button>
            <button
              onClick={toggleDarkMode}
              className="p-2 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
              title={darkMode ? 'Switch to light mode' : 'Switch to dark mode'}
            >
              {darkMode ? (
                <svg className="w-5 h-5 text-yellow-500" fill="currentColor" viewBox="0 0 20 20">
                  <path fillRule="evenodd" d="M10 2a1 1 0 011 1v1a1 1 0 11-2 0V3a1 1 0 011-1zm4 8a4 4 0 11-8 0 4 4 0 018 0zm-.464 4.95l.707.707a1 1 0 001.414-1.414l-.707-.707a1 1 0 00-1.414 1.414zm2.12-10.607a1 1 0 010 1.414l-.706.707a1 1 0 11-1.414-1.414l.707-.707a1 1 0 011.414 0zM17 11a1 1 0 100-2h-1a1 1 0 100 2h1zm-7 4a1 1 0 011 1v1a1 1 0 11-2 0v-1a1 1 0 011-1zM5.05 6.464A1 1 0 106.465 5.05l-.708-.707a1 1 0 00-1.414 1.414l.707.707zm1.414 8.486l-.707.707a1 1 0 01-1.414-1.414l.707-.707a1 1 0 011.414 1.414zM4 11a1 1 0 100-2H3a1 1 0 000 2h1z" clipRule="evenodd" />
                </svg>
              ) : (
                <svg className="w-5 h-5 text-gray-700" fill="currentColor" viewBox="0 0 20 20">
                  <path d="M17.293 13.293A8 8 0 016.707 2.707a8.001 8.001 0 1010.586 10.586z" />
                </svg>
              )}
            </button>
          </div>
        </div>

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
            <Panel id="left-nav" defaultSize={25} minSize={18} maxSize={40}>
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
              />
            </Panel>
            <Separator>
              <div className="w-1.5 h-full cursor-col-resize hover:bg-blue-400/30 flex items-center justify-center transition-colors">
                <div className="w-0.5 h-8 rounded-full bg-gray-300 dark:bg-gray-600 group-hover:bg-blue-500 transition-colors" />
              </div>
            </Separator>
            <Panel id="canvas" minSize={50}>
              <CanvasWorkspace layout={layout} />
            </Panel>
          </Group>
        </div>

        {/* Studio floating mini-player */}
        <StudioMiniPlayer
          notebookId={selectedNotebookId}
          studio={studio}
          toggleStudio={toggleStudio}
          setStudioTab={setStudioTab}
          visualContent={visualContent}
        />

        {/* Toast notifications */}
        <ToastContainer toasts={toasts} onDismiss={dismissToast} />
      </div>
    </CanvasProvider>
  );
}

export default App;
