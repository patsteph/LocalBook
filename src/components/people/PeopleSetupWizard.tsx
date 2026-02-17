import React, { useState, useEffect } from 'react';
import { Button } from '../shared/Button';
import { Modal } from '../shared/Modal';
import { peopleService } from '../../services/people';
import {
  Plus, Trash2, Linkedin, Github, Globe,
  CheckCircle2, AlertCircle, Loader2, Shield
} from 'lucide-react';

interface PeopleSetupWizardProps {
  notebookId: string;
  notebookName: string;
  isOpen: boolean;
  coachingEnabled?: boolean;
  onClose: () => void;
  onComplete: () => void;
}

interface TeamMember {
  name: string;
  social_links: Record<string, string>;
  current_role: string;
  initial_notes: string;
}

interface AuthStatus {
  [platform: string]: {
    platform: string;
    authenticated: boolean;
    reason?: string;
  };
}

const EMPTY_MEMBER: TeamMember = {
  name: '',
  social_links: {},
  current_role: '',
  initial_notes: '',
};

const PLATFORM_CONFIG = [
  { key: 'linkedin', label: 'LinkedIn', icon: Linkedin, placeholder: 'https://linkedin.com/in/username', needsAuth: true },
  { key: 'twitter', label: 'Twitter / X', icon: Globe, placeholder: 'https://x.com/username', needsAuth: true },
  { key: 'github', label: 'GitHub', icon: Github, placeholder: 'https://github.com/username', needsAuth: false },
  { key: 'instagram', label: 'Instagram', icon: Globe, placeholder: 'https://instagram.com/username', needsAuth: true },
  { key: 'personal_site', label: 'Personal Site', icon: Globe, placeholder: 'https://example.com', needsAuth: false },
];

type WizardStep = 'members' | 'auth' | 'schedule' | 'saving';

export const PeopleSetupWizard: React.FC<PeopleSetupWizardProps> = ({
  notebookId,
  notebookName,
  isOpen,
  coachingEnabled = false,
  onClose,
  onComplete,
}) => {
  const [step, setStep] = useState<WizardStep>('members');
  const [members, setMembers] = useState<TeamMember[]>([{ ...EMPTY_MEMBER }]);
  const [schedule, setSchedule] = useState('weekly');
  const [authStatus, setAuthStatus] = useState<AuthStatus>({});
  const [authenticating, setAuthenticating] = useState<string | null>(null);
  const [, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (isOpen) {
      fetchAuthStatus();
    }
  }, [isOpen]);

  const fetchAuthStatus = async () => {
    try {
      const data = await peopleService.getAuthStatus();
      setAuthStatus(data);
    } catch (e) {
      console.error('Failed to fetch auth status:', e);
    }
  };

  // =========================================================================
  // Step navigation
  // =========================================================================

  const canProceedFromMembers = () => {
    return members.some(m => m.name.trim() !== '');
  };

  // Check if any members have social links that require auth (show step regardless of status)
  const hasAuthPlatforms = () => {
    const allLinks = members.flatMap(m => Object.keys(m.social_links).filter(k => m.social_links[k]));
    return PLATFORM_CONFIG.some(p => p.needsAuth && allLinks.includes(p.key));
  };

  // Check if all auth-requiring platforms are already connected
  const allAuthConnected = () => {
    const allLinks = members.flatMap(m => Object.keys(m.social_links).filter(k => m.social_links[k]));
    const platformsNeedingAuth = PLATFORM_CONFIG
      .filter(p => p.needsAuth && allLinks.includes(p.key))
      .map(p => p.key);
    return platformsNeedingAuth.length > 0 && platformsNeedingAuth.every(p => authStatus[p]?.authenticated);
  };

  // Auto-advance past auth step after a brief pause if all platforms already connected
  useEffect(() => {
    if (step === 'auth' && allAuthConnected()) {
      const timer = setTimeout(() => setStep('schedule'), 1500);
      return () => clearTimeout(timer);
    }
  }, [step, authStatus]);

  const handleNext = () => {
    if (step === 'members') {
      if (hasAuthPlatforms()) {
        setStep('auth');
      } else {
        setStep('schedule');
      }
    } else if (step === 'auth') {
      setStep('schedule');
    } else if (step === 'schedule') {
      handleSave();
    }
  };

  const handleBack = () => {
    if (step === 'auth') setStep('members');
    else if (step === 'schedule') {
      if (hasAuthPlatforms()) setStep('auth');
      else setStep('members');
    }
  };

  // =========================================================================
  // Member management
  // =========================================================================

  const updateMember = (index: number, field: keyof TeamMember, value: any) => {
    const updated = [...members];
    updated[index] = { ...updated[index], [field]: value };
    setMembers(updated);
  };

  const updateMemberLink = (index: number, platform: string, url: string) => {
    const updated = [...members];
    updated[index] = {
      ...updated[index],
      social_links: { ...updated[index].social_links, [platform]: url },
    };
    setMembers(updated);
  };

  const addMember = () => {
    setMembers([...members, { ...EMPTY_MEMBER }]);
  };

  const removeMember = (index: number) => {
    if (members.length > 1) {
      setMembers(members.filter((_, i) => i !== index));
    }
  };

  // =========================================================================
  // Auth
  // =========================================================================

  const handleAuthenticate = async (platform: string) => {
    setAuthenticating(platform);
    try {
      await peopleService.authenticate(platform, {});
      await fetchAuthStatus();
    } catch (e) {
      setError('Authentication failed. Please try again.');
    } finally {
      setAuthenticating(null);
    }
  };

  // =========================================================================
  // Save
  // =========================================================================

  const handleSave = async () => {
    setSaving(true);
    setStep('saving');
    setError(null);

    try {
      const validMembers = members.filter(m => m.name.trim() !== '');

      await peopleService.updateConfig(notebookId, {
        notebook_name: notebookName,
        coaching_enabled: coachingEnabled,
        members: validMembers.map(m => ({
          name: m.name.trim(),
          social_links: Object.fromEntries(
            Object.entries(m.social_links).filter(([_, v]) => v.trim())
          ),
          current_role: m.current_role,
          initial_notes: m.initial_notes,
        })),
        collection_schedule: schedule,
      });

      // Trigger first collection
      peopleService.collectAll(notebookId)
        .then(() => console.log('[People Wizard] First collection triggered'))
        .catch(err => console.error('[People Wizard] First collection failed:', err));

      onComplete();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save');
      setStep('schedule');
    } finally {
      setSaving(false);
    }
  };

  // =========================================================================
  // Step indicators
  // =========================================================================

  const steps: WizardStep[] = hasAuthPlatforms()
    ? ['members', 'auth', 'schedule']
    : ['members', 'schedule'];

  const stepIndex = steps.indexOf(step);

  // =========================================================================
  // Render
  // =========================================================================

  const renderMembersStep = () => (
    <div className="space-y-6">
      <p className="text-gray-500 dark:text-gray-400 text-sm">
        Add the people you want to track. Paste their social profile URLs to enable automatic collection.
      </p>

      {members.map((member, idx) => (
        <div key={idx} className="border border-gray-200 dark:border-gray-700 rounded-lg p-4 space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium text-gray-500 dark:text-gray-400">
              Person {idx + 1}
            </span>
            {members.length > 1 && (
              <button
                onClick={() => removeMember(idx)}
                className="text-gray-400 hover:text-red-500 dark:hover:text-red-400"
              >
                <Trash2 className="w-4 h-4" />
              </button>
            )}
          </div>

          <input
            type="text"
            value={member.name}
            onChange={e => updateMember(idx, 'name', e.target.value)}
            placeholder="Full Name"
            className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none"
            autoFocus={idx === 0}
          />

          <input
            type="text"
            value={member.current_role}
            onChange={e => updateMember(idx, 'current_role', e.target.value)}
            placeholder="Role (e.g., Senior Engineer)"
            className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none"
          />

          <div className="space-y-2">
            {PLATFORM_CONFIG.map(platform => (
              <div key={platform.key} className="flex items-center gap-2">
                <platform.icon className="w-4 h-4 text-gray-400 flex-shrink-0" />
                <input
                  type="url"
                  value={member.social_links[platform.key] || ''}
                  onChange={e => updateMemberLink(idx, platform.key, e.target.value)}
                  placeholder={platform.placeholder}
                  className="flex-1 px-3 py-1.5 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 text-xs focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none"
                />
              </div>
            ))}
          </div>

          <textarea
            value={member.initial_notes}
            onChange={e => updateMember(idx, 'initial_notes', e.target.value)}
            placeholder="Initial coaching notes (optional, private)"
            rows={2}
            className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none resize-none"
          />
        </div>
      ))}

      <button
        onClick={addMember}
        className="flex items-center gap-2 text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300 text-sm font-medium"
      >
        <Plus className="w-4 h-4" /> Add another person
      </button>
    </div>
  );

  const renderAuthStep = () => {
    const allLinks = members.flatMap(m =>
      Object.keys(m.social_links).filter(k => m.social_links[k])
    );
    const uniquePlatforms = [...new Set(allLinks)];
    const platformsNeedingAuth = PLATFORM_CONFIG.filter(
      p => p.needsAuth && uniquePlatforms.includes(p.key)
    );

    return (
      <div className="space-y-4">
        <div className="flex items-start gap-3 p-3 bg-blue-50 dark:bg-blue-900/20 rounded-lg">
          <Shield className="w-5 h-5 text-blue-500 flex-shrink-0 mt-0.5" />
          <div className="text-sm text-gray-600 dark:text-gray-300">
            <p className="font-medium text-gray-800 dark:text-gray-100">Secure Session Authentication</p>
            <p className="mt-1">
              A browser will open for you to log in. Your session is encrypted with AES-128
              and stored locally. No passwords are saved — only the session cookie.
            </p>
          </div>
        </div>

        {platformsNeedingAuth.map(platform => {
          const status = authStatus[platform.key];
          const isConnected = status?.authenticated;
          const isLoading = authenticating === platform.key;

          return (
            <div
              key={platform.key}
              className="flex items-center justify-between p-4 border border-gray-200 dark:border-gray-700 rounded-lg"
            >
              <div className="flex items-center gap-3">
                <platform.icon className="w-5 h-5 text-gray-500" />
                <div>
                  <p className="font-medium text-gray-900 dark:text-gray-100">{platform.label}</p>
                  <p className="text-xs text-gray-500 dark:text-gray-400">
                    {isConnected ? 'Connected' : 'Not connected'}
                  </p>
                </div>
              </div>

              {isConnected ? (
                <span className="flex items-center gap-1 text-green-600 dark:text-green-400 text-sm">
                  <CheckCircle2 className="w-4 h-4" /> Connected
                </span>
              ) : (
                <Button
                  size="sm"
                  onClick={() => handleAuthenticate(platform.key)}
                  disabled={isLoading || authenticating !== null}
                >
                  {isLoading ? (
                    <span className="flex items-center gap-1">
                      <Loader2 className="w-3 h-3 animate-spin" /> Waiting...
                    </span>
                  ) : (
                    'Connect'
                  )}
                </Button>
              )}
            </div>
          );
        })}

        {platformsNeedingAuth.length === 0 && (
          <p className="text-gray-500 dark:text-gray-400 text-sm text-center py-4">
            No platforms need authentication. GitHub and personal sites work without login.
          </p>
        )}

        <p className="text-xs text-gray-400 dark:text-gray-500 text-center">
          You can skip this step — platforms without auth will be skipped during collection.
        </p>
      </div>
    );
  };

  const renderScheduleStep = () => (
    <div className="space-y-4">
      <p className="text-gray-500 dark:text-gray-400 text-sm">
        How often should profiles be updated?
      </p>

      {[
        { value: 'daily', label: 'Daily', desc: 'Check for new posts and activity every day' },
        { value: 'weekly', label: 'Weekly', desc: 'Update profiles once a week (recommended)' },
        { value: 'manual', label: 'Manual Only', desc: 'Only collect when you click "Collect Now"' },
      ].map(option => (
        <button
          key={option.value}
          onClick={() => setSchedule(option.value)}
          className={`w-full text-left p-4 rounded-lg border-2 transition-colors ${
            schedule === option.value
              ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/20'
              : 'border-gray-200 dark:border-gray-700 hover:border-gray-300 dark:hover:border-gray-600'
          }`}
        >
          <span className="font-medium text-gray-900 dark:text-gray-100">{option.label}</span>
          <p className="text-sm text-gray-500 dark:text-gray-400 mt-0.5">{option.desc}</p>
        </button>
      ))}
    </div>
  );

  const renderSavingStep = () => (
    <div className="flex flex-col items-center justify-center py-12">
      <Loader2 className="w-10 h-10 animate-spin text-blue-500 mb-4" />
      <p className="text-gray-600 dark:text-gray-300 font-medium">Setting up your team...</p>
      <p className="text-sm text-gray-400 dark:text-gray-500 mt-1">
        Saving profiles and starting first collection
      </p>
    </div>
  );

  const stepTitles: Record<WizardStep, string> = {
    members: 'Add Team Members',
    auth: 'Connect Social Platforms',
    schedule: 'Collection Schedule',
    saving: 'Setting Up...',
  };

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={stepTitles[step]}
      size="lg"
    >
      <div className="p-6">
        {/* Progress dots */}
        {step !== 'saving' && (
          <div className="flex justify-center gap-2 mb-6">
            {steps.map((s, i) => (
              <div
                key={s}
                className={`w-2.5 h-2.5 rounded-full transition-colors ${
                  i <= stepIndex
                    ? 'bg-blue-500'
                    : 'bg-gray-300 dark:bg-gray-600'
                }`}
              />
            ))}
          </div>
        )}

        {/* Step content */}
        {step === 'members' && renderMembersStep()}
        {step === 'auth' && renderAuthStep()}
        {step === 'schedule' && renderScheduleStep()}
        {step === 'saving' && renderSavingStep()}

        {/* Error */}
        {error && (
          <div className="mt-4 flex items-center gap-2 text-red-600 dark:text-red-400 text-sm">
            <AlertCircle className="w-4 h-4" /> {error}
          </div>
        )}

        {/* Navigation */}
        {step !== 'saving' && (
          <div className="flex justify-between items-center mt-6 pt-4 border-t border-gray-200 dark:border-gray-700">
            <div>
              {step === 'members' ? (
                <Button variant="secondary" onClick={onClose}>
                  Cancel
                </Button>
              ) : (
                <Button variant="secondary" onClick={handleBack}>
                  Back
                </Button>
              )}
            </div>
            <div className="flex gap-2">
              {step === 'auth' && (
                <Button variant="secondary" onClick={() => setStep('schedule')}>
                  Skip
                </Button>
              )}
              <Button
                onClick={handleNext}
                disabled={step === 'members' && !canProceedFromMembers()}
              >
                {step === 'schedule' ? 'Complete Setup' : 'Next'}
              </Button>
            </div>
          </div>
        )}
      </div>
    </Modal>
  );
};
