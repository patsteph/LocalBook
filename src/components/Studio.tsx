import React, { useState, useEffect } from 'react';
import { skillsService } from '../services/skills';
import { audioService } from '../services/audio';
import { contentService, ContentGeneration } from '../services/content';
import { Skill, AudioGeneration } from '../types';
import { Button } from './shared/Button';
import { LoadingSpinner } from './shared/LoadingSpinner';
import { ErrorMessage } from './shared/ErrorMessage';

interface StudioProps {
  notebookId: string | null;
}

export const Studio: React.FC<StudioProps> = ({ notebookId }) => {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [selectedSkill, setSelectedSkill] = useState<string>('');
  const [audioGenerations, setAudioGenerations] = useState<AudioGeneration[]>([]);
  const [contentGenerations, setContentGenerations] = useState<ContentGeneration[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [generating, setGenerating] = useState(false);
  const [activeTab, setActiveTab] = useState<'text' | 'audio'>('text');
  const [generatedContent, setGeneratedContent] = useState<string>('');
  const [contentSkillName, setContentSkillName] = useState<string>('');
  const [selectedContentId, setSelectedContentId] = useState<string | null>(null);

  // Form state
  const [topic, setTopic] = useState('');
  const [duration, setDuration] = useState(10);
  const [host1Gender, setHost1Gender] = useState('male');
  const [host2Gender, setHost2Gender] = useState('female');
  const [accent, setAccent] = useState('us');
  const [generatedScript, setGeneratedScript] = useState('');
  const [showScript, setShowScript] = useState(false);

  // Custom skill creation
  const [showCustomSkillForm, setShowCustomSkillForm] = useState(false);
  const [customSkillName, setCustomSkillName] = useState('');
  const [customSkillDescription, setCustomSkillDescription] = useState('');
  const [customSkillPrompt, setCustomSkillPrompt] = useState('');

  useEffect(() => {
    loadSkills();
  }, []);

  useEffect(() => {
    if (notebookId) {
      loadAudioGenerations();
      loadContentGenerations();
    }
  }, [notebookId]);

  // Only poll when there's an active generation processing
  useEffect(() => {
    const hasProcessing = audioGenerations.some(g => g.status === 'processing');
    if (hasProcessing) {
      const interval = setInterval(loadAudioGenerations, 5000);
      return () => clearInterval(interval);
    }
  }, [audioGenerations]);

  const loadSkills = async () => {
    try {
      const data = await skillsService.list();
      setSkills(data);
      // Set default to summary for text tab
      const summary = data.find(s => s.skill_id === 'summary');
      if (summary) {
        setSelectedSkill(summary.skill_id);
      } else if (data.length > 0) {
        setSelectedSkill(data[0].skill_id);
      }
    } catch (err) {
      console.error('Failed to load skills:', err);
    }
  };

  // Update selected skill when tab changes
  useEffect(() => {
    if (skills.length === 0) return;
    const textDefault = skills.find(s => s.skill_id === 'summary');
    const audioDefault = skills.find(s => s.skill_id === 'podcast_script');
    
    if (activeTab === 'text' && textDefault) {
      setSelectedSkill(textDefault.skill_id);
    } else if (activeTab === 'audio' && audioDefault) {
      setSelectedSkill(audioDefault.skill_id);
    }
  }, [activeTab, skills]);

  const loadAudioGenerations = async () => {
    if (!notebookId) return;

    try {
      const data = await audioService.list(notebookId);
      setAudioGenerations(data);
    } catch (err) {
      console.error('Failed to load audio generations:', err);
    }
  };

  const loadContentGenerations = async () => {
    if (!notebookId) return;

    try {
      const data = await contentService.list(notebookId);
      setContentGenerations(data);
    } catch (err) {
      console.error('Failed to load content generations:', err);
    }
  };

  const handleGenerate = async () => {
    if (!notebookId) return;

    setGenerating(true);
    setError(null);
    setGeneratedScript('');

    try {
      const result = await audioService.generate({
        notebook_id: notebookId,
        topic: topic || 'the research content',
        duration_minutes: duration,
        skill_id: selectedSkill,
        host1_gender: host1Gender,
        host2_gender: host2Gender,
        accent: accent,
      });

      setGeneratedScript(result.script);
      setShowScript(true);
      await loadAudioGenerations();

    } catch (err: any) {
      console.error('Failed to generate:', err);
      setError(err.response?.data?.detail || 'Failed to generate audio');
    } finally {
      setGenerating(false);
    }
  };

  const formatDuration = (seconds?: number) => {
    if (!seconds) return 'N/A';
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins}:${secs.toString().padStart(2, '0')}`;
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'completed':
        return 'bg-green-100 text-green-800';
      case 'processing':
        return 'bg-blue-100 text-blue-800';
      case 'failed':
        return 'bg-red-100 text-red-800';
      default:
        return 'bg-gray-100 text-gray-800';
    }
  };

  // Text content generation (non-audio)
  const handleGenerateText = async () => {
    if (!notebookId || !selectedSkill) return;

    setGenerating(true);
    setError(null);
    setGeneratedContent('');

    try {
      const result = await contentService.generate({
        notebook_id: notebookId,
        skill_id: selectedSkill,
        topic: topic || undefined,
      });

      setGeneratedContent(result.content);
      setContentSkillName(result.skill_name);
      setSelectedContentId(null); // Clear selection since we just generated new
      loadContentGenerations(); // Refresh list
    } catch (err: any) {
      console.error('Failed to generate:', err);
      setError(err.message || 'Failed to generate content');
    } finally {
      setGenerating(false);
    }
  };

  // Skills that produce audio vs text-only
  const audioSkillIds = ['podcast_script', 'debate'];
  const textSkillIds = ['summary', 'study_guide', 'faq', 'briefing', 'deep_dive', 'explain'];
  
  // Filter skills based on active tab
  const filteredSkills = skills.filter(s => 
    activeTab === 'audio' 
      ? audioSkillIds.includes(s.skill_id) || !textSkillIds.includes(s.skill_id)
      : textSkillIds.includes(s.skill_id) || !audioSkillIds.includes(s.skill_id)
  );

  if (!notebookId) {
    return (
      <div className="p-6 text-center text-gray-500 dark:text-gray-400">
        <h3 className="font-bold text-lg mb-2 text-gray-900 dark:text-white">Studio</h3>
        <p>Select a notebook to start creating</p>
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col overflow-hidden">
      {/* Header with Tabs */}
      <div className="p-4 border-b border-gray-200 dark:border-gray-700">
        <h3 className="font-bold text-base mb-2 text-gray-900 dark:text-white">Studio</h3>
        <div className="flex gap-1 bg-gray-100 dark:bg-gray-800 rounded-lg p-1">
          <button
            onClick={() => setActiveTab('text')}
            className={`flex-1 px-3 py-1.5 text-sm font-medium rounded-md transition-colors ${
              activeTab === 'text'
                ? 'bg-white dark:bg-gray-700 text-gray-900 dark:text-white shadow-sm'
                : 'text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white'
            }`}
          >
            📄 Documents
          </button>
          <button
            onClick={() => setActiveTab('audio')}
            className={`flex-1 px-3 py-1.5 text-sm font-medium rounded-md transition-colors ${
              activeTab === 'audio'
                ? 'bg-white dark:bg-gray-700 text-gray-900 dark:text-white shadow-sm'
                : 'text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white'
            }`}
          >
            🎙️ Audio
          </button>
        </div>
      </div>

      {/* Main Content - Scrollable */}
      <div className="flex-1 overflow-y-auto p-6 pb-12 space-y-6">
        {error && <ErrorMessage message={error} onDismiss={() => setError(null)} />}

        {/* Skills Selection */}
        <div>
          <div className="flex justify-between items-center mb-2">
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300">
              Content Type
            </label>
            <button
              onClick={() => setShowCustomSkillForm(!showCustomSkillForm)}
              className="text-sm text-blue-600 hover:text-blue-700"
            >
              {showCustomSkillForm ? 'Cancel' : '+ Custom Skill'}
            </button>
          </div>

          {!showCustomSkillForm ? (
            <>
              <select
                value={selectedSkill}
                onChange={(e) => setSelectedSkill(e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
              >
                {filteredSkills.map((skill) => (
                  <option key={skill.skill_id} value={skill.skill_id}>
                    {skill.name}
                  </option>
                ))}
              </select>
              {filteredSkills.find((s) => s.skill_id === selectedSkill)?.description && (
                <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
                  {filteredSkills.find((s) => s.skill_id === selectedSkill)?.description}
                </p>
              )}
            </>
          ) : (
            <div className="space-y-3 border border-gray-300 dark:border-gray-600 rounded-md p-3">
              <input
                type="text"
                value={customSkillName}
                onChange={(e) => setCustomSkillName(e.target.value)}
                placeholder="Skill Name"
                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
              />
              <input
                type="text"
                value={customSkillDescription}
                onChange={(e) => setCustomSkillDescription(e.target.value)}
                placeholder="Description (optional)"
                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
              />
              <textarea
                value={customSkillPrompt}
                onChange={(e) => setCustomSkillPrompt(e.target.value)}
                placeholder="System Prompt (instructions for the AI)"
                rows={4}
                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
              />
              <Button
                onClick={async () => {
                  try {
                    await skillsService.create({
                      name: customSkillName,
                      description: customSkillDescription,
                      system_prompt: customSkillPrompt,
                    });
                    setShowCustomSkillForm(false);
                    setCustomSkillName('');
                    setCustomSkillDescription('');
                    setCustomSkillPrompt('');
                    await loadSkills();
                  } catch (err) {
                    setError('Failed to create custom skill');
                  }
                }}
                disabled={!customSkillName || !customSkillPrompt}
                className="w-full"
              >
                Create Skill
              </Button>
            </div>
          )}
        </div>

        {/* Topic Input */}
        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
            Topic (optional)
          </label>
          <input
            type="text"
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
            placeholder="e.g., AI use cases in healthcare"
            className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
          />
        </div>

        {/* Duration - Only for audio tab */}
        {activeTab === 'audio' && (
        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
            Duration (minutes)
          </label>
          <input
            type="number"
            value={duration}
            onChange={(e) => setDuration(parseInt(e.target.value) || 10)}
            min="5"
            max="30"
            className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
          />
        </div>
        )}

        {/* Voice Configuration - Only for audio tab */}
        {activeTab === 'audio' && (
        <details className="border border-gray-300 dark:border-gray-600 rounded-md" open>
          <summary className="px-3 py-2 cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-700 text-xs font-medium text-gray-700 dark:text-gray-300">
            Voice Configuration
          </summary>
          <div className="p-3 space-y-3 border-t border-gray-300 dark:border-gray-600">
            {/* Host 1 */}
            <div className="flex items-center gap-4">
              <span className="text-sm text-gray-600 dark:text-gray-400 w-20">Host 1:</span>
              <select
                value={host1Gender}
                onChange={(e) => setHost1Gender(e.target.value)}
                className="flex-1 px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
              >
                <option value="male">Male</option>
                <option value="female">Female</option>
              </select>
            </div>

            {/* Host 2 */}
            <div className="flex items-center gap-4">
              <span className="text-sm text-gray-600 dark:text-gray-400 w-20">Host 2:</span>
              <select
                value={host2Gender}
                onChange={(e) => setHost2Gender(e.target.value)}
                className="flex-1 px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
              >
                <option value="male">Male</option>
                <option value="female">Female</option>
              </select>
            </div>

            {/* Accent */}
            <div className="flex items-center gap-4">
              <span className="text-sm text-gray-600 dark:text-gray-400 w-20">Accent:</span>
              <select
                value={accent}
                onChange={(e) => setAccent(e.target.value)}
                className="flex-1 px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
              >
                <option value="us">American (US)</option>
                <option value="uk">British (UK)</option>
              </select>
            </div>
          </div>
        </details>
        )}

        {/* Generate Button - different action based on tab */}
        <Button
          onClick={activeTab === 'audio' ? handleGenerate : handleGenerateText}
          disabled={generating || !notebookId}
          className="w-full"
        >
          {generating ? 'Generating...' : activeTab === 'audio' ? 'Generate Audio' : 'Generate Document'}
        </Button>

        {/* Generated Text Content (Documents tab) */}
        {activeTab === 'text' && generatedContent && (
          <div className="border border-green-300 dark:border-green-700 bg-green-50 dark:bg-green-900/20 rounded-lg p-4">
            <div className="flex justify-between items-center mb-3">
              <h4 className="font-medium text-sm text-green-900 dark:text-green-100">
                ✓ {contentSkillName} Generated
              </h4>
              <div className="flex gap-2">
                <button
                  onClick={async () => {
                    try {
                      await contentService.downloadAsPDF(generatedContent, contentSkillName, contentSkillName.toLowerCase().replace(/\s+/g, '-'));
                    } catch (err) {
                      console.error('PDF download failed:', err);
                    }
                  }}
                  className="text-xs px-2 py-1 bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 rounded hover:bg-blue-100 dark:hover:bg-blue-900/30"
                >
                  📥 PDF
                </button>
                <button
                  onClick={() => setGeneratedContent('')}
                  className="text-xs text-gray-500 hover:text-gray-700"
                >
                  Clear
                </button>
              </div>
            </div>
            <div className="prose prose-sm dark:prose-invert max-w-none max-h-96 overflow-y-auto bg-white dark:bg-gray-800 p-4 rounded border border-gray-200 dark:border-gray-600">
              <pre className="whitespace-pre-wrap text-sm text-gray-700 dark:text-gray-300 font-sans">
                {generatedContent}
              </pre>
            </div>
          </div>
        )}

        {/* Generated Script Preview */}
        {generatedScript && showScript && (
          <div className="border border-green-300 dark:border-green-700 bg-green-50 dark:bg-green-900/20 rounded-lg p-4">
            <div className="flex justify-between items-center mb-2">
              <h4 className="font-medium text-sm text-green-900 dark:text-green-100">✓ Content Generated Successfully</h4>
              <button
                onClick={() => setShowScript(false)}
                className="text-sm text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300"
              >
                Hide
              </button>
            </div>
            <div className="mb-3 p-3 bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-700 rounded text-sm text-blue-900 dark:text-blue-100">
              ✓ <strong>Content generated successfully!</strong> Audio is now being generated and will appear below when ready (usually 1-2 minutes).
            </div>
            <details className="mb-3">
              <summary className="text-sm font-medium text-gray-700 dark:text-gray-300 cursor-pointer hover:text-gray-900 dark:hover:text-white">
                View Generated Content
              </summary>
              <div className="mt-2 text-sm text-gray-700 dark:text-gray-300 whitespace-pre-wrap max-h-64 overflow-y-auto bg-white dark:bg-gray-800 p-3 rounded border border-gray-200 dark:border-gray-600">
                {generatedScript}
              </div>
            </details>
          </div>
        )}

        {/* Previous Document Generations - Only for text tab */}
        {activeTab === 'text' && contentGenerations.length > 0 && (
          <div className="mt-4">
            <h4 className="font-medium text-sm mb-3 text-gray-900 dark:text-white">Previous Documents</h4>
            <div className="space-y-2">
              {contentGenerations.map((gen) => (
                <div
                  key={gen.content_id}
                  className={`p-3 rounded-lg border cursor-pointer transition-colors ${
                    selectedContentId === gen.content_id
                      ? 'border-purple-500 bg-purple-50 dark:bg-purple-900/20'
                      : 'border-gray-200 dark:border-gray-700 hover:border-gray-300 dark:hover:border-gray-600'
                  }`}
                  onClick={() => {
                    setSelectedContentId(gen.content_id);
                    setGeneratedContent(gen.content);
                    setContentSkillName(gen.skill_name);
                  }}
                >
                  <div className="flex justify-between items-start">
                    <div className="flex-1 min-w-0">
                      <p className="font-medium text-sm text-gray-900 dark:text-white truncate">
                        {gen.skill_name}
                      </p>
                      <p className="text-xs text-gray-500 dark:text-gray-400">
                        {new Date(gen.created_at).toLocaleDateString()} • {gen.sources_used} sources
                      </p>
                      {gen.topic && (
                        <p className="text-xs text-gray-400 dark:text-gray-500 truncate mt-0.5">
                          Topic: {gen.topic}
                        </p>
                      )}
                    </div>
                    <button
                      onClick={async (e) => {
                        e.stopPropagation();
                        try {
                          await contentService.delete(gen.content_id);
                          loadContentGenerations();
                          if (selectedContentId === gen.content_id) {
                            setGeneratedContent('');
                            setSelectedContentId(null);
                          }
                        } catch (err) {
                          console.error('Failed to delete:', err);
                        }
                      }}
                      className="text-gray-400 hover:text-red-500 ml-2"
                      title="Delete"
                    >
                      ✕
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Previous Audio Generations - Only for audio tab */}
        {activeTab === 'audio' && (
        <div>
          <h4 className="font-medium text-sm mb-3 text-gray-900 dark:text-white">Previous Audio Generations</h4>
          {audioGenerations.length === 0 ? (
            <p className="text-sm text-gray-500 dark:text-gray-400">No generations yet</p>
          ) : (
            <div className="space-y-3">
              {audioGenerations.map((gen) => (
                <div
                  key={gen.audio_id}
                  className="border border-gray-300 rounded-lg p-4"
                >
                  <div className="flex justify-between items-start mb-2">
                    <div className="flex-1">
                      <span
                        className={`inline-block px-2 py-0.5 text-xs rounded ${getStatusColor(
                          gen.status
                        )}`}
                      >
                        {gen.status}
                      </span>
                      {gen.duration_seconds && (
                        <span className="ml-2 text-sm text-gray-600 dark:text-gray-400">
                          {formatDuration(gen.duration_seconds)}
                        </span>
                      )}
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-gray-500 dark:text-gray-400">
                        {new Date(gen.created_at).toLocaleString()}
                      </span>
                      <button
                        onClick={async () => {
                          if (confirm('Delete this audio generation?')) {
                            try {
                              await audioService.delete(gen.audio_id);
                              loadAudioGenerations();
                            } catch (err) {
                              console.error('Failed to delete:', err);
                            }
                          }
                        }}
                        className="text-red-500 hover:text-red-700 p-1"
                        title="Delete"
                      >
                        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                        </svg>
                      </button>
                    </div>
                  </div>

                  {/* Audio Player */}
                  {gen.status === 'completed' && gen.audio_file_path && (
                    <div className="mt-3">
                      <audio
                        controls
                        className="w-full"
                        src={audioService.getDownloadUrl(gen.audio_id)}
                      >
                        Your browser does not support audio playback.
                      </audio>
                    </div>
                  )}

                  {gen.status === 'processing' && (
                    <div className="mt-3 flex items-center gap-2">
                      <LoadingSpinner size="sm" />
                      <span className="text-sm text-gray-600 dark:text-gray-400">
                        Generating audio...
                      </span>
                    </div>
                  )}

                  {gen.status === 'failed' && gen.error_message && (
                    <div className="mt-3 p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-700 rounded">
                      <p className="text-sm font-medium text-red-800 dark:text-red-200 mb-1">
                        ❌ Generation Failed
                      </p>
                      <p className="text-xs text-red-700 dark:text-red-300">
                        {gen.error_message}
                      </p>
                    </div>
                  )}

                  {/* Content Preview */}
                  {gen.script && (
                    <details className="mt-3">
                      <summary className="text-sm text-blue-600 cursor-pointer hover:text-blue-700">
                        View Content
                      </summary>
                      <div className="mt-2 text-sm text-gray-700 dark:text-gray-300 whitespace-pre-wrap max-h-48 overflow-y-auto bg-gray-50 dark:bg-gray-800 p-3 rounded">
                        {gen.script}
                      </div>
                    </details>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
        )}
      </div>
    </div>
  );
};
