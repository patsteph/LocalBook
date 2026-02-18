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

export const Settings: React.FC = () => {
    const [error, setError] = useState<string | null>(null);
    const [success, setSuccess] = useState<string | null>(null);
    const [activeSection, setActiveSection] = useState<'profile' | 'api-keys' | 'credentials' | 'memory' | 'curator' | 'updates'>('api-keys');

    return (
        <div className="p-4 max-w-4xl mx-auto">
            {/* Settings Navigation */}
            <div className="flex border-b dark:border-gray-700 mb-4">
                <button
                    onClick={() => setActiveSection('profile')}
                    className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                        activeSection === 'profile'
                            ? 'border-blue-600 text-blue-600 dark:text-blue-400'
                            : 'border-transparent text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300'
                    }`}
                >
                    👤 Profile
                </button>
                <button
                    onClick={() => setActiveSection('api-keys')}
                    className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                        activeSection === 'api-keys'
                            ? 'border-blue-600 text-blue-600 dark:text-blue-400'
                            : 'border-transparent text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300'
                    }`}
                >
                    🔑 API Keys
                </button>
                <button
                    onClick={() => setActiveSection('credentials')}
                    className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                        activeSection === 'credentials'
                            ? 'border-blue-600 text-blue-600 dark:text-blue-400'
                            : 'border-transparent text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300'
                    }`}
                >
                    🔐 Site Logins
                </button>
                <button
                    onClick={() => setActiveSection('memory')}
                    className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                        activeSection === 'memory'
                            ? 'border-blue-600 text-blue-600 dark:text-blue-400'
                            : 'border-transparent text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300'
                    }`}
                >
                    🧠 Memory
                </button>
                <button
                    onClick={() => setActiveSection('curator')}
                    className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                        activeSection === 'curator'
                            ? 'border-purple-600 text-purple-600 dark:text-purple-400'
                            : 'border-transparent text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300'
                    }`}
                >
                    🧭 Curator
                </button>
                <button
                    onClick={() => setActiveSection('updates')}
                    className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                        activeSection === 'updates'
                            ? 'border-blue-600 text-blue-600 dark:text-blue-400'
                            : 'border-transparent text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300'
                    }`}
                >
                    🔄 Updates
                </button>
            </div>

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
            {activeSection === 'api-keys' && <APIKeysSection setError={setError} setSuccess={setSuccess} />}
            {activeSection === 'credentials' && <CredentialLocker />}
            {activeSection === 'memory' && <MemorySettings />}
            {activeSection === 'curator' && <CuratorSettings />}
            {activeSection === 'updates' && <UpdatesSection />}
        </div>
    );
};
