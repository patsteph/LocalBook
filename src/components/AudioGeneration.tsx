import React from 'react';

export const AudioGeneration: React.FC = () => {
  return (
    <div className="p-4 border rounded bg-gray-50">
      <h3 className="font-bold mb-2">Generate Audio Overview</h3>
      <p className="text-sm text-gray-600 mb-3">
        Create podcast-style audio summaries of your documents.
      </p>
      <button
        className="px-4 py-2 bg-gray-300 text-gray-500 rounded cursor-not-allowed"
        disabled
      >
        Coming Soon
      </button>
      <p className="text-xs text-gray-500 mt-2">
        This feature will support both local TTS and cloud options like ElevenLabs.
      </p>
    </div>
  );
};
