import React, { useState, useEffect } from 'react';
import { settingsService } from '../../services/settings';

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
    {
        name: 'YouTube',
        key: 'youtube_api_key',
        label: 'YouTube Data API Key',
        description: 'For YouTube site-specific search (free tier available)',
        placeholder: 'AIza...',
        getKeyUrl: 'https://console.cloud.google.com/apis/credentials',
    },
];

interface APIKeysSectionProps {
    setError: (msg: string | null) => void;
    setSuccess: (msg: string | null) => void;
}

export const APIKeysSection: React.FC<APIKeysSectionProps> = ({ setError, setSuccess }) => {
    const [apiKeys, setApiKeys] = useState<{ [key: string]: string }>({});
    const [keysStatus, setKeysStatus] = useState<{ [key: string]: boolean }>({});
    const [saving, setSaving] = useState<string | null>(null);
    const [customLLMEndpoint, setCustomLLMEndpoint] = useState('');
    const [customLLMApiKey, setCustomLLMApiKey] = useState('');
    const [customLLMModel, setCustomLLMModel] = useState('');
    const [customLLMConfigured, setCustomLLMConfigured] = useState(false);

    useEffect(() => {
        loadKeysStatus();
    }, []);

    const loadKeysStatus = async () => {
        try {
            const status = await settingsService.getAPIKeysStatus();
            setKeysStatus(status.configured);
            setCustomLLMConfigured(status.configured['custom_llm'] || false);
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Failed to load settings');
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
            setApiKeys({ ...apiKeys, [keyName]: '' });
            await loadKeysStatus();
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
            await loadKeysStatus();
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Failed to remove API key');
        } finally {
            setSaving(null);
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
            const config = {
                endpoint: customLLMEndpoint.trim(),
                api_key: customLLMApiKey.trim(),
                model: customLLMModel.trim(),
            };
            await settingsService.setAPIKey('custom_llm', JSON.stringify(config));
            setSuccess('Custom LLM configuration saved successfully');
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

    const renderKeyCard = (config: APIKeyConfig) => {
        const isConfigured = keysStatus[config.key];
        const isSaving = saving === config.key;

        return (
            <div
                key={config.key}
                className="p-3 border border-gray-200 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800"
            >
                <div className="flex items-start justify-between mb-2">
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
                        className="flex-1 px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50"
                    />
                    <button
                        onClick={() => handleSaveKey(config.key)}
                        disabled={isSaving || !apiKeys[config.key]}
                        className="px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-blue-400 text-white rounded-lg text-sm font-medium transition-colors"
                    >
                        {isSaving ? 'Saving...' : 'Save'}
                    </button>
                    {isConfigured && (
                        <button
                            onClick={() => handleDeleteKey(config.key)}
                            disabled={isSaving}
                            className="px-4 py-2 bg-red-600 hover:bg-red-700 disabled:bg-red-400 text-white rounded-lg text-sm font-medium transition-colors"
                        >
                            Remove
                        </button>
                    )}
                </div>
            </div>
        );
    };

    return (
        <div className="space-y-4">
            <div>
                <h3 className="text-base font-semibold text-gray-900 dark:text-white mb-1">API Keys</h3>
                <p className="text-sm text-gray-600 dark:text-gray-400 mb-3">
                    API keys are stored securely in your system keychain and never leave your computer.
                </p>

                <div className="space-y-4">
                    {/* Brave Search - First Item */}
                    {renderKeyCard(API_KEY_CONFIGS[0])}

                    {/* Custom LLM - Second Item */}
                    <div className="p-3 border-2 border-blue-200 dark:border-blue-700 rounded-lg bg-blue-50 dark:bg-blue-900/10">
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
                                    <span className="px-2 py-0.5 text-xs font-medium bg-blue-200 dark:bg-blue-800 text-blue-800 dark:text-blue-200 rounded">
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
                                    className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50"
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
                                    className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50"
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
                                    className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50"
                                />
                            </div>
                            <div className="flex gap-2 pt-2">
                                <button
                                    onClick={handleSaveCustomLLM}
                                    disabled={saving === 'custom_llm' || !customLLMEndpoint || !customLLMApiKey || !customLLMModel}
                                    className="flex-1 px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-blue-400 text-white rounded-lg text-sm font-medium transition-colors"
                                >
                                    {saving === 'custom_llm' ? 'Saving...' : 'Save Custom LLM'}
                                </button>
                                {customLLMConfigured && (
                                    <button
                                        onClick={handleDeleteCustomLLM}
                                        disabled={saving === 'custom_llm'}
                                        className="px-4 py-2 bg-red-600 hover:bg-red-700 disabled:bg-red-400 text-white rounded-lg text-sm font-medium transition-colors"
                                    >
                                        Remove
                                    </button>
                                )}
                            </div>
                        </div>
                    </div>

                    {/* Other LLM Providers */}
                    {API_KEY_CONFIGS.slice(1).map(renderKeyCard)}
                </div>
            </div>
        </div>
    );
};
