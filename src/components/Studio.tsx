import React, { useState, useEffect } from 'react';
import { skillsService } from '../services/skills';
import { audioService } from '../services/audio';
import { contentService, ContentGeneration } from '../services/content';
import { writingService, FormatOption } from '../services/writing';
import { Skill, AudioGeneration } from '../types';
import { Button } from './shared/Button';
import { ErrorMessage } from './shared/ErrorMessage';
import { QuizPanel } from './QuizPanel';
import { VisualPanel } from './VisualPanel';
import { WritingPanel } from './WritingPanel';
import { ContentViewer } from './studio/ContentViewer';
import { AudioHistory } from './studio/AudioHistory';

interface StudioProps {
  notebookId: string | null;
  initialVisualContent?: string;
  initialTab?: 'documents' | 'audio' | 'quiz' | 'visual' | 'writing';
  onTabChange?: (tab: 'documents' | 'audio' | 'quiz' | 'visual' | 'writing') => void;
}

export const Studio: React.FC<StudioProps> = ({ notebookId, initialVisualContent, initialTab, onTabChange }) => {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [selectedSkill, setSelectedSkill] = useState<string>('');
  const [audioGenerations, setAudioGenerations] = useState<AudioGeneration[]>([]);
  const [contentGenerations, setContentGenerations] = useState<ContentGeneration[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [generating, setGenerating] = useState(false);
  const [activeTab, setActiveTab] = useState<'documents' | 'audio' | 'quiz' | 'visual' | 'writing'>(initialTab || 'documents');
  const [visualContentFromChat, setVisualContentFromChat] = useState<string>(initialVisualContent || '');

  // Sync with parent tab control
  useEffect(() => {
    if (initialTab && initialTab !== activeTab) {
      setActiveTab(initialTab);
    }
  }, [initialTab]);

  // Sync visual content from chat
  useEffect(() => {
    if (initialVisualContent) {
      setVisualContentFromChat(initialVisualContent);
    }
  }, [initialVisualContent]);

  // Notify parent of tab changes
  const handleTabChange = (tab: typeof activeTab) => {
    setActiveTab(tab);
    onTabChange?.(tab);
  };
  const [generatedContent, setGeneratedContent] = useState<string>('');
  const [contentSkillName, setContentSkillName] = useState<string>('');
  const [selectedContentId, setSelectedContentId] = useState<string | null>(null);
  
  // Style options for Docs tab
  const [styleFormats, setStyleFormats] = useState<FormatOption[]>([]);
  const [selectedStyle, setSelectedStyle] = useState<string>('professional');

  // Form state
  const [topic, setTopic] = useState('');
  const [duration, setDuration] = useState(10);
  const [host1Gender, setHost1Gender] = useState('male');
  const [host2Gender, setHost2Gender] = useState('female');
  const [accent, setAccent] = useState('us');
  const [generatedScript, setGeneratedScript] = useState('');
  const [quizTopic, setQuizTopic] = useState('');
  const [quizDifficulty, setQuizDifficulty] = useState('medium');
  const [showScript, setShowScript] = useState(false);

  // Custom skill creation
  const [showCustomSkillForm, setShowCustomSkillForm] = useState(false);
  const [customSkillName, setCustomSkillName] = useState('');
  const [customSkillDescription, setCustomSkillDescription] = useState('');
  const [customSkillPrompt, setCustomSkillPrompt] = useState('');

  useEffect(() => {
    loadSkills();
    loadStyleFormats();
  }, []);
  
  const loadStyleFormats = async () => {
    try {
      const formats = await writingService.getFormats();
      setStyleFormats(formats);
    } catch (err) {
      console.error('Failed to load style formats:', err);
    }
  };

  useEffect(() => {
    if (notebookId) {
      loadAudioGenerations();
      loadContentGenerations();
    }
  }, [notebookId]);

  // Poll when there's an active generation (pending or processing)
  useEffect(() => {
    const hasActive = audioGenerations.some(g => g.status === 'processing' || g.status === 'pending');
    if (hasActive) {
      const interval = setInterval(loadAudioGenerations, 3000);
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
    
    if (activeTab === 'documents' && textDefault) {
      setSelectedSkill(textDefault.skill_id);
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
      await audioService.generate({
        notebook_id: notebookId,
        topic: topic || 'the research content',
        duration_minutes: duration,
        skill_id: selectedSkill,
        host1_gender: host1Gender,
        host2_gender: host2Gender,
        accent: accent,
      });

      // API returns instantly — script + audio generate in background.
      // The polling interval (useEffect) will pick up status updates.
      await loadAudioGenerations();

    } catch (err: any) {
      console.error('Failed to generate:', err);
      setError(err.response?.data?.detail || 'Failed to generate audio');
    } finally {
      setGenerating(false);
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
        style: selectedStyle,
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
  const textSkillIds = ['summary', 'study_guide', 'faq', 'briefing', 'deep_dive', 'explain', 'feynman_curriculum'];
  
  // Filter skills based on active tab
  const filteredSkills = skills.filter(s => 
    textSkillIds.includes(s.skill_id) || !audioSkillIds.includes(s.skill_id)
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
        <div className="grid grid-cols-5 gap-1 bg-gray-100 dark:bg-gray-800 rounded-lg p-1">
          {[
            { id: 'documents' as const, icon: '📄', label: 'Docs' },
            { id: 'audio' as const, icon: '🎙️', label: 'Audio' },
            { id: 'quiz' as const, icon: '🎯', label: 'Quiz' },
            { id: 'visual' as const, icon: '🧠', label: 'Visual' },
            { id: 'writing' as const, icon: '✍️', label: 'Write' },
          ].map((tab) => (
            <button
              key={tab.id}
              onClick={() => handleTabChange(tab.id)}
              className={`px-2 py-1.5 text-xs font-medium rounded-md transition-colors flex flex-col items-center ${
                activeTab === tab.id
                  ? 'bg-white dark:bg-gray-700 text-gray-900 dark:text-white shadow-sm'
                  : 'text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white'
              }`}
            >
              <span>{tab.icon}</span>
              <span className="mt-0.5">{tab.label}</span>
            </button>
          ))}
        </div>
      </div>

      {/* Main Content - Scrollable */}
      <div className="flex-1 overflow-y-auto p-6 pb-12 space-y-6">
        {error && <ErrorMessage message={error} onDismiss={() => setError(null)} />}

        {/* Documents & Audio Tab Content */}
        {(activeTab === 'documents' || activeTab === 'audio') && (
        <>
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

        {/* Style Selection - Only for documents tab */}
        {activeTab === 'documents' && styleFormats.length > 0 && (
          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
              Output Style
            </label>
            <div className="grid grid-cols-3 gap-2">
              {styleFormats.slice(0, 6).map((format) => (
                <button
                  key={format.value}
                  onClick={() => setSelectedStyle(format.value)}
                  className={`px-2 py-1.5 text-xs rounded-md border text-left transition-colors ${
                    selectedStyle === format.value
                      ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/20 text-blue-700 dark:text-blue-300'
                      : 'border-gray-300 dark:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-800 text-gray-700 dark:text-gray-300'
                  }`}
                  title={format.description}
                >
                  {format.label}
                </button>
              ))}
            </div>
          </div>
        )}

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
            max={selectedSkill === 'feynman_curriculum' ? 45 : 30}
            className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
          />
          {selectedSkill === 'feynman_curriculum' && (
            <p className="mt-1 text-xs text-indigo-600 dark:text-indigo-400">
              4-part progressive teaching: Foundation → Building → First Principles → Mastery (recommended: 30-45 min)
            </p>
          )}
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
          {generating ? 'Generating...' : activeTab === 'audio' ? 'Generate Podcast' : 'Generate Document'}
        </Button>

        {/* Generated Text Content (Documents tab) */}
        {activeTab === 'documents' && (
          <ContentViewer
            generatedContent={generatedContent}
            contentSkillName={contentSkillName}
            selectedSkill={selectedSkill}
            topic={topic}
            selectedContentId={selectedContentId}
            contentGenerations={contentGenerations}
            onClear={() => setGeneratedContent('')}
            onSelectContent={(gen) => {
              setSelectedContentId(gen.content_id);
              setGeneratedContent(gen.content);
              setContentSkillName(gen.skill_name);
            }}
            onDeleteContent={async (contentId) => {
              try {
                await contentService.delete(contentId);
                loadContentGenerations();
                if (selectedContentId === contentId) {
                  setGeneratedContent('');
                  setSelectedContentId(null);
                }
              } catch (err) {
                console.error('Failed to delete:', err);
              }
            }}
            onQuizNav={(t, d) => { setQuizTopic(t); setQuizDifficulty(d); handleTabChange('quiz'); }}
            onVisualNav={(content) => { setVisualContentFromChat(content); handleTabChange('visual'); }}
          />
        )}

        </>
        )}

        {/* Audio History - Only for audio tab */}
        {activeTab === 'audio' && (
          <AudioHistory
            audioGenerations={audioGenerations}
            generatedScript={generatedScript}
            showScript={showScript}
            onHideScript={() => setShowScript(false)}
            onDelete={async (audioId) => {
              try {
                setAudioGenerations(prev => prev.filter(g => g.audio_id !== audioId));
                await audioService.delete(audioId);
                await loadAudioGenerations();
              } catch (err) {
                console.error('Failed to delete:', err);
                await loadAudioGenerations();
              }
            }}
          />
        )}

        {/* Quiz Panel */}
        {activeTab === 'quiz' && (
          <QuizPanel notebookId={notebookId} initialTopic={quizTopic} initialDifficulty={quizDifficulty} />
        )}

        {/* Visual Panel */}
        {activeTab === 'visual' && (
          <VisualPanel notebookId={notebookId} initialContent={visualContentFromChat} />
        )}

        {/* Writing Panel */}
        {activeTab === 'writing' && (
          <WritingPanel notebookId={notebookId} />
        )}

        {/* Voice Panel */}
        {/* Voice/Whisper moved to Audio tab - VoicePanel available for future use */}
      </div>
    </div>
  );
};
