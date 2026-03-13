import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import {
  FileText, Palette, Target, Mic, Video, Presentation, Download, Search,
  Sparkles, Brain, GitBranch, CalendarDays, Network, BarChart3,
  ChevronUp, ChevronDown, MessageCircle,
} from 'lucide-react';
import { useCanvas } from '../canvas/CanvasContext';
import { CanvasItem } from '../canvas/types';
import { CanvasActionPopover } from '../canvas/CanvasActionPopover';
import { contentService } from '../../services/content';
import { visualService } from '../../services/visual';
import { quizService } from '../../services/quiz';
import { audioService } from '../../services/audio';
import { curatorService } from '../../services/curatorApi';
import { exportService } from '../../services/export';
import { skillsService } from '../../services/skills';
import { writingService, FormatOption } from '../../services/writing';
import { videoService, VisualStyle } from '../../services/video';
import { Skill } from '../../types';

// ─── Action definitions ────────────────────────────────────────────────────
const iconSm = 'w-3.5 h-3.5';

interface ActionDef {
  id: string;
  icon: React.ReactNode;
  label: string;
  shortLabel: string;
  enabled: (items: CanvasItem[], nb: string | null) => boolean;
}

const ACTIONS: ActionDef[] = [
  { id: 'docs', icon: <FileText className={iconSm} />, label: 'Generate Document', shortLabel: 'Docs', enabled: (_i, nb) => !!nb },
  { id: 'audio', icon: <Mic className={iconSm} />, label: 'Generate Audio', shortLabel: 'Audio', enabled: (_i, nb) => !!nb },
  { id: 'video', icon: <Video className={iconSm} />, label: 'Create Video', shortLabel: 'Video', enabled: (_i, nb) => !!nb },
  { id: 'visual', icon: <Palette className={iconSm} />, label: 'Create Visual', shortLabel: 'Visual', enabled: (_i, nb) => !!nb },
  { id: 'quiz', icon: <Target className={iconSm} />, label: 'Create Quiz', shortLabel: 'Quiz', enabled: (_i, nb) => !!nb },
  { id: 'pptx', icon: <Presentation className={iconSm} />, label: 'Create Slides', shortLabel: 'PPTX', enabled: (items, nb) => !!nb && items.some(i => i.type === 'document' || i.type === 'chat-response' || i.type === 'note') },
  { id: 'pdf', icon: <Download className={iconSm} />, label: 'Download PDF', shortLabel: 'PDF', enabled: (items) => items.some(i => i.type === 'document' || i.type === 'note') },
  { id: 'crossnb', icon: <Search className={iconSm} />, label: 'Cross-Notebook Discovery', shortLabel: 'Discover', enabled: (_i, nb) => !!nb },
];

// ─── Color themes (matching VisualPanel) ───────────────────────────────────
type ColorTheme = 'auto' | 'vibrant' | 'ocean' | 'sunset' | 'forest' | 'monochrome' | 'pastel';

const COLOR_THEMES: { id: ColorTheme; icon: string; label: string; colors: string[] }[] = [
  { id: 'auto', icon: '✨', label: 'Auto', colors: ['#ef4444', '#f97316', '#eab308', '#22c55e', '#3b82f6', '#8b5cf6'] },
  { id: 'vibrant', icon: '🌈', label: 'Vibrant', colors: ['#ef4444', '#f97316', '#eab308', '#22c55e', '#3b82f6', '#8b5cf6'] },
  { id: 'ocean', icon: '🌊', label: 'Ocean', colors: ['#0ea5e9', '#06b6d4', '#14b8a6', '#0d9488', '#0891b2'] },
  { id: 'sunset', icon: '🌅', label: 'Sunset', colors: ['#f97316', '#fb923c', '#fbbf24', '#f59e0b', '#dc2626'] },
  { id: 'forest', icon: '🌲', label: 'Forest', colors: ['#22c55e', '#16a34a', '#15803d', '#84cc16', '#65a30d'] },
  { id: 'monochrome', icon: '⬛', label: 'Mono', colors: ['#1f2937', '#374151', '#4b5563', '#6b7280', '#9ca3af'] },
  { id: 'pastel', icon: '🎀', label: 'Pastel', colors: ['#fecaca', '#fed7aa', '#fef08a', '#bbf7d0', '#bfdbfe', '#ddd6fe'] },
];

// ─── Advanced visual templates (matching VisualPanel) ──────────────────────
const VISUAL_TEMPLATES: Record<string, { id: string; icon: string; label: string }[]> = {
  'Context': [
    { id: 'key_stats', icon: '📊', label: 'Key Stats' },
    { id: 'overview', icon: '🗺️', label: 'Overview' },
    { id: 'anatomy', icon: '🔬', label: 'Anatomy' },
  ],
  'Mechanism': [
    { id: 'process_flow', icon: '➡️', label: 'Process Flow' },
    { id: 'cycle', icon: '🔄', label: 'Cycle' },
    { id: 'decision_tree', icon: '🌳', label: 'Decision Tree' },
    { id: 'funnel', icon: '📉', label: 'Funnel' },
  ],
  'Analysis': [
    { id: 'side_by_side', icon: '⚖️', label: 'Compare' },
    { id: 'pros_cons', icon: '👍👎', label: 'Pros/Cons' },
    { id: 'matrix', icon: '📐', label: 'Matrix' },
    { id: 'ranking', icon: '🏆', label: 'Ranking' },
  ],
  'Pattern': [
    { id: 'categories', icon: '📁', label: 'Categories' },
    { id: 'hierarchy', icon: '🏛️', label: 'Hierarchy' },
    { id: 'timeline', icon: '📅', label: 'Timeline' },
    { id: 'network', icon: '🕸️', label: 'Network' },
  ],
  'Persuade': [
    { id: 'key_takeaways', icon: '💡', label: 'Takeaways' },
    { id: 'action_plan', icon: '✅', label: 'Action Plan' },
    { id: 'roadmap', icon: '🛣️', label: 'Roadmap' },
    { id: 'before_after', icon: '🔀', label: 'Before/After' },
  ],
  'Feynman': [
    { id: 'feynman_progression', icon: '🎓', label: 'Learning Path' },
    { id: 'feynman_knowledge_map', icon: '🧠', label: 'Knowledge Map' },
    { id: 'feynman_misconceptions', icon: '❌✅', label: 'Misconceptions' },
  ],
};

// ─── Visual type grid ──────────────────────────────────────────────────────
type DiagramType = 'auto' | 'mindmap' | 'flowchart' | 'timeline' | 'classDiagram' | 'quadrant';

const DIAGRAM_OPTIONS: { type: DiagramType; icon: React.ReactNode; label: string }[] = [
  { type: 'auto', icon: <Sparkles className="w-3.5 h-3.5" />, label: 'Auto' },
  { type: 'mindmap', icon: <Brain className="w-3.5 h-3.5" />, label: 'Mindmap' },
  { type: 'flowchart', icon: <GitBranch className="w-3.5 h-3.5" />, label: 'Flow' },
  { type: 'timeline', icon: <CalendarDays className="w-3.5 h-3.5" />, label: 'Timeline' },
  { type: 'classDiagram', icon: <Network className="w-3.5 h-3.5" />, label: 'Hierarchy' },
  { type: 'quadrant', icon: <BarChart3 className="w-3.5 h-3.5" />, label: 'Compare' },
];

// ═══════════════════════════════════════════════════════════════════════════
// ChatActionBar component
// ═══════════════════════════════════════════════════════════════════════════

interface ChatActionBarProps {
  notebookId: string | null;
  expanded?: boolean;
  onToggleExpand?: () => void;
}

export const ChatActionBar: React.FC<ChatActionBarProps> = ({ notebookId, expanded = true, onToggleExpand }) => {
  const ctx = useCanvas();

  // ── Shared data ──────────────────────────────────────────────────────────
  const [skills, setSkills] = useState<Skill[]>([]);
  const [styleFormats, setStyleFormats] = useState<FormatOption[]>([]);
  const [customTemplates, setCustomTemplates] = useState<{ id: string; name: string }[]>([]);

  // ── Popover state ────────────────────────────────────────────────────────
  const [activePopover, setActivePopover] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  // ── Docs config (persisted to localStorage) ─────────────────────────────
  const [docsSkill, setDocsSkill] = useState(() => localStorage.getItem('lb-bar-docs-skill') || 'summary');
  const [docsStyle, setDocsStyle] = useState(() => localStorage.getItem('lb-bar-docs-style') || 'professional');
  const [docsTopic, setDocsTopic] = useState('');

  // ── Audio config ────────────────────────────────────────────────────────
  const [audioSkill, setAudioSkill] = useState(() => localStorage.getItem('lb-bar-audio-skill') || 'podcast_script');
  const [audioTopic, setAudioTopic] = useState('');
  const [audioDuration, setAudioDuration] = useState(() => parseInt(localStorage.getItem('lb-bar-audio-dur') || '15'));
  const [audioVoices, setAudioVoices] = useState(() => localStorage.getItem('lb-bar-audio-voices') || 'mf');
  const [audioAccent, setAudioAccent] = useState(() => localStorage.getItem('lb-bar-audio-accent') || 'us');
  const [showMoreLanguages, setShowMoreLanguages] = useState(false);

  // ── Visual config ───────────────────────────────────────────────────────
  const [visualType, setVisualType] = useState<DiagramType>(() => (localStorage.getItem('lb-bar-visual-type') as DiagramType) || 'auto');
  const [visualColorTheme, setVisualColorTheme] = useState<ColorTheme>(() => (localStorage.getItem('lb-bar-visual-color') as ColorTheme) || 'auto');
  const [selectedTemplate, setSelectedTemplate] = useState<string | null>(null);
  const [showTemplates, setShowTemplates] = useState(false);

  // ── Quiz config ─────────────────────────────────────────────────────────
  const [quizCount, setQuizCount] = useState(() => parseInt(localStorage.getItem('lb-bar-quiz-count') || '5'));
  const [quizDifficulty, setQuizDifficulty] = useState(() => localStorage.getItem('lb-bar-quiz-diff') || 'medium');
  const [quizTopic, setQuizTopic] = useState('');

  // ── PPTX config ─────────────────────────────────────────────────────────
  const [pptxTheme, setPptxTheme] = useState(() => localStorage.getItem('lb-bar-pptx-theme') || 'light');

  // ── PDF config ──────────────────────────────────────────────────────────
  type PdfLayout = 'clean' | 'academic' | 'report';
  const [pdfLayout, setPdfLayout] = useState<PdfLayout>(() => (localStorage.getItem('lb-bar-pdf-layout') as PdfLayout) || 'clean');

  // ── Video config ──────────────────────────────────────────────────────
  const [videoTopic, setVideoTopic] = useState('');
  const [videoDuration, setVideoDuration] = useState(() => parseInt(localStorage.getItem('lb-bar-video-dur') || '5'));
  const [videoStyle, setVideoStyle] = useState(() => localStorage.getItem('lb-bar-video-style') || 'classic');
  const [videoVoice, setVideoVoice] = useState(() => localStorage.getItem('lb-bar-video-voice') || 'af_heart');
  const [videoFormat, setVideoFormat] = useState<'explainer' | 'brief'>(() => (localStorage.getItem('lb-bar-video-format') as any) || 'explainer');
  const [videoStyles, setVideoStyles] = useState<VisualStyle[]>([]);

  // ── Discover config ─────────────────────────────────────────────────────
  const [discoverQuery, setDiscoverQuery] = useState('');

  // Strip @chat qualifier from topic strings before sending to API
  const stripAtChat = (s: string) => s.replace(/\s*@chat\s*/gi, '').trim();

  // @chat qualifier detection — visual feedback when user types @chat in any topic field
  const hasAtChatDocs = useMemo(() => /\b@chat\b/i.test(docsTopic), [docsTopic]);
  const hasAtChatAudio = useMemo(() => /\b@chat\b/i.test(audioTopic), [audioTopic]);
  const hasAtChatQuiz = useMemo(() => /\b@chat\b/i.test(quizTopic), [quizTopic]);
  const hasAtChatVideo = useMemo(() => /\b@chat\b/i.test(videoTopic), [videoTopic]);

  // Mini-hint: show @chat suggestion when user types @ but hasn't completed @chat
  const showAtHintDocs = useMemo(() => /@(?!chat\b)/i.test(docsTopic) && !hasAtChatDocs, [docsTopic, hasAtChatDocs]);
  const showAtHintAudio = useMemo(() => /@(?!chat\b)/i.test(audioTopic) && !hasAtChatAudio, [audioTopic, hasAtChatAudio]);
  const showAtHintQuiz = useMemo(() => /@(?!chat\b)/i.test(quizTopic) && !hasAtChatQuiz, [quizTopic, hasAtChatQuiz]);
  const showAtHintVideo = useMemo(() => /@(?!chat\b)/i.test(videoTopic) && !hasAtChatVideo, [videoTopic, hasAtChatVideo]);

  // ── Persist preferences ─────────────────────────────────────────────────
  useEffect(() => { localStorage.setItem('lb-bar-docs-skill', docsSkill); }, [docsSkill]);
  useEffect(() => { localStorage.setItem('lb-bar-docs-style', docsStyle); }, [docsStyle]);
  useEffect(() => { localStorage.setItem('lb-bar-audio-skill', audioSkill); }, [audioSkill]);
  useEffect(() => { localStorage.setItem('lb-bar-audio-dur', String(audioDuration)); }, [audioDuration]);
  useEffect(() => { localStorage.setItem('lb-bar-audio-voices', audioVoices); }, [audioVoices]);
  useEffect(() => { localStorage.setItem('lb-bar-audio-accent', audioAccent); }, [audioAccent]);
  useEffect(() => { localStorage.setItem('lb-bar-visual-type', visualType); }, [visualType]);
  useEffect(() => { localStorage.setItem('lb-bar-visual-color', visualColorTheme); }, [visualColorTheme]);
  useEffect(() => { localStorage.setItem('lb-bar-quiz-count', String(quizCount)); }, [quizCount]);
  useEffect(() => { localStorage.setItem('lb-bar-quiz-diff', quizDifficulty); }, [quizDifficulty]);
  useEffect(() => { localStorage.setItem('lb-bar-pptx-theme', pptxTheme); }, [pptxTheme]);
  useEffect(() => { localStorage.setItem('lb-bar-pdf-layout', pdfLayout); }, [pdfLayout]);
  useEffect(() => { localStorage.setItem('lb-bar-video-dur', String(videoDuration)); }, [videoDuration]);
  useEffect(() => { localStorage.setItem('lb-bar-video-style', videoStyle); }, [videoStyle]);
  useEffect(() => { localStorage.setItem('lb-bar-video-voice', videoVoice); }, [videoVoice]);
  useEffect(() => { localStorage.setItem('lb-bar-video-format', videoFormat); }, [videoFormat]);

  // ── Load shared data ────────────────────────────────────────────────────
  useEffect(() => {
    skillsService.list().then(setSkills).catch(() => {});
    writingService.getFormats().then(setStyleFormats).catch(() => {});
    exportService.listTemplates()
      .then((t: { id: string; name: string }[]) => setCustomTemplates(t.map(x => ({ id: x.id, name: x.name }))))
      .catch(() => {});
    videoService.listStyles().then(setVideoStyles).catch(() => {});
  }, []);

  // ── Content helpers (use canvasItems ref for freshness during streaming) ─
  const canvasItemsRef = useRef(ctx.canvasItems);
  canvasItemsRef.current = ctx.canvasItems;

  const getPrimaryContent = useCallback((): string => {
    const items = canvasItemsRef.current;
    const doc = items.find(i => i.type === 'document');
    if (doc) return doc.content;
    const note = items.find(i => i.type === 'note');
    if (note) return note.content;
    const chatResp = items.find(i => i.type === 'chat-response');
    if (chatResp) return chatResp.content;
    return items.map(i => i.content).join('\n\n');
  }, []);

  const getPrimaryTitle = useCallback((): string => {
    const items = canvasItemsRef.current;
    const doc = items.find(i => i.type === 'document');
    if (doc) return doc.title;
    const note = items.find(i => i.type === 'note');
    if (note) return note.title;
    return items[0]?.title || 'Document';
  }, []);

  // ═══════════════════════════════════════════════════════════════════════
  // Handlers — each persists to backend stores so Studio can discover them
  // ═══════════════════════════════════════════════════════════════════════

  const handleGenerateDocs = async () => {
    if (!notebookId) return;
    setActionLoading('docs');
    ctx.setGenerationStatus('generating');
    const skillName = skills.find(s => s.skill_id === docsSkill)?.name || 'Document';
    const itemId = `doc-${Date.now()}`;
    ctx.addCanvasItem({
      id: itemId,
      type: 'document',
      title: `Document: ${skillName}`,
      content: '',
      collapsed: true,
      status: 'generating',
      metadata: { notebookId },
    });
    try {
      const result = await contentService.generate({
        notebook_id: notebookId,
        skill_id: docsSkill,
        topic: stripAtChat(docsTopic) || undefined,
        style: docsStyle,
        ...(ctx.chatContext ? { chat_context: ctx.chatContext } : {}),
      });
      ctx.updateCanvasItem(itemId, {
        title: result.skill_name || skillName,
        content: result.content,
        status: 'complete',
      });
      // Can't update sourceNames/relevanceScores via updateCanvasItem — replace the item
      ctx.removeCanvasItem(itemId);
      ctx.addCanvasItem({
        type: 'document',
        title: `Document: ${result.skill_name || skillName}`,
        content: result.content,
        collapsed: true,
        sourceNames: result.source_names || [],
        relevanceScores: result.relevance_scores || {},
        status: 'complete',
        metadata: { notebookId },
      });
      ctx.setGenerationStatus('complete');
      window.dispatchEvent(new CustomEvent('studioContentRefresh'));
    } catch (err: any) {
      console.error('Docs generation failed:', err);
      ctx.updateCanvasItem(itemId, { status: 'error', content: '' });
      ctx.addToast({ type: 'error', title: 'Document generation failed', message: err.message || 'Unknown error' });
      ctx.setGenerationStatus('error');
    }
    setActionLoading(null);
  };

  const handleCreateVisual = async () => {
    if (!notebookId) return;
    setActionLoading('visual');
    ctx.setGenerationStatus('generating');
    try {
      const content = getPrimaryContent() || docsTopic || 'Generate a visual summary';
      const title = getPrimaryTitle();
      await visualService.generateSmartStream(
        notebookId,
        content,
        visualColorTheme,
        (diagram) => {
          ctx.addCanvasItem({
            type: 'visual',
            title: diagram.title || `Visual: ${title}`,
            content: diagram.svg || diagram.code || '',
            collapsed: true,
            metadata: { notebookId },
          });
          setActionLoading(null);
          ctx.setGenerationStatus('complete');
        },
        () => {},
        () => setActionLoading(null),
        (err) => {
          console.error('Visual generation failed:', err);
          ctx.addToast({ type: 'error', title: 'Visual generation failed', message: err });
          setActionLoading(null);
          ctx.setGenerationStatus('error');
        },
        selectedTemplate || undefined,
      );
    } catch (err) {
      console.error('Visual failed:', err);
      setActionLoading(null);
      ctx.setGenerationStatus('error');
    }
  };

  const handleCreateAudio = async (skillOverride?: string) => {
    if (!notebookId) return;
    setActionLoading('audio');
    ctx.setGenerationStatus('generating');
    const itemId = `audio-${Date.now()}`;
    const audioFormatLabels: Record<string, string> = {
      podcast_script: 'Conversation', debate: 'Debate Format', interview: 'Interview Format',
      storytelling: 'Story Format', feynman_curriculum: 'Feynman Lesson',
    };
    const effectiveSkill = skillOverride || audioSkill;
    const formatLabel = audioFormatLabels[effectiveSkill] || 'Audio';
    // Add placeholder immediately so user sees instant feedback
    ctx.addCanvasItem({
      id: itemId,
      type: 'audio',
      title: `Podcast: ${formatLabel}`,
      content: '',
      collapsed: true,
      status: 'generating',
      metadata: { notebookId: notebookId },
    });
    try {
      const content = getPrimaryContent();
      const topic = stripAtChat(audioTopic) || content.substring(0, 200).replace(/[#*_\n]/g, ' ').trim() || 'the research content';
      const voiceMap: Record<string, [string, string]> = { mf: ['male', 'female'], fm: ['female', 'male'], mm: ['male', 'male'], ff: ['female', 'female'] };
      const [h1, h2] = voiceMap[audioVoices] || ['male', 'female'];
      const result = await audioService.generate({
        notebook_id: notebookId,
        topic,
        duration_minutes: audioDuration,
        skill_id: effectiveSkill,
        host1_gender: h1,
        host2_gender: h2,
        accent: audioAccent,
        ...(ctx.chatContext ? { chat_context: ctx.chatContext } : {}),
      });
      // Update with real audio_id so the inline player can poll
      ctx.updateCanvasItem(itemId, {
        status: 'processing',
        metadata: { audioId: result.audio_id, notebookId: notebookId },
      });
      ctx.setGenerationStatus('complete');
      // Signal Studio to refresh audio list
      window.dispatchEvent(new CustomEvent('studioAudioRefresh'));
    } catch (err: any) {
      console.error('Audio failed:', err);
      ctx.updateCanvasItem(itemId, {
        status: 'error',
        metadata: { notebookId: notebookId, errorMessage: err.message || 'Audio generation failed' },
      });
      ctx.addToast({ type: 'error', title: 'Audio generation failed', message: err.message || 'Unknown error' });
      ctx.setGenerationStatus('error');
    }
    setActionLoading(null);
  };

  const handleCreateQuiz = async (topicOverride?: string, difficultyOverride?: string) => {
    if (!notebookId) return;
    setActionLoading('quiz');
    ctx.setGenerationStatus('generating');
    const itemId = `quiz-${Date.now()}`;
    ctx.addCanvasItem({
      id: itemId,
      type: 'quiz',
      title: 'Quiz',
      content: '',
      collapsed: true,
      status: 'generating',
      metadata: { notebookId },
    });
    try {
      const content = getPrimaryContent();
      const topic = topicOverride || stripAtChat(quizTopic) || content.substring(0, 300).replace(/[#*_\n]/g, ' ').trim();
      const difficulty = difficultyOverride || quizDifficulty;
      const quiz = await quizService.generate(notebookId, quizCount, difficulty, topic, ctx.chatContext || undefined);
      if (!quiz.questions || quiz.questions.length === 0) {
        throw new Error('The model could not generate quiz questions. Try again or use a different topic.');
      }
      const quizHtml = quiz.questions.map((q, i) => {
        const optionsHtml = q.options
          ? q.options.map((opt, j) => `<li>${String.fromCharCode(65 + j)}. ${opt}</li>`).join('')
          : '';
        return `<div class="mb-4"><p><strong>Q${i + 1}.</strong> ${q.question}</p>${optionsHtml ? `<ul>${optionsHtml}</ul>` : ''}<details class="mt-1"><summary class="text-sm text-blue-600 cursor-pointer">Show Answer</summary><p class="text-sm text-green-700 dark:text-green-400 mt-1"><strong>Answer:</strong> ${q.answer}</p><p class="text-sm text-gray-600 dark:text-gray-400">${q.explanation}</p></details></div>`;
      }).join('');
      ctx.updateCanvasItem(itemId, {
        title: `Quiz: ${quiz.topic || getPrimaryTitle()}`,
        content: quizHtml,
        status: 'complete',
      });
      ctx.setGenerationStatus('complete');
    } catch (err: any) {
      console.error('Quiz failed:', err);
      ctx.updateCanvasItem(itemId, { status: 'error', content: '' });
      ctx.addToast({ type: 'error', title: 'Quiz generation failed', message: err.message || 'Unknown error' });
      ctx.setGenerationStatus('error');
    }
    setActionLoading(null);
  };

  const handleExportPPTX = () => {
    const isCustom = pptxTheme.startsWith('tpl:');
    const detail: Record<string, string> = {
      content: getPrimaryContent(),
      title: getPrimaryTitle(),
    };
    if (isCustom) {
      detail.customTemplateId = pptxTheme.slice(4);
      detail.theme = 'light';
    } else {
      detail.theme = pptxTheme;
    }
    window.dispatchEvent(new CustomEvent('openExportModal', { detail }));
  };

  const handleExportPDF = async () => {
    setActionLoading('pdf');
    try {
      const content = getPrimaryContent();
      const title = getPrimaryTitle();
      await contentService.downloadAsPDF(content, title, title.toLowerCase().replace(/\s+/g, '-'), pdfLayout);
    } catch (err) {
      console.error('PDF download failed:', err);
      ctx.addToast({ type: 'error', title: 'PDF download failed' });
    }
    setActionLoading(null);
  };

  const handleCreateVideo = async () => {
    if (!notebookId) return;
    setActionLoading('video');
    ctx.setGenerationStatus('generating');
    const itemId = `video-${Date.now()}`;
    const formatLabel = videoFormat === 'brief' ? 'Brief' : 'Explainer';
    ctx.addCanvasItem({
      id: itemId,
      type: 'video',
      title: `Video: ${formatLabel}`,
      content: '',
      collapsed: true,
      status: 'generating',
      metadata: { notebookId },
    });
    try {
      const content = getPrimaryContent();
      // Extract a clean topic: user-supplied > first heading > first sentence > fallback
      let topic = stripAtChat(videoTopic);
      if (!topic && content) {
        const headingMatch = content.match(/^#{1,3}\s+(.+)/m);
        if (headingMatch) {
          topic = headingMatch[1].replace(/[*_`\[\]]/g, '').trim().substring(0, 120);
        } else {
          const firstSentence = content.replace(/[#*_`\n]+/g, ' ').trim().split(/[.!?]\s/)[0];
          if (firstSentence) topic = firstSentence.substring(0, 120).trim();
        }
      }
      const result = await videoService.generate({
        notebook_id: notebookId,
        topic,
        duration_minutes: videoDuration,
        visual_style: videoStyle,
        voice: videoVoice,
        format_type: videoFormat,
        ...(ctx.chatContext ? { chat_context: ctx.chatContext } : {}),
      });
      ctx.updateCanvasItem(itemId, {
        title: `Video: ${topic || formatLabel}`,
        status: 'processing',
        metadata: { videoId: result.video_id, notebookId },
      });
      ctx.setGenerationStatus('complete');
      // Poll for completion
      const pollInterval = setInterval(async () => {
        try {
          const status = await videoService.getStatus(result.video_id);
          if (status.status === 'completed') {
            clearInterval(pollInterval);
            ctx.updateCanvasItem(itemId, {
              status: 'complete',
              metadata: { videoId: result.video_id, notebookId, errorMessage: null },
            });
          } else if (status.status === 'failed') {
            clearInterval(pollInterval);
            ctx.updateCanvasItem(itemId, {
              status: 'error',
              metadata: { videoId: result.video_id, notebookId, errorMessage: status.error_message || 'Video generation failed' },
            });
          } else {
            ctx.updateCanvasItem(itemId, {
              metadata: { videoId: result.video_id, notebookId, errorMessage: status.error_message },
            });
          }
        } catch {
          clearInterval(pollInterval);
        }
      }, 5000);
      // Stop polling after 15 minutes
      setTimeout(() => clearInterval(pollInterval), 15 * 60 * 1000);
    } catch (err: any) {
      console.error('Video failed:', err);
      ctx.updateCanvasItem(itemId, {
        status: 'error',
        metadata: { notebookId, errorMessage: err.message || 'Video generation failed' },
      });
      ctx.addToast({ type: 'error', title: 'Video generation failed', message: err.message || 'Unknown error' });
      ctx.setGenerationStatus('error');
    }
    setActionLoading(null);
  };

  const handleCrossNotebook = async () => {
    if (!notebookId) return;
    setActionLoading('crossnb');
    ctx.setGenerationStatus('generating');
    try {
      const content = getPrimaryContent();
      const query = discoverQuery.trim() || content.substring(0, 500).replace(/[#*_\n]/g, ' ').trim();
      if (!query) {
        ctx.addToast({ type: 'info', title: 'Nothing to discover', message: 'Type a query or add content first' });
        setActionLoading(null);
        ctx.setGenerationStatus('idle');
        return;
      }
      const result = await curatorService.chat(
        `Find connections and insights across all my notebooks related to: ${query}`,
        notebookId
      );
      if (result?.response) {
        ctx.addCanvasItem({
          type: 'chat-response',
          title: 'Cross-Notebook Discovery',
          content: result.response,
          collapsed: true,
          metadata: { notebookId },
        });
      }
      ctx.setGenerationStatus('complete');
    } catch (err: any) {
      console.error('Cross-notebook discovery failed:', err);
      ctx.addToast({ type: 'error', title: 'Cross-notebook search failed', message: err.message || 'Unknown error' });
      ctx.setGenerationStatus('error');
    }
    setActionLoading(null);
  };


  // ── Feynman document interactive block listeners ────────────────────────
  // ChatActionBar is always mounted and can generate directly — one-click magic.
  // Uses refs so event handlers always call the latest version of the functions.
  const handleCreateQuizRef = useRef(handleCreateQuiz);
  handleCreateQuizRef.current = handleCreateQuiz;
  const handleCreateAudioRef = useRef(handleCreateAudio);
  handleCreateAudioRef.current = handleCreateAudio;

  useEffect(() => {
    const onFeynmanQuiz = (e: Event) => {
      const detail = (e as CustomEvent).detail;
      handleCreateQuizRef.current(detail?.topic, detail?.difficulty);
    };
    const onFeynmanAudio = () => {
      handleCreateAudioRef.current('feynman_curriculum');
    };
    window.addEventListener('feynmanQuizNav', onFeynmanQuiz);
    window.addEventListener('feynmanAudioNav', onFeynmanAudio);
    return () => {
      window.removeEventListener('feynmanQuizNav', onFeynmanQuiz);
      window.removeEventListener('feynmanAudioNav', onFeynmanAudio);
    };
  }, []);

  // ── Derived state ───────────────────────────────────────────────────────
  const audioSkillIds = ['podcast_script', 'debate', 'interview', 'storytelling', 'feynman_curriculum'];
  const textSkills = skills.filter(s => !['podcast_script', 'debate', 'interview', 'storytelling'].includes(s.skill_id));
  const audioSkills = skills.filter(s => audioSkillIds.includes(s.skill_id));

  // ═══════════════════════════════════════════════════════════════════════
  // Render
  // ═══════════════════════════════════════════════════════════════════════

  return (
    <div className="border-t border-gray-100 dark:border-gray-700/50 bg-white dark:bg-gray-800">
      {/* Toggle bar */}
      <button
        onClick={onToggleExpand}
        className="w-full flex items-center justify-center gap-1.5 py-1 text-[10px] font-medium text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300 transition-colors"
      >
        {expanded ? <ChevronDown className="w-3 h-3" /> : <ChevronUp className="w-3 h-3" />}
        {expanded ? 'Hide Studio' : 'Studio Actions'}
        {expanded ? <ChevronDown className="w-3 h-3" /> : <ChevronUp className="w-3 h-3" />}
      </button>

      {expanded && (
        <>
          {/* Popovers — rendered above pill row */}
          <div className="relative">
            {/* ── Docs popover ─────────────────────────────────────────── */}
            <CanvasActionPopover
              isOpen={activePopover === 'docs'}
              onClose={() => setActivePopover(null)}
              title="Generate Document"
              generateLabel="Generate"
              generating={actionLoading === 'docs'}
              onGenerate={() => { setActivePopover(null); handleGenerateDocs(); }}
            >
              <div className="space-y-2.5">
                <div>
                  <label className="block text-[11px] font-medium text-gray-500 dark:text-gray-400 mb-1">Content Type</label>
                  <select value={docsSkill} onChange={e => setDocsSkill(e.target.value)} className="w-full px-2.5 py-1.5 text-xs border border-gray-200 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:outline-none focus:ring-1 focus:ring-blue-400">
                    {textSkills.map(s => (
                      <option key={s.skill_id} value={s.skill_id}>{s.name}</option>
                    ))}
                  </select>
                  {textSkills.find(s => s.skill_id === docsSkill)?.description && (
                    <p className="mt-1 text-[10px] text-gray-400 dark:text-gray-500 leading-snug">{textSkills.find(s => s.skill_id === docsSkill)!.description}</p>
                  )}
                </div>
                {styleFormats.length > 0 && (
                  <div>
                    <label className="block text-[11px] font-medium text-gray-500 dark:text-gray-400 mb-1">Style</label>
                    <div className="flex flex-wrap gap-1">
                      {styleFormats.map(f => (
                        <button key={f.value} onClick={() => setDocsStyle(f.value)} className={`px-2 py-1 text-[11px] rounded-lg border transition-colors ${docsStyle === f.value ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/20 text-blue-700 dark:text-blue-300' : 'border-gray-200 dark:border-gray-600 text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700'}`}>{f.label}</button>
                      ))}
                    </div>
                  </div>
                )}
                <div>
                  <label className="block text-[11px] font-medium text-gray-500 dark:text-gray-400 mb-1">Topic <span className="text-gray-400">(optional)</span></label>
                  <input type="text" value={docsTopic} onChange={e => setDocsTopic(e.target.value)} placeholder={ctx.chatContext ? 'e.g., @chat transformers attention' : 'e.g., AI use cases in healthcare'} className={`w-full px-2.5 py-1.5 text-xs border rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white placeholder-gray-400 focus:outline-none focus:ring-1 focus:ring-blue-400 ${hasAtChatDocs ? 'border-purple-400 dark:border-purple-500 ring-1 ring-purple-300 dark:ring-purple-600' : 'border-gray-200 dark:border-gray-600'}`} />
                  {hasAtChatDocs && (
                    <p className="mt-1 text-[10px] text-purple-600 dark:text-purple-400 flex items-center gap-1">
                      <MessageCircle className="w-3 h-3" />
                      Chat context will focus this generation
                    </p>
                  )}
                  {showAtHintDocs && (
                    <p className="mt-1 text-[10px] text-gray-500 dark:text-gray-400 flex items-center gap-1">
                      <MessageCircle className="w-3 h-3" />
                      Type <span className="font-semibold text-purple-500 dark:text-purple-400">@chat</span> to include conversation context
                    </p>
                  )}
                </div>
                {ctx.chatContext && !hasAtChatDocs && (
                  <div className="flex items-center gap-1.5 px-2 py-1 bg-purple-50 dark:bg-purple-900/20 border border-purple-200 dark:border-purple-800 rounded-lg">
                    <MessageCircle className="w-3 h-3 text-purple-500 dark:text-purple-400" />
                    <span className="text-[10px] text-purple-700 dark:text-purple-300">Using chat context</span>
                  </div>
                )}
              </div>
            </CanvasActionPopover>

            {/* ── Visual popover ────────────────────────────────────────── */}
            <CanvasActionPopover
              isOpen={activePopover === 'visual'}
              onClose={() => { setActivePopover(null); setShowTemplates(false); }}
              title="Create Visual"
              generateLabel="Generate"
              generating={actionLoading === 'visual'}
              onGenerate={() => { setActivePopover(null); setShowTemplates(false); handleCreateVisual(); }}
            >
              <div className="space-y-2.5">
                {/* Diagram type */}
                <div>
                  <label className="block text-[11px] font-medium text-gray-500 dark:text-gray-400 mb-1.5">Type</label>
                  <div className="grid grid-cols-3 gap-1.5">
                    {DIAGRAM_OPTIONS.map(opt => (
                      <button key={opt.type} onClick={() => { setVisualType(opt.type); setSelectedTemplate(null); }} className={`flex items-center justify-center gap-1 px-2 py-1.5 text-[11px] rounded-lg border transition-colors ${visualType === opt.type && !selectedTemplate ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/20 text-blue-700 dark:text-blue-300' : 'border-gray-200 dark:border-gray-600 text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700'}`}>
                        {opt.icon} <span>{opt.label}</span>
                      </button>
                    ))}
                  </div>
                </div>
                {/* Color theme */}
                <div>
                  <label className="block text-[11px] font-medium text-gray-500 dark:text-gray-400 mb-1.5">Color Theme</label>
                  <div className="flex gap-1 overflow-x-auto scrollbar-hide">
                    {COLOR_THEMES.map(t => (
                      <button key={t.id} onClick={() => setVisualColorTheme(t.id)} className={`flex flex-col items-center gap-0.5 px-2 py-1 rounded-lg border transition-colors flex-shrink-0 ${visualColorTheme === t.id ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/20' : 'border-gray-200 dark:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-700'}`}>
                        <span className="text-xs">{t.icon}</span>
                        <span className="text-[10px] text-gray-600 dark:text-gray-400">{t.label}</span>
                        <div className="flex gap-px">
                          {t.colors.slice(0, 4).map((c, ci) => (
                            <div key={ci} className="w-2 h-2 rounded-full" style={{ backgroundColor: c }} />
                          ))}
                        </div>
                      </button>
                    ))}
                  </div>
                </div>
                {/* Advanced templates toggle */}
                <div>
                  <button onClick={() => setShowTemplates(!showTemplates)} className="text-[11px] text-blue-500 dark:text-blue-400 hover:underline font-medium">
                    {showTemplates ? 'Hide templates' : 'Advanced templates'}
                  </button>
                  {showTemplates && (
                    <div className="mt-1.5 space-y-1.5 max-h-40 overflow-y-auto">
                      {Object.entries(VISUAL_TEMPLATES).map(([category, templates]) => (
                        <div key={category}>
                          <span className="text-[10px] font-semibold text-gray-400 dark:text-gray-500 uppercase tracking-wider">{category}</span>
                          <div className="flex flex-wrap gap-1 mt-0.5">
                            {templates.map(t => (
                              <button key={t.id} onClick={() => { setSelectedTemplate(t.id); setVisualType('auto'); }} className={`px-1.5 py-0.5 text-[10px] rounded border transition-colors ${selectedTemplate === t.id ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/20 text-blue-700 dark:text-blue-300' : 'border-gray-200 dark:border-gray-600 text-gray-500 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700'}`}>
                                {t.icon} {t.label}
                              </button>
                            ))}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            </CanvasActionPopover>

            {/* ── Audio popover ─────────────────────────────────────────── */}
            <CanvasActionPopover
              isOpen={activePopover === 'audio'}
              onClose={() => setActivePopover(null)}
              title="Generate Audio"
              generateLabel="Generate"
              generating={actionLoading === 'audio'}
              onGenerate={() => { setActivePopover(null); handleCreateAudio(); }}
            >
              <div className="space-y-2.5">
                <div>
                  <label className="block text-[11px] font-medium text-gray-500 dark:text-gray-400 mb-1">Style</label>
                  <select value={audioSkill} onChange={e => setAudioSkill(e.target.value)} className="w-full px-2.5 py-1.5 text-xs border border-gray-200 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:outline-none focus:ring-1 focus:ring-blue-400">
                    {audioSkills.map(s => (
                      <option key={s.skill_id} value={s.skill_id}>{s.name}</option>
                    ))}
                  </select>
                  {audioSkill === 'feynman_curriculum' && (
                    <p className="mt-1 text-[10px] text-blue-500 dark:text-blue-400 leading-snug">4-part progressive teaching: Foundation → Building → First Principles → Mastery (30-45 min recommended)</p>
                  )}
                </div>
                <div>
                  <label className="block text-[11px] font-medium text-gray-500 dark:text-gray-400 mb-1">Topic <span className="text-gray-400">(optional)</span></label>
                  <input type="text" value={audioTopic} onChange={e => setAudioTopic(e.target.value)} placeholder={ctx.chatContext ? 'e.g., @chat transformers attention' : 'Leave blank to use notebook content'} className={`w-full px-2.5 py-1.5 text-xs border rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white placeholder-gray-400 focus:outline-none focus:ring-1 focus:ring-blue-400 ${hasAtChatAudio ? 'border-purple-400 dark:border-purple-500 ring-1 ring-purple-300 dark:ring-purple-600' : 'border-gray-200 dark:border-gray-600'}`} />
                  {hasAtChatAudio && (
                    <p className="mt-1 text-[10px] text-purple-600 dark:text-purple-400 flex items-center gap-1">
                      <MessageCircle className="w-3 h-3" />
                      Chat context will focus this generation
                    </p>
                  )}
                  {showAtHintAudio && (
                    <p className="mt-1 text-[10px] text-gray-500 dark:text-gray-400 flex items-center gap-1">
                      <MessageCircle className="w-3 h-3" />
                      Type <span className="font-semibold text-purple-500 dark:text-purple-400">@chat</span> to include conversation context
                    </p>
                  )}
                </div>
                {ctx.chatContext && !hasAtChatAudio && (
                  <div className="flex items-center gap-1.5 px-2 py-1 bg-purple-50 dark:bg-purple-900/20 border border-purple-200 dark:border-purple-800 rounded-lg">
                    <MessageCircle className="w-3 h-3 text-purple-500 dark:text-purple-400" />
                    <span className="text-[10px] text-purple-700 dark:text-purple-300">Using chat context</span>
                  </div>
                )}
                <div>
                  <label className="block text-[11px] font-medium text-gray-500 dark:text-gray-400 mb-1">Duration: {audioDuration} min</label>
                  <input type="range" min="5" max={audioSkill === 'feynman_curriculum' ? 45 : 30} value={audioDuration} onChange={e => setAudioDuration(parseInt(e.target.value))} className="w-full h-1.5 accent-blue-600" />
                </div>
                <div>
                  <label className="block text-[11px] font-medium text-gray-500 dark:text-gray-400 mb-1">Voices</label>
                  <div className="flex gap-1">
                    {([['mf', 'M / F'], ['fm', 'F / M'], ['mm', 'M / M'], ['ff', 'F / F']] as const).map(([val, label]) => (
                      <button key={val} onClick={() => setAudioVoices(val)} className={`flex-1 px-2 py-1 text-[11px] rounded-lg border transition-colors text-center ${audioVoices === val ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/20 text-blue-700 dark:text-blue-300' : 'border-gray-200 dark:border-gray-600 text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700'}`}>{label}</button>
                    ))}
                  </div>
                </div>
                <div>
                  <label className="block text-[11px] font-medium text-gray-500 dark:text-gray-400 mb-1">Language</label>
                  <div className="grid grid-cols-3 gap-1">
                    {([['us', 'American'], ['uk', 'British']] as const).map(([val, label]) => (
                      <button key={val} onClick={() => setAudioAccent(val)} className={`px-2 py-1 text-[11px] rounded-lg border transition-colors text-center ${audioAccent === val ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/20 text-blue-700 dark:text-blue-300' : 'border-gray-200 dark:border-gray-600 text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700'}`}>{label}</button>
                    ))}
                    <button onClick={() => setShowMoreLanguages(!showMoreLanguages)} className={`px-2 py-1 text-[11px] rounded-lg border transition-colors text-center ${showMoreLanguages || !['us', 'uk'].includes(audioAccent) ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/20 text-blue-700 dark:text-blue-300' : 'border-gray-200 dark:border-gray-600 text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700'}`}>{showMoreLanguages ? '− Languages' : '+ Languages'}</button>
                  </div>
                  {(showMoreLanguages || !['us', 'uk'].includes(audioAccent)) && (
                    <div className="grid grid-cols-3 gap-1 mt-1">
                      {([['es', 'Spanish'], ['fr', 'French'], ['hi', 'Hindi'], ['it', 'Italian'], ['ja', 'Japanese'], ['pt', 'Portuguese'], ['zh', 'Chinese']] as const).map(([val, label]) => (
                        <button key={val} onClick={() => setAudioAccent(val)} className={`px-2 py-1 text-[11px] rounded-lg border transition-colors text-center ${audioAccent === val ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/20 text-blue-700 dark:text-blue-300' : 'border-gray-200 dark:border-gray-600 text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700'}`}>{label}</button>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            </CanvasActionPopover>

            {/* ── Video popover ─────────────────────────────────────────── */}
            <CanvasActionPopover
              isOpen={activePopover === 'video'}
              onClose={() => setActivePopover(null)}
              title="Create Video"
              generateLabel="Generate Video"
              generating={actionLoading === 'video'}
              onGenerate={() => { setActivePopover(null); handleCreateVideo(); }}
            >
              <div className="space-y-2.5">
                <div>
                  <label className="block text-[11px] font-medium text-gray-500 dark:text-gray-400 mb-1">Format</label>
                  <div className="flex gap-1">
                    {([['explainer', 'Explainer (3-7 min)'], ['brief', 'Brief (1-2 min)']] as const).map(([val, label]) => (
                      <button key={val} onClick={() => setVideoFormat(val)} className={`flex-1 px-2 py-1.5 text-[11px] rounded-lg border transition-colors text-center ${videoFormat === val ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/20 text-blue-700 dark:text-blue-300' : 'border-gray-200 dark:border-gray-600 text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700'}`}>{label}</button>
                    ))}
                  </div>
                </div>
                <div>
                  <label className="block text-[11px] font-medium text-gray-500 dark:text-gray-400 mb-1">Topic <span className="text-gray-400">(optional)</span></label>
                  <input type="text" value={videoTopic} onChange={e => setVideoTopic(e.target.value)} placeholder={ctx.chatContext ? 'e.g., @chat transformers attention' : 'Leave blank for auto-topic'} className={`w-full px-2.5 py-1.5 text-xs border rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white placeholder-gray-400 focus:outline-none focus:ring-1 focus:ring-blue-400 ${hasAtChatVideo ? 'border-purple-400 dark:border-purple-500 ring-1 ring-purple-300 dark:ring-purple-600' : 'border-gray-200 dark:border-gray-600'}`} />
                  {hasAtChatVideo && (
                    <p className="mt-1 text-[10px] text-purple-600 dark:text-purple-400 flex items-center gap-1">
                      <MessageCircle className="w-3 h-3" />
                      Chat context will focus this generation
                    </p>
                  )}
                  {showAtHintVideo && (
                    <p className="mt-1 text-[10px] text-gray-500 dark:text-gray-400 flex items-center gap-1">
                      <MessageCircle className="w-3 h-3" />
                      Type <span className="font-semibold text-purple-500 dark:text-purple-400">@chat</span> to include conversation context
                    </p>
                  )}
                </div>
                {ctx.chatContext && !hasAtChatVideo && (
                  <div className="flex items-center gap-1.5 px-2 py-1 bg-purple-50 dark:bg-purple-900/20 border border-purple-200 dark:border-purple-800 rounded-lg">
                    <MessageCircle className="w-3 h-3 text-purple-500 dark:text-purple-400" />
                    <span className="text-[10px] text-purple-700 dark:text-purple-300">Using chat context</span>
                  </div>
                )}
                <div>
                  <label className="block text-[11px] font-medium text-gray-500 dark:text-gray-400 mb-1">Duration: {videoDuration} min</label>
                  <input type="range" min="1" max="10" value={videoDuration} onChange={e => setVideoDuration(parseInt(e.target.value))} className="w-full h-1.5 accent-blue-600" />
                </div>
                <div>
                  <label className="block text-[11px] font-medium text-gray-500 dark:text-gray-400 mb-1">Visual Style</label>
                  <div className="grid grid-cols-3 gap-1">
                    {(videoStyles.length > 0 ? videoStyles.filter(s => !s.is_custom) : [
                      { id: 'classic', name: 'Classic', accent_color: '#4361ee', bg_color: '#FFFFFF' },
                      { id: 'dark', name: 'Dark', accent_color: '#818cf8', bg_color: '#0f0f1a' },
                      { id: 'whiteboard', name: 'Whiteboard', accent_color: '#e74c3c', bg_color: '#faf8f5' },
                      { id: 'midnight', name: 'Midnight', accent_color: '#06d6a0', bg_color: '#0a0a1a' },
                      { id: 'warm', name: 'Warm', accent_color: '#d4763c', bg_color: '#fdf6ee' },
                      { id: 'ocean', name: 'Ocean', accent_color: '#0077b6', bg_color: '#f0f7ff' },
                    ]).map(s => (
                      <button key={s.id} onClick={() => setVideoStyle(s.id)} className={`px-2 py-1 text-[11px] rounded-lg border transition-colors text-center flex items-center justify-center gap-1 ${videoStyle === s.id ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/20 text-blue-700 dark:text-blue-300' : 'border-gray-200 dark:border-gray-600 text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700'}`}>
                        <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ backgroundColor: s.accent_color }} />
                        {s.name}
                      </button>
                    ))}
                  </div>
                  {videoStyles.some(s => s.is_custom) && (
                    <div className="mt-1.5">
                      <label className="block text-[10px] font-medium text-purple-500 dark:text-purple-400 mb-1">Your Templates</label>
                      <div className="grid grid-cols-2 gap-1">
                        {videoStyles.filter(s => s.is_custom).map(s => (
                          <button key={s.id} onClick={() => setVideoStyle(s.id)} className={`px-2 py-1 text-[11px] rounded-lg border transition-colors text-center flex items-center justify-center gap-1 ${videoStyle === s.id ? 'border-purple-500 bg-purple-50 dark:bg-purple-900/20 text-purple-700 dark:text-purple-300 ring-1 ring-purple-400' : 'border-purple-200 dark:border-purple-700 text-purple-600 dark:text-purple-400 hover:bg-purple-50 dark:hover:bg-purple-900/10'}`}>
                            <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ backgroundColor: s.accent_color }} />
                            {s.name}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
                <div>
                  <label className="block text-[11px] font-medium text-gray-500 dark:text-gray-400 mb-1">Narrator Voice</label>
                  <div className="grid grid-cols-2 gap-1">
                    {([['af_heart', 'Heart (US F)'], ['af_bella', 'Bella (US F)'], ['am_adam', 'Adam (US M)'], ['am_michael', 'Michael (US M)'], ['bf_emma', 'Emma (UK F)'], ['bm_george', 'George (UK M)']] as const).map(([val, label]) => (
                      <button key={val} onClick={() => setVideoVoice(val)} className={`px-2 py-1 text-[11px] rounded-lg border transition-colors text-center ${videoVoice === val ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/20 text-blue-700 dark:text-blue-300' : 'border-gray-200 dark:border-gray-600 text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700'}`}>{label}</button>
                    ))}
                  </div>
                </div>
              </div>
            </CanvasActionPopover>

            {/* ── Quiz popover ──────────────────────────────────────────── */}
            <CanvasActionPopover
              isOpen={activePopover === 'quiz'}
              onClose={() => setActivePopover(null)}
              title="Create Quiz"
              generateLabel="Generate"
              generating={actionLoading === 'quiz'}
              onGenerate={() => { setActivePopover(null); handleCreateQuiz(); }}
            >
              <div className="space-y-2.5">
                <div>
                  <label className="block text-[11px] font-medium text-gray-500 dark:text-gray-400 mb-1">Questions: {quizCount}</label>
                  <input type="range" min="3" max="10" value={quizCount} onChange={e => setQuizCount(parseInt(e.target.value))} className="w-full h-1.5 accent-blue-600" />
                </div>
                <div>
                  <label className="block text-[11px] font-medium text-gray-500 dark:text-gray-400 mb-1">Difficulty</label>
                  <div className="flex gap-1">
                    {(['easy', 'medium', 'hard'] as const).map(d => (
                      <button key={d} onClick={() => setQuizDifficulty(d)} className={`flex-1 px-2 py-1.5 text-[11px] rounded-lg border transition-colors capitalize ${quizDifficulty === d ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/20 text-blue-700 dark:text-blue-300' : 'border-gray-200 dark:border-gray-600 text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700'}`}>{d}</button>
                    ))}
                  </div>
                </div>
                <div>
                  <label className="block text-[11px] font-medium text-gray-500 dark:text-gray-400 mb-1">Topic <span className="text-gray-400">(optional)</span></label>
                  <input type="text" value={quizTopic} onChange={e => setQuizTopic(e.target.value)} placeholder={ctx.chatContext ? 'e.g., @chat transformers attention' : 'Auto-detected from content'} className={`w-full px-2.5 py-1.5 text-xs border rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white placeholder-gray-400 focus:outline-none focus:ring-1 focus:ring-blue-400 ${hasAtChatQuiz ? 'border-purple-400 dark:border-purple-500 ring-1 ring-purple-300 dark:ring-purple-600' : 'border-gray-200 dark:border-gray-600'}`} />
                  {hasAtChatQuiz && (
                    <p className="mt-1 text-[10px] text-purple-600 dark:text-purple-400 flex items-center gap-1">
                      <MessageCircle className="w-3 h-3" />
                      Chat context will focus this generation
                    </p>
                  )}
                  {showAtHintQuiz && (
                    <p className="mt-1 text-[10px] text-gray-500 dark:text-gray-400 flex items-center gap-1">
                      <MessageCircle className="w-3 h-3" />
                      Type <span className="font-semibold text-purple-500 dark:text-purple-400">@chat</span> to include conversation context
                    </p>
                  )}
                </div>
                {ctx.chatContext && !hasAtChatQuiz && (
                  <div className="flex items-center gap-1.5 px-2 py-1 bg-purple-50 dark:bg-purple-900/20 border border-purple-200 dark:border-purple-800 rounded-lg">
                    <MessageCircle className="w-3 h-3 text-purple-500 dark:text-purple-400" />
                    <span className="text-[10px] text-purple-700 dark:text-purple-300">Using chat context</span>
                  </div>
                )}
              </div>
            </CanvasActionPopover>

            {/* ── PPTX popover ──────────────────────────────────────────── */}
            <CanvasActionPopover
              isOpen={activePopover === 'pptx'}
              onClose={() => setActivePopover(null)}
              title="Create Slides"
              generateLabel="Build Slides"
              onGenerate={() => { setActivePopover(null); handleExportPPTX(); }}
            >
              <div className="space-y-2.5">
                <div>
                  <label className="block text-[11px] font-medium text-gray-500 dark:text-gray-400 mb-1.5">Theme</label>
                  <div className="grid grid-cols-2 gap-1.5">
                    {([
                      { id: 'light', label: 'Light', cls: 'bg-white border-gray-300 text-gray-800' },
                      { id: 'dark', label: 'Dark', cls: 'bg-gray-800 border-gray-600 text-gray-100' },
                      { id: 'corporate', label: 'Corporate', cls: 'bg-blue-50 border-blue-300 text-blue-900' },
                      { id: 'academic', label: 'Academic', cls: 'bg-amber-50 border-amber-300 text-amber-900' },
                    ]).map(t => (
                      <button key={t.id} onClick={() => setPptxTheme(t.id)} className={`px-2.5 py-1.5 text-[11px] font-medium rounded-lg border transition-all ${t.cls} ${pptxTheme === t.id ? 'ring-2 ring-blue-500 ring-offset-1' : 'opacity-60 hover:opacity-100'}`}>{t.label}</button>
                    ))}
                  </div>
                </div>
                {customTemplates.length > 0 && (
                  <div>
                    <label className="block text-[11px] font-medium text-gray-500 dark:text-gray-400 mb-1.5">Custom Templates</label>
                    <div className="grid grid-cols-2 gap-1.5">
                      {customTemplates.map(t => (
                        <button key={t.id} onClick={() => setPptxTheme(`tpl:${t.id}`)} className={`px-2.5 py-1.5 text-[11px] font-medium rounded-lg border transition-all border-purple-300 dark:border-purple-600 text-purple-800 dark:text-purple-200 bg-purple-50 dark:bg-purple-900/20 ${pptxTheme === `tpl:${t.id}` ? 'ring-2 ring-purple-500 ring-offset-1' : 'opacity-60 hover:opacity-100'}`}>{t.name}</button>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </CanvasActionPopover>

            {/* ── PDF popover ───────────────────────────────────────────── */}
            <CanvasActionPopover
              isOpen={activePopover === 'pdf'}
              onClose={() => setActivePopover(null)}
              title="Download PDF"
              generateLabel="Download"
              generating={actionLoading === 'pdf'}
              onGenerate={() => { setActivePopover(null); handleExportPDF(); }}
            >
              <div>
                <label className="block text-[11px] font-medium text-gray-500 dark:text-gray-400 mb-1.5">Layout</label>
                <div className="grid grid-cols-3 gap-1.5">
                  {([
                    { id: 'clean' as const, label: 'Clean', desc: 'Minimal, modern' },
                    { id: 'academic' as const, label: 'Academic', desc: 'Serif, numbered' },
                    { id: 'report' as const, label: 'Report', desc: 'Cover page, TOC' },
                  ]).map(l => (
                    <button key={l.id} onClick={() => setPdfLayout(l.id)} className={`px-2 py-2 text-center rounded-lg border transition-colors ${pdfLayout === l.id ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/20' : 'border-gray-200 dark:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-700'}`}>
                      <span className={`block text-[11px] font-medium ${pdfLayout === l.id ? 'text-blue-700 dark:text-blue-300' : 'text-gray-700 dark:text-gray-300'}`}>{l.label}</span>
                      <span className="block text-[10px] text-gray-400 mt-0.5">{l.desc}</span>
                    </button>
                  ))}
                </div>
              </div>
            </CanvasActionPopover>

            {/* ── Discover popover ──────────────────────────────────────── */}
            <CanvasActionPopover
              isOpen={activePopover === 'crossnb'}
              onClose={() => setActivePopover(null)}
              title="Cross-Notebook Discovery"
              generateLabel="Discover"
              generating={actionLoading === 'crossnb'}
              onGenerate={() => { setActivePopover(null); handleCrossNotebook(); }}
            >
              <div className="space-y-2">
                <p className="text-[10px] text-gray-500 dark:text-gray-400">Search across all your notebooks for connections, patterns, and related insights.</p>
                <input
                  type="text"
                  value={discoverQuery}
                  onChange={e => setDiscoverQuery(e.target.value)}
                  placeholder="What do you want to explore? (or leave blank to use current content)"
                  className="w-full px-2.5 py-1.5 text-xs border border-gray-200 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white placeholder-gray-400 focus:outline-none focus:ring-1 focus:ring-blue-400"
                  onKeyDown={e => { if (e.key === 'Enter') { setActivePopover(null); handleCrossNotebook(); } }}
                />
              </div>
            </CanvasActionPopover>
          </div>

          {/* ── Pill row ─────────────────────────────────────────────────── */}
          <div className="flex items-center gap-1.5 px-3 py-1.5 overflow-x-auto scrollbar-hide">
            {ACTIONS.map(action => {
              const enabled = action.enabled(ctx.canvasItems, notebookId);
              const loading = actionLoading === action.id;
              const isActive = activePopover === action.id;
              // Check if a canvas item from this action is still processing in the background
              const CANVAS_TYPE_MAP: Record<string, string> = { docs: 'document', audio: 'audio', video: 'video', visual: 'visual', quiz: 'quiz' };
              const canvasType = CANVAS_TYPE_MAP[action.id];
              const working = loading || (canvasType && ctx.canvasItems.some(
                item => item.type === canvasType && (item.status === 'generating' || item.status === 'processing')
              ));
              return (
                <button
                  key={action.id}
                  onClick={() => {
                    if (loading || !enabled) return;
                    setActivePopover(isActive ? null : action.id);
                  }}
                  disabled={!enabled || (!!actionLoading && !isActive)}
                  className={`flex items-center gap-1 px-2.5 py-1.5 rounded-full text-xs font-medium whitespace-nowrap transition-all ${
                    working
                      ? 'bg-blue-100 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400 animate-pulse'
                      : isActive
                        ? 'bg-blue-100 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400 ring-1 ring-blue-400'
                        : enabled && !actionLoading
                          ? 'bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-600 hover:shadow-sm'
                          : 'bg-gray-50 dark:bg-gray-800 text-gray-400 dark:text-gray-600 cursor-not-allowed'
                  }`}
                  title={action.label}
                >
                  <span className="text-xs">{action.icon}</span>
                  <span>{loading ? '...' : action.shortLabel}</span>
                </button>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
};
