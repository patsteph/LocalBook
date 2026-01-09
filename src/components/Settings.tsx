/**
 * Settings Component
 * Manage API keys and application configuration
 */

import React, { useState, useEffect } from 'react';
import { settingsService, UserProfile } from '../services/settings';
import { MemorySettings } from './MemorySettings';
import { CredentialLocker } from './CredentialLocker';
import { API_BASE_URL } from '../services/api';

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
    {
        name: 'YouTube',
        key: 'youtube_api_key',
        label: 'YouTube Data API Key',
        description: 'For YouTube site-specific search (free tier available)',
        placeholder: 'AIza...',
        getKeyUrl: 'https://console.cloud.google.com/apis/credentials',
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
    const [activeSection, setActiveSection] = useState<'profile' | 'api-keys' | 'credentials' | 'memory' | 'updates'>('api-keys');
    
    // User Profile state
    const [userProfile, setUserProfile] = useState<UserProfile>({});
    const [profileLoading, setProfileLoading] = useState(false);
    const [profileSaving, setProfileSaving] = useState(false);
    const [interestsInput, setInterestsInput] = useState('');
    
    // Update checking state
    const [updateInfo, setUpdateInfo] = useState<{
        current_version: string;
        latest_version?: string;
        update_available: boolean;
        release_notes?: string;
        download_url?: string;
        asset_download_url?: string;
        error?: string;
    } | null>(null);
    const [checkingUpdates, setCheckingUpdates] = useState(false);
    const [updateMessage, setUpdateMessage] = useState<string | null>(null);
    const [downloadProgress, setDownloadProgress] = useState<{
        downloading: boolean;
        progress: number;
        message: string;
        error?: string;
    } | null>(null);
    const [readyToInstall, setReadyToInstall] = useState(false);

    useEffect(() => {
        loadKeysStatus();
        loadUserProfile();
    }, []);
    
    const loadUserProfile = async () => {
        try {
            setProfileLoading(true);
            const profile = await settingsService.getUserProfile();
            setUserProfile(profile);
            if (profile.interests) {
                setInterestsInput(profile.interests.join(', '));
            }
        } catch (err) {
            console.error('Failed to load user profile:', err);
        } finally {
            setProfileLoading(false);
        }
    };
    
    const handleSaveProfile = async () => {
        try {
            setProfileSaving(true);
            setError(null);
            
            // Parse interests from comma-separated string
            const interests = interestsInput
                .split(',')
                .map(s => s.trim())
                .filter(s => s.length > 0);
            
            const profileToSave: UserProfile = {
                ...userProfile,
                interests: interests.length > 0 ? interests : undefined,
            };
            
            await settingsService.saveUserProfile(profileToSave);
            setSuccess('Profile saved successfully!');
            setTimeout(() => setSuccess(null), 3000);
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Failed to save profile');
        } finally {
            setProfileSaving(false);
        }
    };

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

    const checkForUpdates = async () => {
        setCheckingUpdates(true);
        setUpdateMessage(null);
        try {
            const response = await fetch(`${API_BASE_URL}/updates/check`);
            if (response.ok) {
                const data = await response.json();
                setUpdateInfo(data);
            } else {
                setUpdateInfo({
                    current_version: '0.1.0',
                    update_available: false,
                    error: 'Failed to check for updates'
                });
            }
        } catch (err) {
            setUpdateInfo({
                current_version: '0.1.0',
                update_available: false,
                error: 'Could not connect to server'
            });
        } finally {
            setCheckingUpdates(false);
        }
    };

    const downloadAndInstall = async () => {
        setUpdateMessage(null);
        setDownloadProgress({ downloading: true, progress: 0, message: 'Starting download...' });
        setReadyToInstall(false);
        
        try {
            // Start the download
            const response = await fetch(`${API_BASE_URL}/updates/download-and-install`, {
                method: 'POST'
            });
            
            if (!response.ok) {
                throw new Error('Failed to start download');
            }
            
            // Poll for progress
            const pollProgress = async () => {
                const progressResponse = await fetch(`${API_BASE_URL}/updates/download-progress`);
                if (progressResponse.ok) {
                    const progress = await progressResponse.json();
                    setDownloadProgress(progress);
                    
                    if (progress.error) {
                        setUpdateMessage(`Error: ${progress.error}`);
                        return false;
                    }
                    
                    if (progress.progress >= 100) {
                        setReadyToInstall(true);
                        return false; // Stop polling
                    }
                    
                    return progress.downloading; // Continue if still downloading
                }
                return false;
            };
            
            // Poll every 500ms
            while (await pollProgress()) {
                await new Promise(resolve => setTimeout(resolve, 500));
            }
            
            const result = await response.json();
            if (result.success) {
                setUpdateMessage(result.message);
                setReadyToInstall(true);
            } else {
                setUpdateMessage(result.message);
                setDownloadProgress(null);
            }
            
        } catch (err) {
            setUpdateMessage(err instanceof Error ? err.message : 'Download failed');
            setDownloadProgress(null);
        }
    };

    const installAndRestart = async () => {
        setUpdateMessage('Installing update...');
        try {
            const response = await fetch(`${API_BASE_URL}/updates/install-and-restart`, {
                method: 'POST'
            });
            
            if (response.ok) {
                const result = await response.json();
                setUpdateMessage(result.message);
                
                if (result.success) {
                    // Quit the app - the install script will relaunch it
                    setTimeout(async () => {
                        try {
                            const { exit } = await import('@tauri-apps/plugin-process');
                            await exit(0);
                        } catch (e) {
                            console.error('Failed to exit app:', e);
                            setUpdateMessage('Please manually quit and reopen the app to complete the update.');
                        }
                    }, 1000);
                }
            } else {
                setUpdateMessage('Failed to install update');
            }
        } catch (err) {
            setUpdateMessage(err instanceof Error ? err.message : 'Installation failed');
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

            {/* Settings Navigation */}
            <div className="flex border-b dark:border-gray-700 mb-6">
                <button
                    onClick={() => setActiveSection('profile')}
                    className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                        activeSection === 'profile'
                            ? 'border-indigo-600 text-indigo-600 dark:text-indigo-400'
                            : 'border-transparent text-gray-600 dark:text-gray-400 hover:text-gray-900'
                    }`}
                >
                    👤 Profile
                </button>
                <button
                    onClick={() => setActiveSection('api-keys')}
                    className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                        activeSection === 'api-keys'
                            ? 'border-blue-600 text-blue-600 dark:text-blue-400'
                            : 'border-transparent text-gray-600 dark:text-gray-400 hover:text-gray-900'
                    }`}
                >
                    🔑 API Keys
                </button>
                <button
                    onClick={() => setActiveSection('credentials')}
                    className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                        activeSection === 'credentials'
                            ? 'border-amber-600 text-amber-600 dark:text-amber-400'
                            : 'border-transparent text-gray-600 dark:text-gray-400 hover:text-gray-900'
                    }`}
                >
                    🔐 Site Logins
                </button>
                <button
                    onClick={() => setActiveSection('memory')}
                    className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                        activeSection === 'memory'
                            ? 'border-purple-600 text-purple-600 dark:text-purple-400'
                            : 'border-transparent text-gray-600 dark:text-gray-400 hover:text-gray-900'
                    }`}
                >
                    🧠 Memory
                </button>
                <button
                    onClick={() => {
                        setActiveSection('updates');
                        checkForUpdates();
                    }}
                    className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                        activeSection === 'updates'
                            ? 'border-green-600 text-green-600 dark:text-green-400'
                            : 'border-transparent text-gray-600 dark:text-gray-400 hover:text-gray-900'
                    }`}
                >
                    🔄 Updates
                </button>
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

            {/* Profile Section */}
            {activeSection === 'profile' && (
                <div className="space-y-6">
                    <div>
                        <h3 className="text-lg font-semibold text-gray-900 dark:text-white mb-2">User Profile</h3>
                        <p className="text-sm text-gray-600 dark:text-gray-400 mb-4">
                            Personalize how the AI responds to you. This information is stored locally and used to make responses feel more natural.
                        </p>
                    </div>

                    {profileLoading ? (
                        <div className="text-center py-8 text-gray-500">Loading profile...</div>
                    ) : (
                        <div className="space-y-4">
                            {/* Name */}
                            <div>
                                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                                    What should I call you?
                                </label>
                                <input
                                    type="text"
                                    value={userProfile.name || ''}
                                    onChange={(e) => setUserProfile({ ...userProfile, name: e.target.value })}
                                    placeholder="Your name or nickname"
                                    className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white text-sm"
                                />
                            </div>

                            {/* Profession */}
                            <div>
                                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                                    What do you do?
                                </label>
                                <input
                                    type="text"
                                    value={userProfile.profession || ''}
                                    onChange={(e) => setUserProfile({ ...userProfile, profession: e.target.value })}
                                    placeholder="e.g., Software Engineer, Researcher, Student"
                                    className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white text-sm"
                                />
                            </div>

                            {/* Expertise Level */}
                            <div>
                                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                                    Technical expertise level
                                </label>
                                <select
                                    value={userProfile.expertise_level || ''}
                                    onChange={(e) => setUserProfile({ ...userProfile, expertise_level: e.target.value as UserProfile['expertise_level'] })}
                                    className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white text-sm"
                                >
                                    <option value="">Select level...</option>
                                    <option value="beginner">Beginner - Explain things simply</option>
                                    <option value="intermediate">Intermediate - Some technical terms OK</option>
                                    <option value="expert">Expert - Use technical terminology freely</option>
                                </select>
                            </div>

                            {/* Response Style */}
                            <div>
                                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                                    Preferred response style
                                </label>
                                <select
                                    value={userProfile.response_style || ''}
                                    onChange={(e) => setUserProfile({ ...userProfile, response_style: e.target.value as UserProfile['response_style'] })}
                                    className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white text-sm"
                                >
                                    <option value="">Select style...</option>
                                    <option value="concise">Concise - Brief and to the point</option>
                                    <option value="balanced">Balanced - Medium detail</option>
                                    <option value="detailed">Detailed - Thorough explanations</option>
                                </select>
                            </div>

                            {/* Interests */}
                            <div>
                                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                                    Your interests (for better examples)
                                </label>
                                <input
                                    type="text"
                                    value={interestsInput}
                                    onChange={(e) => setInterestsInput(e.target.value)}
                                    placeholder="e.g., AI, woodworking, sci-fi, cooking (comma-separated)"
                                    className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white text-sm"
                                />
                                <p className="text-xs text-gray-500 mt-1">Comma-separated list</p>
                            </div>

                            {/* Goals */}
                            <div>
                                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                                    What are you using LocalBook for?
                                </label>
                                <input
                                    type="text"
                                    value={userProfile.goals || ''}
                                    onChange={(e) => setUserProfile({ ...userProfile, goals: e.target.value })}
                                    placeholder="e.g., Research, learning, building a second brain"
                                    className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white text-sm"
                                />
                            </div>

                            {/* Custom Instructions */}
                            <div>
                                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                                    Custom instructions (optional)
                                </label>
                                <textarea
                                    value={userProfile.custom_instructions || ''}
                                    onChange={(e) => setUserProfile({ ...userProfile, custom_instructions: e.target.value })}
                                    placeholder="Any specific instructions for how the AI should respond to you..."
                                    rows={3}
                                    className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white text-sm"
                                />
                            </div>

                            {/* Save Button */}
                            <div className="pt-4">
                                <button
                                    onClick={handleSaveProfile}
                                    disabled={profileSaving}
                                    className="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 disabled:bg-indigo-400 text-white rounded-lg text-sm font-medium transition-colors"
                                >
                                    {profileSaving ? 'Saving...' : 'Save Profile'}
                                </button>
                            </div>
                        </div>
                    )}
                </div>
            )}

            {/* Credentials Section */}
            {activeSection === 'credentials' && (
                <CredentialLocker />
            )}

            {/* Memory Section */}
            {activeSection === 'memory' && (
                <MemorySettings />
            )}

            {/* Updates Section */}
            {activeSection === 'updates' && (
                <div className="space-y-6">
                    <div>
                        <h3 className="text-lg font-semibold text-gray-900 dark:text-white mb-4">Software Updates</h3>
                        <p className="text-sm text-gray-600 dark:text-gray-400 mb-6">
                            Check for and install updates from GitHub.
                        </p>
                    </div>

                    {/* Current Version */}
                    <div className="p-4 border border-gray-200 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800">
                        <div className="flex items-center justify-between">
                            <div>
                                <h4 className="font-medium text-gray-900 dark:text-white">Current Version</h4>
                                <p className="text-2xl font-bold text-blue-600 dark:text-blue-400 mt-1">
                                    v{updateInfo?.current_version || '0.1.0'}
                                </p>
                            </div>
                            <button
                                onClick={checkForUpdates}
                                disabled={checkingUpdates}
                                className="px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-blue-400 text-white rounded-lg text-sm font-medium transition-colors"
                            >
                                {checkingUpdates ? 'Checking...' : 'Check for Updates'}
                            </button>
                        </div>
                    </div>

                    {/* Update Status */}
                    {updateInfo && (
                        <div className={`p-4 border rounded-lg ${
                            updateInfo.update_available 
                                ? 'border-green-200 dark:border-green-800 bg-green-50 dark:bg-green-900/20'
                                : 'border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800'
                        }`}>
                            {updateInfo.error ? (
                                <div className="text-red-600 dark:text-red-400">
                                    <p className="font-medium">Error checking for updates</p>
                                    <p className="text-sm mt-1">{updateInfo.error}</p>
                                </div>
                            ) : updateInfo.update_available ? (
                                <div>
                                    <div className="flex items-center gap-2 mb-3">
                                        <span className="text-green-600 dark:text-green-400 text-xl">🎉</span>
                                        <h4 className="font-medium text-green-700 dark:text-green-300">
                                            Update Available: v{updateInfo.latest_version}
                                        </h4>
                                    </div>
                                    {updateInfo.release_notes && (
                                        <div className="mb-4 p-3 bg-white dark:bg-gray-800 rounded text-sm text-gray-600 dark:text-gray-400">
                                            <p className="font-medium text-gray-900 dark:text-white mb-1">Release Notes:</p>
                                            <p className="whitespace-pre-wrap">{updateInfo.release_notes}</p>
                                        </div>
                                    )}
                                    {/* Download Progress */}
                                    {downloadProgress && downloadProgress.downloading && (
                                        <div className="mb-4">
                                            <div className="flex items-center justify-between mb-2">
                                                <span className="text-sm text-gray-600 dark:text-gray-400">{downloadProgress.message}</span>
                                                <span className="text-sm font-medium text-gray-900 dark:text-white">{downloadProgress.progress}%</span>
                                            </div>
                                            <div className="w-full bg-gray-200 dark:bg-gray-700 rounded-full h-2">
                                                <div 
                                                    className="bg-green-600 h-2 rounded-full transition-all duration-300"
                                                    style={{ width: `${downloadProgress.progress}%` }}
                                                />
                                            </div>
                                        </div>
                                    )}
                                    
                                    <div className="flex gap-3">
                                        {/* Show Install & Restart if download is complete */}
                                        {readyToInstall ? (
                                            <button
                                                onClick={installAndRestart}
                                                className="px-4 py-2 bg-green-600 hover:bg-green-700 text-white rounded-lg text-sm font-medium transition-colors flex items-center gap-2"
                                            >
                                                <span>🚀</span> Install & Restart
                                            </button>
                                        ) : (
                                            <button
                                                onClick={downloadAndInstall}
                                                disabled={downloadProgress?.downloading}
                                                className="px-4 py-2 bg-green-600 hover:bg-green-700 disabled:bg-green-400 text-white rounded-lg text-sm font-medium transition-colors"
                                            >
                                                {downloadProgress?.downloading ? 'Downloading...' : 'Download & Install'}
                                            </button>
                                        )}
                                        {updateInfo.download_url && !downloadProgress?.downloading && !readyToInstall && (
                                            <a
                                                href={updateInfo.download_url}
                                                target="_blank"
                                                rel="noopener noreferrer"
                                                className="px-4 py-2 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors"
                                            >
                                                View on GitHub →
                                            </a>
                                        )}
                                    </div>
                                </div>
                            ) : (
                                <div className="flex items-center gap-2 text-gray-600 dark:text-gray-400">
                                    <span className="text-xl">✅</span>
                                    <p>You're running the latest version!</p>
                                </div>
                            )}
                        </div>
                    )}

                    {/* Update Message */}
                    {updateMessage && (
                        <div className="p-4 border border-blue-200 dark:border-blue-800 bg-blue-50 dark:bg-blue-900/20 rounded-lg">
                            <p className="text-blue-700 dark:text-blue-300">{updateMessage}</p>
                        </div>
                    )}

                    {/* Data Safety Note */}
                    <div className="p-4 border border-gray-200 dark:border-gray-700 rounded-lg bg-gray-50 dark:bg-gray-800/50">
                        <h4 className="font-medium text-gray-900 dark:text-white mb-2">💾 Your Data is Safe</h4>
                        <p className="text-sm text-gray-600 dark:text-gray-400">
                            All your notebooks, sources, and settings are stored separately from the app. 
                            Updates will never affect your data.
                        </p>
                    </div>
                </div>
            )}

            {/* API Keys Section */}
            {activeSection === 'api-keys' && (
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
            )}
        </div>
    );
};
