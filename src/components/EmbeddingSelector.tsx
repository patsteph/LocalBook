import React, { useState, useEffect } from 'react';
import { embeddingService } from '../services/embeddings';

interface EmbeddingSelectorProps {
  notebookId: string | null;
  onModelChange?: () => void;
}

interface EmbeddingModel {
  id: string;
  name: string;
  description: string;
  dimensions: number;
  size: string;
  icon: string;
  isDefault?: boolean;
}

const EMBEDDING_MODELS: EmbeddingModel[] = [
  {
    id: 'nomic-embed-text',
    name: 'Nomic Embed',
    description: 'High quality via Ollama, 8K context (Default)',
    dimensions: 768,
    size: '274MB',
    icon: '⭐',
    isDefault: true,
  },
  {
    id: 'mxbai-embed-large',
    name: 'MixedBread Large',
    description: 'Best quality, outperforms OpenAI ada-002',
    dimensions: 1024,
    size: '670MB',
    icon: '🎯',
  },
  {
    id: 'all-minilm',
    name: 'MiniLM Fast',
    description: 'Fastest inference via Ollama',
    dimensions: 384,
    size: '46MB',
    icon: '⚡',
  },
  {
    id: 'snowflake-arctic-embed',
    name: 'Snowflake Arctic',
    description: 'Excellent for retrieval tasks',
    dimensions: 1024,
    size: '670MB',
    icon: '❄️',
  },
];

export const EmbeddingSelector: React.FC<EmbeddingSelectorProps> = ({ notebookId, onModelChange }) => {
  const [currentModel, setCurrentModel] = useState<string>('nomic-embed-text');
  const [loading, setLoading] = useState(true);
  const [changing, setChanging] = useState(false);
  const [reembedding, setReembedding] = useState(false);
  const [reembedProgress, setReembedProgress] = useState({ current: 0, total: 0 });
  const [error, setError] = useState<string | null>(null);
  const [needsReembedding, setNeedsReembedding] = useState(false);

  useEffect(() => {
    loadCurrentModel();
  }, [notebookId]);

  const loadCurrentModel = async () => {
    if (!notebookId) {
      setLoading(false);
      return;
    }

    try {
      setLoading(true);
      const response = await embeddingService.getCurrentModel(notebookId);
      setCurrentModel(response.model_name);
      setNeedsReembedding(response.needs_reembedding || false);
    } catch (err) {
      console.error('Failed to load current model:', err);
      // Default to the default model if loading fails
      setCurrentModel('nomic-embed-text');
    } finally {
      setLoading(false);
    }
  };

  const handleModelChange = async (modelId: string) => {
    if (!notebookId || modelId === currentModel) {
      return;
    }

    setChanging(true);
    setError(null);

    try {
      await embeddingService.changeModel(notebookId, modelId);
      setCurrentModel(modelId);
      setNeedsReembedding(true);

      if (onModelChange) {
        onModelChange();
      }
    } catch (err: any) {
      console.error('Failed to change model:', err);
      setError(err.response?.data?.detail || err.message || 'Failed to change model');
    } finally {
      setChanging(false);
    }
  };

  const handleReembed = async () => {
    if (!notebookId) {
      return;
    }

    setReembedding(true);
    setError(null);
    setReembedProgress({ current: 0, total: 0 });

    try {
      // Start re-embedding process
      await embeddingService.reembedNotebook(notebookId);

      // Poll for progress
      const pollInterval = setInterval(async () => {
        try {
          const progress = await embeddingService.getReembedProgress(notebookId);
          setReembedProgress(progress);

          if (progress.current >= progress.total && progress.total > 0) {
            clearInterval(pollInterval);
            setReembedding(false);
            setNeedsReembedding(false);

            if (onModelChange) {
              onModelChange();
            }
          }
        } catch (err) {
          console.error('Failed to get progress:', err);
          clearInterval(pollInterval);
          setReembedding(false);
        }
      }, 1000);

      // Timeout after 10 minutes
      setTimeout(() => {
        clearInterval(pollInterval);
        if (reembedding) {
          setReembedding(false);
          setError('Re-embedding timeout. Please try again.');
        }
      }, 600000);

    } catch (err: any) {
      console.error('Failed to start re-embedding:', err);
      setError(err.response?.data?.detail || err.message || 'Failed to start re-embedding');
      setReembedding(false);
    }
  };

  if (!notebookId) {
    return (
      <div className="p-6">
        <div className="text-center text-gray-500 dark:text-gray-400">
          Select a notebook to manage embedding models
        </div>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="p-6">
        <div className="flex items-center justify-center">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600"></div>
        </div>
      </div>
    );
  }

  const selectedModel = EMBEDDING_MODELS.find(m => m.id === currentModel);
  const progressPercentage = reembedProgress.total > 0
    ? (reembedProgress.current / reembedProgress.total) * 100
    : 0;

  return (
    <div className="p-6">
      <div className="mb-6">
        <h3 className="text-lg font-semibold text-gray-900 dark:text-white mb-2">
          Embedding Model
        </h3>
        <p className="text-sm text-gray-600 dark:text-gray-400">
          Choose how your documents are embedded for semantic search
        </p>
      </div>

      {error && (
        <div className="mb-4 p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg">
          <p className="text-sm text-red-600 dark:text-red-400">{error}</p>
        </div>
      )}

      {/* Current Model Info */}
      {selectedModel && (
        <div className="mb-6 p-4 bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-lg">
          <div className="flex items-center gap-3">
            <span className="text-2xl">{selectedModel.icon}</span>
            <div className="flex-1">
              <div className="font-medium text-gray-900 dark:text-white">
                Current: {selectedModel.name}
              </div>
              <div className="text-sm text-gray-600 dark:text-gray-400">
                {selectedModel.dimensions}d • {selectedModel.size}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Re-embedding Warning */}
      {needsReembedding && !reembedding && (
        <div className="mb-4 p-4 bg-yellow-50 dark:bg-yellow-900/20 border border-yellow-200 dark:border-yellow-800 rounded-lg">
          <div className="flex items-start gap-3">
            <span className="text-xl">⚠️</span>
            <div className="flex-1">
              <p className="text-sm font-medium text-yellow-800 dark:text-yellow-200 mb-2">
                Documents need to be re-embedded
              </p>
              <p className="text-xs text-yellow-700 dark:text-yellow-300 mb-3">
                You changed the embedding model. Re-embed all documents to use the new model.
              </p>
              <button
                onClick={handleReembed}
                className="px-4 py-2 bg-yellow-600 hover:bg-yellow-700 text-white text-sm font-medium rounded-lg transition-colors"
              >
                Re-embed All Documents
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Re-embedding Progress */}
      {reembedding && (
        <div className="mb-4 p-4 bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-lg">
          <div className="space-y-3">
            <div className="flex items-center justify-between text-sm">
              <span className="font-medium text-gray-900 dark:text-white">
                Re-embedding documents...
              </span>
              <span className="text-gray-600 dark:text-gray-400">
                {reembedProgress.current} / {reembedProgress.total}
              </span>
            </div>
            <div className="w-full bg-gray-200 dark:bg-gray-700 rounded-full h-2">
              <div
                className="bg-blue-600 h-2 rounded-full transition-all duration-300"
                style={{ width: `${progressPercentage}%` }}
              />
            </div>
            <p className="text-xs text-gray-600 dark:text-gray-400">
              This may take a few minutes for large notebooks...
            </p>
          </div>
        </div>
      )}

      {/* Model Selection */}
      <div className="space-y-3">
        <h4 className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-3">
          Select Model
        </h4>
        {EMBEDDING_MODELS.map((model) => (
          <button
            key={model.id}
            onClick={() => handleModelChange(model.id)}
            disabled={changing || reembedding || currentModel === model.id}
            className={`w-full p-4 rounded-lg border-2 transition-all text-left ${
              currentModel === model.id
                ? 'border-blue-600 bg-blue-50 dark:bg-blue-900/20'
                : 'border-gray-300 dark:border-gray-600 hover:border-blue-400 bg-white dark:bg-gray-800'
            } ${(changing || reembedding) ? 'opacity-50 cursor-not-allowed' : ''}`}
          >
            <div className="flex items-start gap-3">
              <span className="text-2xl">{model.icon}</span>
              <div className="flex-1">
                <div className="flex items-center gap-2 mb-1">
                  <span className="font-semibold text-gray-900 dark:text-white">
                    {model.name}
                  </span>
                  {model.isDefault && (
                    <span className="px-2 py-0.5 text-xs font-medium bg-blue-100 dark:bg-blue-900 text-blue-800 dark:text-blue-200 rounded">
                      Default
                    </span>
                  )}
                </div>
                <p className="text-sm text-gray-600 dark:text-gray-400 mb-2">
                  {model.description}
                </p>
                <div className="flex gap-3 text-xs text-gray-500 dark:text-gray-500">
                  <span>{model.dimensions} dimensions</span>
                  <span>•</span>
                  <span>{model.size}</span>
                </div>
              </div>
              {currentModel === model.id && (
                <svg className="w-6 h-6 text-blue-600 flex-shrink-0" fill="currentColor" viewBox="0 0 20 20">
                  <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
                </svg>
              )}
            </div>
          </button>
        ))}
      </div>

      {/* Info Section */}
      <div className="mt-6 p-4 bg-gray-50 dark:bg-gray-800 rounded-lg">
        <h4 className="text-sm font-medium text-gray-900 dark:text-white mb-2">
          ℹ️ About Embedding Models
        </h4>
        <ul className="text-xs text-gray-600 dark:text-gray-400 space-y-1">
          <li>• Embeddings convert text into numerical vectors for semantic search</li>
          <li>• Different models offer different speed/quality tradeoffs</li>
          <li>• Changing models requires re-embedding all documents</li>
          <li>• Higher dimensions = better accuracy but slower search</li>
        </ul>
      </div>
    </div>
  );
};
