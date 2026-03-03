import React, { useState, useEffect, useRef, useCallback } from 'react';
import { notebookService, NOTEBOOK_COLORS } from '../services/notebooks';
import { exportService } from '../services/export';
import { sourceService } from '../services/sources';
import { API_BASE_URL } from '../services/api';
import { curatorService } from '../services/curatorApi';
import { Notebook, NotebookSection } from '../types';
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

interface ContextMenuState {
  x: number;
  y: number;
  notebookId: string;
}

export const NotebookManager: React.FC<NotebookManagerProps> = ({
  onNotebookSelect,
  selectedNotebookId,
  refreshTrigger,
  onCollectorConfigured,
}) => {
  const [notebooks, setNotebooks] = useState<Notebook[]>([]);
  const [sections, setSections] = useState<NotebookSection[]>([]);
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
  const [primaryNotebookId, setPrimaryNotebookId] = useState<string | null>(null);
  const [showCollectorSetup, setShowCollectorSetup] = useState(false);
  const [newlyCreatedNotebook, setNewlyCreatedNotebook] = useState<Notebook | null>(null);
  const [inferredConfig, setInferredConfig] = useState<any>(null);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [contextMenu, setContextMenu] = useState<ContextMenuState | null>(null);
  const [editingSectionId, setEditingSectionId] = useState<string | null>(null);
  const [editingSectionName, setEditingSectionName] = useState('');
  const [showAddSection, setShowAddSection] = useState(false);
  const [newSectionName, setNewSectionName] = useState('');
  const renameInputRef = useRef<HTMLInputElement>(null);
  const contextMenuRef = useRef<HTMLDivElement>(null);
  const sectionInputRef = useRef<HTMLInputElement>(null);
  const newSectionInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    loadNotebooks();
  }, []);

  useEffect(() => {
    if (refreshTrigger !== undefined && refreshTrigger > 0) {
      loadNotebooks();
    }
  }, [refreshTrigger]);

  useEffect(() => {
    const handleOpenExport = () => {
      if (selectedNotebookId) {
        setNotebookToExport(selectedNotebookId);
        setShowExportModal(true);
      }
    };
    window.addEventListener('openExportModal', handleOpenExport);
    return () => window.removeEventListener('openExportModal', handleOpenExport);
  }, [selectedNotebookId]);

  useEffect(() => {
    if (renamingId && renameInputRef.current) {
      renameInputRef.current.focus();
      renameInputRef.current.select();
    }
  }, [renamingId]);

  useEffect(() => {
    if (editingSectionId && sectionInputRef.current) {
      sectionInputRef.current.focus();
      sectionInputRef.current.select();
    }
  }, [editingSectionId]);

  useEffect(() => {
    if (showAddSection && newSectionInputRef.current) {
      newSectionInputRef.current.focus();
    }
  }, [showAddSection]);

  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (contextMenuRef.current && !contextMenuRef.current.contains(e.target as Node)) {
        setContextMenu(null);
      }
    };
    if (contextMenu) document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, [contextMenu]);

  const loadNotebooks = async () => {
    try {
      setError(null);
      const [data, sectionData] = await Promise.all([
        notebookService.list(),
        notebookService.listSections().catch(() => [] as NotebookSection[]),
      ]);
      setNotebooks(data);
      setSections(sectionData);
      if (data.length > 0 && !selectedNotebookId) {
        onNotebookSelect(data[0].id);
      }
      try {
        const prefsRes = await fetch(`${API_BASE_URL}/settings/primary-notebook`);
        if (prefsRes.ok) {
          const prefsData = await prefsRes.json();
          setPrimaryNotebookId(prefsData.primary_notebook_id);
        }
      } catch (e) {
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

  const handleRenameStart = (notebook: Notebook) => {
    setRenamingId(notebook.id);
    setRenameValue(notebook.title);
    setContextMenu(null);
  };

  const handleRenameSubmit = async () => {
    if (!renamingId || !renameValue.trim()) {
      setRenamingId(null);
      return;
    }
    try {
      const updated = await notebookService.rename(renamingId, renameValue.trim());
      setNotebooks(notebooks.map(nb => nb.id === renamingId ? { ...nb, title: updated.title } : nb));
    } catch (err) {
      console.error('Failed to rename notebook:', err);
      setError('Failed to rename notebook');
    } finally {
      setRenamingId(null);
    }
  };

  const handleMoveToSection = async (notebookId: string, sectionId: string | null) => {
    try {
      await notebookService.move(notebookId, sectionId);
      setNotebooks(notebooks.map(nb => nb.id === notebookId ? { ...nb, section_id: sectionId } : nb));
      setContextMenu(null);
    } catch (err) {
      console.error('Failed to move notebook:', err);
      setError('Failed to move notebook');
    }
  };

  const handleCreateSection = async () => {
    if (!newSectionName.trim()) {
      setShowAddSection(false);
      return;
    }
    try {
      const section = await notebookService.createSection(newSectionName.trim());
      setSections([...sections, section]);
      setNewSectionName('');
      setShowAddSection(false);
    } catch (err) {
      console.error('Failed to create section:', err);
      setError('Failed to create section');
    }
  };

  const handleRenameSection = async (sectionId: string) => {
    if (!editingSectionName.trim()) {
      setEditingSectionId(null);
      return;
    }
    try {
      const updated = await notebookService.updateSection(sectionId, { name: editingSectionName.trim() });
      setSections(sections.map(s => s.id === sectionId ? updated : s));
    } catch (err) {
      console.error('Failed to rename section:', err);
      setError('Failed to rename section');
    } finally {
      setEditingSectionId(null);
    }
  };

  const handleToggleSection = async (sectionId: string) => {
    const section = sections.find(s => s.id === sectionId);
    if (!section) return;
    const newCollapsed = !section.collapsed;
    setSections(sections.map(s => s.id === sectionId ? { ...s, collapsed: newCollapsed } : s));
    try {
      await notebookService.updateSection(sectionId, { collapsed: newCollapsed });
    } catch (err) {
      setSections(sections.map(s => s.id === sectionId ? { ...s, collapsed: !newCollapsed } : s));
    }
  };

  const handleDeleteSection = async (sectionId: string) => {
    try {
      await notebookService.deleteSection(sectionId);
      setSections(sections.filter(s => s.id !== sectionId));
      setNotebooks(notebooks.map(nb => nb.section_id === sectionId ? { ...nb, section_id: null } : nb));
    } catch (err) {
      console.error('Failed to delete section:', err);
      setError('Failed to delete section');
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

        if (filenames.length > 0) {
          try {
            const suggested = await curatorService.inferConfig(files);
            setInferredConfig(suggested);
          } catch (err) {
          }
        }
      }

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
    setContextMenu(null);
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
    setNotebookToExport(id);
    setShowExportModal(true);
    setContextMenu(null);
  };

  const handleExport = async (format: 'markdown' | 'html' | 'pdf' | 'pptx', pptxTheme?: 'light' | 'dark' | 'corporate' | 'academic') => {
    if (!notebookToExport) return;
    setExporting(true);
    setError(null);
    try {
      if (format === 'pdf') {
        const notebook = notebooks.find(nb => nb.id === notebookToExport);
        const notebookTitle = notebook?.title || 'Notebook';
        const sources = await sourceService.list(notebookToExport);
        const blob = await exportService.generatePDF(notebookTitle, sources);
        const filename = `${notebookTitle.replace(/\s+/g, '_')}.pdf`;
        await exportService.downloadBlob(blob, filename);
        setShowExportModal(false);
        setNotebookToExport(null);
      } else {
        const blob = await exportService.exportNotebook({
          notebookId: notebookToExport,
          format,
          includeSourcesContent: false,
          ...(format === 'pptx' && pptxTheme ? { pptxTheme } : {}),
        });
        const notebook = notebooks.find(nb => nb.id === notebookToExport);
        const ext = format === 'markdown' ? 'md' : format;
        const filename = `${notebook?.title.replace(/\s+/g, '_') || 'notebook'}.${ext}`;
        await exportService.downloadBlob(blob, filename);
        setShowExportModal(false);
        setNotebookToExport(null);
      }
    } catch (err) {
      console.error('Export error caught:', err);
      setError(err instanceof Error ? err.message : 'Failed to export notebook');
    } finally {
      setExporting(false);
    }
  };

  const handleContextMenu = useCallback((e: React.MouseEvent, notebookId: string) => {
    e.preventDefault();
    e.stopPropagation();
    setContextMenu({ x: e.clientX, y: e.clientY, notebookId });
  }, []);

  const renderNotebookTitle = (notebook: Notebook) => {
    if (renamingId === notebook.id) {
      return (
        <input
          ref={renameInputRef}
          value={renameValue}
          onChange={(e) => setRenameValue(e.target.value)}
          onBlur={handleRenameSubmit}
          onKeyDown={(e) => {
            if (e.key === 'Enter') handleRenameSubmit();
            if (e.key === 'Escape') setRenamingId(null);
          }}
          className="font-medium text-sm text-gray-900 dark:text-white bg-white dark:bg-gray-700 border border-blue-400 rounded px-1 py-0 w-full outline-none"
          onClick={(e) => e.stopPropagation()}
        />
      );
    }
    return (
      <h3
        className="font-medium text-sm text-gray-900 dark:text-white cursor-text"
        onDoubleClick={(e) => { e.stopPropagation(); handleRenameStart(notebook); }}
        title="Double-click to rename"
      >
        {notebook.title}
      </h3>
    );
  };

  const renderNotebookCard = (notebook: Notebook) => {
    const isThisSelected = notebook.id === selectedNotebookId;
    return (
      <div
        key={notebook.id}
        className={`group p-2 rounded-lg border transition-all cursor-pointer ${
          isThisSelected
            ? 'border-blue-500/30 bg-gradient-to-r from-blue-50 to-blue-100/50 dark:from-blue-900/30 dark:to-blue-800/20 dark:border-blue-500/40 hover:border-blue-500/50'
            : 'border-gray-200 dark:border-gray-600 hover:border-gray-400 dark:hover:border-gray-500 bg-white dark:bg-gray-700'
        }`}
        onClick={() => onNotebookSelect(notebook.id)}
        onContextMenu={(e) => handleContextMenu(e, notebook.id)}
      >
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 flex-1 min-w-0">
            <div className="relative flex-shrink-0">
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  setShowColorPicker(showColorPicker === notebook.id ? null : notebook.id);
                }}
                className={`rounded-full ring-2 ring-white dark:ring-gray-700 shadow-sm hover:scale-110 transition-transform ${
                  isThisSelected ? 'w-4 h-4' : 'w-3 h-3'
                }`}
                style={{ backgroundColor: notebook.color || '#3B82F6' }}
                title="Change color"
              />
              {showColorPicker === notebook.id && (
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
                        onClick={() => handleColorChange(notebook.id, color)}
                        style={{ 
                          backgroundColor: color,
                          width: '32px',
                          height: '32px',
                          borderRadius: '50%',
                          border: notebook.color === color ? '3px solid #3B82F6' : 'none',
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
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-1">
                {primaryNotebookId === notebook.id && <span className="text-purple-500 text-xs flex-shrink-0">★</span>}
                {renderNotebookTitle(notebook)}
              </div>
              <p className="text-xs text-gray-500 dark:text-gray-400">
                {notebook.source_count} sources
              </p>
            </div>
          </div>
          <div className="flex gap-0.5 items-center opacity-0 group-hover:opacity-100 transition-opacity flex-shrink-0" onClick={(e) => e.stopPropagation()}>
            <button
              onClick={(e) => {
                e.stopPropagation();
                if (primaryNotebookId !== notebook.id) handleSetPrimary(notebook.id);
              }}
              className={`text-xs px-1 py-0.5 rounded transition-colors ${
                primaryNotebookId === notebook.id
                  ? 'text-purple-500'
                  : 'text-gray-400 hover:text-purple-500'
              }`}
              title={primaryNotebookId === notebook.id ? 'Primary notebook' : 'Set as primary'}
            >
              {primaryNotebookId === notebook.id ? '★' : '☆'}
            </button>
            <button
              onClick={(e) => { e.stopPropagation(); handleRenameStart(notebook); }}
              className="text-gray-400 hover:text-blue-500 text-xs px-1 py-0.5 rounded transition-colors"
              title="Rename"
            >
              <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" /></svg>
            </button>
            <button
              onClick={(e) => {
                e.stopPropagation();
                const rect = e.currentTarget.getBoundingClientRect();
                setContextMenu({ x: rect.right, y: rect.bottom + 2, notebookId: notebook.id });
              }}
              className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 text-xs px-1 py-0.5 rounded transition-colors"
              title="More options"
            >
              <svg className="w-3 h-3" fill="currentColor" viewBox="0 0 20 20"><circle cx="10" cy="4" r="1.5"/><circle cx="10" cy="10" r="1.5"/><circle cx="10" cy="16" r="1.5"/></svg>
            </button>
          </div>
        </div>
      </div>
    );
  };

  if (loading) {
    return (
      <div className="px-3 py-2">
        <LoadingSpinner />
      </div>
    );
  }

  const unsectionedNotebooks = notebooks.filter(nb => !nb.section_id);
  const getNotebooksForSection = (sectionId: string) => notebooks.filter(nb => nb.section_id === sectionId);

  return (
    <div className="px-3 py-2">
      {error && <ErrorMessage message={error} onDismiss={() => setError(null)} />}

      <div className="flex items-center justify-end gap-1 mb-1">
        <button
          onClick={() => setShowAddSection(true)}
          className="flex items-center gap-1 px-2 py-1 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg transition-colors text-xs"
          title="New Section"
        >
          <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h7" />
          </svg>
        </button>
        <button
          onClick={() => setShowCreateModal(true)}
          disabled={creating}
          className="flex items-center gap-1 px-2 py-1 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg transition-colors disabled:opacity-50"
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

      {/* New section input */}
      {showAddSection && (
        <div className="mb-2 flex items-center gap-1">
          <input
            ref={newSectionInputRef}
            value={newSectionName}
            onChange={(e) => setNewSectionName(e.target.value)}
            onBlur={handleCreateSection}
            onKeyDown={(e) => {
              if (e.key === 'Enter') handleCreateSection();
              if (e.key === 'Escape') { setShowAddSection(false); setNewSectionName(''); }
            }}
            placeholder="Section name..."
            className="flex-1 text-xs px-2 py-1 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-gray-900 dark:text-white outline-none focus:border-blue-400"
          />
        </div>
      )}

      {notebooks.length === 0 ? (
        <p className="text-gray-500 dark:text-gray-400 text-sm">No notebooks yet. Create one to get started!</p>
      ) : (
        <div className="space-y-1">
          {/* Unsectioned notebooks */}
          {unsectionedNotebooks.map(nb => renderNotebookCard(nb))}

          {/* Sections */}
          {sections.map(section => {
            const sectionNotebooks = getNotebooksForSection(section.id);
            return (
              <div key={section.id} className="mt-1">
                <div className="flex items-center group">
                  <button
                    onClick={() => handleToggleSection(section.id)}
                    className="flex items-center gap-1 flex-1 px-1 py-1 hover:bg-gray-100 dark:hover:bg-gray-700/50 rounded transition-colors"
                  >
                    <svg
                      className={`w-3 h-3 text-gray-400 transition-transform ${section.collapsed ? '' : 'rotate-90'}`}
                      fill="none" stroke="currentColor" viewBox="0 0 24 24"
                    >
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                    </svg>
                    {editingSectionId === section.id ? (
                      <input
                        ref={sectionInputRef}
                        value={editingSectionName}
                        onChange={(e) => setEditingSectionName(e.target.value)}
                        onBlur={() => handleRenameSection(section.id)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') handleRenameSection(section.id);
                          if (e.key === 'Escape') setEditingSectionId(null);
                        }}
                        onClick={(e) => e.stopPropagation()}
                        className="text-[11px] font-semibold uppercase tracking-wide text-gray-600 dark:text-gray-400 bg-white dark:bg-gray-700 border border-blue-400 rounded px-1 py-0 outline-none"
                      />
                    ) : (
                      <span className="text-[11px] font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wide">
                        {section.name}
                      </span>
                    )}
                    <span className="text-[10px] text-gray-400 dark:text-gray-500">
                      {sectionNotebooks.length}
                    </span>
                  </button>
                  <div className="flex gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
                    <button
                      onClick={() => { setEditingSectionId(section.id); setEditingSectionName(section.name); }}
                      className="text-gray-400 hover:text-blue-500 p-0.5 rounded transition-colors"
                      title="Rename section"
                    >
                      <svg className="w-2.5 h-2.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" /></svg>
                    </button>
                    <button
                      onClick={() => handleDeleteSection(section.id)}
                      className="text-gray-400 hover:text-red-500 p-0.5 rounded transition-colors"
                      title="Delete section"
                    >
                      <svg className="w-2.5 h-2.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" /></svg>
                    </button>
                  </div>
                </div>
                {!section.collapsed && (
                  <div className="ml-3 pl-2 border-l-2 border-gray-200 dark:border-gray-600 space-y-1 mt-0.5">
                    {sectionNotebooks.length === 0 ? (
                      <p className="text-xs text-gray-400 dark:text-gray-500 italic py-1 pl-1">No notebooks</p>
                    ) : (
                      sectionNotebooks.map(nb => renderNotebookCard(nb))
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Right-click context menu */}
      {contextMenu && (
        <div
          ref={contextMenuRef}
          className="fixed z-[9999] bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-600 rounded-lg shadow-xl py-1 min-w-[180px]"
          style={{ left: Math.min(contextMenu.x, window.innerWidth - 200), top: contextMenu.y }}
        >
          <button
            onClick={() => {
              const nb = notebooks.find(n => n.id === contextMenu.notebookId);
              if (nb) handleRenameStart(nb);
            }}
            className="w-full text-left px-3 py-1.5 text-xs text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 flex items-center gap-2"
          >
            <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" /></svg>
            Rename
          </button>
          {sections.length > 0 && (
            <>
              <div className="border-t border-gray-200 dark:border-gray-700 my-1" />
              <div className="px-3 py-1 text-[10px] font-semibold text-gray-400 uppercase tracking-wider">Move to</div>
              <button
                onClick={() => handleMoveToSection(contextMenu.notebookId, null)}
                className={`w-full text-left px-3 py-1.5 text-xs hover:bg-gray-100 dark:hover:bg-gray-700 flex items-center gap-2 ${
                  !notebooks.find(n => n.id === contextMenu.notebookId)?.section_id
                    ? 'text-blue-600 dark:text-blue-400 font-medium'
                    : 'text-gray-700 dark:text-gray-300'
                }`}
              >
                <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 8h14M5 8a2 2 0 110-4h14a2 2 0 110 4M5 8v10a2 2 0 002 2h10a2 2 0 002-2V8" /></svg>
                Unsectioned
              </button>
              {sections.map(s => (
                <button
                  key={s.id}
                  onClick={() => handleMoveToSection(contextMenu.notebookId, s.id)}
                  className={`w-full text-left px-3 py-1.5 text-xs hover:bg-gray-100 dark:hover:bg-gray-700 flex items-center gap-2 ${
                    notebooks.find(n => n.id === contextMenu.notebookId)?.section_id === s.id
                      ? 'text-blue-600 dark:text-blue-400 font-medium'
                      : 'text-gray-700 dark:text-gray-300'
                  }`}
                >
                  <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" /></svg>
                  {s.name}
                </button>
              ))}
            </>
          )}
          <div className="border-t border-gray-200 dark:border-gray-700 my-1" />
          <button
            onClick={() => {
              const nb = notebooks.find(n => n.id === contextMenu.notebookId);
              if (nb) {
                if (primaryNotebookId !== nb.id) handleSetPrimary(nb.id);
              }
              setContextMenu(null);
            }}
            className="w-full text-left px-3 py-1.5 text-xs text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 flex items-center gap-2"
          >
            <span className="w-3 text-center text-purple-500">{primaryNotebookId === contextMenu.notebookId ? '★' : '☆'}</span>
            {primaryNotebookId === contextMenu.notebookId ? 'Primary notebook' : 'Set as primary'}
          </button>
          <button
            onClick={() => {
              const nb = notebooks.find(n => n.id === contextMenu.notebookId);
              if (nb) { setNewlyCreatedNotebook(nb); setShowCollectorSetup(true); }
              setContextMenu(null);
            }}
            className="w-full text-left px-3 py-1.5 text-xs text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 flex items-center gap-2"
          >
            <span className="w-3 text-center">⚙</span>
            Configure Collector
          </button>
          <button
            onClick={() => handleExportClick(contextMenu.notebookId)}
            className="w-full text-left px-3 py-1.5 text-xs text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 flex items-center gap-2"
          >
            <span className="w-3 text-center">↓</span>
            Export
          </button>
          <button
            onClick={() => handleDeleteClick(contextMenu.notebookId)}
            className="w-full text-left px-3 py-1.5 text-xs text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20 flex items-center gap-2"
          >
            <span className="w-3 text-center">✕</span>
            Delete
          </button>
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
        <div className="p-4">
          <p className="text-sm text-gray-600 dark:text-gray-400 mb-4">
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
        notebookId={notebookToExport}
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
