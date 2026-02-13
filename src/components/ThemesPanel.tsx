/**
 * ThemesPanel - Display discovered themes from the knowledge graph
 */
import React, { useState, useEffect, useRef } from 'react';
import { themesService, Theme, TopConcept } from '../services/themes';
import { WS_BASE_URL } from '../services/api';

interface ThemesPanelProps {
    notebookId: string | null;
    onConceptClick?: (conceptName: string, relatedConcepts?: string[]) => void;
}

// Status states for the contextual badge
type ThemeStatus = 'idle' | 'waiting' | 'discovering';

export const ThemesPanel: React.FC<ThemesPanelProps> = ({ notebookId, onConceptClick }) => {
    const [themes, setThemes] = useState<Theme[]>([]);
    const [topConcepts, setTopConcepts] = useState<TopConcept[]>([]);
    const [totalConcepts, setTotalConcepts] = useState(0);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [expandedTheme, setExpandedTheme] = useState<string | null>(null);
    
    // Status badge state
    const [status, setStatus] = useState<ThemeStatus>('idle');
    const [clusterProgress, setClusterProgress] = useState<{ phase: string; progress: number; total?: number } | null>(null);

    useEffect(() => {
        if (notebookId) {
            setStatus('idle');  // Reset status when switching notebooks
            setClusterProgress(null);
            loadThemes();
        }
    }, [notebookId]);

    // WebSocket connection for real-time updates during builds
    const wsRef = useRef<WebSocket | null>(null);
    const refreshIntervalRef = useRef<NodeJS.Timeout | null>(null);
    
    useEffect(() => {
        if (!notebookId) return;
        
        const connectWebSocket = () => {
            try {
                // Connect to the same WebSocket as Constellation3D for synchronized updates
                const ws = new WebSocket(`${WS_BASE_URL}/constellation/ws`);
                
                ws.onmessage = (event) => {
                    try {
                        const message = JSON.parse(event.data);
                        
                        // v0.6.5: Topics updated from BERTopic - refresh themes
                        // Handle both direct topics_updated event and legacy concept_added
                        if (message.type === 'topics_updated') {
                            console.log('[ThemesPanel] Topics updated, refreshing themes');
                            loadThemes();
                        }
                        
                        // Enhancement complete - refresh to show enhanced names
                        if (message.type === 'enhancement_progress' && message.data?.status === 'complete') {
                            console.log('[ThemesPanel] Enhancement complete, refreshing themes');
                            loadThemes();
                        }
                        
                        // Build is in progress - show "Waiting..." status
                        // Only react if this is for OUR notebook (or no notebook specified)
                        if (message.type === 'build_progress') {
                            const msgNotebook = message.data?.notebook_id;
                            if (!msgNotebook || msgNotebook === notebookId) {
                                setStatus('waiting');
                                setClusterProgress(null);
                                // Start auto-refresh during build progress (fallback)
                                if (!refreshIntervalRef.current) {
                                    refreshIntervalRef.current = setInterval(loadThemes, 15000);
                                }
                                // Fallback: clear waiting after 60s if build_complete never arrives
                                setTimeout(() => {
                                    setStatus(prev => prev === 'waiting' ? 'idle' : prev);
                                }, 60000);
                            }
                        }
                        
                        // Build complete - topics ready
                        if (message.type === 'build_complete') {
                            console.log('[ThemesPanel] Build complete, refreshing themes');
                            setStatus('idle');
                            // Small delay to ensure topics are saved before fetching
                            setTimeout(() => loadThemes(), 500);
                            // Clear polling interval
                            if (refreshIntervalRef.current) {
                                clearInterval(refreshIntervalRef.current);
                                refreshIntervalRef.current = null;
                            }
                        }
                        
                        // Clustering in progress - show "Discovering..." status
                        if (message.type === 'cluster_progress') {
                            setStatus('discovering');
                            setClusterProgress(message.data);
                        }
                        
                        // Clustering complete - refresh and clear status
                        if (message.type === 'cluster_complete') {
                            console.log('[ThemesPanel] Cluster complete, refreshing themes');
                            setStatus('idle');
                            setClusterProgress(null);
                            loadThemes();
                        }
                    } catch {
                        // Ignore parse errors
                    }
                };
                
                ws.onclose = () => {
                    // Reconnect after delay
                    setTimeout(connectWebSocket, 5000);
                };
                
                wsRef.current = ws;
            } catch {
                // WebSocket not supported
            }
        };
        
        connectWebSocket();
        
        return () => {
            if (wsRef.current) wsRef.current.close();
            if (refreshIntervalRef.current) clearInterval(refreshIntervalRef.current);
        };
    }, [notebookId]);

    const loadThemes = async () => {
        if (!notebookId) return;
        
        // Don't set loading=true if we already have themes (prevents blinking)
        const hadThemes = themes.length > 0;
        if (!hadThemes) {
            setLoading(true);
        }
        setError(null);
        
        try {
            const data = await themesService.getThemes(notebookId);
            // Only update if we got valid data (prevents clearing on empty response)
            if (data.themes && data.themes.length > 0) {
                setThemes(data.themes);
                setTopConcepts(data.top_concepts);
                setTotalConcepts(data.total_concepts);
                // Themes loaded successfully — clear any waiting status
                if (status === 'waiting') {
                    setStatus('idle');
                }
            } else if (!hadThemes) {
                // Only clear if we didn't have themes before
                setThemes([]);
                setTopConcepts([]);
                setTotalConcepts(0);
            }
            // If we had themes and got empty response, keep existing themes
        } catch (err) {
            console.error('Failed to load themes:', err);
            if (!hadThemes) {
                setError('Failed to load themes');
            }
        } finally {
            setLoading(false);
        }
    };

    const handleRefresh = async () => {
        if (!notebookId) return;
        
        try {
            setLoading(true);
            setStatus('waiting');
            await themesService.rebuildThemes(notebookId);
            // build_complete WebSocket event will trigger loadThemes automatically
            // But set a fallback timeout just in case
            setTimeout(() => {
                if (loading) {
                    loadThemes();
                    setStatus('idle');
                }
            }, 30000);
        } catch (err) {
            setError('Failed to rebuild themes');
            setLoading(false);
            setStatus('idle');
        }
    };

    if (!notebookId) {
        return (
            <div className="p-4 text-center text-gray-500 dark:text-gray-400">
                <p className="text-sm">Select a notebook to see themes</p>
            </div>
        );
    }

    return (
        <div className="h-full flex flex-col">
            {/* Header */}
            <div className="p-4 border-b border-gray-200 dark:border-gray-700">
                <div className="flex items-center justify-between mb-1">
                    <div className="flex items-center gap-2">
                        <h3 className="font-semibold text-gray-900 dark:text-white">Key Themes</h3>
                        {/* Contextual Status Badge */}
                        {status === 'waiting' && (
                            <span 
                                className="inline-flex items-center gap-1 px-2 py-0.5 bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300 text-xs rounded-full animate-pulse"
                                title="Waiting for concepts to be extracted"
                            >
                                <span>⏳</span>
                                <span>Waiting...</span>
                            </span>
                        )}
                        {status === 'discovering' && (
                            <span 
                                className="inline-flex items-center gap-1 px-2 py-0.5 bg-purple-100 dark:bg-purple-900/30 text-purple-700 dark:text-purple-300 text-xs rounded-full"
                                title={clusterProgress ? `Finding patterns (${clusterProgress.progress}%)` : "Finding patterns in your concepts"}
                            >
                                <span className="animate-pulse">✨</span>
                                <span>Discovering{clusterProgress?.total ? ` ${clusterProgress.total}` : ''}...</span>
                            </span>
                        )}
                    </div>
                    <button
                        onClick={handleRefresh}
                        disabled={loading || status !== 'idle'}
                        className="text-xs text-blue-600 hover:text-blue-700 disabled:opacity-50"
                        title="Refresh themes"
                    >
                        {loading ? '...' : '↻ Refresh'}
                    </button>
                </div>
                <p className="text-xs text-gray-500 dark:text-gray-400">
                    {totalConcepts} concepts discovered
                </p>
            </div>

            {/* Content */}
            <div className="flex-1 overflow-y-auto p-4 space-y-4">
                {error && (
                    <div className="p-2 bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-300 text-xs rounded">
                        {error}
                    </div>
                )}

                {loading && themes.length === 0 && (
                    <div className="text-center py-8 text-gray-500 dark:text-gray-400">
                        <div className="animate-pulse">Loading themes...</div>
                    </div>
                )}

                {/* Themes List */}
                {themes.length > 0 ? (
                    <div className="space-y-3">
                        {themes.map((theme) => (
                            <div
                                key={theme.id}
                                className="bg-gradient-to-r from-purple-50 to-blue-50 dark:from-purple-900/20 dark:to-blue-900/20 border border-purple-200 dark:border-purple-800 rounded-lg overflow-hidden"
                            >
                                <button
                                    onClick={() => setExpandedTheme(expandedTheme === theme.id ? null : theme.id)}
                                    className="w-full p-3 text-left hover:bg-purple-100/50 dark:hover:bg-purple-900/30 transition-colors"
                                >
                                    <div className="flex items-start justify-between">
                                        <div className="flex-1">
                                            <h4 className="font-medium text-sm text-purple-900 dark:text-purple-100">
                                                {theme.name || 'Unnamed Theme'}
                                            </h4>
                                            <p className="text-xs text-gray-600 dark:text-gray-400 mt-0.5">
                                                {theme.concept_count} related concepts
                                            </p>
                                        </div>
                                        <span className="text-gray-400 text-xs">
                                            {expandedTheme === theme.id ? '▼' : '▶'}
                                        </span>
                                    </div>
                                </button>
                                
                                {expandedTheme === theme.id && (
                                    <div className="px-3 pb-3 border-t border-purple-200 dark:border-purple-800">
                                        {theme.description && (
                                            <p className="text-xs text-gray-600 dark:text-gray-400 mt-2 mb-2">
                                                {theme.description}
                                            </p>
                                        )}
                                        <div className="flex flex-wrap gap-1.5 mt-2">
                                            {theme.concepts.map((concept, idx) => {
                                                // Get other concepts in this theme for context
                                                const relatedConcepts = theme.concepts.filter(c => c !== concept).slice(0, 4);
                                                return (
                                                    <button
                                                        key={idx}
                                                        onClick={() => onConceptClick?.(concept, relatedConcepts)}
                                                        className="px-2 py-0.5 bg-white dark:bg-gray-800 text-xs text-gray-700 dark:text-gray-300 rounded-full border border-gray-200 dark:border-gray-600 hover:border-purple-400 hover:text-purple-700 dark:hover:text-purple-300 transition-colors"
                                                    >
                                                        {concept}
                                                    </button>
                                                );
                                            })}
                                            {theme.concept_count > theme.concepts.length && (
                                                <span className="px-2 py-0.5 text-xs text-gray-400">
                                                    +{theme.concept_count - theme.concepts.length} more
                                                </span>
                                            )}
                                        </div>
                                    </div>
                                )}
                            </div>
                        ))}
                    </div>
                ) : !loading && (
                    <div className="text-center py-8">
                        <div className="text-gray-400 dark:text-gray-500 mb-2">
                            <svg className="w-12 h-12 mx-auto" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
                            </svg>
                        </div>
                        <p className="text-sm text-gray-500 dark:text-gray-400">No themes discovered yet</p>
                        <p className="text-xs text-gray-400 dark:text-gray-500 mt-1">
                            Add more sources to discover themes
                        </p>
                    </div>
                )}

                {/* Top Concepts (not in themes) */}
                {topConcepts.length > 0 && (
                    <div className="mt-4 pt-4 border-t border-gray-200 dark:border-gray-700">
                        <h4 className="text-xs font-medium text-gray-600 dark:text-gray-400 mb-2">
                            Top Concepts
                        </h4>
                        <div className="flex flex-wrap gap-1.5">
                            {topConcepts.slice(0, 15).map((concept) => (
                                <button
                                    key={concept.id}
                                    onClick={() => onConceptClick?.(concept.name)}
                                    className="px-2 py-0.5 bg-gray-100 dark:bg-gray-800 text-xs text-gray-600 dark:text-gray-400 rounded-full hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors"
                                >
                                    {concept.name}
                                </button>
                            ))}
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
};
