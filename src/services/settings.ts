/**
 * Settings service for managing API keys and configuration
 */

import { API_BASE_URL } from './api';

const API_BASE = API_BASE_URL;

export interface APIKeysStatus {
    configured: {
        [key: string]: boolean;
    };
}

export interface LLMInfo {
    model_name: string;
    provider: string;
}

export interface UserProfile {
    name?: string;
    profession?: string;
    expertise_level?: 'beginner' | 'intermediate' | 'expert';
    response_style?: 'concise' | 'detailed' | 'balanced';
    tone?: 'formal' | 'casual' | 'professional';
    interests?: string[];
    favorite_authors?: string[];
    favorite_topics?: string[];
    goals?: string;
    custom_instructions?: string;
}

export const settingsService = {
    /**
     * Get the status of all API keys (configured or not)
     */
    async getAPIKeysStatus(): Promise<APIKeysStatus> {
        const response = await fetch(`${API_BASE}/settings/api-keys/status`);

        if (!response.ok) {
            throw new Error(`Failed to get API keys status: ${response.statusText}`);
        }

        return response.json();
    },

    /**
     * Set an API key
     */
    async setAPIKey(keyName: string, value: string): Promise<void> {
        const response = await fetch(`${API_BASE}/settings/api-keys/set`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                key_name: keyName,
                value,
            }),
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || `Failed to set API key: ${response.statusText}`);
        }

        return response.json();
    },

    /**
     * Delete an API key
     */
    async deleteAPIKey(keyName: string): Promise<void> {
        const response = await fetch(`${API_BASE}/settings/api-keys/${keyName}`, {
            method: 'DELETE',
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || `Failed to delete API key: ${response.statusText}`);
        }

        return response.json();
    },

    /**
     * Get current LLM model information
     */
    async getLLMInfo(): Promise<LLMInfo> {
        const response = await fetch(`${API_BASE}/settings/llm-info`);

        if (!response.ok) {
            throw new Error(`Failed to get LLM info: ${response.statusText}`);
        }

        return response.json();
    },

    /**
     * Get user profile for personalization
     */
    async getUserProfile(): Promise<UserProfile> {
        const response = await fetch(`${API_BASE}/settings/user-profile`);

        if (!response.ok) {
            throw new Error(`Failed to get user profile: ${response.statusText}`);
        }

        return response.json();
    },

    /**
     * Save user profile for personalization
     */
    async saveUserProfile(profile: UserProfile): Promise<void> {
        const response = await fetch(`${API_BASE}/settings/user-profile`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(profile),
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || `Failed to save user profile: ${response.statusText}`);
        }
    },

    /**
     * Delete user profile
     */
    async deleteUserProfile(): Promise<void> {
        const response = await fetch(`${API_BASE}/settings/user-profile`, {
            method: 'DELETE',
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || `Failed to delete user profile: ${response.statusText}`);
        }
    },
};
