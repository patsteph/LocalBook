/**
 * ExplorationPanel - Display user's learning journey through their notebook
 */
import React, { useState, useEffect } from 'react';
import { explorationService, QueryRecord, TopicExplored } from '../services/exploration';

interface ExplorationPanelProps {
    notebookId: string | null;
    onQueryClick?: (query: string) => void;
    onTopicClick?: (topic: string) => void;
}

export const ExplorationPanel: React.FC<ExplorationPanelProps> = ({ 
    notebookId, 
    onQueryClick,
    onTopicClick 
}) => {
    const [queries, setQueries] = useState<QueryRecord[]>([]);
    const [topics, setTopics] = useState<TopicExplored[]>([]);
    const [totalQueries, setTotalQueries] = useState(0);
    const [loading, setLoading] = useState(false);
    const [activeView, setActiveView] = useState<'journey' | 'topics'>('journey');

    useEffect(() => {
        if (notebookId) {
            loadJourney();
        }
    }, [notebookId]);

    const loadJourney = async () => {
        if (!notebookId) return;
        
        setLoading(true);
        try {
            const data = await explorationService.getJourney(notebookId, 30);
            setQueries(data.queries);
            setTopics(data.topics_explored);
            setTotalQueries(data.total_queries);
        } catch (err) {
            console.error('Failed to load journey:', err);
        } finally {
            setLoading(false);
        }
    };

    const formatTime = (timestamp: string) => {
        const date = new Date(timestamp);
        const now = new Date();
        const diff = now.getTime() - date.getTime();
        
        if (diff < 60000) return 'Just now';
        if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
        if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
        return date.toLocaleDateString();
    };

    const getConfidenceColor = (confidence: number) => {
        if (confidence >= 0.6) return 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-300';
        if (confidence >= 0.4) return 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-300';
        return 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300';
    };

    if (!notebookId) {
        return (
            <div className="p-4 text-center text-gray-500 dark:text-gray-400">
                <p className="text-sm">Select a notebook to see your journey</p>
            </div>
        );
    }

    return (
        <div className="h-full flex flex-col">
            {/* Header */}
            <div className="p-4 border-b border-gray-200 dark:border-gray-700">
                <div className="flex items-center justify-between mb-2">
                    <h3 className="font-semibold text-gray-900 dark:text-white">Your Journey</h3>
                    <span className="text-xs text-gray-500 dark:text-gray-400">
                        {totalQueries} questions asked
                    </span>
                </div>
                
                {/* View Toggle */}
                <div className="flex gap-1 bg-gray-100 dark:bg-gray-800 rounded-lg p-0.5">
                    <button
                        onClick={() => setActiveView('journey')}
                        className={`flex-1 px-2 py-1 text-xs font-medium rounded transition-colors ${
                            activeView === 'journey'
                                ? 'bg-white dark:bg-gray-700 text-gray-900 dark:text-white shadow-sm'
                                : 'text-gray-600 dark:text-gray-400'
                        }`}
                    >
                        Recent
                    </button>
                    <button
                        onClick={() => setActiveView('topics')}
                        className={`flex-1 px-2 py-1 text-xs font-medium rounded transition-colors ${
                            activeView === 'topics'
                                ? 'bg-white dark:bg-gray-700 text-gray-900 dark:text-white shadow-sm'
                                : 'text-gray-600 dark:text-gray-400'
                        }`}
                    >
                        Topics
                    </button>
                </div>
            </div>

            {/* Content */}
            <div className="flex-1 overflow-y-auto p-4">
                {loading && queries.length === 0 && (
                    <div className="text-center py-8 text-gray-500 dark:text-gray-400 animate-pulse">
                        Loading...
                    </div>
                )}

                {/* Journey View - Recent Queries */}
                {activeView === 'journey' && (
                    <div className="space-y-3">
                        {queries.length === 0 && !loading ? (
                            <div className="text-center py-8">
                                <div className="text-gray-400 dark:text-gray-500 mb-2">
                                    <svg className="w-10 h-10 mx-auto" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" />
                                    </svg>
                                </div>
                                <p className="text-sm text-gray-500 dark:text-gray-400">No questions yet</p>
                                <p className="text-xs text-gray-400 dark:text-gray-500 mt-1">Start asking questions to track your journey</p>
                            </div>
                        ) : (
                            queries.map((query) => (
                                <button
                                    key={query.id}
                                    onClick={() => onQueryClick?.(query.query)}
                                    className="w-full text-left p-3 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg hover:border-blue-300 dark:hover:border-blue-600 transition-colors group"
                                >
                                    <div className="flex items-start justify-between gap-2 mb-1">
                                        <p className="text-sm text-gray-800 dark:text-gray-200 line-clamp-2 group-hover:text-blue-600 dark:group-hover:text-blue-400">
                                            {query.query}
                                        </p>
                                        <span className="text-xs text-gray-400 whitespace-nowrap">
                                            {formatTime(query.timestamp)}
                                        </span>
                                    </div>
                                    
                                    {query.topics.length > 0 && (
                                        <div className="flex flex-wrap gap-1 mt-2">
                                            {query.topics.slice(0, 3).map((topic, i) => (
                                                <span
                                                    key={i}
                                                    className="px-1.5 py-0.5 bg-purple-100 dark:bg-purple-900/30 text-purple-700 dark:text-purple-300 text-xs rounded"
                                                >
                                                    {topic}
                                                </span>
                                            ))}
                                        </div>
                                    )}
                                    
                                    <div className="flex items-center gap-2 mt-2">
                                        <span className={`px-1.5 py-0.5 text-xs rounded ${getConfidenceColor(query.confidence)}`}>
                                            {Math.round(query.confidence * 100)}% confident
                                        </span>
                                        <span className="text-xs text-gray-400 dark:text-gray-500">
                                            {query.sources_used.length} sources
                                        </span>
                                    </div>
                                </button>
                            ))
                        )}
                    </div>
                )}

                {/* Topics View */}
                {activeView === 'topics' && (
                    <div className="space-y-2">
                        {topics.length === 0 && !loading ? (
                            <div className="text-center py-8">
                                <p className="text-sm text-gray-500 dark:text-gray-400">No topics explored yet</p>
                            </div>
                        ) : (
                            topics.map((topic) => (
                                <button
                                    key={topic.name}
                                    onClick={() => onTopicClick?.(topic.name)}
                                    className="w-full flex items-center justify-between p-3 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg hover:border-purple-300 dark:hover:border-purple-600 transition-colors"
                                >
                                    <div className="flex items-center gap-2">
                                        <span className="w-6 h-6 flex items-center justify-center bg-purple-100 dark:bg-purple-900/30 text-purple-700 dark:text-purple-300 text-xs font-medium rounded-full">
                                            {topic.count}
                                        </span>
                                        <span className="text-sm text-gray-800 dark:text-gray-200 capitalize">
                                            {topic.name}
                                        </span>
                                    </div>
                                    <span className="text-xs text-gray-400 dark:text-gray-500">
                                        {formatTime(topic.last_seen)}
                                    </span>
                                </button>
                            ))
                        )}
                    </div>
                )}
            </div>
        </div>
    );
};
