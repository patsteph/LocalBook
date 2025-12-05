import React from 'react';

export const AudioUpload: React.FC = () => {
  return (
    <div className="p-4 border rounded bg-gray-50">
      <h3 className="font-bold mb-2">Audio Transcription</h3>
      <p className="text-sm text-gray-600 mb-3">
        Upload audio files to transcribe and add to your notebook.
      </p>
      <button
        className="px-4 py-2 bg-gray-300 text-gray-500 rounded cursor-not-allowed"
        disabled
      >
        Coming Soon
      </button>
      <p className="text-xs text-gray-500 mt-2">
        This feature will use Whisper for local audio transcription.
      </p>
    </div>
  );
};
