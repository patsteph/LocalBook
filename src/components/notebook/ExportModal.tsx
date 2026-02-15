import React from 'react';
import { Button } from '../shared/Button';
import { Modal } from '../shared/Modal';

interface ExportModalProps {
  isOpen: boolean;
  onClose: () => void;
  onExport: (format: 'markdown' | 'html' | 'pdf') => void;
  exporting: boolean;
}

export const ExportModal: React.FC<ExportModalProps> = ({
  isOpen,
  onClose,
  onExport,
  exporting,
}) => {
  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="Export Notebook"
    >
      <div className="p-6">
        <p className="text-gray-700 dark:text-gray-300 mb-6">
          Choose the format to export your notebook:
        </p>
        <div className="space-y-3">
          <button
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              onExport('markdown');
            }}
            disabled={exporting}
            className="w-full flex items-center justify-between p-4 border border-gray-300 dark:border-gray-600 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <div className="flex items-center gap-3">
              <svg className="w-6 h-6 text-gray-600 dark:text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
              </svg>
              <div className="text-left">
                <div className="font-medium text-gray-900 dark:text-white">Markdown (.md)</div>
                <div className="text-sm text-gray-500 dark:text-gray-400">Plain text with formatting</div>
              </div>
            </div>
            <svg className="w-5 h-5 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
            </svg>
          </button>

          <button
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              onExport('pdf');
            }}
            disabled={exporting}
            className="w-full flex items-center justify-between p-4 border border-gray-300 dark:border-gray-600 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <div className="flex items-center gap-3">
              <svg className="w-6 h-6 text-gray-600 dark:text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 17h2a2 2 0 002-2v-4a2 2 0 00-2-2H5a2 2 0 00-2 2v4a2 2 0 002 2h2m2 4h6a2 2 0 002-2v-4a2 2 0 00-2-2H9a2 2 0 00-2 2v4a2 2 0 002 2zm8-12V5a2 2 0 00-2-2H9a2 2 0 00-2 2v4h10z" />
              </svg>
              <div className="text-left">
                <div className="font-medium text-gray-900 dark:text-white">PDF (.pdf)</div>
                <div className="text-sm text-gray-500 dark:text-gray-400">Opens browser print dialog</div>
              </div>
            </div>
            <svg className="w-5 h-5 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
            </svg>
          </button>
        </div>

        {exporting && (
          <div className="mt-4 flex items-center justify-center gap-2 text-gray-600 dark:text-gray-400">
            <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-blue-600"></div>
            <span>Exporting...</span>
          </div>
        )}

        <div className="mt-6 flex justify-end">
          <Button
            variant="secondary"
            onClick={onClose}
            disabled={exporting}
          >
            Cancel
          </Button>
        </div>
      </div>
    </Modal>
  );
};
