import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { ArrowUpCircle } from 'lucide-react';
import { invoke } from '@tauri-apps/api/core';
import { Group, Panel, Separator } from 'react-resizable-panels';
import { LeftNavColumn } from './components/layout/LeftNavColumn';
import { CanvasWorkspace } from './components/canvas/CanvasWorkspace';
import { AppShellProvider, CanvasItemsProvider, AppShellContextValue, CanvasItemsContextValue } from './components/canvas/CanvasContext';
import { CanvasItem } from './components/canvas/types';
import { LayoutNode, PanelView, countLeaves, replaceLeaf, removeLeaf, findLeaf, VIEW_LABELS, VIEW_ICONS } from './components/canvas/types';
import { useCanvasLayout, useDrawerState, useStudioState } from './hooks/useLayoutPersistence';
import { ToastContainer, ToastMessage } from './components/shared/Toast';
import { ErrorBoundary } from './components/shared/ErrorBoundary';
import { Modal } from './components/shared/Modal';
import { Settings } from './components/Settings';
import { LLMSelector } from './components/LLMSelector';
import { EmbeddingSelector } from './components/EmbeddingSelector';
import { API_BASE_URL } from './services/api';
import { useReconnectingWebSocket } from './hooks/useReconnectingWebSocket';
import { prewarmMermaid } from './components/shared/MermaidRenderer';
import { useSystemHealth, STATUS_COLORS } from './hooks/useSystemHealth';
import { openUrl } from '@tauri-apps/plugin-opener';

function App() {
  const [selectedNotebookId, setSelectedNotebookId] = useState<string | null>(null);
  const [selectedNotebookName, setSelectedNotebookName] = useState<string>('Notebook');
  const [backendReady, setBackendReady] = useState(false);
  const [backendError, setBackendError] = useState<string | null>(null);
  const [backendStatusMessage, setBackendStatusMessage] = useState<string>('Initializing backend services...');
  const [startupProgress, setStartupProgress] = useState(0);
  const [startupStage, setStartupStage] = useState<string>('starting');
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
  const [visualContent] = useState<string>('');
  const [morningBrief, setMorningBrief] = useState<any>(null);
  const [weeklyWrap, setWeeklyWrap] = useState<any>(null);
  const [curatorBriefData, setCuratorBriefData] = useState<any>(null);
  const [showSettingsModal, setShowSettingsModal] = useState(false);
  const [showLLMModal, setShowLLMModal] = useState(false);
  const [showEmbeddingModal, setShowEmbeddingModal] = useState(false);
  const [canvasItems, setCanvasItems] = useState<CanvasItem[]>([]);
  const [generationStatus, setGenerationStatusRaw] = useState<'idle' | 'generating' | 'complete' | 'error'>('idle');
  const [chatContext, setChatContext] = useState<string>('');
  const [showViewMenu, setShowViewMenu] = useState(false);
  const [showUtilMenu, setShowUtilMenu] = useState(false);
  const viewMenuRef = useRef<HTMLDivElement>(null);
  const utilMenuRef = useRef<HTMLDivElement>(null);
  const health = useSystemHealth();

  // Auto-reset generation status back to idle after transient states
  const setGenerationStatus = useCallback((status: 'idle' | 'generating' | 'complete' | 'error') => {
    setGenerationStatusRaw(status);
    if (status === 'complete' || status === 'error') {
      setTimeout(() => setGenerationStatusRaw('idle'), 2500);
    }
  }, []);

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

  // Views that open in the universal canvas instead of splitting the layout
  const CANVAS_VIEWS: Record<string, CanvasItem['type']> = {
    'content-viewer': 'document',
    'quiz-viewer': 'quiz',
    'visual-viewer': 'visual',
  };

  const addCanvasItem = useCallback((item: Omit<CanvasItem, 'id' | 'timestamp'> & { id?: string }) => {
    const newItem: CanvasItem = {
      ...item,
      id: item.id || `canvas-${Date.now()}-${Math.random().toString(36).substr(2, 5)}`,
      timestamp: Date.now(),
    };
    setCanvasItems(prev => [...prev, newItem]);
  }, []);

  const removeCanvasItem = useCallback((id: string) => {
    setCanvasItems(prev => prev.filter(item => item.id !== id));
  }, []);

  const updateCanvasItem = useCallback((id: string, updates: Partial<Pick<CanvasItem, 'title' | 'content' | 'status' | 'metadata'>>) => {
    setCanvasItems(prev => prev.map(item =>
      item.id === id ? { ...item, ...updates } : item
    ));
  }, []);

  const toggleCanvasItemCollapse = useCallback((id: string) => {
    setCanvasItems(prev => prev.map(item =>
      item.id === id ? { ...item, collapsed: !item.collapsed } : item
    ));
  }, []);

  const clearCanvas = useCallback(() => {
    setCanvasItems([]);
  }, []);

  const openPanel = useCallback((view: PanelView, props?: Record<string, any>) => {
    // Content viewers open in the universal canvas workspace
    const canvasType = CANVAS_VIEWS[view];
    if (canvasType) {
      const newItem: CanvasItem = {
        id: `canvas-${Date.now()}-${Math.random().toString(36).substr(2, 5)}`,
        type: canvasType,
        title: props?.title || 'Document',
        content: props?.content || '',
        collapsed: canvasType === 'note' ? false : true,
        timestamp: Date.now(),
        metadata: { notebookId: selectedNotebookId },
      };
      // Fresh open: clear previous items and add the new one
      setCanvasItems([newItem]);
      return;
    }
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
  }, [setLayout, findFirstLeafId, selectedNotebookId]);

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

  // === Split context values ===
  // Canvas items context — changes frequently during streaming
  const canvasItemsCtx: CanvasItemsContextValue = useMemo(() => ({
    canvasItems,
    addCanvasItem,
    removeCanvasItem,
    updateCanvasItem,
    toggleCanvasItemCollapse,
    clearCanvas,
  }), [canvasItems, addCanvasItem, removeCanvasItem, updateCanvasItem, toggleCanvasItemCollapse, clearCanvas]);

  // App shell context — changes infrequently (notebook, layout, dark mode)
  const appShellCtx: AppShellContextValue = useMemo(() => ({
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
    generationStatus,
    setGenerationStatus,
    chatContext,
    setChatContext,
  }), [
    selectedNotebookId, selectedNotebookName, selectedSourceId, selectedLLMProvider,
    refreshSources, refreshNotebooks, collectorRefreshKey, addToast,
    openPanel, closePanel, splitPanel, changePanelView, layout,
    openWebResearch, openSettings, openLLMSelector, openEmbeddingSelector,
    chatPrefillQuery, navigateToChat, darkMode, toggleDarkMode,
    morningBrief, curatorBriefData, generationStatus, setGenerationStatus,
    chatContext, setChatContext,
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
  const wsUrl = useMemo(() => API_BASE_URL.replace('http', 'ws') + '/constellation/ws', []);
  useReconnectingWebSocket({
    url: wsUrl,
    enabled: !!selectedNotebookId,
    onMessage: useCallback((message: any) => {
      if (message.type === 'source_updated' && message.data?.notebook_id === selectedNotebookId) {
        setRefreshSources(prev => prev + 1);
        if (message.data.status === 'failed') {
          addToast({
            type: 'error',
            title: 'Failed to add source',
            message: message.data.title || message.data.error || 'Unknown error',
            duration: 8000,
          });
        }
      }
    }, [selectedNotebookId, addToast]),
  });

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

  // Secondary morning brief / weekly wrap trigger — fires when app regains focus
  // Backend handles time-of-day gating (morning window OR 8+ hour absence)
  useEffect(() => {
    if (!backendReady) return;

    const handleVisibilityChange = () => {
      if (document.visibilityState !== 'visible') return;
      if (morningBrief || weeklyWrap) return;

      const localHour = new Date().getHours();
      fetch(`${API_BASE_URL}/curator/morning-brief/should-show?local_hour=${localHour}`)
        .then(r => r.ok ? r.json() : null)
        .then(check => {
          if (check?.should_show_weekly) {
            return fetch(`${API_BASE_URL}/curator/weekly-wrap`)
              .then(r => r.ok ? r.json() : null)
              .then(wrap => {
                if (wrap?.narrative) {
                  setWeeklyWrap(wrap);
                  fetch(`${API_BASE_URL}/curator/morning-brief/mark-shown`, { method: 'POST' }).catch(() => {});
                  fetch(`${API_BASE_URL}/curator/weekly-wrap/save`, {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(wrap)
                  }).catch(() => {});
                }
              });
          } else if (check?.should_show) {
            const hoursAway = check.hours_away || 12;
            return fetch(`${API_BASE_URL}/curator/morning-brief?hours_away=${hoursAway}`)
              .then(r => r.ok ? r.json() : null)
              .then(brief => {
                if (brief && (brief.notebooks?.length > 0 || brief.cross_notebook_insight || brief.narrative)) {
                  setMorningBrief(brief);
                  fetch(`${API_BASE_URL}/curator/morning-brief/mark-shown`, { method: 'POST' }).catch(() => {});
                  fetch(`${API_BASE_URL}/curator/morning-brief/save`, {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(brief)
                  }).catch(() => {});
                }
              });
          }
        })
        .catch(() => {});
    };

    document.addEventListener('visibilitychange', handleVisibilityChange);
    return () => document.removeEventListener('visibilitychange', handleVisibilityChange);
  }, [backendReady, morningBrief, weeklyWrap]);

  // Outside-click handlers for unified top bar menus
  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (viewMenuRef.current && !viewMenuRef.current.contains(e.target as Node)) setShowViewMenu(false);
      if (utilMenuRef.current && !utilMenuRef.current.contains(e.target as Node)) setShowUtilMenu(false);
    };
    if (showViewMenu || showUtilMenu) document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, [showViewMenu, showUtilMenu]);

  const MAIN_VIEWS: PanelView[] = ['chat', 'constellation', 'timeline', 'findings', 'curator'];
  const primaryPanelId = findFirstLeafId(layout);
  const primaryLeaf = findLeaf(layout, primaryPanelId);
  const currentView = primaryLeaf?.view || 'chat';

  // Listen for "Open in Canvas" events from chat visual actions
  useEffect(() => {
    const handleOpenCanvasVisual = (event: CustomEvent<{ content: string }>) => {
      // Open the content in canvas as a document item for visual generation
      setCanvasItems([{
        id: `canvas-${Date.now()}-${Math.random().toString(36).substr(2, 5)}`,
        type: 'document',
        title: 'Visual Content',
        content: event.detail.content,
        collapsed: true,
        timestamp: Date.now(),
        metadata: { notebookId: selectedNotebookId },
      }]);
    };

    window.addEventListener('openCanvasVisual', handleOpenCanvasVisual as EventListener);
    return () => {
      window.removeEventListener('openCanvasVisual', handleOpenCanvasVisual as EventListener);
    };
  }, [selectedNotebookId]);

  useEffect(() => {
    // Check if backend is ready
    const checkBackend = async () => {
      try {
        try {
          const status = await invoke<{ stage: string; message: string; last_error: string | null }>('get_backend_status');
          if (status?.message) {
            setBackendStatusMessage(status.message);
          }
          if (status?.stage && status.stage !== 'error') {
            setStartupStage(status.stage);
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
            if (startupStatus.status === 'ready') {
              startupComplete = true;
              setStartupStage('ready');
            } else if (startupStatus.status) {
              // Backend is up but still doing startup tasks (upgrading, migrating, etc.)
              setStartupStage('backend_setup');
            }
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
          // Fetch morning brief or weekly wrap — check should-show first
          const localHour = new Date().getHours();
          fetch(`${API_BASE_URL}/curator/morning-brief/should-show?local_hour=${localHour}`)
            .then(r => r.ok ? r.json() : null)
            .then(check => {
              if (check?.should_show_weekly) {
                // Monday — fetch weekly wrap up
                fetch(`${API_BASE_URL}/curator/weekly-wrap`)
                  .then(r => r.ok ? r.json() : null)
                  .then(wrap => {
                    if (wrap?.narrative) {
                      setWeeklyWrap(wrap);
                      fetch(`${API_BASE_URL}/curator/morning-brief/mark-shown`, { method: 'POST' }).catch(() => {});
                      fetch(`${API_BASE_URL}/curator/weekly-wrap/save`, {
                        method: 'POST', headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(wrap)
                      }).catch(() => {});
                    }
                  }).catch(() => {});
              } else if (check?.should_show) {
                const hoursAway = check.hours_away || 12;
                fetch(`${API_BASE_URL}/curator/morning-brief?hours_away=${hoursAway}`)
                  .then(r => r.ok ? r.json() : null)
                  .then(brief => {
                    if (brief && (brief.notebooks?.length > 0 || brief.cross_notebook_insight || brief.narrative)) {
                      setMorningBrief(brief);
                      fetch(`${API_BASE_URL}/curator/morning-brief/mark-shown`, { method: 'POST' }).catch(() => {});
                      fetch(`${API_BASE_URL}/curator/morning-brief/save`, {
                        method: 'POST', headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(brief)
                      }).catch(() => {});
                    }
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
    // Trigger sources list refresh and notebook count refresh
    setRefreshSources(prev => prev + 1);
    setRefreshNotebooks(prev => prev + 1);
  };


  // Show loading screen while backend starts
  if (!backendReady) {
    // Map startup stages to 5 user-visible steps
    const STEPS = [
      { label: 'Preparing: Starting Services' },
      { label: startupStage === 'downloading_model' ? 'Preparing: Downloading Models' : 'Preparing: Verifying Models' },
      { label: 'Waiting for Backend' },
      { label: 'Backend Ready' },
      { label: 'LocalBook Ready' },
    ];
    const STAGE_MAP: Record<string, number> = {
      starting: 0, starting_ollama: 0,
      checking_models: 1, downloading_model: 1,
      starting_backend: 2, waiting_for_backend: 2,
      backend_setup: 3,
      ready: 4,
    };
    const activeStep = STAGE_MAP[startupStage] ?? 0;
    const STEP_PROGRESS = [10, 35, 60, 85, 100];
    const derivedProgress = Math.max(STEP_PROGRESS[activeStep] || 5, startupProgress);

    return (
      <div className="h-screen flex items-center justify-center bg-gradient-to-br from-gray-50 via-white to-blue-50 dark:from-gray-900 dark:via-gray-900 dark:to-gray-800 animate-fade-in">
        <div className="max-w-sm w-full px-6">
          {/* Header */}
          <div className="text-center mb-6">
            <h2 className="text-lg font-semibold text-gray-900 dark:text-white mb-1 flex items-center justify-center gap-2">
              {isUpgrade && <ArrowUpCircle className="w-5 h-5 text-blue-600 dark:text-blue-400" />}
              {isUpgrade ? 'Upgrading LocalBook' : 'Starting LocalBook'}
            </h2>
            {currentVersion && (
              <p className="text-xs font-medium text-blue-600 dark:text-blue-400">v{currentVersion}</p>
            )}
          </div>

          {/* Step-by-step indicator */}
          <div className="space-y-3 mb-6">
            {STEPS.map((step, i) => {
              const isDone = i < activeStep;
              const isActive = i === activeStep;
              return (
                <div key={i} className={`flex items-start gap-2.5 transition-opacity duration-300 ${i > activeStep ? 'opacity-30' : ''}`}>
                  {isDone ? (
                    <svg className="w-4 h-4 text-green-500 mt-0.5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
                    </svg>
                  ) : isActive ? (
                    <div className="w-4 h-4 mt-0.5 flex-shrink-0 relative">
                      <div className="absolute inset-0 rounded-full border-2 border-gray-200 dark:border-gray-600" />
                      <div className="absolute inset-0 rounded-full border-2 border-transparent border-t-blue-500 animate-spin" />
                    </div>
                  ) : (
                    <div className="w-4 h-4 mt-0.5 rounded-full border-2 border-gray-300 dark:border-gray-600 flex-shrink-0" />
                  )}
                  <div className="min-w-0 flex-1">
                    <span className={`text-sm leading-5 ${
                      isActive ? 'text-gray-900 dark:text-white font-medium' :
                      isDone ? 'text-gray-500 dark:text-gray-400' :
                      'text-gray-400 dark:text-gray-500'
                    }`}>
                      {step.label}
                    </span>
                    {isActive && backendStatusMessage && (
                      <p className="text-xs text-gray-400 dark:text-gray-500 mt-0.5 truncate">{backendStatusMessage}</p>
                    )}
                  </div>
                </div>
              );
            })}
          </div>

          {/* Progress bar */}
          <div className="w-full bg-gray-200 dark:bg-gray-700 rounded-full h-1.5 overflow-hidden">
            <div
              className="h-full rounded-full bg-gradient-to-r from-blue-500 via-blue-600 to-indigo-600 transition-all duration-700 ease-out"
              style={{ width: `${Math.max(derivedProgress, 5)}%` }}
            />
          </div>

          {isUpgrade && currentVersion && (
            <div className="mt-4 p-3 bg-blue-50 dark:bg-blue-900/30 border border-blue-200 dark:border-blue-800 rounded-lg">
              <p className="text-xs text-blue-700 dark:text-blue-300">
                Upgrading to v{currentVersion} — please wait…
              </p>
            </div>
          )}
          {backendError && (
            <div className="mt-4 p-3 bg-yellow-50 dark:bg-yellow-900/30 border border-yellow-200 dark:border-yellow-800 rounded-lg">
              <p className="text-xs text-yellow-800 dark:text-yellow-300">{backendError}</p>
            </div>
          )}
        </div>
      </div>
    );
  }

  return (
    <AppShellProvider value={appShellCtx}>
    <CanvasItemsProvider value={canvasItemsCtx}>
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
                <span className="text-xs text-blue-500 dark:text-blue-400">Away {morningBrief.away_duration}</span>
              </div>
              <div className="flex items-center gap-3">
                <span
                  role="button"
                  onClick={(e) => { e.stopPropagation(); setCuratorBriefData(morningBrief); changePanelView(findFirstLeafId(layout), 'curator'); setMorningBrief(null); }}
                  className="text-xs text-blue-400 dark:text-blue-500 hover:text-blue-600 dark:hover:text-blue-300 transition-colors underline cursor-pointer"
                >
                  Read full brief &rarr;
                </span>
                <span
                  role="button"
                  onClick={(e) => { e.stopPropagation(); setMorningBrief(null); }}
                  className="text-blue-400 hover:text-blue-600 dark:hover:text-blue-300 text-xs p-1 -mr-1 rounded-lg hover:bg-blue-500/10"
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

        {/* Weekly Wrap Up — replaces morning brief on Mondays */}
        {weeklyWrap && (
          <button
            onClick={() => { setCuratorBriefData(weeklyWrap); changePanelView(findFirstLeafId(layout), 'curator'); setWeeklyWrap(null); }}
            className="mx-4 mt-2 mb-1 p-3 bg-gradient-to-r from-purple-50 to-violet-50 dark:from-purple-900/20 dark:to-violet-900/20 border border-purple-200 dark:border-purple-800/40 rounded-lg text-left hover:border-purple-400 dark:hover:border-purple-600 transition-colors flex-shrink-0"
          >
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="text-base">&#x1F4CA;</span>
                <h4 className="text-xs font-semibold text-purple-800 dark:text-purple-200 uppercase tracking-wide">Weekly Wrap Up</h4>
                <span className="text-xs text-purple-500 dark:text-purple-400">{weeklyWrap.week_start} — {weeklyWrap.week_end}</span>
              </div>
              <div className="flex items-center gap-3">
                <span
                  role="button"
                  onClick={(e) => { e.stopPropagation(); setCuratorBriefData(weeklyWrap); changePanelView(findFirstLeafId(layout), 'curator'); setWeeklyWrap(null); }}
                  className="text-xs text-purple-400 dark:text-purple-500 hover:text-purple-600 dark:hover:text-purple-300 transition-colors underline cursor-pointer"
                >
                  Read full wrap up &rarr;
                </span>
                <span
                  role="button"
                  onClick={(e) => { e.stopPropagation(); setWeeklyWrap(null); }}
                  className="text-purple-400 hover:text-purple-600 dark:hover:text-purple-300 text-xs p-1 -mr-1 rounded-lg hover:bg-purple-500/10"
                >
                  &#x2715;
                </span>
              </div>
            </div>
            <div className="mt-1.5 text-xs text-purple-700 dark:text-purple-300">
              {weeklyWrap.narrative ? (
                <p className="line-clamp-2">{weeklyWrap.narrative.replace(/\*\*/g, '').replace(/^#+\s/gm, '').slice(0, 250)}</p>
              ) : (
                <p>Your week in review — {weeklyWrap.totals?.sources_added || 0} sources added, {weeklyWrap.totals?.conversations || 0} conversations</p>
              )}
            </div>
          </button>
        )}

        {/* Main content — resizable left nav + canvas, headers inside panels for seamless tracking */}
        <div className="flex-1 overflow-hidden">
          <Group orientation="horizontal" id="main-layout">
            <Panel id="left-nav" defaultSize="25%" minSize="20%" maxSize="30%">
              <div className="flex flex-col h-full">
              {/* Left panel header — app name */}
              <div className="flex items-center px-3 h-8 bg-gray-50 dark:bg-gray-900 border-b border-gray-200 dark:border-gray-700 flex-shrink-0">
                <span className="text-xs font-bold text-gray-700 dark:text-gray-300 tracking-wide">LocalBook</span>
              </div>
              <div className="flex-1 overflow-hidden">
              <ErrorBoundary fallbackTitle="Sidebar crashed">
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
              </ErrorBoundary>
              </div>
              </div>
            </Panel>
            <Separator>
              <div className="w-1.5 h-full cursor-col-resize hover:bg-blue-400/30 flex items-center justify-center transition-colors">
                <div className="w-0.5 h-8 rounded-full bg-gray-300 dark:bg-gray-600 group-hover:bg-blue-500 transition-colors" />
              </div>
            </Separator>
            <Panel id="canvas" defaultSize="75%" minSize="50%">
              <div className="flex flex-col h-full">
              {/* Canvas panel header — view selector + controls */}
              <div className="flex items-center justify-between px-3 h-8 bg-gray-50 dark:bg-gray-900 border-b border-gray-200 dark:border-gray-700 flex-shrink-0">
                <div className="relative" ref={viewMenuRef}>
                  <button
                    onClick={() => setShowViewMenu(!showViewMenu)}
                    className="flex items-center gap-1.5 px-2 py-0.5 text-xs font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-700 rounded-lg transition-colors"
                  >
                    <span>{VIEW_ICONS[currentView as PanelView]}</span>
                    <span>{VIEW_LABELS[currentView as PanelView]}</span>
                    <svg className="w-3 h-3 opacity-50" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                    </svg>
                  </button>
                  {showViewMenu && (
                    <div className="absolute top-full left-0 mt-1 w-48 bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-lg shadow-lg z-50 py-1">
                      <div className="px-2 py-1 text-xs font-semibold text-gray-400 uppercase tracking-wider">Views</div>
                      {MAIN_VIEWS.map(v => (
                        <button
                          key={v}
                          onClick={() => { changePanelView(primaryPanelId, v); setShowViewMenu(false); }}
                          className={`w-full text-left px-3 py-1.5 text-xs flex items-center gap-2 hover:bg-gray-100 dark:hover:bg-gray-700 ${
                            v === currentView ? 'text-blue-600 dark:text-blue-400 font-medium' : 'text-gray-700 dark:text-gray-300'
                          }`}
                        >
                          <span>{VIEW_ICONS[v]}</span> {VIEW_LABELS[v]}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
                <div className="flex items-center gap-0.5">
                  {selectedNotebookId && (
                    <span className="text-xs font-medium text-gray-500 dark:text-gray-400 truncate max-w-[180px] mr-1" title={selectedNotebookName}>
                      {selectedNotebookName}
                    </span>
                  )}
                  <button
                    onClick={toggleDarkMode}
                    className="p-1 rounded-lg hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors"
                    title={darkMode ? 'Switch to light mode' : 'Switch to dark mode'}
                  >
                    {darkMode ? (
                      <svg className="w-3.5 h-3.5 text-yellow-500" fill="currentColor" viewBox="0 0 20 20">
                        <path fillRule="evenodd" d="M10 2a1 1 0 011 1v1a1 1 0 11-2 0V3a1 1 0 011-1zm4 8a4 4 0 11-8 0 4 4 0 018 0zm-.464 4.95l.707.707a1 1 0 001.414-1.414l-.707-.707a1 1 0 00-1.414 1.414zm2.12-10.607a1 1 0 010 1.414l-.706.707a1 1 0 11-1.414-1.414l.707-.707a1 1 0 011.414 0zM17 11a1 1 0 100-2h-1a1 1 0 100 2h1zm-7 4a1 1 0 011 1v1a1 1 0 11-2 0v-1a1 1 0 011-1zM5.05 6.464A1 1 0 106.465 5.05l-.708-.707a1 1 0 00-1.414 1.414l.707.707zm1.414 8.486l-.707.707a1 1 0 01-1.414-1.414l.707-.707a1 1 0 011.414 1.414zM4 11a1 1 0 100-2H3a1 1 0 000 2h1z" clipRule="evenodd" />
                      </svg>
                    ) : (
                      <svg className="w-3.5 h-3.5 text-gray-600 dark:text-gray-400" fill="currentColor" viewBox="0 0 20 20">
                        <path d="M17.293 13.293A8 8 0 016.707 2.707a8.001 8.001 0 1010.586 10.586z" />
                      </svg>
                    )}
                  </button>
                  <div className="relative" ref={utilMenuRef}>
                    <button
                      onClick={() => setShowUtilMenu(!showUtilMenu)}
                      className="p-1 rounded-lg hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors"
                      title="Settings & tools"
                    >
                      <svg className="w-3.5 h-3.5 text-gray-600 dark:text-gray-400" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" viewBox="0 0 24 24">
                        <line x1="21" x2="14" y1="4" y2="4" /><line x1="10" x2="3" y1="4" y2="4" /><line x1="21" x2="12" y1="12" y2="12" /><line x1="8" x2="3" y1="12" y2="12" /><line x1="21" x2="16" y1="20" y2="20" /><line x1="12" x2="3" y1="20" y2="20" /><circle cx="12" cy="4" r="2" /><circle cx="10" cy="12" r="2" /><circle cx="14" cy="20" r="2" />
                      </svg>
                    </button>
                    {showUtilMenu && (
                      <div className="absolute top-full right-0 mt-1 w-52 bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-lg shadow-lg z-50 py-1">
                        <button onClick={() => { setShowLLMModal(true); setShowUtilMenu(false); }} className="w-full text-left px-3 py-1.5 text-xs flex items-center gap-2.5 hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-700 dark:text-gray-300">
                          <svg className={`w-3.5 h-3.5 flex-shrink-0 ${STATUS_COLORS[health.llm] || 'text-purple-600 dark:text-purple-400'}`} fill="currentColor" viewBox="0 0 512 512"><path d="M184 0c30.9 0 56 25.1 56 56V456c0 30.9-25.1 56-56 56c-28.9 0-52.7-21.9-55.7-50.1c-5.2 1.4-10.7 2.1-16.3 2.1c-35.3 0-64-28.7-64-64c0-7.4 1.3-14.6 3.6-21.2C21.4 367.4 0 338.2 0 304c0-31.9 18.7-59.5 45.8-72.3C37.1 220.8 32 207 32 192c0-30.7 21.6-56.3 50.4-62.6C80.8 123.9 80 118 80 112c0-29.9 20.6-55.1 48.3-62.1C131.3 21.9 155.1 0 184 0zM328 0c28.9 0 52.6 21.9 55.7 49.9c27.8 7 48.3 32.1 48.3 62.1c0 6-.8 11.9-2.4 17.4c28.8 6.2 50.4 31.9 50.4 62.6c0 15-5.1 28.8-13.8 39.7C493.3 244.5 512 272.1 512 304c0 34.2-21.4 63.4-51.6 74.8c2.3 6.6 3.6 13.8 3.6 21.2c0 35.3-28.7 64-64 64c-5.6 0-11.1-.7-16.3-2.1c-3 28.2-26.8 50.1-55.7 50.1c-30.9 0-56-25.1-56-56V56c0-30.9 25.1-56 56-56z"/></svg>
                          AI Brain {health.llm === 'error' && <span className="text-red-500 text-[10px]">●</span>}
                        </button>
                        <button onClick={() => { setShowEmbeddingModal(true); setShowUtilMenu(false); }} className="w-full text-left px-3 py-1.5 text-xs flex items-center gap-2.5 hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-700 dark:text-gray-300">
                          <svg className={`w-3.5 h-3.5 flex-shrink-0 ${STATUS_COLORS[health.embedding] || 'text-blue-600 dark:text-blue-400'}`} fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" /></svg>
                          Embedding Model {health.embedding === 'error' && <span className="text-red-500 text-[10px]">●</span>}
                        </button>
                        <button onClick={() => { openUrl(`${API_BASE_URL}/health/portal`); setShowUtilMenu(false); }} className="w-full text-left px-3 py-1.5 text-xs flex items-center gap-2.5 hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-700 dark:text-gray-300">
                          <svg className={`w-3.5 h-3.5 flex-shrink-0 ${STATUS_COLORS[health.system] || 'text-green-600 dark:text-green-400'}`} fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" /></svg>
                          System Health {health.system === 'error' && <span className="text-red-500 text-[10px]">●</span>}
                        </button>
                        <div className="border-t dark:border-gray-700 my-1" />
                        <button onClick={() => { setShowSettingsModal(true); setShowUtilMenu(false); }} className="w-full text-left px-3 py-1.5 text-xs flex items-center gap-2.5 hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-700 dark:text-gray-300">
                          <svg className="w-3.5 h-3.5 text-gray-500 dark:text-gray-400 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" /><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" /></svg>
                          Settings
                        </button>
                      </div>
                    )}
                  </div>
                </div>
              </div>
              <div className="flex-1 overflow-hidden">
              <ErrorBoundary fallbackTitle="Workspace crashed">
              <CanvasWorkspace layout={layout} />
              </ErrorBoundary>
              </div>
              </div>
            </Panel>
          </Group>
        </div>

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
      <ToastContainer toasts={toasts} onDismiss={dismissToast} />
    </CanvasItemsProvider>
    </AppShellProvider>
  );
}

export default App;
