import React, { useState, useEffect } from 'react';
import { notebookService, NOTEBOOK_COLORS } from '../services/notebooks';
import { exportService } from '../services/export';
import { sourceService } from '../services/sources';
import { Notebook } from '../types';
import { Button } from './shared/Button';
import { LoadingSpinner } from './shared/LoadingSpinner';
import { ErrorMessage } from './shared/ErrorMessage';
import { Modal } from './shared/Modal';

interface NotebookManagerProps {
  onNotebookSelect: (notebookId: string) => void;
  selectedNotebookId: string | null;
  refreshTrigger?: number;
}

export const NotebookManager: React.FC<NotebookManagerProps> = ({
  onNotebookSelect,
  selectedNotebookId,
  refreshTrigger,
}) => {
  const [notebooks, setNotebooks] = useState<Notebook[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [newNotebookTitle, setNewNotebookTitle] = useState('');
  const [showDeleteModal, setShowDeleteModal] = useState(false);
  const [notebookToDelete, setNotebookToDelete] = useState<string | null>(null);
  const [showExportModal, setShowExportModal] = useState(false);
  const [notebookToExport, setNotebookToExport] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [newNotebookColor, setNewNotebookColor] = useState(NOTEBOOK_COLORS[0]);
  const [showColorPicker, setShowColorPicker] = useState<string | null>(null);
  const [showOtherNotebooks, setShowOtherNotebooks] = useState(false);
  const [primaryNotebookId, setPrimaryNotebookId] = useState<string | null>(null);

  useEffect(() => {
    console.log('NotebookManager mounted, loading notebooks...');
    loadNotebooks();
  }, []);

  // Refresh when refreshTrigger changes
  useEffect(() => {
    if (refreshTrigger !== undefined && refreshTrigger > 0) {
      console.log('NotebookManager refreshTrigger changed, reloading notebooks...', refreshTrigger);
      loadNotebooks();
    }
  }, [refreshTrigger]);

  const loadNotebooks = async () => {
    try {
      console.log('Loading notebooks from API...');
      setError(null);
      const data = await notebookService.list();
      console.log('Notebooks loaded:', data);
      setNotebooks(data);
      if (data.length > 0 && !selectedNotebookId) {
        onNotebookSelect(data[0].id);
      }
      
      // Load primary notebook preference
      try {
        const prefsRes = await fetch('http://localhost:8000/settings/primary-notebook');
        if (prefsRes.ok) {
          const prefsData = await prefsRes.json();
          setPrimaryNotebookId(prefsData.primary_notebook_id);
        }
      } catch (e) {
        console.log('Failed to load primary notebook preference');
      }
    } catch (err) {
      console.error('Failed to load notebooks:', err);
      setError('Failed to load notebooks. Please check if the backend is running.');
    } finally {
      setLoading(false);
    }
  };

  const handleSetPrimary = async (notebookId: string) => {
    try {
      const res = await fetch(`http://localhost:8000/settings/primary-notebook/${notebookId}`, {
        method: 'POST'
      });
      if (res.ok) {
        setPrimaryNotebookId(notebookId);
      }
    } catch (err) {
      console.error('Failed to set primary notebook:', err);
      setError('Failed to set primary notebook');
    }
  };

  const handleCreateClick = () => {
    setShowCreateModal(true);
    setNewNotebookTitle('');
    setNewNotebookColor(NOTEBOOK_COLORS[0]);
  };

  const handleColorChange = async (notebookId: string, color: string) => {
    try {
      await notebookService.updateColor(notebookId, color);
      setNotebooks(notebooks.map(nb => 
        nb.id === notebookId ? { ...nb, color } : nb
      ));
      setShowColorPicker(null);
    } catch (err) {
      console.error('Failed to update color:', err);
      setError('Failed to update notebook color');
    }
  };

  const handleCreateSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!newNotebookTitle.trim()) {
      return;
    }

    setCreating(true);
    setError(null);
    try {
      const newNotebook = await notebookService.create(newNotebookTitle.trim(), undefined, newNotebookColor);
      setNotebooks([...notebooks, newNotebook]);
      onNotebookSelect(newNotebook.id);
      setShowCreateModal(false);
      setNewNotebookTitle('');
    } catch (err) {
      console.error('Failed to create notebook:', err);
      setError('Failed to create notebook');
    } finally {
      setCreating(false);
    }
  };

  const handleDeleteClick = (id: string) => {
    setNotebookToDelete(id);
    setShowDeleteModal(true);
  };

  const handleDeleteConfirm = async () => {
    if (!notebookToDelete) return;

    try {
      setError(null);
      await notebookService.delete(notebookToDelete);
      const updatedNotebooks = notebooks.filter(nb => nb.id !== notebookToDelete);
      setNotebooks(updatedNotebooks);
      if (selectedNotebookId === notebookToDelete) {
        onNotebookSelect(updatedNotebooks[0]?.id || '');
      }
      setShowDeleteModal(false);
      setNotebookToDelete(null);
    } catch (err) {
      console.error('Failed to delete notebook:', err);
      setError('Failed to delete notebook');
    }
  };

  const handleExportClick = (id: string) => {
    console.log('Export clicked for notebook:', id);
    setNotebookToExport(id);
    setShowExportModal(true);
    console.log('Export modal state set to true');
  };

  const handleExport = async (format: 'markdown' | 'html' | 'pdf') => {
    console.log('handleExport called with format:', format);
    console.log('notebookToExport:', notebookToExport);

    if (!notebookToExport) {
      console.log('No notebook to export, returning');
      return;
    }

    console.log('Starting export...');
    setExporting(true);
    setError(null);

    try {
      console.log('About to call exportService.exportNotebook');
      if (format === 'pdf') {
        console.log('PDF format - generating PDF with jsPDF');

        // Get notebook info and sources
        const notebook = notebooks.find(nb => nb.id === notebookToExport);
        const notebookTitle = notebook?.title || 'Notebook';

        // Fetch sources for the notebook
        console.log('Fetching sources for notebook:', notebookToExport);
        const sources = await sourceService.list(notebookToExport);
        console.log('Sources fetched:', sources);

        // Generate PDF
        console.log('Generating PDF...');
        const blob = await exportService.generatePDF(notebookTitle, sources);
        console.log('PDF generated, blob size:', blob.size);

        const filename = `${notebookTitle.replace(/\s+/g, '_')}.pdf`;
        console.log('Saving PDF as:', filename);

        await exportService.downloadBlob(blob, filename);

        setShowExportModal(false);
        setNotebookToExport(null);
        console.log('PDF export complete');
      } else {
        console.log(`${format} format - downloading directly`);
        // For markdown and HTML, download directly
        const blob = await exportService.exportNotebook({
          notebookId: notebookToExport,
          format,
          includeSourcesContent: false,
        });
        console.log('Got blob:', blob);

        const notebook = notebooks.find(nb => nb.id === notebookToExport);
        const filename = `${notebook?.title.replace(/\s+/g, '_') || 'notebook'}.${format === 'markdown' ? 'md' : format}`;
        console.log('Downloading as:', filename);

        await exportService.downloadBlob(blob, filename);
        setShowExportModal(false);
        setNotebookToExport(null);
        console.log('Download complete');
      }
    } catch (err) {
      console.error('Export error caught:', err);
      console.error('Error details:', err);
      setError(err instanceof Error ? err.message : 'Failed to export notebook');
    } finally {
      console.log('Export finally block');
      setExporting(false);
    }
  };

  if (loading) {
    return (
      <div className="p-4">
        <LoadingSpinner />
      </div>
    );
  }

  return (
    <div className="p-4 bg-white dark:bg-gray-800 border-b dark:border-gray-700">
      {error && <ErrorMessage message={error} onDismiss={() => setError(null)} />}

      <div className="flex items-center justify-between mb-4">
        <h2 className="text-xl font-bold text-gray-900 dark:text-white">Notebooks</h2>
        <Button onClick={handleCreateClick} disabled={creating} size="sm">
          + New Notebook
        </Button>
      </div>

      {notebooks.length === 0 ? (
        <p className="text-gray-500 dark:text-gray-400">No notebooks yet. Create one to get started!</p>
      ) : (
        <div className="space-y-2">
          {/* Selected Notebook - Always visible and prominent */}
          {(() => {
            const selectedNotebook = notebooks.find(nb => nb.id === selectedNotebookId);
            const otherNotebooks = notebooks.filter(nb => nb.id !== selectedNotebookId);
            
            return (
              <>
                {selectedNotebook && (
                  <div
                    className="group p-3 rounded-lg border border-blue-500/30 bg-gradient-to-r from-blue-50 to-blue-100/50 dark:from-blue-900/30 dark:to-blue-800/20 dark:border-blue-500/40 hover:border-blue-500/50 transition-all"
                  >
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-3 flex-1">
                        {/* Color indicator with picker */}
                        <div className="relative">
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              setShowColorPicker(showColorPicker === selectedNotebook.id ? null : selectedNotebook.id);
                            }}
                            className="w-4 h-4 rounded-full ring-2 ring-white dark:ring-gray-700 shadow-sm hover:scale-110 transition-transform"
                            style={{ backgroundColor: selectedNotebook.color || '#3B82F6' }}
                            title="Change color"
                          />
                          {showColorPicker === selectedNotebook.id && (
                            <div 
                              className="absolute left-6 top-0 z-50 bg-white dark:bg-gray-800 rounded-lg shadow-xl p-4 border border-gray-200 dark:border-gray-600"
                              onClick={(e) => e.stopPropagation()}
                              style={{ minWidth: '200px' }}
                            >
                              <p className="text-xs text-gray-500 dark:text-gray-400 mb-3">Choose color</p>
                              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 32px)', gap: '12px' }}>
                                {NOTEBOOK_COLORS.map((color) => (
                                  <button
                                    key={color}
                                    onClick={() => handleColorChange(selectedNotebook.id, color)}
                                    style={{ 
                                      backgroundColor: color,
                                      width: '32px',
                                      height: '32px',
                                      borderRadius: '50%',
                                      border: selectedNotebook.color === color ? '3px solid #3B82F6' : 'none',
                                      cursor: 'pointer',
                                      transition: 'transform 0.1s',
                                    }}
                                    onMouseEnter={(e) => e.currentTarget.style.transform = 'scale(1.1)'}
                                    onMouseLeave={(e) => e.currentTarget.style.transform = 'scale(1)'}
                                  />
                                ))}
                              </div>
                            </div>
                          )}
                        </div>
                        <div>
                          <div className="flex items-center gap-2">
                            <h3 className="font-medium text-gray-900 dark:text-white">{selectedNotebook.title}</h3>
                          </div>
                          <p className="text-xs text-gray-500 dark:text-gray-400">
                            {selectedNotebook.source_count} sources
                          </p>
                        </div>
                      </div>
                      {/* Actions - visible on hover */}
                      <div className="flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            if (primaryNotebookId !== selectedNotebook.id) {
                              handleSetPrimary(selectedNotebook.id);
                            }
                          }}
                          className={`text-xs px-2 py-1 rounded transition-colors ${
                            primaryNotebookId === selectedNotebook.id
                              ? 'text-purple-500 cursor-default'
                              : 'text-purple-500 hover:text-purple-600 hover:bg-purple-100 dark:hover:bg-purple-900/30 cursor-pointer'
                          }`}
                          title={primaryNotebookId === selectedNotebook.id ? "Primary notebook" : "Set as primary"}
                        >
                          {primaryNotebookId === selectedNotebook.id ? '★' : '☆'}
                        </button>
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            handleExportClick(selectedNotebook.id);
                          }}
                          className="text-gray-500 hover:text-blue-600 text-xs px-2 py-1 rounded hover:bg-blue-100 dark:hover:bg-blue-900/30 transition-colors"
                          title="Export"
                        >
                          ↓
                        </button>
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            handleDeleteClick(selectedNotebook.id);
                          }}
                          className="text-gray-500 hover:text-red-600 text-xs px-2 py-1 rounded hover:bg-red-100 dark:hover:bg-red-900/30 transition-colors"
                          title="Delete"
                        >
                          ✕
                        </button>
                      </div>
                    </div>
                  </div>
                )}

                {/* Other Notebooks - Collapsible */}
                {otherNotebooks.length > 0 && (
                  <div className="mt-2">
                    <button
                      onClick={() => setShowOtherNotebooks(!showOtherNotebooks)}
                      className="w-full flex items-center justify-between px-3 py-2 text-sm text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700 rounded transition-colors"
                    >
                      <span className="flex items-center gap-2">
                        <svg 
                          className={`w-4 h-4 transition-transform ${showOtherNotebooks ? 'rotate-90' : ''}`} 
                          fill="none" 
                          stroke="currentColor" 
                          viewBox="0 0 24 24"
                        >
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                        </svg>
                        Other Notebooks
                      </span>
                      <span className="bg-gray-200 dark:bg-gray-600 text-gray-700 dark:text-gray-300 px-2 py-0.5 rounded-full text-xs">
                        {otherNotebooks.length}
                      </span>
                    </button>
                    
                    {showOtherNotebooks && (
                      <div className="mt-2 space-y-1 pl-2 border-l-2 border-gray-200 dark:border-gray-600">
                        {otherNotebooks.map((notebook) => (
                          <div
                            key={notebook.id}
                            className="p-2 rounded border border-gray-200 dark:border-gray-600 hover:border-gray-400 dark:hover:border-gray-500 bg-white dark:bg-gray-700 cursor-pointer transition"
                            onClick={() => {
                              onNotebookSelect(notebook.id);
                              setShowOtherNotebooks(false);
                            }}
                          >
                            <div className="flex items-center justify-between">
                              <div className="flex items-center gap-2 flex-1">
                                <div
                                  className="w-3 h-3 rounded-full"
                                  style={{ backgroundColor: notebook.color || '#3B82F6' }}
                                />
                                <div>
                                  <div className="flex items-center gap-1">
                                    <h3 className="font-medium text-sm text-gray-900 dark:text-white">{notebook.title}</h3>
                                  </div>
                                  <p className="text-xs text-gray-500 dark:text-gray-400">
                                    {notebook.source_count} sources
                                  </p>
                                </div>
                              </div>
                              <div className="flex gap-1 items-center" onClick={(e) => e.stopPropagation()}>
                                <button
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    if (primaryNotebookId !== notebook.id) {
                                      handleSetPrimary(notebook.id);
                                    }
                                  }}
                                  className={`text-xs px-1 ${
                                    primaryNotebookId === notebook.id
                                      ? 'text-purple-500 cursor-default'
                                      : 'text-purple-400 dark:text-purple-500 hover:text-purple-600 dark:hover:text-purple-400 cursor-pointer'
                                  }`}
                                  title={primaryNotebookId === notebook.id ? "Primary notebook" : "Set as primary"}
                                >
                                  {primaryNotebookId === notebook.id ? '★' : '☆'}
                                </button>
                                <button
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    handleExportClick(notebook.id);
                                  }}
                                  className="text-blue-600 dark:text-blue-400 hover:text-blue-700 text-xs px-1"
                                  title="Export"
                                >
                                  Export
                                </button>
                                <button
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    handleDeleteClick(notebook.id);
                                  }}
                                  className="text-red-600 dark:text-red-400 hover:text-red-700 text-xs px-1"
                                  title="Delete"
                                >
                                  Delete
                                </button>
                              </div>
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </>
            );
          })()}
        </div>
      )}

      {/* Create Notebook Modal */}
      <Modal
        isOpen={showCreateModal}
        onClose={() => setShowCreateModal(false)}
        title="Create New Notebook"
      >
        <div className="p-6">
          <form onSubmit={handleCreateSubmit}>
            <div className="mb-4">
              <label htmlFor="notebook-title" className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                Notebook Name
              </label>
              <input
                id="notebook-title"
                type="text"
                value={newNotebookTitle}
                onChange={(e) => setNewNotebookTitle(e.target.value)}
                placeholder="My Research Project"
                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-gray-700 text-gray-900 dark:text-white placeholder-gray-400 dark:placeholder-gray-500"
                autoFocus
                disabled={creating}
              />
            </div>
            <div className="mb-4">
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                Color
              </label>
              <div className="flex gap-2 flex-wrap">
                {NOTEBOOK_COLORS.map((color) => (
                  <button
                    key={color}
                    type="button"
                    onClick={() => setNewNotebookColor(color)}
                    className={`w-8 h-8 rounded-full hover:scale-110 transition-transform ${
                      newNotebookColor === color ? 'ring-2 ring-offset-2 ring-blue-500' : ''
                    }`}
                    style={{ backgroundColor: color }}
                  />
                ))}
              </div>
            </div>
            <div className="flex justify-end gap-2">
              <Button
                type="button"
                variant="secondary"
                onClick={() => setShowCreateModal(false)}
                disabled={creating}
              >
                Cancel
              </Button>
              <Button
                type="submit"
                disabled={creating || !newNotebookTitle.trim()}
              >
                {creating ? 'Creating...' : 'Create'}
              </Button>
            </div>
          </form>
        </div>
      </Modal>

      {/* Delete Confirmation Modal */}
      <Modal
        isOpen={showDeleteModal}
        onClose={() => setShowDeleteModal(false)}
        title="Delete Notebook"
      >
        <div className="p-6">
          <p className="text-gray-700 dark:text-gray-300 mb-6">
            Are you sure you want to delete this notebook? This action cannot be undone.
          </p>
          <div className="flex justify-end gap-2">
            <Button
              variant="secondary"
              onClick={() => setShowDeleteModal(false)}
            >
              Cancel
            </Button>
            <Button
              variant="danger"
              onClick={handleDeleteConfirm}
            >
              Delete
            </Button>
          </div>
        </div>
      </Modal>

      {/* Export Modal */}
      <Modal
        isOpen={showExportModal}
        onClose={() => {
          console.log('Export modal close clicked');
          setShowExportModal(false);
        }}
        title="Export Notebook"
      >
        <div className="p-6">
          <p className="text-gray-700 dark:text-gray-300 mb-6">
            Choose the format to export your notebook:
          </p>
          <div className="space-y-3">
            <button
              onClick={(e) => {
                console.log('Markdown button clicked');
                e.preventDefault();
                e.stopPropagation();
                handleExport('markdown');
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
                console.log('PDF button clicked');
                e.preventDefault();
                e.stopPropagation();
                handleExport('pdf');
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
              onClick={() => setShowExportModal(false)}
              disabled={exporting}
            >
              Cancel
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  );
};
