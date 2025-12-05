import React, { useState, useEffect } from 'react';
import { settingsService } from '../services/settings';

interface LLMSelectorProps {
  selectedProvider: string;
  onProviderChange: (provider: string) => void;
}

export const LLMSelector: React.FC<LLMSelectorProps> = ({ selectedProvider, onProviderChange }) => {
  const [mode, setMode] = useState<'local' | 'cloud'>('local');
  const [availableProviders, setAvailableProviders] = useState<{[key: string]: boolean}>({});
  const [currentModel, setCurrentModel] = useState<string>('mistral-nemo');

  useEffect(() => {
    loadAvailableProviders();
    loadCurrentModel();
    // Set mode based on selected provider
    if (selectedProvider === 'ollama') {
      setMode('local');
    } else {
      setMode('cloud');
    }
  }, [selectedProvider]);

  const loadAvailableProviders = async () => {
    try {
      const status = await settingsService.getAPIKeysStatus();
      setAvailableProviders(status.configured);
    } catch (err) {
      console.error('Failed to load provider status:', err);
    }
  };

  const loadCurrentModel = async () => {
    try {
      const info = await settingsService.getLLMInfo();
      setCurrentModel(info.model_name);
    } catch (err) {
      console.error('Failed to load LLM info:', err);
    }
  };

  const handleModeToggle = (newMode: 'local' | 'cloud') => {
    setMode(newMode);
    if (newMode === 'local') {
      onProviderChange('ollama');
    } else {
      // Select first available cloud provider
      if (availableProviders.custom_llm) {
        onProviderChange('custom_llm');
      } else if (availableProviders.openai) {
        onProviderChange('openai');
      } else if (availableProviders.anthropic) {
        onProviderChange('anthropic');
      } else if (availableProviders.google_ai) {
        onProviderChange('google_ai');
      }
    }
  };

  const cloudProviders = [
    { id: 'custom_llm', name: 'Custom LLM', subtitle: 'Company Internal', available: availableProviders.custom_llm, special: true },
    { id: 'openai', name: 'OpenAI', subtitle: 'GPT-4o', available: availableProviders.openai },
    { id: 'anthropic', name: 'Anthropic', subtitle: 'Claude 3.5 Sonnet', available: availableProviders.anthropic },
    { id: 'google_ai', name: 'Google AI', subtitle: 'Gemini 1.5 Flash', available: availableProviders.google_ai },
  ];

  return (
    <div className="p-6">
      {/* Toggle Switch */}
      <div className="flex items-center justify-center mb-6">
        <div className="relative inline-flex items-center bg-gray-200 dark:bg-gray-700 rounded-full p-1">
          <button
            onClick={() => handleModeToggle('local')}
            className={`px-6 py-2 rounded-full text-sm font-medium transition-all ${
              mode === 'local'
                ? 'bg-blue-600 text-white shadow-lg'
                : 'text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white'
            }`}
          >
            🏠 Local
          </button>
          <button
            onClick={() => handleModeToggle('cloud')}
            className={`px-6 py-2 rounded-full text-sm font-medium transition-all ${
              mode === 'cloud'
                ? 'bg-blue-600 text-white shadow-lg'
                : 'text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white'
            }`}
          >
            ☁️ Cloud
          </button>
        </div>
      </div>

      {/* Local Mode */}
      {mode === 'local' && (
        <div className="space-y-3">
          <button
            onClick={() => onProviderChange('ollama')}
            className={`w-full p-4 rounded-lg border-2 transition-all ${
              selectedProvider === 'ollama'
                ? 'border-blue-600 bg-blue-50 dark:bg-blue-900/20'
                : 'border-gray-300 dark:border-gray-600 hover:border-blue-400 bg-white dark:bg-gray-800'
            }`}
          >
            <div className="flex items-center gap-3">
              <div className="text-3xl">🦙</div>
              <div className="flex-1 text-left">
                <div className="font-semibold text-gray-900 dark:text-white">Ollama</div>
                <div className="text-sm text-gray-600 dark:text-gray-400">Local Model: {currentModel}</div>
              </div>
              {selectedProvider === 'ollama' && (
                <svg className="w-6 h-6 text-blue-600" fill="currentColor" viewBox="0 0 20 20">
                  <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
                </svg>
              )}
            </div>
          </button>
          <div className="text-sm text-gray-500 dark:text-gray-400 text-center">
            Running locally on your machine - completely private
          </div>
        </div>
      )}

      {/* Cloud Mode */}
      {mode === 'cloud' && (
        <div className="space-y-3">
          {cloudProviders.map((provider) => (
            <button
              key={provider.id}
              onClick={() => provider.available && onProviderChange(provider.id)}
              disabled={!provider.available}
              className={`w-full p-4 rounded-lg border-2 transition-all ${
                selectedProvider === provider.id
                  ? provider.special
                    ? 'border-purple-600 bg-purple-50 dark:bg-purple-900/20'
                    : 'border-blue-600 bg-blue-50 dark:bg-blue-900/20'
                  : provider.available
                  ? provider.special
                    ? 'border-purple-300 dark:border-purple-700 hover:border-purple-400 bg-white dark:bg-gray-800'
                    : 'border-gray-300 dark:border-gray-600 hover:border-blue-400 bg-white dark:bg-gray-800'
                  : 'border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 opacity-50 cursor-not-allowed'
              }`}
            >
              <div className="flex items-center gap-3">
                <div className="text-2xl">
                  {provider.id === 'custom_llm' ? '🔐' :
                   provider.id === 'openai' ? '🤖' :
                   provider.id === 'anthropic' ? '🧠' : '✨'}
                </div>
                <div className="flex-1 text-left">
                  <div className="flex items-center gap-2">
                    <div className={`font-semibold ${
                      provider.available
                        ? 'text-gray-900 dark:text-white'
                        : 'text-gray-500 dark:text-gray-500'
                    }`}>
                      {provider.name}
                    </div>
                    {provider.special && provider.available && (
                      <span className="px-2 py-0.5 text-xs font-medium bg-purple-200 dark:bg-purple-800 text-purple-800 dark:text-purple-200 rounded">
                        Secure
                      </span>
                    )}
                  </div>
                  <div className={`text-sm ${
                    provider.available
                      ? 'text-gray-600 dark:text-gray-400'
                      : 'text-gray-500 dark:text-gray-500'
                  }`}>
                    {provider.available ? provider.subtitle : 'Not configured'}
                  </div>
                </div>
                {selectedProvider === provider.id && provider.available && (
                  <svg className={`w-6 h-6 ${provider.special ? 'text-purple-600' : 'text-blue-600'}`} fill="currentColor" viewBox="0 0 20 20">
                    <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
                  </svg>
                )}
              </div>
            </button>
          ))}
          {!cloudProviders.some(p => p.available) && (
            <div className="text-center py-4">
              <p className="text-sm text-gray-500 dark:text-gray-400 mb-3">
                No cloud providers configured
              </p>
              <p className="text-xs text-gray-400 dark:text-gray-500">
                Configure API keys in Settings to use cloud LLMs
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  );
};
