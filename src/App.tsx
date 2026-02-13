import { useState, useEffect, useCallback } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { NotebookManager } from './components/NotebookManager';
import { SourceUpload } from './components/SourceUpload';
import { SourcesList } from './components/SourcesList';
import { ChatInterface } from './components/ChatInterface';
import { Studio } from './components/Studio';
import { Modal } from './components/shared/Modal';
import { WebSearchResults } from './components/WebSearchResults';
import { SiteSearch } from './components/SiteSearch';
import { Settings } from './components/Settings';
import { LLMSelector } from './components/LLMSelector';
import { EmbeddingSelector } from './components/EmbeddingSelector';
import { openUrl } from '@tauri-apps/plugin-opener';
import { Timeline } from './components/Timeline';
import { Constellation3D } from './components/Constellation3D';
import { ThemesPanel } from './components/ThemesPanel';
import { ExplorationPanel } from './components/ExplorationPanel';
import { FindingsPanel } from './components/FindingsPanel';
import { CollectorPanel } from './components/CollectorPanel';
import { CuratorPanel } from './components/CuratorPanel';
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
  const [leftSidebarCollapsed, setLeftSidebarCollapsed] = useState(false);
  const [rightSidebarCollapsed, setRightSidebarCollapsed] = useState(false);
  const [isWebSearchModalOpen, setIsWebSearchModalOpen] = useState(false);
  const [webSearchInitialQuery, setWebSearchInitialQuery] = useState('');
  const [webSearchTab, setWebSearchTab] = useState<'web' | 'site'>('web');
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [isLLMSelectorOpen, setIsLLMSelectorOpen] = useState(false);
  const [isEmbeddingSelectorOpen, setIsEmbeddingSelectorOpen] = useState(false);
  const [selectedLLMProvider, setSelectedLLMProvider] = useState<string>(() => {
    // Load saved LLM provider preference
    return localStorage.getItem('llmProvider') || 'ollama';
  });
  const [activeTab, setActiveTab] = useState<'chat' | 'constellation' | 'timeline' | 'findings' | 'curator'>('chat');
  const [insightTab, setInsightTab] = useState<'themes' | 'journey'>('themes');
  const [chatPrefillQuery, setChatPrefillQuery] = useState<string>('');
  const [selectedSourceId, setSelectedSourceId] = useState<string | null>(null);
  const [toasts, setToasts] = useState<ToastMessage[]>([]);
  const [visualContent, setVisualContent] = useState<string>('');
  const [studioTab, setStudioTab] = useState<'documents' | 'audio' | 'quiz' | 'visual' | 'writing'>('documents');
  const [morningBrief, setMorningBrief] = useState<any>(null);
  const [curatorBriefData, setCuratorBriefData] = useState<any>(null);

  // Toast management
  const addToast = useCallback((toast: Omit<ToastMessage, 'id'>) => {
    const id = `toast-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
    setToasts((prev) => [...prev, { ...toast, id }]);
  }, []);

  const dismissToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

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
      setStudioTab('visual');
      setRightSidebarCollapsed(false); // Ensure Studio is visible
    };

    window.addEventListener('openStudioVisual', handleOpenStudioVisual as EventListener);
    return () => {
      window.removeEventListener('openStudioVisual', handleOpenStudioVisual as EventListener);
    };
  }, []);

  const toggleDarkMode = () => {
    setDarkMode(!darkMode);
    if (!darkMode) {
      document.documentElement.classList.add('dark');
      localStorage.setItem('theme', 'dark');
    } else {
      document.documentElement.classList.remove('dark');
      localStorage.setItem('theme', 'light');
    }
  };

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
    <div className="h-screen flex flex-col bg-gray-50 dark:bg-gray-900">
      {/* Header */}
      <div className="bg-white dark:bg-gray-800 border-b dark:border-gray-700 px-6 py-4 flex justify-between items-center">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white">LocalBook</h1>
        </div>
        <div className="flex items-center gap-2">
          {/* LLM Provider Selector */}
          <button
            onClick={() => setIsLLMSelectorOpen(true)}
            className="p-2 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
            title="Select AI Brain"
          >
            <svg className="w-6 h-6 text-purple-600 dark:text-purple-400" fill="currentColor" viewBox="0 0 512 512">
              <path d="M184 0c30.9 0 56 25.1 56 56V456c0 30.9-25.1 56-56 56c-28.9 0-52.7-21.9-55.7-50.1c-5.2 1.4-10.7 2.1-16.3 2.1c-35.3 0-64-28.7-64-64c0-7.4 1.3-14.6 3.6-21.2C21.4 367.4 0 338.2 0 304c0-31.9 18.7-59.5 45.8-72.3C37.1 220.8 32 207 32 192c0-30.7 21.6-56.3 50.4-62.6C80.8 123.9 80 118 80 112c0-29.9 20.6-55.1 48.3-62.1C131.3 21.9 155.1 0 184 0zM328 0c28.9 0 52.6 21.9 55.7 49.9c27.8 7 48.3 32.1 48.3 62.1c0 6-.8 11.9-2.4 17.4c28.8 6.2 50.4 31.9 50.4 62.6c0 15-5.1 28.8-13.8 39.7C493.3 244.5 512 272.1 512 304c0 34.2-21.4 63.4-51.6 74.8c2.3 6.6 3.6 13.8 3.6 21.2c0 35.3-28.7 64-64 64c-5.6 0-11.1-.7-16.3-2.1c-3 28.2-26.8 50.1-55.7 50.1c-30.9 0-56-25.1-56-56V56c0-30.9 25.1-56 56-56z"/>
            </svg>
          </button>
          {/* Embedding Model Selector */}
          <button
            onClick={() => setIsEmbeddingSelectorOpen(true)}
            className="p-2 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
            title="Select Embedding Model"
          >
            <svg className="w-6 h-6 text-blue-600 dark:text-blue-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
            </svg>
          </button>
          {/* System Health button - opens in browser */}
          <button
            onClick={() => openUrl(`${API_BASE_URL}/health/portal`)}
            className="p-2 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
            title="System Health"
          >
            <svg className="w-6 h-6 text-green-600 dark:text-green-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
            </svg>
          </button>
          {/* Settings button */}
          <button
            onClick={() => setIsSettingsOpen(true)}
            className="p-2 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
            title="Settings"
          >
            <svg className="w-6 h-6 text-gray-700 dark:text-gray-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
            </svg>
          </button>
          {/* Dark mode toggle */}
          <button
            onClick={toggleDarkMode}
            className="p-2 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
            title={darkMode ? 'Switch to light mode' : 'Switch to dark mode'}
          >
            {darkMode ? (
              <svg className="w-6 h-6 text-yellow-500" fill="currentColor" viewBox="0 0 20 20">
                <path fillRule="evenodd" d="M10 2a1 1 0 011 1v1a1 1 0 11-2 0V3a1 1 0 011-1zm4 8a4 4 0 11-8 0 4 4 0 018 0zm-.464 4.95l.707.707a1 1 0 001.414-1.414l-.707-.707a1 1 0 00-1.414 1.414zm2.12-10.607a1 1 0 010 1.414l-.706.707a1 1 0 11-1.414-1.414l.707-.707a1 1 0 011.414 0zM17 11a1 1 0 100-2h-1a1 1 0 100 2h1zm-7 4a1 1 0 011 1v1a1 1 0 11-2 0v-1a1 1 0 011-1zM5.05 6.464A1 1 0 106.465 5.05l-.708-.707a1 1 0 00-1.414 1.414l.707.707zm1.414 8.486l-.707.707a1 1 0 01-1.414-1.414l.707-.707a1 1 0 011.414 1.414zM4 11a1 1 0 100-2H3a1 1 0 000 2h1z" clipRule="evenodd" />
              </svg>
            ) : (
              <svg className="w-6 h-6 text-gray-700" fill="currentColor" viewBox="0 0 20 20">
                <path d="M17.293 13.293A8 8 0 016.707 2.707a8.001 8.001 0 1010.586 10.586z" />
              </svg>
            )}
          </button>
        </div>
      </div>

      {/* Main content - 3 column layout */}
      <div className="flex-1 flex overflow-hidden">
        {/* Left sidebar - Notebooks & Documents */}
        <div
          className={`border-r dark:border-gray-700 bg-white dark:bg-gray-800 overflow-y-auto flex flex-col transition-all duration-300 ${
            leftSidebarCollapsed ? 'w-12' : 'w-80'
          }`}
        >
          <div className="flex items-center justify-between p-2 border-b dark:border-gray-700">
            {!leftSidebarCollapsed && <span className="text-sm font-semibold text-gray-700 dark:text-gray-300 pl-2">Sources</span>}
            <button
              onClick={() => setLeftSidebarCollapsed(!leftSidebarCollapsed)}
              className="p-1 rounded hover:bg-gray-100 dark:hover:bg-gray-700"
              title={leftSidebarCollapsed ? 'Expand sources' : 'Collapse sources'}
            >
              <svg className="w-5 h-5 text-gray-600 dark:text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d={leftSidebarCollapsed ? "M9 5l7 7-7 7" : "M15 19l-7-7 7-7"} />
              </svg>
            </button>
          </div>
          {!leftSidebarCollapsed ? (
            <>
              <NotebookManager
                onNotebookSelect={setSelectedNotebookId}
                selectedNotebookId={selectedNotebookId}
                refreshTrigger={refreshNotebooks}
                onCollectorConfigured={() => setCollectorRefreshKey(k => k + 1)}
              />
              <SourceUpload
                notebookId={selectedNotebookId || ''}
                onUploadComplete={handleUploadComplete}
              />
              {/* Web Research Button */}
              <div className="px-4 py-3 border-b dark:border-gray-700">
                <button
                  onClick={() => setIsWebSearchModalOpen(true)}
                  disabled={!selectedNotebookId}
                  title="Search the web or paste URLs (web pages, YouTube) to scrape and add to your research"
                  className="w-full flex items-center justify-center gap-2 px-3 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-md text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:bg-blue-600"
                >
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                  </svg>
                  <span>Web Research</span>
                </button>
              </div>
              <div className="flex-1 overflow-y-auto">
                <SourcesList
                  key={`${selectedNotebookId}-${refreshSources}`}
                  notebookId={selectedNotebookId}
                  onSourcesChange={() => setRefreshNotebooks(prev => prev + 1)}
                  selectedSourceId={selectedSourceId}
                  onSourceSelect={(sourceId) => {
                    // Toggle selection - click again to deselect
                    setSelectedSourceId(prev => prev === sourceId ? null : sourceId);
                  }}
                />
              </div>
            </>
          ) : (
            <div className="flex flex-col items-center gap-4 mt-4">
              <button
                className="p-2 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700"
                title="Notebooks"
              >
                <svg className="w-6 h-6 text-gray-600 dark:text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" />
                </svg>
              </button>
              <button
                className="p-2 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700"
                title="Documents"
              >
                <svg className="w-6 h-6 text-gray-600 dark:text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 21h10a2 2 0 002-2V9.414a1 1 0 00-.293-.707l-5.414-5.414A1 1 0 0012.586 3H7a2 2 0 00-2 2v14a2 2 0 002 2z" />
                </svg>
              </button>
            </div>
          )}
        </div>

        {/* Center - Tabbed Interface (Chat / Timeline) */}
        <div className="flex-1 bg-white dark:bg-gray-800 overflow-hidden flex flex-col">
          {/* Collector Panel - Collapsible above tabs */}
          <CollectorPanel notebookId={selectedNotebookId} notebookName={selectedNotebookName} refreshKey={collectorRefreshKey} onSourcesRefresh={() => { setRefreshSources(prev => prev + 1); setRefreshNotebooks(prev => prev + 1); }} />

          {/* Morning Brief — click navigates to Curator tab */}
          {morningBrief && (
            <button
              onClick={() => { setCuratorBriefData(morningBrief); setActiveTab('curator'); setMorningBrief(null); }}
              className="w-[calc(100%-2rem)] mx-4 mt-2 mb-1 p-3 bg-gradient-to-r from-blue-50 to-indigo-50 dark:from-blue-900/20 dark:to-indigo-900/20 border border-blue-200 dark:border-blue-800/40 rounded-lg text-left hover:border-blue-400 dark:hover:border-blue-600 transition-colors group"
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="text-base">&#x2600;&#xFE0F;</span>
                  <h4 className="text-xs font-semibold text-blue-800 dark:text-blue-200 uppercase tracking-wide">
                    Morning Brief
                  </h4>
                  <span className="text-[10px] text-blue-500 dark:text-blue-400">
                    Away {morningBrief.away_duration}
                  </span>
                </div>
                <div className="flex items-center gap-3">
                  <span
                    role="button"
                    onClick={(e) => { e.stopPropagation(); setCuratorBriefData(morningBrief); setActiveTab('curator'); setMorningBrief(null); }}
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
              {/* Narrative preview or notebook summary */}
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
          
          {/* Tab Bar */}
          <div className="flex border-b dark:border-gray-700 bg-gray-50 dark:bg-gray-900">
            <button
              onClick={() => setActiveTab('chat')}
              className={`flex items-center gap-2 px-6 py-3 text-sm font-medium transition-colors border-b-2 ${
                activeTab === 'chat'
                  ? 'border-blue-600 text-blue-600 dark:text-blue-400 bg-white dark:bg-gray-800'
                  : 'border-transparent text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-200'
              }`}
            >
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
              </svg>
              Chat
            </button>
            <button
              onClick={() => setActiveTab('constellation')}
              className={`flex items-center gap-2 px-6 py-3 text-sm font-medium transition-colors border-b-2 ${
                activeTab === 'constellation'
                  ? 'border-purple-600 text-purple-600 dark:text-purple-400 bg-white dark:bg-gray-800'
                  : 'border-transparent text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-200'
              }`}
            >
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 3v4M3 5h4M6 17v4m-2-2h4m5-16l2.286 6.857L21 12l-5.714 2.143L13 21l-2.286-6.857L5 12l5.714-2.143L13 3z" />
              </svg>
              Constellation
            </button>
            <button
              onClick={() => setActiveTab('timeline')}
              className={`flex items-center gap-2 px-6 py-3 text-sm font-medium transition-colors border-b-2 ${
                activeTab === 'timeline'
                  ? 'border-blue-600 text-blue-600 dark:text-blue-400 bg-white dark:bg-gray-800'
                  : 'border-transparent text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-200'
              }`}
            >
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
              </svg>
              Timeline
            </button>
            <button
              onClick={() => setActiveTab('findings')}
              className={`flex items-center gap-2 px-6 py-3 text-sm font-medium transition-colors border-b-2 ${
                activeTab === 'findings'
                  ? 'border-green-600 text-green-600 dark:text-green-400 bg-white dark:bg-gray-800'
                  : 'border-transparent text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-200'
              }`}
            >
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 5a2 2 0 012-2h10a2 2 0 012 2v16l-7-3.5L5 21V5z" />
              </svg>
              Findings
            </button>
            <button
              onClick={() => setActiveTab('curator')}
              className={`flex items-center gap-2 px-6 py-3 text-sm font-medium transition-colors border-b-2 ${
                activeTab === 'curator'
                  ? 'border-indigo-600 text-indigo-600 dark:text-indigo-400 bg-white dark:bg-gray-800'
                  : 'border-transparent text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-200'
              }`}
            >
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
              </svg>
              Curator
            </button>
          </div>

          {/* Tab Content - All rendered, visibility toggled to preserve state */}
          <div className="flex-1 overflow-hidden relative">
            <div className={`absolute inset-0 ${activeTab === 'chat' ? 'block' : 'hidden'}`}>
              <ChatInterface
                notebookId={selectedNotebookId}
                llmProvider={selectedLLMProvider}
                onOpenWebSearch={(query) => {
                  setWebSearchInitialQuery(query || '');
                  setIsWebSearchModalOpen(true);
                }}
                prefillQuery={chatPrefillQuery}
              />
            </div>
            <div className={`absolute inset-0 ${activeTab === 'constellation' ? 'flex' : 'hidden'}`}>
              {/* Insights Sidebar with Tabs */}
              <div className="w-72 border-r border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 flex-shrink-0 overflow-hidden flex flex-col">
                {/* Sidebar Tabs */}
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
                {/* Panel Content */}
                <div className="flex-1 overflow-hidden">
                  {insightTab === 'themes' ? (
                    <ThemesPanel 
                      notebookId={selectedNotebookId}
                      onConceptClick={(concept, relatedConcepts) => {
                        // Generate a rich question like the old "Ask about this" did
                        const query = relatedConcepts && relatedConcepts.length > 0
                          ? `Tell me about ${concept} and how it relates to ${relatedConcepts.join(', ')}`
                          : `Tell me about ${concept}`;
                        setChatPrefillQuery(query);
                        setActiveTab('chat');
                      }}
                    />
                  ) : (
                    <ExplorationPanel
                      notebookId={selectedNotebookId}
                      onQueryClick={(query) => {
                        setChatPrefillQuery(query);
                        setActiveTab('chat');
                      }}
                      onTopicClick={(topic) => {
                        setChatPrefillQuery(`Tell me more about ${topic}`);
                        setActiveTab('chat');
                      }}
                    />
                  )}
                </div>
              </div>
              {/* 3D Visualization */}
              <div className="flex-1 relative">
                <Constellation3D 
                  notebookId={selectedNotebookId}
                  selectedSourceId={selectedSourceId}
                  rightSidebarCollapsed={rightSidebarCollapsed}
                />
              </div>
            </div>
            <div className={`absolute inset-0 ${activeTab === 'timeline' ? 'block' : 'hidden'}`}>
              <Timeline notebookId={selectedNotebookId} sourcesRefreshTrigger={refreshSources} />
            </div>
            <div className={`absolute inset-0 ${activeTab === 'findings' ? 'block' : 'hidden'}`}>
              <FindingsPanel notebookId={selectedNotebookId} />
            </div>
            <div className={`absolute inset-0 ${activeTab === 'curator' ? 'block' : 'hidden'}`}>
              <CuratorPanel notebookId={selectedNotebookId} morningBrief={curatorBriefData} />
            </div>
          </div>
        </div>

        {/* Right sidebar - Studio features */}
        <div
          className={`border-l dark:border-gray-700 bg-white dark:bg-gray-800 flex flex-col overflow-hidden transition-all duration-300 ${
            rightSidebarCollapsed ? 'w-12' : 'w-96'
          }`}
        >
          <div className="flex items-center justify-between p-2 border-b dark:border-gray-700 flex-shrink-0">
            <button
              onClick={() => setRightSidebarCollapsed(!rightSidebarCollapsed)}
              className="p-1 rounded hover:bg-gray-100 dark:hover:bg-gray-700"
              title={rightSidebarCollapsed ? 'Expand studio' : 'Collapse studio'}
            >
              <svg className="w-5 h-5 text-gray-600 dark:text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d={rightSidebarCollapsed ? "M15 19l-7-7 7-7" : "M9 5l7 7-7 7"} />
              </svg>
            </button>
            {!rightSidebarCollapsed && <span className="text-sm font-semibold text-gray-700 dark:text-gray-300 pr-2">Studio</span>}
          </div>
          {!rightSidebarCollapsed ? (
            <div className="flex-1 overflow-y-auto">
              <Studio 
                notebookId={selectedNotebookId} 
                initialVisualContent={visualContent}
                initialTab={studioTab}
                onTabChange={setStudioTab}
              />
            </div>
          ) : (
            <div className="flex flex-col items-center gap-3 mt-4">
              {[
                { tab: 'documents' as const, icon: '📄', title: 'Documents' },
                { tab: 'audio' as const, icon: '🎙️', title: 'Audio' },
                { tab: 'quiz' as const, icon: '🎯', title: 'Quiz' },
                { tab: 'visual' as const, icon: '🧠', title: 'Visual' },
                { tab: 'writing' as const, icon: '✍️', title: 'Writing' },
              ].map(({ tab, icon, title }) => (
                <button
                  key={tab}
                  onClick={() => { setStudioTab(tab); setRightSidebarCollapsed(false); }}
                  className={`p-2 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors text-lg ${
                    studioTab === tab ? 'bg-blue-50 dark:bg-blue-900/30' : ''
                  }`}
                  title={title}
                >
                  {icon}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Web Research Modal */}
      <Modal
        isOpen={isWebSearchModalOpen}
        onClose={() => {
          setIsWebSearchModalOpen(false);
          setWebSearchInitialQuery('');
          setWebSearchTab('web');
        }}
        title="Web Research - Add Sources"
        size="xl"
      >
        {selectedNotebookId && (
          <div className="flex flex-col h-[70vh]">
            {/* Tab Selector */}
            <div className="flex border-b border-gray-200 dark:border-gray-700 mb-0">
              <button
                onClick={() => setWebSearchTab('web')}
                className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                  webSearchTab === 'web'
                    ? 'border-blue-500 text-blue-600 dark:text-blue-400'
                    : 'border-transparent text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-300'
                }`}
              >
                🌐 Web Search
              </button>
              <button
                onClick={() => setWebSearchTab('site')}
                className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                  webSearchTab === 'site'
                    ? 'border-blue-500 text-blue-600 dark:text-blue-400'
                    : 'border-transparent text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-300'
                }`}
              >
                🎯 Site Search
              </button>
            </div>
            
            {/* Tab Content */}
            <div className="flex-1 overflow-hidden">
              {webSearchTab === 'web' ? (
                <WebSearchResults
                  notebookId={selectedNotebookId}
                  onSourceAdded={() => {
                    setRefreshSources(prev => prev + 1);
                    setRefreshNotebooks(prev => prev + 1);
                  }}
                  initialQuery={webSearchInitialQuery}
                />
              ) : (
                <SiteSearch
                  notebookId={selectedNotebookId}
                  onSourceAdded={() => {
                    setRefreshSources(prev => prev + 1);
                    setRefreshNotebooks(prev => prev + 1);
                  }}
                  initialQuery={webSearchInitialQuery}
                />
              )}
            </div>
          </div>
        )}
      </Modal>

      {/* Settings Modal */}
      <Modal
        isOpen={isSettingsOpen}
        onClose={() => setIsSettingsOpen(false)}
        title="Settings"
        size="lg"
      >
        <Settings onClose={() => setIsSettingsOpen(false)} />
      </Modal>

      {/* LLM Selector Modal */}
      <Modal
        isOpen={isLLMSelectorOpen}
        onClose={() => setIsLLMSelectorOpen(false)}
        title="🧠 Select AI Brain"
        size="md"
      >
        <LLMSelector
          selectedProvider={selectedLLMProvider}
          onProviderChange={(provider) => {
            setSelectedLLMProvider(provider);
            setIsLLMSelectorOpen(false);
          }}
        />
      </Modal>

      {/* Embedding Model Selector Modal */}
      <Modal
        isOpen={isEmbeddingSelectorOpen}
        onClose={() => setIsEmbeddingSelectorOpen(false)}
        title="📊 Embedding Model"
        size="md"
      >
        <EmbeddingSelector
          notebookId={selectedNotebookId}
          onModelChange={() => {
            setRefreshSources(prev => prev + 1);
          }}
        />
      </Modal>

      {/* Toast notifications for background events */}
      <ToastContainer toasts={toasts} onDismiss={dismissToast} />
    </div>
  );
}

export default App;
