import React, { useState, useEffect, useRef } from 'react';
import { Upload, Trash2, FileText } from 'lucide-react';
import { exportService } from '../../services/export';

interface Template {
  id: string;
  name: string;
  filename: string;
  size: number;
  uploaded: string;
}

interface TemplatesSectionProps {
  setError: (msg: string | null) => void;
  setSuccess: (msg: string | null) => void;
}

export const TemplatesSection: React.FC<TemplatesSectionProps> = ({ setError, setSuccess }) => {
  const [templates, setTemplates] = useState<Template[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [newName, setNewName] = useState('');
  const fileRef = useRef<HTMLInputElement>(null);

  const loadTemplates = async () => {
    try {
      const list = await exportService.listTemplates();
      setTemplates(list);
    } catch {
      setError('Failed to load templates');
    }
    setLoading(false);
  };

  useEffect(() => { loadTemplates(); }, []);

  const handleUpload = async () => {
    const file = fileRef.current?.files?.[0];
    if (!file) return;
    if (!file.name.toLowerCase().endsWith('.pptx')) {
      setError('Only .pptx files are allowed');
      return;
    }
    if (file.size > 25 * 1024 * 1024) {
      setError('File too large. Maximum 25MB.');
      return;
    }
    setUploading(true);
    setError(null);
    try {
      await exportService.uploadTemplate(file, newName || file.name.replace('.pptx', ''));
      setSuccess('Template uploaded successfully');
      setNewName('');
      if (fileRef.current) fileRef.current.value = '';
      await loadTemplates();
    } catch (err: any) {
      setError(err.message || 'Upload failed');
    }
    setUploading(false);
  };

  const handleDelete = async (id: string, name: string) => {
    if (!window.confirm(`Delete template "${name}"?`)) return;
    try {
      await exportService.deleteTemplate(id);
      setSuccess(`Deleted "${name}"`);
      await loadTemplates();
    } catch {
      setError('Failed to delete template');
    }
  };

  const formatSize = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-lg font-semibold text-gray-900 dark:text-white mb-1">Presentation Templates</h3>
        <p className="text-sm text-gray-500 dark:text-gray-400">Upload custom .pptx templates to use when generating slides. Templates provide slide masters, fonts, and color schemes. Max 5 templates, 25MB each.</p>
      </div>

      {/* Upload form */}
      <div className="p-4 bg-gray-50 dark:bg-gray-800/50 border border-gray-200 dark:border-gray-700 rounded-lg space-y-3">
        <div className="flex items-center gap-3">
          <input
            type="text"
            value={newName}
            onChange={e => setNewName(e.target.value)}
            placeholder="Template name (e.g., Company Dark)"
            className="flex-1 px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white placeholder-gray-400"
          />
        </div>
        <div className="flex items-center gap-3">
          <input
            ref={fileRef}
            type="file"
            accept=".pptx"
            className="flex-1 text-sm text-gray-600 dark:text-gray-400 file:mr-3 file:py-1.5 file:px-3 file:rounded-lg file:border file:border-gray-300 dark:file:border-gray-600 file:text-sm file:font-medium file:bg-white dark:file:bg-gray-700 file:text-gray-700 dark:file:text-gray-300 hover:file:bg-gray-50 dark:hover:file:bg-gray-600"
          />
          <button
            onClick={handleUpload}
            disabled={uploading || templates.length >= 5}
            className="flex items-center gap-1.5 px-4 py-2 text-sm font-medium bg-blue-600 hover:bg-blue-700 disabled:bg-gray-300 dark:disabled:bg-gray-700 text-white rounded-lg transition-colors disabled:cursor-not-allowed"
          >
            <Upload className="w-3.5 h-3.5" />
            {uploading ? 'Uploading...' : 'Upload'}
          </button>
        </div>
      </div>

      {/* Template list */}
      {loading ? (
        <p className="text-sm text-gray-400">Loading templates...</p>
      ) : templates.length === 0 ? (
        <p className="text-sm text-gray-400 italic">No custom templates yet. Upload a .pptx file to get started.</p>
      ) : (
        <div className="space-y-2">
          {templates.map(t => (
            <div key={t.id} className="flex items-center gap-3 p-3 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg">
              <FileText className="w-5 h-5 text-orange-500 flex-shrink-0" />
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-gray-900 dark:text-white truncate">{t.name}</p>
                <p className="text-xs text-gray-400">{t.filename} &middot; {formatSize(t.size)} &middot; {new Date(t.uploaded).toLocaleDateString()}</p>
              </div>
              <button
                onClick={() => handleDelete(t.id, t.name)}
                className="p-1.5 text-red-400 hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-lg transition-colors"
                title="Delete template"
              >
                <Trash2 className="w-4 h-4" />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
