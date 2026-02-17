import React, { useState } from 'react';
import { Button } from '../shared/Button';
import { Modal } from '../shared/Modal';
import { curatorService } from '../../services/curatorApi';
import { collectorService } from '../../services/collector';
import { SourceReview } from './SourceReview';
import { PeopleSetupWizard } from '../people/PeopleSetupWizard';
import './CollectorSetupWizard.css';

const COACHING_KEYWORDS = [
  'coach', 'coaching', '1:1', '1-on-1', 'one on one', 'one-on-one',
  'team member', 'direct report', 'personal development', 'mentoring',
  'mentor', 'performance review', 'people management', 'team management',
  'growth plan', 'development plan', 'leadership development',
  'personnel', 'people', 'team',
];

function looksLikeCoaching(subject: string, intent: string): boolean {
  const text = `${subject} ${intent}`.toLowerCase();
  return COACHING_KEYWORDS.some(kw => text.includes(kw));
}

// =========================================================================
// Quick-Start Templates
// =========================================================================

interface Template {
  id: string;
  icon: string;
  label: string;
  description: string;
  defaults: {
    collection_mode: string;
    approval_mode: string;
    focus_suggestions: string[];
    intent_template: string;
  };
}

const TEMPLATES: Template[] = [
  {
    id: 'company_intel',
    icon: '\u{1F3E2}',
    label: 'Company Intel',
    description: 'Track a company + competitors',
    defaults: {
      collection_mode: 'hybrid',
      approval_mode: 'mixed',
      focus_suggestions: ['News', 'Financials', 'Competitors', 'Executive Changes'],
      intent_template: 'Track {subject} news, financials, competitive positioning, and industry developments.',
    }
  },
  {
    id: 'industry_watch',
    icon: '\u{1F4CA}',
    label: 'Industry Watch',
    description: 'Monitor an industry or sector',
    defaults: {
      collection_mode: 'hybrid',
      approval_mode: 'mixed',
      focus_suggestions: ['Trends', 'Key Players', 'Market Data', 'Regulation'],
      intent_template: 'Monitor the {subject} industry for trends, key players, market data, and regulatory changes.',
    }
  },
  {
    id: 'topic_research',
    icon: '\u{1F52C}',
    label: 'Topic Research',
    description: 'Academic or deep research',
    defaults: {
      collection_mode: 'hybrid',
      approval_mode: 'show_me',
      focus_suggestions: ['Papers', 'Key Authors', 'Methods', 'Applications'],
      intent_template: 'Deep research on {subject} — papers, key authors, methodologies, and real-world applications.',
    }
  },
  {
    id: 'project_archive',
    icon: '\u{1F4C1}',
    label: 'Project Archive',
    description: 'Team docs + light discovery',
    defaults: {
      collection_mode: 'manual',
      approval_mode: 'trust_me',
      focus_suggestions: ['Updates', 'Deliverables', 'Dependencies'],
      intent_template: 'Archive and track {subject} project documents, updates, and deliverables.',
    }
  },
  {
    id: 'people',
    icon: '\u{1F465}',
    label: 'People',
    description: 'Coaching & performance management',
    defaults: {
      collection_mode: 'hybrid',
      approval_mode: 'mixed',
      focus_suggestions: ['Activity', 'Growth', 'Coaching'],
      intent_template: 'Profile and track {subject} for coaching and development.',
    }
  },
  {
    id: 'custom',
    icon: '\u{2728}',
    label: 'Custom',
    description: 'Define everything yourself',
    defaults: {
      collection_mode: 'hybrid',
      approval_mode: 'mixed',
      focus_suggestions: [],
      intent_template: '',
    }
  },
];

// =========================================================================
// Component
// =========================================================================

interface SuggestedConfig {
  suggested_subject?: string;
  suggested_intent?: string;
  suggested_focus_areas?: string[];
  suggested_template?: string;
  curator_message?: string;
}

interface CollectorSetupWizardProps {
  notebookId: string;
  notebookName: string;
  isOpen: boolean;
  onClose: () => void;
  onComplete: (curatorFollowUp?: string) => void;
  initialConfig?: SuggestedConfig;
}

type WizardScreen = 'template' | 'refine';

export const CollectorSetupWizard: React.FC<CollectorSetupWizardProps> = ({
  notebookId,
  notebookName,
  isOpen,
  onClose,
  onComplete,
  initialConfig,
}) => {
  // If initialConfig is provided (e.g. from file-drop), skip to refinement
  const [screen, setScreen] = useState<WizardScreen>(initialConfig ? 'refine' : 'template');
  const [selectedTemplate, setSelectedTemplate] = useState<Template | null>(() => {
    if (initialConfig?.suggested_template) {
      return TEMPLATES.find(t => t.id === initialConfig.suggested_template) || TEMPLATES[4]; // fallback to Custom
    }
    return null;
  });

  // Refinement state — pre-populate from initialConfig if available
  const [subject, setSubject] = useState(initialConfig?.suggested_subject || notebookName || '');
  const [focusAreas, setFocusAreas] = useState<string[]>(initialConfig?.suggested_focus_areas || []);
  const [chipInput, setChipInput] = useState('');
  const [approvalMode, setApprovalMode] = useState(
    selectedTemplate?.defaults.approval_mode || 'mixed'
  );
  const [collectionMode, setCollectionMode] = useState(
    selectedTemplate?.defaults.collection_mode || 'hybrid'
  );

  const [frequency, setFrequency] = useState('daily');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showSourceReview, setShowSourceReview] = useState(false);
  const [showPeopleWizard, setShowPeopleWizard] = useState(false);
  const [coachingEnabled, setCoachingEnabled] = useState(false);

  // Fetch Curator follow-up message after setup
  const fetchFollowUp = async (): Promise<string | undefined> => {
    try {
      const data = await curatorService.getSetupFollowup(notebookId);
      return data.message || undefined;
    } catch (err) {
      console.log('[Wizard] Follow-up fetch failed (non-fatal):', err);
    }
    return undefined;
  };

  // -----------------------------------------------------------------------
  // Template selection
  // -----------------------------------------------------------------------

  const handleTemplateSelect = (template: Template) => {
    // People template → skip refinement, go straight to PeopleSetupWizard
    if (template.id === 'people') {
      setCoachingEnabled(true);
      setShowPeopleWizard(true);
      return;
    }
    setSelectedTemplate(template);
    // Pre-populate from template defaults
    setFocusAreas(template.defaults.focus_suggestions);
    setApprovalMode(template.defaults.approval_mode);
    setCollectionMode(template.defaults.collection_mode);
    setScreen('refine');
  };

  // -----------------------------------------------------------------------
  // Refinement — chips
  // -----------------------------------------------------------------------

  const handleChipAdd = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' && chipInput.trim()) {
      e.preventDefault();
      if (!focusAreas.includes(chipInput.trim())) {
        setFocusAreas([...focusAreas, chipInput.trim()]);
      }
      setChipInput('');
    }
  };

  const handleChipRemove = (index: number) => {
    setFocusAreas(focusAreas.filter((_, i) => i !== index));
  };

  // -----------------------------------------------------------------------
  // Save config (same API contract as before)
  // -----------------------------------------------------------------------

  const saveConfig = async () => {
    if (!subject.trim()) return;
    setSaving(true);
    setError(null);

    try {
      // Generate intent from template
      const intent = selectedTemplate?.defaults.intent_template
        ? selectedTemplate.defaults.intent_template.replace('{subject}', subject.trim())
        : `Research on ${subject.trim()}`;

      const config = {
        name: notebookName,
        subject: subject.trim(),
        intent,
        focus_areas: focusAreas,
        collection_mode: collectionMode,
        approval_mode: approvalMode,
        schedule: { frequency, max_items_per_run: frequency === 'hourly' ? 5 : frequency.includes('hours') || frequency === 'twice_daily' ? 8 : 10 },
      };

      await collectorService.updateConfig(notebookId, config as any);

      // Check if this looks like a coaching/people notebook
      if (looksLikeCoaching(subject, intent)) {
        setCoachingEnabled(true);
        setShowPeopleWizard(true);
      } else {
        setShowSourceReview(true);
      }
    } catch (err) {
      console.error('Error saving collector config:', err);
      setError('Failed to save configuration. Please try again.');
    } finally {
      setSaving(false);
    }
  };

  // -----------------------------------------------------------------------
  // Post-setup flows (preserved from original)
  // -----------------------------------------------------------------------

  const handleSourceReviewComplete = async (sourcesAdded: number) => {
    console.log(`Source review complete: ${sourcesAdded} sources added`);
    // Don't setShowSourceReview(false) — onComplete unmounts the wizard from parent.
    // Collection is triggered by the parent's onComplete handler with proper UI feedback.

    const followUp = await fetchFollowUp();
    onComplete(followUp);
  };

  const handleSourceReviewCancel = async () => {
    // Don't setShowSourceReview(false) — onComplete unmounts the wizard.
    // Collection is triggered by the parent's onComplete handler with proper UI feedback.

    const followUp = await fetchFollowUp();
    onComplete(followUp);
  };

  // -----------------------------------------------------------------------
  // Render: People Wizard (coaching detected)
  // -----------------------------------------------------------------------

  if (showPeopleWizard) {
    return (
      <PeopleSetupWizard
        notebookId={notebookId}
        notebookName={notebookName}
        isOpen={true}
        coachingEnabled={coachingEnabled}
        onClose={() => {
          setShowPeopleWizard(false);
          onComplete();
        }}
        onComplete={() => {
          setShowPeopleWizard(false);
          onComplete();
        }}
      />
    );
  }

  // -----------------------------------------------------------------------
  // Render: Source Review
  // -----------------------------------------------------------------------

  if (showSourceReview) {
    const intent = selectedTemplate?.defaults.intent_template
      ? selectedTemplate.defaults.intent_template.replace('{subject}', subject.trim())
      : `Research on ${subject.trim()}`;
    return (
      <SourceReview
        notebookId={notebookId}
        subject={subject}
        intent={intent}
        focusAreas={focusAreas}
        onComplete={handleSourceReviewComplete}
        onCancel={handleSourceReviewCancel}
      />
    );
  }

  // -----------------------------------------------------------------------
  // Render: Screen 1 — Template Picker
  // -----------------------------------------------------------------------

  if (screen === 'template') {
    return (
      <Modal
        isOpen={isOpen}
        onClose={onClose}
        title="Set Up Your Collector"
        size="md"
      >
        <div className="collector-wizard">
          <div className="wizard-progress">
            <div className="progress-dot active" />
            <div className="progress-dot" />
          </div>

          <div className="wizard-content">
            <h3 className="wizard-question">What kind of research is this?</h3>
            <div className="template-grid">
              {TEMPLATES.map(template => (
                <button
                  key={template.id}
                  type="button"
                  className="template-card"
                  onClick={() => handleTemplateSelect(template)}
                >
                  <span className="template-card__icon">{template.icon}</span>
                  <span className="template-card__label">{template.label}</span>
                  <span className="template-card__desc">{template.description}</span>
                </button>
              ))}
            </div>
          </div>

          <div className="wizard-actions">
            <div className="actions-left">
              <Button variant="secondary" onClick={onClose}>
                Skip for now
              </Button>
            </div>
            <div className="actions-right" />
          </div>
        </div>
      </Modal>
    );
  }

  // -----------------------------------------------------------------------
  // Render: Screen 2 — Refinement Card
  // -----------------------------------------------------------------------

  const APPROVAL_OPTIONS = [
    { value: 'show_me', label: 'Manual' },
    { value: 'mixed', label: 'Mixed' },
    { value: 'trust_me', label: 'Auto' },
  ];

  const FREQUENCY_OPTIONS = [
    { value: 'hourly', label: 'Hourly' },
    { value: 'every_4_hours', label: '4 Hours' },
    { value: 'twice_daily', label: 'Twice Daily' },
    { value: 'daily', label: 'Daily' },
    { value: 'every_3_days', label: '3 Days' },
    { value: 'weekly', label: 'Weekly' },
    { value: 'manual', label: 'Manual' },
  ];

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="Set Up Your Collector"
      size="md"
    >
      <div className="collector-wizard">
        <div className="wizard-progress">
          <div className="progress-dot active" />
          <div className="progress-dot active" />
        </div>

        <div className="wizard-content">
          {/* Template badge */}
          <div className="refine-template-badge">
            <span>{selectedTemplate?.icon}</span>
            <span>{selectedTemplate?.label}</span>
          </div>

          {/* Subject */}
          <label className="refine-label">Subject</label>
          <input
            type="text"
            value={subject}
            onChange={e => setSubject(e.target.value)}
            placeholder="e.g., Costco, Tesla, Machine Learning..."
            className="wizard-input"
            autoFocus
          />

          {/* Focus areas (chips) */}
          <label className="refine-label refine-label--mt">Focus areas</label>
          <div className="wizard-chips-container">
            <div className="chips-list">
              {focusAreas.map((chip, index) => (
                <span key={index} className="chip">
                  {chip}
                  <button
                    type="button"
                    className="chip-remove"
                    onClick={() => handleChipRemove(index)}
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
            <input
              type="text"
              value={chipInput}
              onChange={e => setChipInput(e.target.value)}
              onKeyDown={handleChipAdd}
              placeholder="Add topic and press Enter"
              className="wizard-input chips-input"
            />
          </div>

          {/* Approval mode — compact toggle row */}
          <label className="refine-label refine-label--mt">Approval</label>
          <div className="approval-toggle-row">
            {APPROVAL_OPTIONS.map(opt => (
              <button
                key={opt.value}
                type="button"
                className={`approval-toggle ${approvalMode === opt.value ? 'approval-toggle--active' : ''}`}
                onClick={() => setApprovalMode(opt.value)}
              >
                {opt.label}
              </button>
            ))}
          </div>
          <p className="approval-toggle-hint">
            {approvalMode === 'show_me' && 'Review every item before it enters your notebook'}
            {approvalMode === 'mixed' && 'Auto-add high confidence, queue the rest for review'}
            {approvalMode === 'trust_me' && 'Collector adds relevant items automatically'}
          </p>

          {/* Collection frequency */}
          <label className="refine-label refine-label--mt">Check Frequency</label>
          <div className="approval-toggle-row" style={{ flexWrap: 'wrap', gap: '4px' }}>
            {FREQUENCY_OPTIONS.map(opt => (
              <button
                key={opt.value}
                type="button"
                className={`approval-toggle ${frequency === opt.value ? 'approval-toggle--active' : ''}`}
                onClick={() => setFrequency(opt.value)}
              >
                {opt.label}
              </button>
            ))}
          </div>
          <p className="approval-toggle-hint">
            {frequency === 'hourly' && 'Check for new content every hour'}
            {frequency === 'every_4_hours' && 'Check every 4 hours during the day'}
            {frequency === 'twice_daily' && 'Check morning and evening'}
            {frequency === 'daily' && 'Check once per day (recommended)'}
            {frequency === 'every_3_days' && 'Check every few days — low-traffic topics'}
            {frequency === 'weekly' && 'Weekly digest — minimal checking'}
            {frequency === 'manual' && 'Only collect when you click Collect Now'}
          </p>

          {error && <p className="wizard-error">{error}</p>}
        </div>

        <div className="wizard-actions">
          <div className="actions-left">
            <Button variant="secondary" onClick={() => { setScreen('template'); setSelectedTemplate(null); }}>
              Back
            </Button>
          </div>
          <div className="actions-right">
            <Button
              onClick={saveConfig}
              disabled={!subject.trim() || saving}
            >
              {saving ? 'Saving...' : 'Discover Sources →'}
            </Button>
          </div>
        </div>
      </div>
    </Modal>
  );
};

export default CollectorSetupWizard;
