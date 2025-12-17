/**
 * Exploration Service - Track and retrieve user's learning journey
 */
import { API_BASE_URL } from './api';

export interface QueryRecord {
    id: string;
    query: string;
    topics: string[];
    sources_used: string[];
    confidence: number;
    answer_preview: string;
    timestamp: string;
}

export interface TopicExplored {
    name: string;
    count: number;
    first_seen: string;
    last_seen: string;
}

export interface SourceAccessed {
    source_id: string;
    count: number;
    first_accessed: string;
    last_accessed: string;
}

export interface JourneyResponse {
    notebook_id: string;
    queries: QueryRecord[];
    topics_explored: TopicExplored[];
    sources_accessed: SourceAccessed[];
    total_queries: number;
}

export interface Suggestion {
    type: 'continue' | 'dive_deeper';
    message: string;
    query?: string;
    topic?: string;
}

export interface SuggestionsResponse {
    suggestions: Suggestion[];
    shallow_topics: { name: string; count: number }[];
}

export const explorationService = {
    /**
     * Record a query in the exploration history
     */
    async recordQuery(
        notebookId: string,
        query: string,
        topics: string[] = [],
        sourcesUsed: string[] = [],
        confidence: number = 0.5,
        answerPreview: string = ''
    ): Promise<{ status: string; query_id: string }> {
        const response = await fetch(`${API_BASE_URL}/exploration/record`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                notebook_id: notebookId,
                query,
                topics,
                sources_used: sourcesUsed,
                confidence,
                answer_preview: answerPreview,
            }),
        });

        if (!response.ok) {
            throw new Error('Failed to record query');
        }

        return response.json();
    },

    /**
     * Get the exploration journey for a notebook
     */
    async getJourney(notebookId: string, limit: number = 50): Promise<JourneyResponse> {
        const response = await fetch(`${API_BASE_URL}/exploration/journey/${notebookId}?limit=${limit}`);

        if (!response.ok) {
            throw new Error('Failed to get journey');
        }

        return response.json();
    },

    /**
     * Get suggestions for continuing exploration
     */
    async getSuggestions(notebookId: string): Promise<SuggestionsResponse> {
        const response = await fetch(`${API_BASE_URL}/exploration/suggestions/${notebookId}`);

        if (!response.ok) {
            throw new Error('Failed to get suggestions');
        }

        return response.json();
    },

    /**
     * Clear exploration history for a notebook
     */
    async clearHistory(notebookId: string): Promise<void> {
        const response = await fetch(`${API_BASE_URL}/exploration/clear/${notebookId}`, {
            method: 'DELETE',
        });

        if (!response.ok) {
            throw new Error('Failed to clear history');
        }
    },
};
