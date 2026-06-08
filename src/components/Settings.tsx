/**
 * Settings Component
 * Manage API keys and application configuration
 */

import React, { useState } from 'react';
import { MemorySettings } from './MemorySettings';
import { CredentialLocker } from './CredentialLocker';
import { ProfileSection } from './settings/ProfileSection';
import { UpdatesSection } from './settings/UpdatesSection';
import { APIKeysSection } from './settings/APIKeysSection';
import { CuratorSettings } from './CuratorSettings';
import { CorrespondentSettings } from './CorrespondentSettings';
import { TemplatesSection } from './settings/TemplatesSection';
import { VoiceProfileSection } from './settings/VoiceProfileSection';

class SettingsErrorBoundary extends React.Component<
  { children: React.ReactNode; fallbackLabel: string },
  { hasError: boolean; error: string }
> {
  state = { hasError: false, error: '' };
  static getDerivedStateFromError(err: Error) {
    return { hasError: true, error: err.message || 'Unknown error' };
  }
  render() {
    if (this.state.hasError) {
      return (
        <div className="p-6 text-center space-y-3">
          <p className="text-sm font-medium text-red-600 dark:text-red-400">
            {this.props.fallbackLabel} failed to load
          </p>
          <p className="text-xs text-gray-500 dark:text-gray-400">{this.state.error}</p>
          <button
            onClick={() => this.setState({ hasError: false, error: '' })}
            className="px-3 py-1.5 text-xs font-medium bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-300 rounded-lg hover:bg-gray-200 dark:hover:bg-gray-600"
          >
            Retry
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

type SectionId =
    | 'profile' | 'voice'
    | 'api-keys' | 'credentials' | 'correspondent'
    | 'curator' | 'memory'
    | 'templates' | 'updates';

interface SectionDef {
    id: SectionId;
    label: string;
    icon: string;
    accent: 'blue' | 'purple' | 'amber';
}

// Grouped vertical sidebar (2026-06-08) — replaces the horizontal tab strip
// that overflowed once Correspondent was added. Order within group + group
// order are the navigational hierarchy.
const SECTION_GROUPS: { title: string; items: SectionDef[] }[] = [
    {
        title: 'You',
        items: [
            { id: 'profile',       label: 'Profile', icon: '👤', accent: 'blue' },
            { id: 'voice',         label: 'Voice',   icon: '🗣️', accent: 'blue' },
        ],
    },
    {
        title: 'Connections',
        items: [
            { id: 'api-keys',      label: 'API Keys',      icon: '🔑', accent: 'blue' },
            { id: 'credentials',   label: 'Site Logins',   icon: '🔐', accent: 'blue' },
            { id: 'correspondent', label: 'Correspondent', icon: '📬', accent: 'amber' },
        ],
    },
    {
        title: 'Agents & Memory',
        items: [
            { id: 'curator', label: 'Curator', icon: '🧭', accent: 'purple' },
            { id: 'memory',  label: 'Memory',  icon: '🧠', accent: 'blue' },
        ],
    },
    {
        title: 'Workspace',
        items: [
            { id: 'templates', label: 'Templates', icon: '📊', accent: 'blue' },
            { id: 'updates',   label: 'Updates',   icon: '🔄', accent: 'blue' },
        ],
    },
];

const ACCENT_CLASSES: Record<SectionDef['accent'], string> = {
    blue:   'border-blue-600 bg-blue-50 text-blue-700 dark:bg-blue-900/20 dark:text-blue-300',
    purple: 'border-purple-600 bg-purple-50 text-purple-700 dark:bg-purple-900/20 dark:text-purple-300',
    amber:  'border-amber-600 bg-amber-50 text-amber-700 dark:bg-amber-900/20 dark:text-amber-300',
};

export const Settings: React.FC = () => {
    const [error, setError] = useState<string | null>(null);
    const [success, setSuccess] = useState<string | null>(null);
    const [activeSection, setActiveSection] = useState<SectionId>('api-keys');

    return (
        <div className="p-4 max-w-5xl mx-auto">
            <div className="flex gap-4 min-h-[480px]">
                {/* Left rail */}
                <nav className="w-44 flex-shrink-0 border-r dark:border-gray-700 pr-2 space-y-3">
                    {SECTION_GROUPS.map((group) => (
                        <div key={group.title}>
                            <p className="px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500">
                                {group.title}
                            </p>
                            <div className="space-y-0.5">
                                {group.items.map((item) => {
                                    const active = activeSection === item.id;
                                    return (
                                        <button
                                            key={item.id}
                                            onClick={() => setActiveSection(item.id)}
                                            className={`w-full flex items-center gap-2 px-2 py-1.5 text-sm rounded-md text-left transition-colors border-l-2 ${
                                                active
                                                    ? ACCENT_CLASSES[item.accent]
                                                    : 'border-transparent text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-800 hover:text-gray-800 dark:hover:text-gray-200'
                                            }`}
                                        >
                                            <span className="text-base leading-none">{item.icon}</span>
                                            <span className="font-medium">{item.label}</span>
                                        </button>
                                    );
                                })}
                            </div>
                        </div>
                    ))}
                </nav>

                {/* Content pane */}
                <div className="flex-1 min-w-0">
                    {error && (
                        <div className="mb-4 p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg text-sm text-red-700 dark:text-red-400">
                            {error}
                        </div>
                    )}
                    {success && (
                        <div className="mb-4 p-3 bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800 rounded-lg text-sm text-green-700 dark:text-green-400">
                            {success}
                        </div>
                    )}

                    {activeSection === 'profile' && <ProfileSection setError={setError} setSuccess={setSuccess} />}
                    {activeSection === 'voice' && <SettingsErrorBoundary fallbackLabel="Voice Profile"><VoiceProfileSection /></SettingsErrorBoundary>}
                    {activeSection === 'api-keys' && <APIKeysSection setError={setError} setSuccess={setSuccess} />}
                    {activeSection === 'credentials' && <CredentialLocker />}
                    {activeSection === 'memory' && <SettingsErrorBoundary fallbackLabel="Memory"><MemorySettings /></SettingsErrorBoundary>}
                    {activeSection === 'curator' && <SettingsErrorBoundary fallbackLabel="Curator"><CuratorSettings /></SettingsErrorBoundary>}
                    {activeSection === 'correspondent' && <SettingsErrorBoundary fallbackLabel="Correspondent"><CorrespondentSettings /></SettingsErrorBoundary>}
                    {activeSection === 'templates' && <TemplatesSection setError={setError} setSuccess={setSuccess} />}
                    {activeSection === 'updates' && <UpdatesSection />}
                </div>
            </div>
        </div>
    );
};
