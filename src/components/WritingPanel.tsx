import React, { useState, useEffect } from 'react';
import ReactMarkdown from 'react-markdown';
import { writingService, FormatOption, WritingResult } from '../services/writing';
import { Button } from './shared/Button';
import { LoadingSpinner } from './shared/LoadingSpinner';
import { BookmarkButton } from './shared/BookmarkButton';

interface WritingPanelProps {
  notebookId: string;
}

export const WritingPanel: React.FC<WritingPanelProps> = ({ notebookId }) => {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [formats, setFormats] = useState<FormatOption[]>([]);
  const [tasks, setTasks] = useState<{ value: string; label: string; description: string }[]>([]);
  
  // Direct input only - "From Sources" moved to Docs tab
  const [directInput, setDirectInput] = useState('');
  
  const [selectedTask, setSelectedTask] = useState('improve');
  const [selectedFormat, setSelectedFormat] = useState('professional');
  const [maxWords, setMaxWords] = useState(500);
  const [result, setResult] = useState<WritingResult | null>(null);

  useEffect(() => {
    loadOptions();
  }, []);

  const loadOptions = async () => {
    try {
      const [formatsData, tasksData] = await Promise.all([
        writingService.getFormats(),
        writingService.getTasks(),
      ]);
      setFormats(formatsData);
      setTasks(tasksData);
    } catch (err) {
      console.error('Failed to load options:', err);
    }
  };

  const handleGenerate = async () => {
    if (!directInput.trim()) {
      setError('Please enter some text to transform');
      return;
    }
    
    setLoading(true);
    setError(null);
    try {
      const data = await writingService.transformText(
        directInput,
        selectedTask,
        selectedFormat,
        maxWords
      );
      setResult(data);
    } catch (err: any) {
      setError(err.message || 'Failed to generate writing');
    } finally {
      setLoading(false);
    }
  };

  const copyToClipboard = () => {
    if (result) {
      navigator.clipboard.writeText(result.content);
    }
  };

  return (
    <div className="space-y-4">
      {/* Header */}
      <p className="text-xs text-gray-500 dark:text-gray-400">
        ✏️ Transform your text with AI assistance
      </p>

      {/* Direct Input Text Area */}
      <div>
        <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
          Your Text
        </label>
        <textarea
          value={directInput}
          onChange={(e) => setDirectInput(e.target.value)}
          placeholder="Paste your text here to transform..."
          rows={5}
          className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white resize-none"
        />
        <p className="mt-1 text-xs text-gray-500">
          {directInput.split(/\s+/).filter(Boolean).length} words
        </p>
      </div>

      {/* Task Selection */}
      <div>
        <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
          Task
        </label>
        <select
          value={selectedTask}
          onChange={(e) => setSelectedTask(e.target.value)}
          className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
        >
          {tasks.map((task) => (
            <option key={task.value} value={task.value}>
              {task.label}
            </option>
          ))}
        </select>
        {tasks.find(t => t.value === selectedTask)?.description && (
          <p className="mt-1 text-xs text-gray-500">
            {tasks.find(t => t.value === selectedTask)?.description}
          </p>
        )}
      </div>

      {/* Format Selection */}
      <div>
        <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
          Style
        </label>
        <div className="grid grid-cols-2 gap-2">
          {formats.slice(0, 6).map((format) => (
            <button
              key={format.value}
              onClick={() => setSelectedFormat(format.value)}
              className={`px-2 py-1.5 text-xs rounded-lg border text-left transition-colors ${
                selectedFormat === format.value
                  ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/20 text-blue-700 dark:text-blue-300'
                  : 'border-gray-300 dark:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-800 text-gray-700 dark:text-gray-300'
              }`}
            >
              {format.label}
            </button>
          ))}
        </div>
      </div>

      {/* Word Count */}
      <div>
        <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
          Target Length: ~{maxWords} words
        </label>
        <input
          type="range"
          min="100"
          max="1000"
          step="100"
          value={maxWords}
          onChange={(e) => setMaxWords(Number(e.target.value))}
          className="w-full"
        />
      </div>

      <Button onClick={handleGenerate} disabled={loading || !directInput.trim()} className="w-full">
        {loading ? <LoadingSpinner size="sm" /> : '✨ Transform'}
      </Button>

      {error && (
        <div className="bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400 p-3 rounded-lg text-sm">
          {error}
        </div>
      )}

      {/* Result */}
      {result && (
        <div className="space-y-3">
          <div className="flex justify-between items-center">
            <span className="text-sm text-gray-500">
              {result.word_count} words • {result.format_used}
            </span>
            <div className="flex items-center gap-2">
              <BookmarkButton
                notebookId={notebookId}
                type="note"
                title={`${selectedTask} - ${result.word_count} words`}
                content={{
                  text: result.content,
                  task: selectedTask,
                  format: result.format_used,
                }}
              />
              <button
                onClick={copyToClipboard}
                className="text-xs text-blue-600 hover:text-blue-700"
              >
                Copy
              </button>
            </div>
          </div>

          <div className="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg p-4 max-h-64 overflow-y-auto">
            <div className="prose prose-sm dark:prose-invert max-w-none text-gray-900 dark:text-gray-100 prose-p:my-2 prose-headings:mt-4 prose-headings:mb-1 prose-ul:my-2 prose-li:my-0 prose-hr:my-4">
              <ReactMarkdown>{result.content}</ReactMarkdown>
            </div>
          </div>

          {result.suggestions.length > 0 && (
            <div className="bg-yellow-50 dark:bg-yellow-900/20 p-3 rounded-lg">
              <p className="text-xs font-medium text-yellow-800 dark:text-yellow-300 mb-1">Suggestions:</p>
              <ul className="text-xs text-yellow-700 dark:text-yellow-400 space-y-1">
                {result.suggestions.map((s, i) => (
                  <li key={i}>• {s}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
};
