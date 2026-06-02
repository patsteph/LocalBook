/**
 * StudioDrawer — Unified Studio interface (Tier 5, 2026-06-01).
 *
 * Replaces the fragmented Studio surfaces (ChatActionBar pill row,
 * Canvas overlay pills, LeftNav Studio drawer, Studio.tsx tabbed panel,
 * and various sub-panels) with ONE place users go for all generation.
 *
 * MVP: 5 most-used types (docs/audio/video/visual/quiz). Cards/PPTX/PDF
 * remain in the legacy pill row for now and will be migrated in a
 * follow-up turn.
 *
 * Architecture:
 *   - Top: type row (one chip per type, color-coded for fast recognition)
 *   - Body: shared fields (topic, voice register) + type-specific config
 *   - Bottom: Generate button + close
 *
 * Talks directly to existing APIs — no behind-the-scenes refactor needed.
 */
import React, { useState, useEffect, useCallback } from 'react';
import {
  FileText, Mic, Video, Palette, Target, X, Sparkles,
} from 'lucide-react';
import { contentService } from '../../services/content';
import { audioService } from '../../services/audio';
import { videoService } from '../../services/video';
import { quizService } from '../../services/quiz';
import { skillsService } from '../../services/skills';
import { useGenerateVisualToCanvas } from '../../hooks/useGenerateVisualToCanvas';
import { useCanvasItems } from '../canvas/CanvasContext';
import { Skill } from '../../types';

// Audio skill_id → human-readable format label (used for canvas-item titles).
const AUDIO_FORMAT_LABELS: Record<string, string> = {
  podcast_script: 'Conversation',
  debate: 'Debate Format',
  interview: 'Interview Format',
  storytelling: 'Story Format',
  feynman_curriculum: 'Feynman Lesson',
};

// ─── Types ──────────────────────────────────────────────────────────────────
type StudioType = 'docs' | 'audio' | 'video' | 'visual' | 'quiz';
type Register = 'auto' | 'measured' | 'engaged' | 'warm' | 'urgent';

interface StudioDrawerProps {
  notebookId: string | null;
  open: boolean;
  onClose: () => void;
  /** Optional starting type, e.g. when summoned with a preferred action. */
  initialType?: StudioType;
  /** Optional chat context for "From Chat" mode. */
  chatContext?: string;
  /** Toast helper from app shell. */
  onToast?: (kind: 'info' | 'error' | 'success', title: string, msg?: string) => void;
}

// ─── Type definitions row ───────────────────────────────────────────────────
const TYPE_DEFS: Array<{
  id: StudioType;
  icon: React.ReactNode;
  label: string;
  accent: string;
}> = [
  { id: 'docs', icon: <FileText className="w-3.5 h-3.5" />, label: 'Document', accent: 'blue' },
  { id: 'audio', icon: <Mic className="w-3.5 h-3.5" />, label: 'Audio', accent: 'purple' },
  { id: 'video', icon: <Video className="w-3.5 h-3.5" />, label: 'Video', accent: 'red' },
  { id: 'visual', icon: <Palette className="w-3.5 h-3.5" />, label: 'Visual', accent: 'amber' },
  { id: 'quiz', icon: <Target className="w-3.5 h-3.5" />, label: 'Quiz', accent: 'emerald' },
];

const REGISTERS: Register[] = ['auto', 'measured', 'engaged', 'warm', 'urgent'];

// ─── Component ──────────────────────────────────────────────────────────────
export const StudioDrawer: React.FC<StudioDrawerProps> = ({
  notebookId,
  open,
  onClose,
  initialType = 'docs',
  chatContext,
  onToast,
}) => {
  const [type, setType] = useState<StudioType>(initialType);
  const [topic, setTopic] = useState('');
  const [register, setRegister] = useState<Register>(
    () => (localStorage.getItem('lb-studio-register') as Register) || 'auto'
  );
  const [generating, setGenerating] = useState(false);

  // Per-type state — persisted via localStorage so prefs survive sessions.
  const [docsSkill, setDocsSkill] = useState(() => localStorage.getItem('lb-studio-docs-skill') || 'briefing');
  const [docsStyle, setDocsStyle] = useState(() => localStorage.getItem('lb-studio-docs-style') || 'professional');
  const [audioSkill, setAudioSkill] = useState(() => localStorage.getItem('lb-studio-audio-skill') || 'podcast_script');
  const [audioDuration, setAudioDuration] = useState(() => parseInt(localStorage.getItem('lb-studio-audio-dur') || '15'));
  const [audioVoices, setAudioVoices] = useState(() => localStorage.getItem('lb-studio-audio-voices') || 'mf');
  const [audioAccent, setAudioAccent] = useState(() => localStorage.getItem('lb-studio-audio-accent') || 'us');
  const [videoDuration, setVideoDuration] = useState(() => parseInt(localStorage.getItem('lb-studio-video-dur') || '5'));
  const [videoFormat, setVideoFormat] = useState<'explainer' | 'brief'>(() => (localStorage.getItem('lb-studio-video-format') as any) || 'explainer');
  const [videoNarrationStyle, setVideoNarrationStyle] = useState<'explainer' | 'narrative' | 'journalistic' | 'study_deep_dive'>(
    () => (localStorage.getItem('lb-studio-video-narration') as any) || 'explainer'
  );
  // Narrator gender stays at the user's last setting from the legacy bar
  // for now; we don't expose it in the MVP drawer to keep the surface clean.
  const videoNarratorGender = localStorage.getItem('lb-studio-video-narrator') || localStorage.getItem('lb-bar-video-narrator') || 'female';
  const [quizCount, setQuizCount] = useState(() => parseInt(localStorage.getItem('lb-studio-quiz-count') || '5'));
  const [quizDifficulty, setQuizDifficulty] = useState(() => localStorage.getItem('lb-studio-quiz-difficulty') || 'medium');

  const [textSkills, setTextSkills] = useState<Skill[]>([]);
  const [audioSkills, setAudioSkillList] = useState<Skill[]>([]);

  // Persist prefs whenever they change.
  useEffect(() => { localStorage.setItem('lb-studio-register', register); }, [register]);
  useEffect(() => { localStorage.setItem('lb-studio-docs-skill', docsSkill); }, [docsSkill]);
  useEffect(() => { localStorage.setItem('lb-studio-docs-style', docsStyle); }, [docsStyle]);
  useEffect(() => { localStorage.setItem('lb-studio-audio-skill', audioSkill); }, [audioSkill]);
  useEffect(() => { localStorage.setItem('lb-studio-audio-dur', String(audioDuration)); }, [audioDuration]);
  useEffect(() => { localStorage.setItem('lb-studio-audio-voices', audioVoices); }, [audioVoices]);
  useEffect(() => { localStorage.setItem('lb-studio-audio-accent', audioAccent); }, [audioAccent]);
  useEffect(() => { localStorage.setItem('lb-studio-video-dur', String(videoDuration)); }, [videoDuration]);
  useEffect(() => { localStorage.setItem('lb-studio-video-format', videoFormat); }, [videoFormat]);
  useEffect(() => { localStorage.setItem('lb-studio-video-narration', videoNarrationStyle); }, [videoNarrationStyle]);
  useEffect(() => { localStorage.setItem('lb-studio-quiz-count', String(quizCount)); }, [quizCount]);
  useEffect(() => { localStorage.setItem('lb-studio-quiz-difficulty', quizDifficulty); }, [quizDifficulty]);

  // Load skills lists.
  useEffect(() => {
    if (!open) return;
    skillsService.list().then((skills: Skill[]) => {
      const audioIds = ['podcast_script', 'debate', 'interview', 'storytelling', 'feynman_curriculum'];
      setTextSkills(skills.filter(s => !audioIds.includes(s.skill_id)));
      setAudioSkillList(skills.filter(s => audioIds.includes(s.skill_id)));
    }).catch(() => {
      // Skills service failure isn't fatal — user can still pick a default.
    });
  }, [open]);

  // Reset transient state on close.
  useEffect(() => {
    if (!open) {
      setTopic('');
      setGenerating(false);
    }
  }, [open]);

  const generateVisualToCanvas = useGenerateVisualToCanvas();
  // Canvas item methods — every generation from the drawer drops a tombstone
  // on the canvas with status='generating' BEFORE the API call returns, so
  // the user has something to watch progress on. Without this, long-running
  // generations (especially video) feel like the app froze.
  const { addCanvasItem, updateCanvasItem } = useCanvasItems();

  const handleGenerate = useCallback(async () => {
    if (!notebookId) return;
    setGenerating(true);

    const trimmedTopic = topic.trim() || undefined;
    const regOverride = register !== 'auto' ? register : undefined;
    const titleTopic = trimmedTopic
      ? (trimmedTopic.length > 50 ? trimmedTopic.substring(0, 47).trim() + '…' : trimmedTopic)
      : '';

    try {
      switch (type) {
        case 'docs': {
          const skillName = textSkills.find(s => s.skill_id === docsSkill)?.name || 'Document';
          const itemId = `doc-${Date.now()}`;
          addCanvasItem({
            id: itemId,
            type: 'document' as any,
            title: titleTopic ? `${skillName}: ${titleTopic}` : `Document: ${skillName}`,
            content: '',
            collapsed: true,
            status: 'generating',
            metadata: { notebookId, source: 'studio_drawer' } as any,
          });
          try {
            const result = await contentService.generate({
              notebook_id: notebookId,
              skill_id: docsSkill,
              topic: trimmedTopic,
              style: docsStyle,
              ...(chatContext ? { chat_context: chatContext } : {}),
              ...(regOverride ? { register: regOverride } : {}),
            });
            updateCanvasItem(itemId, {
              title: result.skill_name || skillName,
              content: result.content,
              status: 'complete',
              metadata: { notebookId, contentId: (result as any).content_id, source: 'studio_drawer' } as any,
            });
            window.dispatchEvent(new CustomEvent('contentUpdated'));
            onToast?.('success', 'Document ready');
          } catch (err) {
            updateCanvasItem(itemId, {
              status: 'error',
              metadata: { notebookId, errorMessage: err instanceof Error ? err.message : 'Generation failed', source: 'studio_drawer' } as any,
            });
            throw err;
          }
          break;
        }
        case 'audio': {
          const voiceMap: Record<string, [string, string]> = { mf: ['male', 'female'], fm: ['female', 'male'], mm: ['male', 'male'], ff: ['female', 'female'] };
          const [h1, h2] = voiceMap[audioVoices] || ['male', 'female'];
          const formatLabel = AUDIO_FORMAT_LABELS[audioSkill] || 'Audio';
          const itemId = `audio-${Date.now()}`;
          addCanvasItem({
            id: itemId,
            type: 'audio' as any,
            title: titleTopic ? `${formatLabel}: ${titleTopic}` : `Podcast: ${formatLabel}`,
            content: '',
            collapsed: true,
            status: 'generating',
            metadata: { notebookId, source: 'studio_drawer' } as any,
          });
          try {
            const result = await audioService.generate({
              notebook_id: notebookId,
              topic: trimmedTopic,
              duration_minutes: audioDuration,
              skill_id: audioSkill,
              host1_gender: h1,
              host2_gender: h2,
              accent: audioAccent,
              ...(chatContext ? { chat_context: chatContext } : {}),
              ...(regOverride ? { register: regOverride } : {}),
            });
            updateCanvasItem(itemId, {
              status: 'processing',
              metadata: { audioId: result.audio_id, notebookId, source: 'studio_drawer' } as any,
            });
            window.dispatchEvent(new CustomEvent('audioUpdated'));
            onToast?.('success', 'Podcast generating');
          } catch (err) {
            updateCanvasItem(itemId, {
              status: 'error',
              metadata: { notebookId, errorMessage: err instanceof Error ? err.message : 'Generation failed', source: 'studio_drawer' } as any,
            });
            throw err;
          }
          break;
        }
        case 'video': {
          const itemId = `video-${Date.now()}`;
          const formatLabel = videoFormat === 'brief' ? 'Brief' : 'Explainer';
          addCanvasItem({
            id: itemId,
            type: 'video' as any,
            title: titleTopic ? `Video: ${titleTopic}` : `Video: ${formatLabel}`,
            content: '',
            collapsed: true,
            status: 'generating',
            metadata: { notebookId, source: 'studio_drawer' } as any,
          });
          try {
            const result = await videoService.generate({
              notebook_id: notebookId,
              topic: trimmedTopic,
              duration_minutes: videoDuration,
              visual_style: 'classic',
              narrator_gender: videoNarratorGender,
              accent: 'us',
              format_type: videoFormat,
              narration_style: videoNarrationStyle,
              ...(chatContext ? { chat_context: chatContext } : {}),
              ...(regOverride ? { register: regOverride } : {}),
            });
            updateCanvasItem(itemId, {
              status: 'processing',
              metadata: { videoId: result.video_id, notebookId, source: 'studio_drawer' } as any,
            });
            // Poll for completion — video runs in the background on the server.
            const pollInterval = setInterval(async () => {
              try {
                const status = await videoService.getStatus(result.video_id);
                if (status.status === 'completed') {
                  clearInterval(pollInterval);
                  updateCanvasItem(itemId, {
                    status: 'complete',
                    metadata: { videoId: result.video_id, notebookId, source: 'studio_drawer', errorMessage: null } as any,
                  });
                  window.dispatchEvent(new CustomEvent('videoUpdated'));
                } else if (status.status === 'failed') {
                  clearInterval(pollInterval);
                  updateCanvasItem(itemId, {
                    status: 'error',
                    metadata: { videoId: result.video_id, notebookId, source: 'studio_drawer', errorMessage: status.error_message || 'Video generation failed' } as any,
                  });
                } else {
                  updateCanvasItem(itemId, {
                    metadata: { videoId: result.video_id, notebookId, source: 'studio_drawer', errorMessage: status.error_message } as any,
                  });
                }
              } catch (pollErr) {
                console.warn('[StudioDrawer] video status poll failed (non-fatal):', pollErr);
              }
            }, 4000);
            onToast?.('success', 'Video generating');
          } catch (err) {
            updateCanvasItem(itemId, {
              status: 'error',
              metadata: { notebookId, errorMessage: err instanceof Error ? err.message : 'Generation failed', source: 'studio_drawer' } as any,
            });
            throw err;
          }
          break;
        }
        case 'visual':
          if (!trimmedTopic) {
            onToast?.('error', 'Topic required for visual');
            return;
          }
          // generateVisualToCanvas already drops its own canvas item.
          await generateVisualToCanvas(notebookId, trimmedTopic, { source: 'studio_bar' });
          break;
        case 'quiz': {
          const itemId = `quiz-${Date.now()}`;
          addCanvasItem({
            id: itemId,
            type: 'quiz' as any,
            title: titleTopic ? `Quiz: ${titleTopic}` : `Quiz (${quizCount} questions)`,
            content: '',
            collapsed: true,
            status: 'generating',
            metadata: { notebookId, source: 'studio_drawer' } as any,
          });
          try {
            const result = await quizService.generate(notebookId, quizCount, quizDifficulty, trimmedTopic, chatContext);
            updateCanvasItem(itemId, {
              status: 'complete',
              content: '',
              metadata: { notebookId, quiz: result, source: 'studio_drawer' } as any,
            });
            // Library auto-refresh hook (Tier 5).
            window.dispatchEvent(new CustomEvent('quizzesUpdated'));
            onToast?.('success', 'Quiz ready');
          } catch (err) {
            updateCanvasItem(itemId, {
              status: 'error',
              metadata: { notebookId, errorMessage: err instanceof Error ? err.message : 'Generation failed', source: 'studio_drawer' } as any,
            });
            throw err;
          }
          break;
        }
      }
      onClose();
    } catch (err) {
      console.error('[StudioDrawer] generate failed:', err);
      onToast?.('error', 'Generation failed', err instanceof Error ? err.message : 'Unknown error');
    } finally {
      setGenerating(false);
    }
  }, [notebookId, topic, register, type, docsSkill, docsStyle, audioSkill, audioDuration, audioVoices, audioAccent, videoDuration, videoFormat, videoNarrationStyle, videoNarratorGender, quizCount, quizDifficulty, chatContext, onClose, onToast, generateVisualToCanvas, addCanvasItem, updateCanvasItem, textSkills]);

  if (!open) return null;

  const accentClasses = (t: StudioType, active: boolean): string => {
    const d = TYPE_DEFS.find(td => td.id === t);
    const a = d?.accent || 'blue';
    if (!active) return 'border-gray-200 dark:border-gray-700 text-gray-500 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-800';
    return {
      blue: 'border-blue-500 bg-blue-50 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300',
      purple: 'border-purple-500 bg-purple-50 dark:bg-purple-900/30 text-purple-700 dark:text-purple-300',
      red: 'border-red-500 bg-red-50 dark:bg-red-900/30 text-red-700 dark:text-red-300',
      amber: 'border-amber-500 bg-amber-50 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300',
      emerald: 'border-emerald-500 bg-emerald-50 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-300',
    }[a] || '';
  };

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-40 bg-black/30 dark:bg-black/50 transition-opacity"
        onClick={onClose}
      />
      {/* Drawer */}
      <div className="fixed inset-x-0 bottom-0 z-50 max-h-[80vh] overflow-y-auto bg-white dark:bg-gray-900 border-t border-gray-200 dark:border-gray-700 shadow-2xl rounded-t-2xl">
        <div className="mx-auto max-w-3xl p-4 space-y-3">
          {/* Header */}
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Sparkles className="w-4 h-4 text-amber-500" />
              <h2 className="text-sm font-semibold text-gray-900 dark:text-gray-100">Studio</h2>
            </div>
            <button
              onClick={onClose}
              className="p-1 rounded-md text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-800"
              title="Close"
            >
              <X className="w-4 h-4" />
            </button>
          </div>

          {/* Type row */}
          <div className="flex flex-wrap gap-1.5">
            {TYPE_DEFS.map((td) => (
              <button
                key={td.id}
                onClick={() => setType(td.id)}
                className={`flex items-center gap-1.5 px-2.5 py-1.5 text-[11px] rounded-lg border transition-colors ${accentClasses(td.id, type === td.id)}`}
              >
                {td.icon}
                <span>{td.label}</span>
              </button>
            ))}
          </div>

          {/* Shared: Topic */}
          <div>
            <label className="block text-[10px] uppercase tracking-wide font-medium text-gray-500 dark:text-gray-400 mb-1">
              Topic <span className="normal-case font-normal text-gray-400">(optional for most, required for visual)</span>
            </label>
            <textarea
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              rows={2}
              placeholder={chatContext ? 'e.g. @chat transformers attention' : 'What should this be about?'}
              className="w-full text-xs px-2 py-1.5 border border-gray-200 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 placeholder-gray-400 focus:outline-none focus:ring-1 focus:ring-blue-400 resize-y"
            />
          </div>

          {/* Shared: Voice register (docs/audio/video) */}
          {(type === 'docs' || type === 'audio' || type === 'video') && (
            <div>
              <label className="block text-[10px] uppercase tracking-wide font-medium text-gray-500 dark:text-gray-400 mb-1">
                Voice <span className="normal-case font-normal text-gray-400">(auto picks per type)</span>
              </label>
              <div className="flex flex-wrap gap-1">
                {REGISTERS.map((r) => (
                  <button
                    key={r}
                    onClick={() => setRegister(r)}
                    className={`px-2 py-1 text-[10px] rounded-lg border transition-colors capitalize ${
                      register === r
                        ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/20 text-blue-700 dark:text-blue-300'
                        : 'border-gray-200 dark:border-gray-600 text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700'
                    }`}
                  >
                    {r}
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Per-type config */}
          {type === 'docs' && (
            <div className="space-y-2">
              <div>
                <label className="block text-[10px] uppercase tracking-wide font-medium text-gray-500 dark:text-gray-400 mb-1">Content Type</label>
                <select
                  value={docsSkill}
                  onChange={(e) => setDocsSkill(e.target.value)}
                  className="w-full px-2 py-1.5 text-xs border border-gray-200 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                >
                  {textSkills.map((s) => (
                    <option key={s.skill_id} value={s.skill_id}>{s.name}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-[10px] uppercase tracking-wide font-medium text-gray-500 dark:text-gray-400 mb-1">Style</label>
                <div className="flex flex-wrap gap-1">
                  {['professional', 'academic', 'casual', 'technical', 'blog'].map((s) => (
                    <button
                      key={s}
                      onClick={() => setDocsStyle(s)}
                      className={`px-2 py-0.5 text-[10px] rounded-lg border transition-colors capitalize ${
                        docsStyle === s
                          ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/20 text-blue-700 dark:text-blue-300'
                          : 'border-gray-200 dark:border-gray-600 text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700'
                      }`}
                    >
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          )}

          {type === 'audio' && (
            <div className="space-y-2">
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="block text-[10px] uppercase tracking-wide font-medium text-gray-500 dark:text-gray-400 mb-1">Style</label>
                  <select
                    value={audioSkill}
                    onChange={(e) => setAudioSkill(e.target.value)}
                    className="w-full px-2 py-1.5 text-xs border border-gray-200 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                  >
                    {audioSkills.map((s) => (
                      <option key={s.skill_id} value={s.skill_id}>{s.name}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="block text-[10px] uppercase tracking-wide font-medium text-gray-500 dark:text-gray-400 mb-1">Duration ({audioDuration}m)</label>
                  <input
                    type="range"
                    min={5}
                    max={45}
                    step={5}
                    value={audioDuration}
                    onChange={(e) => setAudioDuration(parseInt(e.target.value))}
                    className="w-full"
                  />
                </div>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="block text-[10px] uppercase tracking-wide font-medium text-gray-500 dark:text-gray-400 mb-1">Hosts</label>
                  <div className="flex gap-1">
                    {(['mf', 'fm', 'mm', 'ff'] as const).map((v) => (
                      <button
                        key={v}
                        onClick={() => setAudioVoices(v)}
                        className={`flex-1 px-1 py-1 text-[10px] rounded-lg border transition-colors uppercase ${
                          audioVoices === v
                            ? 'border-purple-500 bg-purple-50 dark:bg-purple-900/20 text-purple-700 dark:text-purple-300'
                            : 'border-gray-200 dark:border-gray-600 text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700'
                        }`}
                      >
                        {v.split('').join('/')}
                      </button>
                    ))}
                  </div>
                </div>
                <div>
                  <label className="block text-[10px] uppercase tracking-wide font-medium text-gray-500 dark:text-gray-400 mb-1">Accent</label>
                  <select
                    value={audioAccent}
                    onChange={(e) => setAudioAccent(e.target.value)}
                    className="w-full px-2 py-1.5 text-xs border border-gray-200 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                  >
                    {['us', 'uk', 'es', 'fr', 'de', 'it', 'pt', 'ja', 'zh'].map((a) => (
                      <option key={a} value={a}>{a.toUpperCase()}</option>
                    ))}
                  </select>
                </div>
              </div>
            </div>
          )}

          {type === 'video' && (
            <div className="space-y-2">
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="block text-[10px] uppercase tracking-wide font-medium text-gray-500 dark:text-gray-400 mb-1">Format</label>
                  <div className="flex gap-1">
                    {(['explainer', 'brief'] as const).map((v) => (
                      <button
                        key={v}
                        onClick={() => setVideoFormat(v)}
                        className={`flex-1 px-2 py-1 text-[10px] rounded-lg border transition-colors capitalize ${
                          videoFormat === v
                            ? 'border-red-500 bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-300'
                            : 'border-gray-200 dark:border-gray-600 text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700'
                        }`}
                      >
                        {v}
                      </button>
                    ))}
                  </div>
                </div>
                <div>
                  <label className="block text-[10px] uppercase tracking-wide font-medium text-gray-500 dark:text-gray-400 mb-1">Duration ({videoDuration}m)</label>
                  <input
                    type="range"
                    min={1}
                    max={10}
                    step={1}
                    value={videoDuration}
                    onChange={(e) => setVideoDuration(parseInt(e.target.value))}
                    className="w-full"
                  />
                </div>
              </div>
              <div>
                <label className="block text-[10px] uppercase tracking-wide font-medium text-gray-500 dark:text-gray-400 mb-1">Narration Style</label>
                <div className="flex flex-wrap gap-1">
                  {(['explainer', 'narrative', 'journalistic', 'study_deep_dive'] as const).map((v) => (
                    <button
                      key={v}
                      onClick={() => setVideoNarrationStyle(v)}
                      className={`px-2 py-0.5 text-[10px] rounded-lg border transition-colors capitalize ${
                        videoNarrationStyle === v
                          ? 'border-red-500 bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-300'
                          : 'border-gray-200 dark:border-gray-600 text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700'
                      }`}
                    >
                      {v.replace(/_/g, ' ')}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          )}

          {type === 'visual' && (
            <div className="text-[11px] text-gray-500 dark:text-gray-400 leading-relaxed">
              Describe what you want to see. The system will pick the right path —
              clean illustration, structured diagram, or art-directed render.
              Type a multi-line description above; longer + more specific = better.
            </div>
          )}

          {type === 'quiz' && (
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="block text-[10px] uppercase tracking-wide font-medium text-gray-500 dark:text-gray-400 mb-1">Questions ({quizCount})</label>
                <input
                  type="range"
                  min={3}
                  max={20}
                  step={1}
                  value={quizCount}
                  onChange={(e) => setQuizCount(parseInt(e.target.value))}
                  className="w-full"
                />
              </div>
              <div>
                <label className="block text-[10px] uppercase tracking-wide font-medium text-gray-500 dark:text-gray-400 mb-1">Difficulty</label>
                <div className="flex gap-1">
                  {['easy', 'medium', 'hard'].map((d) => (
                    <button
                      key={d}
                      onClick={() => setQuizDifficulty(d)}
                      className={`flex-1 px-2 py-1 text-[10px] rounded-lg border transition-colors capitalize ${
                        quizDifficulty === d
                          ? 'border-emerald-500 bg-emerald-50 dark:bg-emerald-900/20 text-emerald-700 dark:text-emerald-300'
                          : 'border-gray-200 dark:border-gray-600 text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700'
                      }`}
                    >
                      {d}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          )}

          {/* Generate */}
          <div className="flex items-center justify-end gap-2 pt-2 border-t border-gray-200 dark:border-gray-700">
            <button
              onClick={onClose}
              disabled={generating}
              className="px-3 py-1.5 text-xs rounded-lg border border-gray-200 dark:border-gray-600 text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800 disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              onClick={handleGenerate}
              disabled={generating || !notebookId}
              className={`px-3 py-1.5 text-xs rounded-lg font-medium ${
                generating
                  ? 'bg-blue-300 text-white cursor-wait'
                  : 'bg-blue-600 hover:bg-blue-700 text-white disabled:bg-gray-300 dark:disabled:bg-gray-700 disabled:cursor-not-allowed'
              }`}
            >
              {generating ? 'Generating…' : 'Generate'}
            </button>
          </div>
        </div>
      </div>
    </>
  );
};
