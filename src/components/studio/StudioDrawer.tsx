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
import { emitEvent } from '../../lib/events';
import {
  FileText, Mic, Video, Palette, Target, Layers, X, Sparkles, GitCompare, Telescope,
} from 'lucide-react';
import { contentService } from '../../services/content';
import { audioService } from '../../services/audio';
import { videoService } from '../../services/video';
import { quizService } from '../../services/quiz';
import { skillsService } from '../../services/skills';
import { sourceService } from '../../services/sources';
import { comparisonService } from '../../services/comparison';
import { synthesisService } from '../../services/synthesis';
import { useGenerateVisualToCanvas } from '../../hooks/useGenerateVisualToCanvas';
import { useCanvasItems } from '../canvas/CanvasContext';
import { Skill, Source } from '../../types';

// Audio skill_id → human-readable format label (used for canvas-item titles).
const AUDIO_FORMAT_LABELS: Record<string, string> = {
  podcast_script: 'Conversation',
  debate: 'Debate Format',
  interview: 'Interview Format',
  storytelling: 'Story Format',
  feynman_curriculum: 'Feynman Lesson',
};

// ─── Types ──────────────────────────────────────────────────────────────────
type StudioType = 'docs' | 'audio' | 'video' | 'visual' | 'quiz' | 'cards' | 'comparison' | 'perspectives' | 'dashboard' | 'deep-dive';
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
  { id: 'cards', icon: <Layers className="w-3.5 h-3.5" />, label: 'Cards', accent: 'fuchsia' },
  { id: 'comparison', icon: <GitCompare className="w-3.5 h-3.5" />, label: 'Compare', accent: 'cyan' },
  { id: 'perspectives', icon: <Telescope className="w-3.5 h-3.5" />, label: 'Perspectives', accent: 'sky' },
  { id: 'dashboard', icon: <Sparkles className="w-3.5 h-3.5" />, label: 'Dashboard', accent: 'indigo' },
  { id: 'deep-dive', icon: <Telescope className="w-3.5 h-3.5" />, label: 'Deep dive', accent: 'violet' },
];

const REGISTERS: Register[] = ['auto', 'measured', 'engaged', 'warm', 'urgent'];

// Accent / Language options shared by Audio and Video. Values match Kokoro
// voice catalog locale codes; labels show language name for non-English so
// the dropdown reads naturally ("Spanish" beats "ES").
const ACCENT_LANGUAGE_OPTIONS: ReadonlyArray<readonly [string, string]> = [
  ['us', 'American'],
  ['uk', 'British'],
  ['es', 'Spanish'],
  ['fr', 'French'],
  ['de', 'German'],
  ['hi', 'Hindi'],
  ['it', 'Italian'],
  ['ja', 'Japanese'],
  ['pt', 'Portuguese'],
  ['zh', 'Chinese'],
] as const;

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
  const [videoNarratorGender, setVideoNarratorGender] = useState<'female' | 'male'>(
    () => (localStorage.getItem('lb-studio-video-narrator') || localStorage.getItem('lb-bar-video-narrator')) === 'male' ? 'male' : 'female'
  );
  const [videoAccent, setVideoAccent] = useState(
    () => localStorage.getItem('lb-studio-video-accent') || localStorage.getItem('lb-bar-video-accent') || 'us'
  );
  const [quizCount, setQuizCount] = useState(() => parseInt(localStorage.getItem('lb-studio-quiz-count') || '5'));
  const [quizDifficulty, setQuizDifficulty] = useState(() => localStorage.getItem('lb-studio-quiz-difficulty') || 'medium');
  // Phase 11 — opt-in interactive HTML quiz (iframe sandbox + postMessage).
  const [quizInteractive, setQuizInteractive] = useState(() => localStorage.getItem('lb-studio-quiz-interactive') === '1');
  // Cards (flash cards). Tutor accent kept as us/uk only — the underlying
  // flashcards TTS pipeline only ships those two voices.
  const [cardsCount, setCardsCount] = useState(() => parseInt(localStorage.getItem('lb-studio-cards-count') || localStorage.getItem('lb-bar-cards-count') || '10'));
  const [cardsDifficulty, setCardsDifficulty] = useState<'easy' | 'medium' | 'hard'>(
    () => (localStorage.getItem('lb-studio-cards-diff') || localStorage.getItem('lb-bar-cards-diff') || 'medium') as any
  );
  const [cardsTutorGender, setCardsTutorGender] = useState<'female' | 'male'>(
    () => ((localStorage.getItem('lb-studio-cards-tutor-gender') || localStorage.getItem('lb-bar-cards-tutor-gender')) === 'male' ? 'male' : 'female')
  );
  const [cardsTutorAccent, setCardsTutorAccent] = useState<'us' | 'uk'>(
    () => ((localStorage.getItem('lb-studio-cards-tutor-accent') || localStorage.getItem('lb-bar-cards-tutor-accent')) === 'uk' ? 'uk' : 'us')
  );
  const [cardsTutorAutoplay, setCardsTutorAutoplay] = useState(
    () => (localStorage.getItem('lb-studio-cards-tutor-autoplay') ?? localStorage.getItem('lb-bar-cards-tutor-autoplay')) !== 'false'
  );
  const [cardsIncludeVisuals, setCardsIncludeVisuals] = useState(
    () => (localStorage.getItem('lb-studio-cards-visuals') || localStorage.getItem('lb-bar-cards-visuals')) === 'true'
  );

  const [textSkills, setTextSkills] = useState<Skill[]>([]);
  const [audioSkills, setAudioSkillList] = useState<Skill[]>([]);

  // Phase 4 comparison drawer state.
  const [availableSources, setAvailableSources] = useState<Source[]>([]);
  const [compareSourceA, setCompareSourceA] = useState<string>('');
  const [compareSourceB, setCompareSourceB] = useState<string>('');
  const [compareFocus, setCompareFocus] = useState<string>('');
  // Phase 12 perspectives drawer state.
  const [perspectivesQuery, setPerspectivesQuery] = useState<string>(() => localStorage.getItem('lb-studio-perspectives-query') || '');
  const [perspectivesCrossNotebook, setPerspectivesCrossNotebook] = useState<boolean>(() => localStorage.getItem('lb-studio-perspectives-cross') === '1');
  useEffect(() => { localStorage.setItem('lb-studio-perspectives-query', perspectivesQuery); }, [perspectivesQuery]);
  useEffect(() => { localStorage.setItem('lb-studio-perspectives-cross', perspectivesCrossNotebook ? '1' : '0'); }, [perspectivesCrossNotebook]);
  // Phase 13 — deep-dive drawer state.
  const [deepDiveEntity, setDeepDiveEntity] = useState<string>(() => localStorage.getItem('lb-studio-deepdive-entity') || '');
  const [deepDiveCrossNotebook, setDeepDiveCrossNotebook] = useState<boolean>(() => localStorage.getItem('lb-studio-deepdive-cross') !== '0');
  useEffect(() => { localStorage.setItem('lb-studio-deepdive-entity', deepDiveEntity); }, [deepDiveEntity]);
  useEffect(() => { localStorage.setItem('lb-studio-deepdive-cross', deepDiveCrossNotebook ? '1' : '0'); }, [deepDiveCrossNotebook]);

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
  useEffect(() => { localStorage.setItem('lb-studio-video-narrator', videoNarratorGender); }, [videoNarratorGender]);
  useEffect(() => { localStorage.setItem('lb-studio-video-accent', videoAccent); }, [videoAccent]);
  useEffect(() => { localStorage.setItem('lb-studio-quiz-count', String(quizCount)); }, [quizCount]);
  useEffect(() => { localStorage.setItem('lb-studio-quiz-difficulty', quizDifficulty); }, [quizDifficulty]);
  useEffect(() => { localStorage.setItem('lb-studio-quiz-interactive', quizInteractive ? '1' : '0'); }, [quizInteractive]);
  useEffect(() => { localStorage.setItem('lb-studio-cards-count', String(cardsCount)); }, [cardsCount]);
  useEffect(() => { localStorage.setItem('lb-studio-cards-diff', cardsDifficulty); }, [cardsDifficulty]);
  useEffect(() => { localStorage.setItem('lb-studio-cards-tutor-gender', cardsTutorGender); }, [cardsTutorGender]);
  useEffect(() => { localStorage.setItem('lb-studio-cards-tutor-accent', cardsTutorAccent); }, [cardsTutorAccent]);
  useEffect(() => { localStorage.setItem('lb-studio-cards-tutor-autoplay', String(cardsTutorAutoplay)); }, [cardsTutorAutoplay]);
  useEffect(() => { localStorage.setItem('lb-studio-cards-visuals', String(cardsIncludeVisuals)); }, [cardsIncludeVisuals]);

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

  // Phase 4 — fetch the notebook's sources when the comparison type opens.
  useEffect(() => {
    if (!open || type !== 'comparison' || !notebookId) return;
    sourceService.list(notebookId).then(setAvailableSources).catch(() => {
      // Non-fatal — user sees an empty dropdown and a fallback hint.
    });
  }, [open, type, notebookId]);

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
            emitEvent('contentUpdated');
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
            emitEvent('audioUpdated');
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
              accent: videoAccent,
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
                  emitEvent('videoUpdated');
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
        case 'cards': {
          // Cards drop a flashcards canvas item with status='generating';
          // FlashcardsCanvasTile.tsx reads the metadata and self-generates
          // the deck. Same contract the old ChatActionBar used.
          const itemId = `flashcards-${Date.now()}`;
          const cardTopic = trimmedTopic || titleTopic || '';
          const title = cardTopic
            ? `Flash Cards: ${cardTopic.length > 50 ? cardTopic.substring(0, 47).trim() + '…' : cardTopic}`
            : `Flash Cards (${cardsCount} ${cardsDifficulty})`;
          addCanvasItem({
            id: itemId,
            type: 'flashcards' as any,
            title,
            content: '',
            collapsed: false,
            status: 'generating',
            metadata: {
              notebookId,
              topic: cardTopic,
              difficulty: cardsDifficulty,
              count: cardsCount,
              tutorGender: cardsTutorGender,
              tutorAccent: cardsTutorAccent,
              tutorAutoplay: cardsTutorAutoplay,
              includeVisuals: cardsIncludeVisuals,
              source: 'studio_drawer',
              ...(chatContext ? { chatContext } : {}),
            } as any,
          });
          // No await — the tile owns its lifecycle from here.
          onToast?.('success', 'Flash cards generating');
          break;
        }
        case 'dashboard': {
          const itemId = `dashboard-${Date.now()}`;
          addCanvasItem({
            id: itemId,
            type: 'document' as any,
            title: 'Notebook dashboard',
            content: '',
            collapsed: false,
            status: 'generating',
            metadata: { notebookId, source: 'studio_drawer' } as any,
          });
          try {
            const result = await synthesisService.getNotebookDashboard(notebookId);
            updateCanvasItem(itemId, {
              status: 'complete',
              metadata: { notebookId, source: 'studio_drawer', synthesis_html: result.html } as any,
            });
            onToast?.('success', 'Dashboard ready');
          } catch (err) {
            updateCanvasItem(itemId, {
              status: 'error',
              metadata: { notebookId, errorMessage: err instanceof Error ? err.message : 'Generation failed', source: 'studio_drawer' } as any,
            });
            throw err;
          }
          break;
        }
        case 'deep-dive': {
          const ent = deepDiveEntity.trim();
          if (!ent) {
            onToast?.('error', 'Entity required', 'Type the name of the person, paper, podcast, or topic to deep-dive on.');
            return;
          }
          const itemId = `deepdive-${Date.now()}`;
          addCanvasItem({
            id: itemId,
            type: 'document' as any,
            title: `Deep dive: ${ent.slice(0, 60)}${ent.length > 60 ? '…' : ''}`,
            content: '',
            collapsed: false,
            status: 'generating',
            metadata: { notebookId, source: 'studio_drawer' } as any,
          });
          try {
            const result = await synthesisService.findDeepDive(ent, notebookId, deepDiveCrossNotebook);
            updateCanvasItem(itemId, {
              status: 'complete',
              metadata: { notebookId, source: 'studio_drawer', synthesis_html: result.html, perspectives: result.perspectives } as any,
            });
            onToast?.('success', 'Deep dive ready');
          } catch (err) {
            updateCanvasItem(itemId, {
              status: 'error',
              metadata: { notebookId, errorMessage: err instanceof Error ? err.message : 'Generation failed', source: 'studio_drawer' } as any,
            });
            throw err;
          }
          break;
        }
        case 'perspectives': {
          const q = perspectivesQuery.trim();
          if (!q) {
            onToast?.('error', 'Topic required', 'Type a topic or question to gather perspectives on.');
            return;
          }
          const itemId = `perspectives-${Date.now()}`;
          addCanvasItem({
            id: itemId,
            type: 'document' as any,
            title: `Perspectives: ${q.slice(0, 60)}${q.length > 60 ? '…' : ''}`,
            content: '',
            collapsed: false,
            status: 'generating',
            metadata: { notebookId, source: 'studio_drawer' } as any,
          });
          try {
            const result = await synthesisService.findPerspectives(
              q, notebookId, perspectivesCrossNotebook,
            );
            updateCanvasItem(itemId, {
              status: 'complete',
              content: '',
              metadata: {
                notebookId,
                source: 'studio_drawer',
                synthesis_html: result.html,
                perspectives: result.perspectives,
              } as any,
            });
            onToast?.('success', 'Perspectives ready');
          } catch (err) {
            updateCanvasItem(itemId, {
              status: 'error',
              metadata: { notebookId, errorMessage: err instanceof Error ? err.message : 'Generation failed', source: 'studio_drawer' } as any,
            });
            throw err;
          }
          break;
        }
        case 'comparison': {
          if (!compareSourceA || !compareSourceB) {
            onToast?.('error', 'Pick two sources', 'Both Source A and Source B are required.');
            return;
          }
          if (compareSourceA === compareSourceB) {
            onToast?.('error', 'Pick different sources', 'Source A and Source B must differ.');
            return;
          }
          const itemId = `comparison-${Date.now()}`;
          const titleA = availableSources.find(s => s.id === compareSourceA)?.filename || 'Source A';
          const titleB = availableSources.find(s => s.id === compareSourceB)?.filename || 'Source B';
          addCanvasItem({
            id: itemId,
            type: 'comparison' as any,
            title: `${titleA} vs ${titleB}`,
            content: '',
            collapsed: false,
            status: 'generating',
            metadata: { notebookId, source: 'studio_drawer' } as any,
          });
          try {
            const artifact = await comparisonService.generate(
              notebookId,
              compareSourceA,
              compareSourceB,
              compareFocus.trim() || undefined,
            );
            updateCanvasItem(itemId, {
              status: 'complete',
              metadata: {
                notebookId,
                source: 'studio_drawer',
                comparison: artifact.payload,
                artifact,
              } as any,
            });
            onToast?.('success', 'Comparison ready');
          } catch (err) {
            updateCanvasItem(itemId, {
              status: 'error',
              metadata: { notebookId, errorMessage: err instanceof Error ? err.message : 'Generation failed', source: 'studio_drawer' } as any,
            });
            throw err;
          }
          break;
        }
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
            // Phase 11 — when the interactive toggle is on, compose the
            // sandbox-iframe HTML page server-side and stash on metadata.
            // CanvasItemCard dispatches via the InteractiveHtml renderer
            // when this field is present; otherwise falls back to the
            // existing StudioQuizBlock path (back-compat).
            let interactiveHtml: string | undefined;
            if (quizInteractive) {
              try {
                interactiveHtml = await quizService.toInteractiveHtml(result.questions, result.topic);
              } catch (e) {
                console.warn('[StudioDrawer] interactive quiz compose failed; falling back:', e);
              }
            }
            updateCanvasItem(itemId, {
              status: 'complete',
              content: '',
              metadata: {
                notebookId,
                quiz: result,
                source: 'studio_drawer',
                ...(interactiveHtml ? { interactive_html: interactiveHtml } : {}),
              } as any,
            });
            // Library auto-refresh hook (Tier 5).
            emitEvent('quizzesUpdated');
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
  }, [notebookId, topic, register, type, docsSkill, docsStyle, audioSkill, audioDuration, audioVoices, audioAccent, videoDuration, videoFormat, videoNarrationStyle, videoNarratorGender, videoAccent, quizCount, quizDifficulty, quizInteractive, cardsCount, cardsDifficulty, cardsTutorGender, cardsTutorAccent, cardsTutorAutoplay, cardsIncludeVisuals, perspectivesQuery, perspectivesCrossNotebook, deepDiveEntity, deepDiveCrossNotebook, compareSourceA, compareSourceB, compareFocus, availableSources, chatContext, onClose, onToast, generateVisualToCanvas, addCanvasItem, updateCanvasItem, textSkills]);

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
      fuchsia: 'border-fuchsia-500 bg-fuchsia-50 dark:bg-fuchsia-900/30 text-fuchsia-700 dark:text-fuchsia-300',
    }[a] || '';
  };

  return (
    <>
      {/* Backdrop — covers only the parent positioning context (the canvas
          area), not the whole viewport. This keeps LeftNav + top nav
          visible and interactive while the drawer is open. */}
      <div
        className="absolute inset-0 z-40 bg-black/20 dark:bg-black/40 transition-opacity"
        onClick={onClose}
      />
      {/* Drawer — slides up from the bottom of the parent (the canvas
          area), capped at 85% of the parent's height. Not fixed-viewport. */}
      <div className="absolute inset-x-0 bottom-0 z-50 max-h-[85%] overflow-y-auto bg-white dark:bg-gray-900 border-t border-gray-200 dark:border-gray-700 shadow-2xl rounded-t-2xl">
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

          {/* Type rows — legacy generators on row 1, v2.0 synthesis types on row 2. */}
          {(() => {
            const LEGACY_IDS: StudioType[] = ['docs', 'audio', 'video', 'visual', 'quiz', 'cards'];
            const legacy = TYPE_DEFS.filter(td => LEGACY_IDS.includes(td.id));
            const synthesis = TYPE_DEFS.filter(td => !LEGACY_IDS.includes(td.id));
            const renderRow = (defs: typeof TYPE_DEFS) => (
              <div className="flex flex-wrap gap-1.5">
                {defs.map((td) => (
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
            );
            return (
              <div className="space-y-1.5">
                {renderRow(legacy)}
                {renderRow(synthesis)}
              </div>
            );
          })()}

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
                  <label className="block text-[10px] uppercase tracking-wide font-medium text-gray-500 dark:text-gray-400 mb-1">Accent / Language</label>
                  <select
                    value={audioAccent}
                    onChange={(e) => setAudioAccent(e.target.value)}
                    className="w-full px-2 py-1.5 text-xs border border-gray-200 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                  >
                    {ACCENT_LANGUAGE_OPTIONS.map(([val, label]) => (
                      <option key={val} value={val}>{label}</option>
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
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="block text-[10px] uppercase tracking-wide font-medium text-gray-500 dark:text-gray-400 mb-1">Narrator</label>
                  <div className="flex gap-1">
                    {(['female', 'male'] as const).map((g) => (
                      <button
                        key={g}
                        onClick={() => setVideoNarratorGender(g)}
                        className={`flex-1 px-2 py-1 text-[10px] rounded-lg border transition-colors capitalize ${
                          videoNarratorGender === g
                            ? 'border-red-500 bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-300'
                            : 'border-gray-200 dark:border-gray-600 text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700'
                        }`}
                      >
                        {g}
                      </button>
                    ))}
                  </div>
                </div>
                <div>
                  <label className="block text-[10px] uppercase tracking-wide font-medium text-gray-500 dark:text-gray-400 mb-1">Accent / Language</label>
                  <select
                    value={videoAccent}
                    onChange={(e) => setVideoAccent(e.target.value)}
                    className="w-full px-2 py-1.5 text-xs border border-gray-200 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                  >
                    {ACCENT_LANGUAGE_OPTIONS.map(([val, label]) => (
                      <option key={val} value={val}>{label}</option>
                    ))}
                  </select>
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
            <div className="space-y-2">
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
              <label className="inline-flex items-center gap-1.5 text-[11px] text-gray-600 dark:text-gray-300 cursor-pointer pt-1">
                <input
                  type="checkbox"
                  checked={quizInteractive}
                  onChange={(e) => setQuizInteractive(e.target.checked)}
                  className="rounded border-gray-300 dark:border-gray-600"
                />
                Render as interactive HTML (sandbox iframe)
              </label>
            </div>
          )}

          {type === 'cards' && (
            <div className="space-y-2">
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="block text-[10px] uppercase tracking-wide font-medium text-gray-500 dark:text-gray-400 mb-1">Cards ({cardsCount})</label>
                  <input
                    type="range"
                    min={3}
                    max={30}
                    step={1}
                    value={cardsCount}
                    onChange={(e) => setCardsCount(parseInt(e.target.value))}
                    className="w-full"
                  />
                </div>
                <div>
                  <label className="block text-[10px] uppercase tracking-wide font-medium text-gray-500 dark:text-gray-400 mb-1">Difficulty</label>
                  <div className="flex gap-1">
                    {(['easy', 'medium', 'hard'] as const).map((d) => (
                      <button
                        key={d}
                        onClick={() => setCardsDifficulty(d)}
                        className={`flex-1 px-2 py-1 text-[10px] rounded-lg border transition-colors capitalize ${
                          cardsDifficulty === d
                            ? 'border-fuchsia-500 bg-fuchsia-50 dark:bg-fuchsia-900/20 text-fuchsia-700 dark:text-fuchsia-300'
                            : 'border-gray-200 dark:border-gray-600 text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700'
                        }`}
                      >
                        {d}
                      </button>
                    ))}
                  </div>
                </div>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="block text-[10px] uppercase tracking-wide font-medium text-gray-500 dark:text-gray-400 mb-1">Tutor</label>
                  <div className="flex gap-1">
                    {(['female', 'male'] as const).map((g) => (
                      <button
                        key={g}
                        onClick={() => setCardsTutorGender(g)}
                        className={`flex-1 px-2 py-1 text-[10px] rounded-lg border transition-colors capitalize ${
                          cardsTutorGender === g
                            ? 'border-fuchsia-500 bg-fuchsia-50 dark:bg-fuchsia-900/20 text-fuchsia-700 dark:text-fuchsia-300'
                            : 'border-gray-200 dark:border-gray-600 text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700'
                        }`}
                      >
                        {g}
                      </button>
                    ))}
                  </div>
                </div>
                <div>
                  <label className="block text-[10px] uppercase tracking-wide font-medium text-gray-500 dark:text-gray-400 mb-1">Accent</label>
                  <div className="flex gap-1">
                    {(['us', 'uk'] as const).map((a) => (
                      <button
                        key={a}
                        onClick={() => setCardsTutorAccent(a)}
                        className={`flex-1 px-2 py-1 text-[10px] rounded-lg border transition-colors uppercase ${
                          cardsTutorAccent === a
                            ? 'border-fuchsia-500 bg-fuchsia-50 dark:bg-fuchsia-900/20 text-fuchsia-700 dark:text-fuchsia-300'
                            : 'border-gray-200 dark:border-gray-600 text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700'
                        }`}
                      >
                        {a}
                      </button>
                    ))}
                  </div>
                </div>
              </div>
              <div className="flex flex-wrap items-center gap-3 pt-1">
                <label className="inline-flex items-center gap-1.5 text-[11px] text-gray-600 dark:text-gray-300 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={cardsTutorAutoplay}
                    onChange={(e) => setCardsTutorAutoplay(e.target.checked)}
                    className="rounded border-gray-300 dark:border-gray-600"
                  />
                  Tutor autoplay
                </label>
                <label className="inline-flex items-center gap-1.5 text-[11px] text-gray-600 dark:text-gray-300 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={cardsIncludeVisuals}
                    onChange={(e) => setCardsIncludeVisuals(e.target.checked)}
                    className="rounded border-gray-300 dark:border-gray-600"
                  />
                  Include visual cards
                </label>
              </div>
            </div>
          )}

          {type === 'comparison' && (
            <div className="space-y-2">
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="block text-[10px] uppercase tracking-wide font-medium text-gray-500 dark:text-gray-400 mb-1">Source A</label>
                  <select
                    value={compareSourceA}
                    onChange={(e) => setCompareSourceA(e.target.value)}
                    className="w-full px-2 py-1 text-xs rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                  >
                    <option value="">— pick a source —</option>
                    {availableSources.map((s) => (
                      <option key={s.id} value={s.id}>{s.filename}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="block text-[10px] uppercase tracking-wide font-medium text-gray-500 dark:text-gray-400 mb-1">Source B</label>
                  <select
                    value={compareSourceB}
                    onChange={(e) => setCompareSourceB(e.target.value)}
                    className="w-full px-2 py-1 text-xs rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                  >
                    <option value="">— pick a source —</option>
                    {availableSources.map((s) => (
                      <option key={s.id} value={s.id}>{s.filename}</option>
                    ))}
                  </select>
                </div>
              </div>
              <div>
                <label className="block text-[10px] uppercase tracking-wide font-medium text-gray-500 dark:text-gray-400 mb-1">Focus (optional)</label>
                <input
                  type="text"
                  value={compareFocus}
                  onChange={(e) => setCompareFocus(e.target.value)}
                  placeholder="e.g. methodology, conclusions, terminology"
                  className="w-full px-2 py-1 text-xs rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 placeholder-gray-400"
                />
              </div>
              {availableSources.length < 2 && (
                <p className="text-[11px] italic text-gray-500 dark:text-gray-400">
                  Comparison needs at least two sources in the notebook.
                </p>
              )}
            </div>
          )}

          {type === 'dashboard' && (
            <p className="text-[11px] italic text-gray-500 dark:text-gray-400">
              Generate a one-shot HTML overview of the active notebook — digest, themes, recent activity, what's converging this week.
            </p>
          )}

          {type === 'deep-dive' && (
            <div className="space-y-2">
              <div>
                <label className="block text-[10px] uppercase tracking-wide font-medium text-gray-500 dark:text-gray-400 mb-1">Entity</label>
                <input
                  type="text"
                  value={deepDiveEntity}
                  onChange={(e) => setDeepDiveEntity(e.target.value)}
                  placeholder="e.g. Patrick Collison, the LK-99 paper, Hard Fork podcast"
                  className="w-full px-2 py-1 text-sm rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 placeholder-gray-400"
                />
              </div>
              <label className="inline-flex items-center gap-1.5 text-[11px] text-gray-600 dark:text-gray-300 cursor-pointer pt-1">
                <input
                  type="checkbox"
                  checked={deepDiveCrossNotebook}
                  onChange={(e) => setDeepDiveCrossNotebook(e.target.checked)}
                  className="rounded border-gray-300 dark:border-gray-600"
                />
                Include all notebooks
              </label>
            </div>
          )}

          {type === 'perspectives' && (
            <div className="space-y-2">
              <div>
                <label className="block text-[10px] uppercase tracking-wide font-medium text-gray-500 dark:text-gray-400 mb-1">Topic</label>
                <input
                  type="text"
                  value={perspectivesQuery}
                  onChange={(e) => setPerspectivesQuery(e.target.value)}
                  placeholder="e.g. how each source frames the Fed decision"
                  className="w-full px-2 py-1 text-sm rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 placeholder-gray-400"
                />
              </div>
              <label className="inline-flex items-center gap-1.5 text-[11px] text-gray-600 dark:text-gray-300 cursor-pointer pt-1">
                <input
                  type="checkbox"
                  checked={perspectivesCrossNotebook}
                  onChange={(e) => setPerspectivesCrossNotebook(e.target.checked)}
                  className="rounded border-gray-300 dark:border-gray-600"
                />
                Include all notebooks (cross-notebook scope)
              </label>
              <p className="text-[11px] italic text-gray-500 dark:text-gray-400">
                Returns up to 8 sources' takes side-by-side, plus where they agree and diverge.
              </p>
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
