/**
 * Themes Service - Access discovered themes from the knowledge graph
 */
import { API_BASE_URL } from './api';

export interface TopicSource {
    source_id: string;
    filename: string;
    chunk_count: number;
}

export interface Theme {
    id: string;
    name: string;
    description: string | null;
    concepts: string[];
    concept_count: number;
    coherence_score: number;
    topic_id?: number;  // v0.6.5: BERTopic topic ID
    enhanced?: boolean;  // v0.6.5: Whether name has been LLM-enhanced
    sources?: TopicSource[];  // Source attribution
}

export interface TopConcept {
    id: string;
    name: string;
    size: number;
}

export interface ThemesResponse {
    notebook_id: string;
    themes: Theme[];
    theme_count: number;
    top_concepts: TopConcept[];
    total_concepts: number;
}

export interface ExplorationQuestionsResponse {
    topic_id: number;
    topic_name: string;
    questions: string[];
}

export const themesService = {
    /**
     * Get themes for a notebook
     */
    async getThemes(notebookId: string, limit: number = 50): Promise<ThemesResponse> {
        const response = await fetch(`${API_BASE_URL}/graph/themes/${notebookId}?limit=${limit}`);
        
        if (!response.ok) {
            throw new Error('Failed to fetch themes');
        }
        
        return response.json();
    },

    /**
     * Get exploration questions for a specific topic
     */
    async getTopicQuestions(topicId: number, notebookId: string): Promise<ExplorationQuestionsResponse> {
        const response = await fetch(
            `${API_BASE_URL}/graph/topics/${topicId}/questions?notebook_id=${notebookId}`
        );
        
        if (!response.ok) {
            throw new Error('Failed to fetch exploration questions');
        }
        
        return response.json();
    },

    /**
     * Trigger rebuild of themes for a notebook
     * v0.6.5: Now calls /graph/build/{notebookId} instead of deprecated /graph/cluster
     */
    async rebuildThemes(notebookId: string): Promise<{ message: string; status: string }> {
        const response = await fetch(`${API_BASE_URL}/graph/build/${notebookId}`, {
            method: 'POST',
        });
        
        if (!response.ok) {
            throw new Error('Failed to rebuild themes');
        }
        
        return response.json();
    },
};
