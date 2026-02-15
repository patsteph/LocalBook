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

interface SettingsProps {
    onClose?: () => void;
}

export const Settings: React.FC<SettingsProps> = ({ onClose }) => {
    const [error, setError] = useState<string | null>(null);
    const [success, setSuccess] = useState<string | null>(null);
    const [activeSection, setActiveSection] = useState<'profile' | 'api-keys' | 'credentials' | 'memory' | 'updates'>('api-keys');

    return (
        <div className="p-6 max-w-4xl mx-auto">
            <div className="flex justify-between items-center mb-6">
                <h2 className="text-2xl font-bold text-gray-900 dark:text-white">Settings</h2>
                {onClose && (
                    <button
                        onClick={onClose}
                        className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
                    >
                        <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                        </svg>
                    </button>
                )}
            </div>

            {/* Settings Navigation */}
            <div className="flex border-b dark:border-gray-700 mb-6">
                <button
                    onClick={() => setActiveSection('profile')}
                    className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                        activeSection === 'profile'
                            ? 'border-indigo-600 text-indigo-600 dark:text-indigo-400'
                            : 'border-transparent text-gray-600 dark:text-gray-400 hover:text-gray-900'
                    }`}
                >
                    👤 Profile
                </button>
                <button
                    onClick={() => setActiveSection('api-keys')}
                    className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                        activeSection === 'api-keys'
                            ? 'border-blue-600 text-blue-600 dark:text-blue-400'
                            : 'border-transparent text-gray-600 dark:text-gray-400 hover:text-gray-900'
                    }`}
                >
                    🔑 API Keys
                </button>
                <button
                    onClick={() => setActiveSection('credentials')}
                    className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                        activeSection === 'credentials'
                            ? 'border-amber-600 text-amber-600 dark:text-amber-400'
                            : 'border-transparent text-gray-600 dark:text-gray-400 hover:text-gray-900'
                    }`}
                >
                    🔐 Site Logins
                </button>
                <button
                    onClick={() => setActiveSection('memory')}
                    className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                        activeSection === 'memory'
                            ? 'border-purple-600 text-purple-600 dark:text-purple-400'
                            : 'border-transparent text-gray-600 dark:text-gray-400 hover:text-gray-900'
                    }`}
                >
                    🧠 Memory
                </button>
                <button
                    onClick={() => setActiveSection('updates')}
                    className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                        activeSection === 'updates'
                            ? 'border-green-600 text-green-600 dark:text-green-400'
                            : 'border-transparent text-gray-600 dark:text-gray-400 hover:text-gray-900'
                    }`}
                >
                    🔄 Updates
                </button>
            </div>

            {error && (
                <div className="mb-4 p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded text-sm text-red-700 dark:text-red-400">
                    {error}
                </div>
            )}

            {success && (
                <div className="mb-4 p-3 bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800 rounded text-sm text-green-700 dark:text-green-400">
                    {success}
                </div>
            )}

            {activeSection === 'profile' && <ProfileSection setError={setError} setSuccess={setSuccess} />}
            {activeSection === 'api-keys' && <APIKeysSection setError={setError} setSuccess={setSuccess} />}
            {activeSection === 'credentials' && <CredentialLocker />}
            {activeSection === 'memory' && <MemorySettings />}
            {activeSection === 'updates' && <UpdatesSection />}
        </div>
    );
};
