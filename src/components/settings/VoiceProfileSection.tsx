import React, { useState, useEffect } from 'react';
import { API_BASE_URL } from '../../services/api';

interface VoiceProfile {
    vocabulary?: string;
    style_markers?: string;
    thinking_framework?: string;
    formality?: string;
    interests?: string[];
}

export const VoiceProfileSection: React.FC = () => {
    const [profile, setProfile] = useState<VoiceProfile | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        const fetchProfile = async () => {
            try {
                const res = await fetch(`${API_BASE_URL}/settings/voice-profile`);
                if (!res.ok) throw new Error('Failed to fetch voice profile');
                const data = await res.json();
                // It returns empty object if none exists
                setProfile(Object.keys(data).length > 0 ? data : null);
            } catch (err) {
                setError(err instanceof Error ? err.message : 'Unknown error');
            } finally {
                setLoading(false);
            }
        };

        fetchProfile();
    }, []);

    if (loading) {
        return <div className="text-center py-8 text-gray-500">Loading your voice profile...</div>;
    }

    if (error) {
        return <div className="text-red-500 py-4">{error}</div>;
    }

    return (
        <div className="space-y-4">
            <div>
                <h3 className="text-lg font-semibold text-gray-900 dark:text-white mb-1">Your Voice Profile</h3>
                <p className="text-sm text-gray-600 dark:text-gray-400 mb-6">
                    This profile is automatically built over time by analyzing your writing style across notes and chat. It helps LocalBook adapt to your unique way of communicating.
                </p>
            </div>

            {!profile ? (
                <div className="p-4 bg-gray-50 dark:bg-gray-800 rounded-lg text-center text-sm text-gray-600 dark:text-gray-400">
                    Your voice profile is currently empty. Keep writing notes and chatting to provide more samples!
                </div>
            ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div className="p-4 border border-gray-200 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800">
                        <h4 className="text-sm font-semibold text-gray-900 dark:text-gray-100 mb-2">Vocabulary</h4>
                        <p className="text-sm text-gray-700 dark:text-gray-300">{profile.vocabulary || 'Not enough data'}</p>
                    </div>
                    
                    <div className="p-4 border border-gray-200 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800">
                        <h4 className="text-sm font-semibold text-gray-900 dark:text-gray-100 mb-2">Style Markers</h4>
                        <p className="text-sm text-gray-700 dark:text-gray-300">{profile.style_markers || 'Not enough data'}</p>
                    </div>

                    <div className="p-4 border border-gray-200 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800">
                        <h4 className="text-sm font-semibold text-gray-900 dark:text-gray-100 mb-2">Thinking Framework</h4>
                        <p className="text-sm text-gray-700 dark:text-gray-300">{profile.thinking_framework || 'Not enough data'}</p>
                    </div>

                    <div className="p-4 border border-gray-200 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800">
                        <h4 className="text-sm font-semibold text-gray-900 dark:text-gray-100 mb-2">Formality</h4>
                        <p className="text-sm text-gray-700 dark:text-gray-300">{profile.formality || 'Not enough data'}</p>
                    </div>

                    {profile.interests && profile.interests.length > 0 && (
                        <div className="md:col-span-2 p-4 border border-gray-200 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800">
                            <h4 className="text-sm font-semibold text-gray-900 dark:text-gray-100 mb-2">Inferred Interests</h4>
                            <div className="flex flex-wrap gap-2">
                                {profile.interests.map((interest, idx) => (
                                    <span key={idx} className="px-2 py-1 bg-blue-100 dark:bg-blue-900 text-blue-800 dark:text-blue-100 text-xs rounded-full">
                                        {interest}
                                    </span>
                                ))}
                            </div>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
};
