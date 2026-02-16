import React, { useState, useEffect } from 'react';
import { notebookService, NOTEBOOK_COLORS } from '../services/notebooks';
import { exportService } from '../services/export';
import { sourceService } from '../services/sources';
import { API_BASE_URL } from '../services/api';
import { curatorService } from '../services/curatorApi';
import { Notebook } from '../types';
import { Button } from './shared/Button';
import { LoadingSpinner } from './shared/LoadingSpinner';
import { ErrorMessage } from './shared/ErrorMessage';
import { Modal } from './shared/Modal';
import { CollectorSetupWizard } from './collector';
import { CreateNotebookModal } from './notebook/CreateNotebookModal';
import { ExportModal } from './notebook/ExportModal';

interface NotebookManagerProps {
  onNotebookSelect: (notebookId: string) => void;
  selectedNotebookId: string | null;
  refreshTrigger?: number;
  onCollectorConfigured?: () => void;
}

export const NotebookManager: React.FC<NotebookManagerProps> = ({
  onNotebookSelect,
  selectedNotebookId,
  refreshTrigger,
  onCollectorConfigured,
}) => {
  const [notebooks, setNotebooks] = useState<Notebook[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [showDeleteModal, setShowDeleteModal] = useState(false);
  const [notebookToDelete, setNotebookToDelete] = useState<string | null>(null);
  const [showExportModal, setShowExportModal] = useState(false);
  const [notebookToExport, setNotebookToExport] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [showColorPicker, setShowColorPicker] = useState<string | null>(null);
  const [showOtherNotebooks, setShowOtherNotebooks] = useState(false);
  const [primaryNotebookId, setPrimaryNotebookId] = useState<string | null>(null);
  const [showCollectorSetup, setShowCollectorSetup] = useState(false);
  const [newlyCreatedNotebook, setNewlyCreatedNotebook] = useState<Notebook | null>(null);
  const [inferredConfig, setInferredConfig] = useState<any>(null);

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
        const prefsRes = await fetch(`${API_BASE_URL}/settings/primary-notebook`);
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
      const res = await fetch(`${API_BASE_URL}/settings/primary-notebook/${notebookId}`, {
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

  const handleCreateNotebook = async (title: string, color: string, files: File[]) => {
    setCreating(true);
    setError(null);
    try {
      const newNotebook = await notebookService.create(title, undefined, color);
      setNotebooks([...notebooks, newNotebook]);
      onNotebookSelect(newNotebook.id);
      setShowCreateModal(false);

      // If files were dropped, upload them and infer config
      if (files.length > 0) {
        const filenames: string[] = [];
        let sampleContent = '';
        for (const file of files) {
          try {
            await sourceService.upload(newNotebook.id, file);
            filenames.push(file.name);
            if (!sampleContent && file.type.startsWith('text/')) {
              sampleContent = await file.text().then((t: string) => t.slice(0, 2000));
            }
          } catch (err) {
            console.error(`Failed to upload ${file.name}:`, err);
          }
        }

        // Ask Curator to infer config from uploaded files
        if (filenames.length > 0) {
          try {
            const suggested = await curatorService.inferConfig(files);
            setInferredConfig(suggested);
          } catch (err) {
            console.log('[NotebookManager] Config inference failed (non-fatal):', err);
          }
        }
      }

      // Trigger collector setup wizard
      setNewlyCreatedNotebook(newNotebook);
      setShowCollectorSetup(true);
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

      <div className="flex items-center justify-end mb-3">
        <button
          onClick={() => setShowCreateModal(true)}
          disabled={creating}
          className="flex items-center gap-1 px-2 py-1 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 rounded transition-colors disabled:opacity-50"
          title="New Notebook"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
          </svg>
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" />
          </svg>
        </button>
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
                            setNewlyCreatedNotebook(selectedNotebook);
                            setShowCollectorSetup(true);
                          }}
                          className="text-gray-500 hover:text-green-600 text-xs px-2 py-1 rounded hover:bg-green-100 dark:hover:bg-green-900/30 transition-colors"
                          title="Configure Collector"
                        >
                          ⚙
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
                            className="group p-2 rounded border border-gray-200 dark:border-gray-600 hover:border-gray-400 dark:hover:border-gray-500 bg-white dark:bg-gray-700 cursor-pointer transition"
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
                              <div className="flex gap-1 items-center opacity-0 group-hover:opacity-100 transition-opacity" onClick={(e) => e.stopPropagation()}>
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
                                    setNewlyCreatedNotebook(notebook);
                                    setShowCollectorSetup(true);
                                  }}
                                  className="text-gray-400 hover:text-green-500 text-xs px-1 transition-colors"
                                  title="Configure Collector"
                                >
                                  ⚙
                                </button>
                                <button
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    handleExportClick(notebook.id);
                                  }}
                                  className="text-gray-400 hover:text-blue-500 text-xs px-1 transition-colors"
                                  title="Export"
                                >
                                  ↓
                                </button>
                                <button
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    handleDeleteClick(notebook.id);
                                  }}
                                  className="text-gray-400 hover:text-red-500 text-xs px-1 transition-colors"
                                  title="Delete"
                                >
                                  ✕
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
      <CreateNotebookModal
        isOpen={showCreateModal}
        onClose={() => setShowCreateModal(false)}
        onSubmit={handleCreateNotebook}
        creating={creating}
      />

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
      <ExportModal
        isOpen={showExportModal}
        onClose={() => setShowExportModal(false)}
        onExport={handleExport}
        exporting={exporting}
      />

      {/* Collector Setup Wizard */}
      {newlyCreatedNotebook && (
        <CollectorSetupWizard
          notebookId={newlyCreatedNotebook.id}
          notebookName={newlyCreatedNotebook.title}
          isOpen={showCollectorSetup}
          onClose={() => {
            setShowCollectorSetup(false);
            setNewlyCreatedNotebook(null);
            setInferredConfig(null);
          }}
          onComplete={() => {
            setShowCollectorSetup(false);
            setNewlyCreatedNotebook(null);
            setInferredConfig(null);
            onCollectorConfigured?.();
          }}
          initialConfig={inferredConfig || undefined}
        />
      )}
    </div>
  );
};
