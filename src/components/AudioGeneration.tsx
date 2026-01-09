import React from 'react';

export const AudioGeneration: React.FC = () => {
  return (
    <div className="p-4 border border-gray-200 dark:border-gray-700 rounded bg-gray-50 dark:bg-gray-800">
      <h3 className="font-bold mb-2 text-gray-900 dark:text-white">Generate Audio Overview</h3>
      <p className="text-sm text-gray-600 dark:text-gray-400 mb-3">
        Create podcast-style audio summaries of your documents.
      </p>
      <button
        className="px-4 py-2 bg-gray-300 dark:bg-gray-700 text-gray-500 dark:text-gray-400 rounded cursor-not-allowed"
        disabled
      >
        Coming Soon
      </button>
      <p className="text-xs text-gray-500 dark:text-gray-400 mt-2">
        This feature will support both local TTS and cloud options like ElevenLabs.
      </p>
    </div>
  );
};
