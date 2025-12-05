/**
 * Settings Component
 * Manage API keys and application configuration
 */

import React, { useState, useEffect } from 'react';
import { settingsService } from '../services/settings';

interface SettingsProps {
    onClose?: () => void;
}

interface APIKeyConfig {
    name: string;
    key: string;
    label: string;
    description: string;
    placeholder: string;
    getKeyUrl?: string;
}

const API_KEY_CONFIGS: APIKeyConfig[] = [
    {
        name: 'Brave Search',
        key: 'brave_api_key',
        label: 'Brave Search API Key',
        description: 'For web search functionality (20 results/query, 2,000 queries/month free)',
        placeholder: 'BSA...',
        getKeyUrl: 'https://brave.com/search/api/',
    },
    {
        name: 'OpenAI',
        key: 'openai_api_key',
        label: 'OpenAI API Key',
        description: 'For GPT-4, GPT-3.5, and other OpenAI models',
        placeholder: 'sk-...',
        getKeyUrl: 'https://platform.openai.com/api-keys',
    },
    {
        name: 'Anthropic',
        key: 'anthropic_api_key',
        label: 'Anthropic API Key',
        description: 'For Claude models (Claude 3.5 Sonnet, etc.)',
        placeholder: 'sk-ant-...',
        getKeyUrl: 'https://console.anthropic.com/settings/keys',
    },
    {
        name: 'Google AI',
        key: 'gemini_api_key',
        label: 'Google AI API Key',
        description: 'For Gemini models',
        placeholder: 'AI...',
        getKeyUrl: 'https://aistudio.google.com/app/apikey',
    },
];

export const Settings: React.FC<SettingsProps> = ({ onClose }) => {
    const [apiKeys, setApiKeys] = useState<{ [key: string]: string }>({});
    const [keysStatus, setKeysStatus] = useState<{ [key: string]: boolean }>({});
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [success, setSuccess] = useState<string | null>(null);

    // Custom LLM configuration
    const [customLLMEndpoint, setCustomLLMEndpoint] = useState('');
    const [customLLMApiKey, setCustomLLMApiKey] = useState('');
    const [customLLMModel, setCustomLLMModel] = useState('');
    const [customLLMConfigured, setCustomLLMConfigured] = useState(false);

    useEffect(() => {
        loadKeysStatus();
    }, []);

    const loadKeysStatus = async () => {
        try {
            setLoading(true);
            const status = await settingsService.getAPIKeysStatus();
            setKeysStatus(status.configured);

            // Check if custom LLM is configured
            setCustomLLMConfigured(status.configured['custom_llm'] || false);
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Failed to load settings');
        } finally {
            setLoading(false);
        }
    };

    const handleSaveCustomLLM = async () => {
        if (!customLLMEndpoint.trim() || !customLLMApiKey.trim() || !customLLMModel.trim()) {
            setError('Please fill in all Custom LLM fields');
            return;
        }

        setSaving('custom_llm');
        setError(null);
        setSuccess(null);

        try {
            // Save as a JSON object
            const config = {
                endpoint: customLLMEndpoint.trim(),
                api_key: customLLMApiKey.trim(),
                model: customLLMModel.trim(),
            };
            await settingsService.setAPIKey('custom_llm', JSON.stringify(config));
            setSuccess('Custom LLM configuration saved successfully');

            // Clear inputs
            setCustomLLMEndpoint('');
            setCustomLLMApiKey('');
            setCustomLLMModel('');

            await loadKeysStatus();
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Failed to save Custom LLM configuration');
        } finally {
            setSaving(null);
        }
    };

    const handleDeleteCustomLLM = async () => {
        if (!window.confirm('Are you sure you want to remove the Custom LLM configuration?')) {
            return;
        }

        setSaving('custom_llm');
        setError(null);
        setSuccess(null);

        try {
            await settingsService.deleteAPIKey('custom_llm');
            setSuccess('Custom LLM configuration removed successfully');
            await loadKeysStatus();
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Failed to remove Custom LLM configuration');
        } finally {
            setSaving(null);
        }
    };

    const handleSaveKey = async (keyName: string) => {
        const value = apiKeys[keyName];
        if (!value || !value.trim()) {
            setError('Please enter an API key');
            return;
        }

        setSaving(keyName);
        setError(null);
        setSuccess(null);

        try {
            await settingsService.setAPIKey(keyName, value.trim());
            setSuccess(`${API_KEY_CONFIGS.find(c => c.key === keyName)?.name} API key saved successfully`);
            setApiKeys({ ...apiKeys, [keyName]: '' }); // Clear input
            await loadKeysStatus(); // Refresh status
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Failed to save API key');
        } finally {
            setSaving(null);
        }
    };

    const handleDeleteKey = async (keyName: string) => {
        if (!window.confirm(`Are you sure you want to remove the ${API_KEY_CONFIGS.find(c => c.key === keyName)?.name} API key?`)) {
            return;
        }

        setSaving(keyName);
        setError(null);
        setSuccess(null);

        try {
            await settingsService.deleteAPIKey(keyName);
            setSuccess(`${API_KEY_CONFIGS.find(c => c.key === keyName)?.name} API key removed successfully`);
            await loadKeysStatus(); // Refresh status
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Failed to remove API key');
        } finally {
            setSaving(null);
        }
    };

    if (loading) {
        return (
            <div className="flex items-center justify-center p-8">
                <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600"></div>
            </div>
        );
    }

    return (
        <div className="p-6 max-w-4xl mx-auto">
            <div className="flex justify-between items-center mb-6">
                <h2 className="text-2xl font-bold text-gray-900 dark:text-white">Settings</h2>
                {onClose && (
                    <button
                        onClick={onClose}
                        className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
                    >
                        <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                        </svg>
                    </button>
                )}
            </div>

            {error && (
                <div className="mb-4 p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded text-sm text-red-700 dark:text-red-400">
                    {error}
                </div>
            )}

            {success && (
                <div className="mb-4 p-3 bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800 rounded text-sm text-green-700 dark:text-green-400">
                    {success}
                </div>
            )}

            <div className="space-y-6">
                <div>
                    <h3 className="text-lg font-semibold text-gray-900 dark:text-white mb-4">API Keys</h3>
                    <p className="text-sm text-gray-600 dark:text-gray-400 mb-6">
                        API keys are stored securely in your system keychain and never leave your computer.
                    </p>

                    <div className="space-y-6">
                        {/* Brave Search - First Item */}
                        {(() => {
                            const config = API_KEY_CONFIGS[0]; // Brave Search
                            const isConfigured = keysStatus[config.key];
                            const isSaving = saving === config.key;

                            return (
                                <div
                                    key={config.key}
                                    className="p-4 border border-gray-200 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800"
                                >
                                    <div className="flex items-start justify-between mb-3">
                                        <div className="flex-1">
                                            <div className="flex items-center gap-2">
                                                <h4 className="font-medium text-gray-900 dark:text-white">
                                                    {config.label}
                                                </h4>
                                                {isConfigured && (
                                                    <span className="px-2 py-0.5 text-xs font-medium bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400 rounded">
                                                        Configured
                                                    </span>
                                                )}
                                            </div>
                                            <p className="text-sm text-gray-600 dark:text-gray-400 mt-1">
                                                {config.description}
                                            </p>
                                        </div>
                                        {config.getKeyUrl && (
                                            <a
                                                href={config.getKeyUrl}
                                                target="_blank"
                                                rel="noopener noreferrer"
                                                className="text-sm text-blue-600 dark:text-blue-400 hover:underline ml-4"
                                            >
                                                Get Key →
                                            </a>
                                        )}
                                    </div>

                                    <div className="flex gap-2">
                                        <input
                                            type="password"
                                            value={apiKeys[config.key] || ''}
                                            onChange={(e) => setApiKeys({ ...apiKeys, [config.key]: e.target.value })}
                                            placeholder={isConfigured ? '••••••••' : config.placeholder}
                                            disabled={isSaving}
                                            className="flex-1 px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-gray-900 dark:text-white text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50"
                                        />
                                        <button
                                            onClick={() => handleSaveKey(config.key)}
                                            disabled={isSaving || !apiKeys[config.key]}
                                            className="px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-gray-400 text-white rounded text-sm font-medium transition-colors"
                                        >
                                            {isSaving ? 'Saving...' : 'Save'}
                                        </button>
                                        {isConfigured && (
                                            <button
                                                onClick={() => handleDeleteKey(config.key)}
                                                disabled={isSaving}
                                                className="px-4 py-2 bg-red-600 hover:bg-red-700 disabled:bg-gray-400 text-white rounded text-sm font-medium transition-colors"
                                            >
                                                Remove
                                            </button>
                                        )}
                                    </div>
                                </div>
                            );
                        })()}

                        {/* Custom LLM - Second Item */}
                        <div className="p-4 border-2 border-purple-200 dark:border-purple-700 rounded-lg bg-purple-50 dark:bg-purple-900/10">
                            <div className="flex items-start justify-between mb-3">
                                <div className="flex-1">
                                    <div className="flex items-center gap-2">
                                        <h4 className="font-medium text-gray-900 dark:text-white">
                                            Custom LLM (Secure)
                                        </h4>
                                        {customLLMConfigured && (
                                            <span className="px-2 py-0.5 text-xs font-medium bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400 rounded">
                                                Configured
                                            </span>
                                        )}
                                        <span className="px-2 py-0.5 text-xs font-medium bg-purple-200 dark:bg-purple-800 text-purple-800 dark:text-purple-200 rounded">
                                            Company Internal
                                        </span>
                                    </div>
                                    <p className="text-sm text-gray-600 dark:text-gray-400 mt-1">
                                        Configure your company's secure internal LLM API endpoint
                                    </p>
                                </div>
                            </div>

                            <div className="space-y-3">
                                <div>
                                    <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
                                        API Endpoint URL
                                    </label>
                                    <input
                                        type="text"
                                        value={customLLMEndpoint}
                                        onChange={(e) => setCustomLLMEndpoint(e.target.value)}
                                        placeholder="https://api.yourcompany.com/v1"
                                        disabled={saving === 'custom_llm'}
                                        className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-gray-900 dark:text-white text-sm focus:outline-none focus:ring-2 focus:ring-purple-500 disabled:opacity-50"
                                    />
                                </div>
                                <div>
                                    <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
                                        API Key
                                    </label>
                                    <input
                                        type="password"
                                        value={customLLMApiKey}
                                        onChange={(e) => setCustomLLMApiKey(e.target.value)}
                                        placeholder={customLLMConfigured ? '••••••••' : 'Your API key'}
                                        disabled={saving === 'custom_llm'}
                                        className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-gray-900 dark:text-white text-sm focus:outline-none focus:ring-2 focus:ring-purple-500 disabled:opacity-50"
                                    />
                                </div>
                                <div>
                                    <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
                                        Model Name
                                    </label>
                                    <input
                                        type="text"
                                        value={customLLMModel}
                                        onChange={(e) => setCustomLLMModel(e.target.value)}
                                        placeholder="gpt-4, claude-3, etc."
                                        disabled={saving === 'custom_llm'}
                                        className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-gray-900 dark:text-white text-sm focus:outline-none focus:ring-2 focus:ring-purple-500 disabled:opacity-50"
                                    />
                                </div>
                                <div className="flex gap-2 pt-2">
                                    <button
                                        onClick={handleSaveCustomLLM}
                                        disabled={saving === 'custom_llm' || !customLLMEndpoint || !customLLMApiKey || !customLLMModel}
                                        className="flex-1 px-4 py-2 bg-purple-600 hover:bg-purple-700 disabled:bg-gray-400 text-white rounded text-sm font-medium transition-colors"
                                    >
                                        {saving === 'custom_llm' ? 'Saving...' : 'Save Custom LLM'}
                                    </button>
                                    {customLLMConfigured && (
                                        <button
                                            onClick={handleDeleteCustomLLM}
                                            disabled={saving === 'custom_llm'}
                                            className="px-4 py-2 bg-red-600 hover:bg-red-700 disabled:bg-gray-400 text-white rounded text-sm font-medium transition-colors"
                                        >
                                            Remove
                                        </button>
                                    )}
                                </div>
                            </div>
                        </div>

                        {/* Other LLM Providers */}
                        {API_KEY_CONFIGS.slice(1).map((config) => {
                            const isConfigured = keysStatus[config.key];
                            const isSaving = saving === config.key;

                            return (
                                <div
                                    key={config.key}
                                    className="p-4 border border-gray-200 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800"
                                >
                                    <div className="flex items-start justify-between mb-3">
                                        <div className="flex-1">
                                            <div className="flex items-center gap-2">
                                                <h4 className="font-medium text-gray-900 dark:text-white">
                                                    {config.label}
                                                </h4>
                                                {isConfigured && (
                                                    <span className="px-2 py-0.5 text-xs font-medium bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400 rounded">
                                                        Configured
                                                    </span>
                                                )}
                                            </div>
                                            <p className="text-sm text-gray-600 dark:text-gray-400 mt-1">
                                                {config.description}
                                            </p>
                                        </div>
                                        {config.getKeyUrl && (
                                            <a
                                                href={config.getKeyUrl}
                                                target="_blank"
                                                rel="noopener noreferrer"
                                                className="text-sm text-blue-600 dark:text-blue-400 hover:underline ml-4"
                                            >
                                                Get Key →
                                            </a>
                                        )}
                                    </div>

                                    <div className="flex gap-2">
                                        <input
                                            type="password"
                                            value={apiKeys[config.key] || ''}
                                            onChange={(e) => setApiKeys({ ...apiKeys, [config.key]: e.target.value })}
                                            placeholder={isConfigured ? '••••••••' : config.placeholder}
                                            disabled={isSaving}
                                            className="flex-1 px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-gray-900 dark:text-white text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50"
                                        />
                                        <button
                                            onClick={() => handleSaveKey(config.key)}
                                            disabled={isSaving || !apiKeys[config.key]}
                                            className="px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-gray-400 text-white rounded text-sm font-medium transition-colors"
                                        >
                                            {isSaving ? 'Saving...' : 'Save'}
                                        </button>
                                        {isConfigured && (
                                            <button
                                                onClick={() => handleDeleteKey(config.key)}
                                                disabled={isSaving}
                                                className="px-4 py-2 bg-red-600 hover:bg-red-700 disabled:bg-gray-400 text-white rounded text-sm font-medium transition-colors"
                                            >
                                                Remove
                                            </button>
                                        )}
                                    </div>
                                </div>
                            );
                        })}
                    </div>
                </div>
            </div>
        </div>
    );
};
