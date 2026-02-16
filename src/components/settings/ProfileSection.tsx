import React, { useState, useEffect } from 'react';
import { settingsService, UserProfile } from '../../services/settings';

interface ProfileSectionProps {
    setError: (msg: string | null) => void;
    setSuccess: (msg: string | null) => void;
}

export const ProfileSection: React.FC<ProfileSectionProps> = ({ setError, setSuccess }) => {
    const [userProfile, setUserProfile] = useState<UserProfile>({});
    const [profileLoading, setProfileLoading] = useState(false);
    const [profileSaving, setProfileSaving] = useState(false);
    const [interestsInput, setInterestsInput] = useState('');

    useEffect(() => {
        loadUserProfile();
    }, []);

    const loadUserProfile = async () => {
        try {
            setProfileLoading(true);
            const profile = await settingsService.getUserProfile();
            setUserProfile(profile);
            if (profile.interests) {
                setInterestsInput(profile.interests.join(', '));
            }
        } catch (err) {
            console.error('Failed to load user profile:', err);
        } finally {
            setProfileLoading(false);
        }
    };

    const handleSaveProfile = async () => {
        try {
            setProfileSaving(true);
            setError(null);

            const interests = interestsInput
                .split(',')
                .map(s => s.trim())
                .filter(s => s.length > 0);

            const profileToSave: UserProfile = {
                ...userProfile,
                interests: interests.length > 0 ? interests : undefined,
            };

            await settingsService.saveUserProfile(profileToSave);
            setSuccess('Profile saved successfully!');
            setTimeout(() => setSuccess(null), 3000);
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Failed to save profile');
        } finally {
            setProfileSaving(false);
        }
    };

    if (profileLoading) {
        return <div className="text-center py-8 text-gray-500">Loading profile...</div>;
    }

    return (
        <div className="space-y-6">
            <div>
                <h3 className="text-lg font-semibold text-gray-900 dark:text-white mb-2">User Profile</h3>
                <p className="text-sm text-gray-600 dark:text-gray-400 mb-4">
                    Personalize how the AI responds to you. This information is stored locally and used to make responses feel more natural.
                </p>
            </div>

            <div className="space-y-4">
                {/* Name */}
                <div>
                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                        What should I call you?
                    </label>
                    <input
                        type="text"
                        value={userProfile.name || ''}
                        onChange={(e) => setUserProfile({ ...userProfile, name: e.target.value })}
                        placeholder="Your name or nickname"
                        className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white text-sm"
                    />
                </div>

                {/* Profession */}
                <div>
                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                        What do you do?
                    </label>
                    <input
                        type="text"
                        value={userProfile.profession || ''}
                        onChange={(e) => setUserProfile({ ...userProfile, profession: e.target.value })}
                        placeholder="e.g., Software Engineer, Researcher, Student"
                        className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white text-sm"
                    />
                </div>

                {/* Expertise Level */}
                <div>
                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                        Technical expertise level
                    </label>
                    <select
                        value={userProfile.expertise_level || ''}
                        onChange={(e) => setUserProfile({ ...userProfile, expertise_level: e.target.value as UserProfile['expertise_level'] })}
                        className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white text-sm"
                    >
                        <option value="">Select level...</option>
                        <option value="beginner">Beginner - Explain things simply</option>
                        <option value="intermediate">Intermediate - Some technical terms OK</option>
                        <option value="expert">Expert - Use technical terminology freely</option>
                    </select>
                </div>

                {/* Response Style */}
                <div>
                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                        Preferred response style
                    </label>
                    <select
                        value={userProfile.response_style || ''}
                        onChange={(e) => setUserProfile({ ...userProfile, response_style: e.target.value as UserProfile['response_style'] })}
                        className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white text-sm"
                    >
                        <option value="">Select style...</option>
                        <option value="concise">Concise - Brief and to the point</option>
                        <option value="balanced">Balanced - Medium detail</option>
                        <option value="detailed">Detailed - Thorough explanations</option>
                    </select>
                </div>

                {/* Interests */}
                <div>
                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                        Your interests (for better examples)
                    </label>
                    <input
                        type="text"
                        value={interestsInput}
                        onChange={(e) => setInterestsInput(e.target.value)}
                        placeholder="e.g., AI, woodworking, sci-fi, cooking (comma-separated)"
                        className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white text-sm"
                    />
                    <p className="text-xs text-gray-500 mt-1">Comma-separated list</p>
                </div>

                {/* Goals */}
                <div>
                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                        What are you using LocalBook for?
                    </label>
                    <input
                        type="text"
                        value={userProfile.goals || ''}
                        onChange={(e) => setUserProfile({ ...userProfile, goals: e.target.value })}
                        placeholder="e.g., Research, learning, building a second brain"
                        className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white text-sm"
                    />
                </div>

                {/* Custom Instructions */}
                <div>
                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                        Custom instructions (optional)
                    </label>
                    <textarea
                        value={userProfile.custom_instructions || ''}
                        onChange={(e) => setUserProfile({ ...userProfile, custom_instructions: e.target.value })}
                        placeholder="Any specific instructions for how the AI should respond to you..."
                        rows={3}
                        className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white text-sm"
                    />
                </div>

                {/* Save Button */}
                <div className="pt-4">
                    <button
                        onClick={handleSaveProfile}
                        disabled={profileSaving}
                        className="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 disabled:bg-indigo-400 text-white rounded-lg text-sm font-medium transition-colors"
                    >
                        {profileSaving ? 'Saving...' : 'Save Profile'}
                    </button>
                </div>
            </div>
        </div>
    );
};
