/**
 * Themes Service - Access discovered themes from the knowledge graph
 */
import { API_BASE_URL } from './api';

export interface Theme {
    id: string;
    name: string;
    description: string | null;
    concepts: string[];
    concept_count: number;
    coherence_score: number;
    topic_id?: number;  // v0.6.5: BERTopic topic ID
    enhanced?: boolean;  // v0.6.5: Whether name has been LLM-enhanced
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

export const themesService = {
    /**
     * Get themes for a notebook
     */
    async getThemes(notebookId: string, limit: number = 10): Promise<ThemesResponse> {
        const response = await fetch(`${API_BASE_URL}/graph/themes/${notebookId}?limit=${limit}`);
        
        if (!response.ok) {
            throw new Error('Failed to fetch themes');
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
