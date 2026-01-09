import React, { useState, useEffect } from 'react';
import { API_BASE_URL } from '../services/api';

interface EmbeddingSelectorProps {
  notebookId: string | null;
  onModelChange?: () => void;
}

interface EmbeddingInfo {
  model: string;
  dimensions: number;
  reranker: string;
  reranker_type: string;
}

export const EmbeddingSelector: React.FC<EmbeddingSelectorProps> = () => {
  const [embeddingInfo, setEmbeddingInfo] = useState<EmbeddingInfo | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadEmbeddingInfo();
  }, []);

  const loadEmbeddingInfo = async () => {
    try {
      setLoading(true);
      const response = await fetch(`${API_BASE_URL}/embeddings/info`);
      if (response.ok) {
        const info = await response.json();
        setEmbeddingInfo(info);
      }
    } catch (err) {
      console.error('Failed to load embedding info:', err);
    } finally {
      setLoading(false);
    }
  };

  if (loading) {
    return (
      <div className="p-6">
        <div className="flex items-center justify-center">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600"></div>
        </div>
      </div>
    );
  }

  return (
    <div className="p-6">
      <div className="mb-6">
        <h3 className="text-lg font-semibold text-gray-900 dark:text-white mb-2">
          Retrieval Configuration
        </h3>
        <p className="text-sm text-gray-600 dark:text-gray-400">
          Current embedding and reranking models for semantic search
        </p>
      </div>

      {/* Embedding Model */}
      <div className="mb-4 p-4 bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-lg">
        <div className="flex items-center gap-3">
          <span className="text-2xl">❄️</span>
          <div className="flex-1">
            <div className="font-medium text-gray-900 dark:text-white">
              Embedding Model
            </div>
            <div className="text-sm text-gray-600 dark:text-gray-400">
              {embeddingInfo?.model || 'snowflake-arctic-embed2'}
            </div>
            <div className="text-xs text-gray-500 dark:text-gray-500 mt-1">
              {embeddingInfo?.dimensions || 1024} dimensions • Frontier quality
            </div>
          </div>
        </div>
      </div>

      {/* Reranker */}
      <div className="mb-4 p-4 bg-purple-50 dark:bg-purple-900/20 border border-purple-200 dark:border-purple-800 rounded-lg">
        <div className="flex items-center gap-3">
          <span className="text-2xl">🎯</span>
          <div className="flex-1">
            <div className="font-medium text-gray-900 dark:text-white">
              Reranker
            </div>
            <div className="text-sm text-gray-600 dark:text-gray-400">
              {embeddingInfo?.reranker || 'ms-marco-MiniLM-L-12-v2'}
            </div>
            <div className="text-xs text-gray-500 dark:text-gray-500 mt-1">
              {embeddingInfo?.reranker_type || 'FlashRank'} • Ultra-fast CPU inference
            </div>
          </div>
        </div>
      </div>

      {/* Info Section */}
      <div className="mt-6 p-4 bg-gray-50 dark:bg-gray-800 rounded-lg">
        <h4 className="text-sm font-medium text-gray-900 dark:text-white mb-2">
          ℹ️ Two-Stage Retrieval
        </h4>
        <ul className="text-xs text-gray-600 dark:text-gray-400 space-y-1">
          <li>• <strong>Stage 1:</strong> Vector search finds candidate chunks using embeddings</li>
          <li>• <strong>Stage 2:</strong> Reranker scores and reorders for relevance</li>
          <li>• This combination provides both speed and accuracy</li>
        </ul>
      </div>
    </div>
  );
};
