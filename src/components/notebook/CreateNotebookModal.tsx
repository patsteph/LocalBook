import React, { useState, useRef } from 'react';
import { NOTEBOOK_COLORS } from '../../services/notebooks';
import { Button } from '../shared/Button';
import { Modal } from '../shared/Modal';

interface CreateNotebookModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSubmit: (title: string, color: string, files: File[]) => Promise<void>;
  creating: boolean;
}

export const CreateNotebookModal: React.FC<CreateNotebookModalProps> = ({
  isOpen,
  onClose,
  onSubmit,
  creating,
}) => {
  const [title, setTitle] = useState('');
  const [color, setColor] = useState(NOTEBOOK_COLORS[0]);
  const [droppedFiles, setDroppedFiles] = useState<File[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  const dropRef = useRef<HTMLDivElement>(null);

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(true);
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
    const files = Array.from(e.dataTransfer.files);
    if (files.length > 0) {
      setDroppedFiles(prev => [...prev, ...files]);
    }
  };

  const removeDroppedFile = (index: number) => {
    setDroppedFiles(prev => prev.filter((_, i) => i !== index));
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!title.trim()) return;
    await onSubmit(title.trim(), color, droppedFiles);
    setTitle('');
    setColor(NOTEBOOK_COLORS[0]);
    setDroppedFiles([]);
  };

  const handleClose = () => {
    setDroppedFiles([]);
    onClose();
  };

  return (
    <Modal isOpen={isOpen} onClose={handleClose} title="Create New Notebook">
      <div className="p-4">
        <form onSubmit={handleSubmit}>
          <div className="mb-3">
            <label htmlFor="notebook-title" className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
              Notebook Name
            </label>
            <input
              id="notebook-title"
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="My Research Project"
              className="w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-gray-700 text-gray-900 dark:text-white placeholder-gray-400 dark:placeholder-gray-500"
              autoFocus
              disabled={creating}
            />
          </div>
          <div className="mb-3">
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
              Color
            </label>
            <div className="flex gap-2 flex-wrap">
              {NOTEBOOK_COLORS.map((c) => (
                <button
                  key={c}
                  type="button"
                  onClick={() => setColor(c)}
                  className={`w-8 h-8 rounded-full hover:scale-110 transition-transform ${
                    color === c ? 'ring-2 ring-offset-2 ring-blue-500' : ''
                  }`}
                  style={{ backgroundColor: c }}
                />
              ))}
            </div>
          </div>
          {/* File drop zone */}
          <div className="mb-3">
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
              Starting Files <span className="text-gray-400 font-normal">(optional)</span>
            </label>
            <div
              ref={dropRef}
              onDragOver={handleDragOver}
              onDragLeave={handleDragLeave}
              onDrop={handleDrop}
              className={`border-2 border-dashed rounded-lg p-4 text-center transition-colors ${
                isDragging
                  ? 'border-blue-400 bg-blue-50 dark:bg-blue-900/20'
                  : 'border-gray-300 dark:border-gray-600 hover:border-gray-400'
              }`}
            >
              {droppedFiles.length === 0 ? (
                <p className="text-sm text-gray-500 dark:text-gray-400">
                  Drop files here to seed your notebook
                </p>
              ) : (
                <div className="space-y-1">
                  {droppedFiles.map((file, i) => (
                    <div key={i} className="flex items-center justify-between text-sm">
                      <span className="text-gray-700 dark:text-gray-300 truncate">
                        📄 {file.name}
                      </span>
                      <button
                        type="button"
                        onClick={() => removeDroppedFile(i)}
                        className="text-gray-400 hover:text-red-500 ml-2"
                      >
                        ✕
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
            {droppedFiles.length > 0 && (
              <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                {droppedFiles.length} file{droppedFiles.length !== 1 ? 's' : ''} — will be uploaded and analyzed to suggest Collector config
              </p>
            )}
          </div>

          <div className="flex justify-end gap-2">
            <Button
              type="button"
              variant="secondary"
              onClick={handleClose}
              disabled={creating}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={creating || !title.trim()}
            >
              {creating ? 'Creating...' : 'Create'}
            </Button>
          </div>
        </form>
      </div>
    </Modal>
  );
};
