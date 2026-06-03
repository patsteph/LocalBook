import React, { useState, useEffect } from 'react';
import { API_BASE_URL, localFetch } from '../../services/api';

// The voice profile is built by an LLM (phi4-mini in voice_engine.py)
// with only json.loads() validation — there's no schema enforcement,
// so a noisy LLM run can produce e.g. {vocabulary: {type: "academic"}}
// instead of "academic". Rendering that directly into JSX trips React
// error #31. Type everything as `unknown` and coerce at render time.
interface VoiceProfile {
    vocabulary?: unknown;
    style_markers?: unknown;
    thinking_framework?: unknown;
    formality?: unknown;
    interests?: unknown;
}

/**
 * Render any LLM-produced field as a string. Strings pass through; objects
 * and arrays serialize via JSON.stringify (so the user still sees something
 * meaningful); nullish renders as the fallback. Never returns a non-string.
 */
const renderField = (value: unknown, fallback = 'Not enough data'): string => {
    if (value == null) return fallback;
    if (typeof value === 'string') return value.trim() || fallback;
    if (typeof value === 'number' || typeof value === 'boolean') return String(value);
    try {
        return JSON.stringify(value);
    } catch {
        return fallback;
    }
};

/** Normalize the interests field into a flat array of strings. */
const renderInterests = (value: unknown): string[] => {
    if (!Array.isArray(value)) return [];
    return value
        .map((item) => {
            if (typeof item === 'string') return item.trim();
            if (item == null) return '';
            if (typeof item === 'object') {
                // Common LLM shapes: {name: "..."} or {topic: "..."} or {interest: "..."}
                const obj = item as Record<string, unknown>;
                const first = obj.name ?? obj.topic ?? obj.interest ?? obj.label;
                if (typeof first === 'string') return first.trim();
                try { return JSON.stringify(item); } catch { return ''; }
            }
            return String(item);
        })
        .filter(Boolean);
};

export const VoiceProfileSection: React.FC = () => {
    const [profile, setProfile] = useState<VoiceProfile | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        const fetchProfile = async () => {
            try {
                const res = await localFetch(`${API_BASE_URL}/settings/voice-profile`);
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
                        <p className="text-sm text-gray-700 dark:text-gray-300">{renderField(profile.vocabulary)}</p>
                    </div>

                    <div className="p-4 border border-gray-200 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800">
                        <h4 className="text-sm font-semibold text-gray-900 dark:text-gray-100 mb-2">Style Markers</h4>
                        <p className="text-sm text-gray-700 dark:text-gray-300">{renderField(profile.style_markers)}</p>
                    </div>

                    <div className="p-4 border border-gray-200 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800">
                        <h4 className="text-sm font-semibold text-gray-900 dark:text-gray-100 mb-2">Thinking Framework</h4>
                        <p className="text-sm text-gray-700 dark:text-gray-300">{renderField(profile.thinking_framework)}</p>
                    </div>

                    <div className="p-4 border border-gray-200 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800">
                        <h4 className="text-sm font-semibold text-gray-900 dark:text-gray-100 mb-2">Formality</h4>
                        <p className="text-sm text-gray-700 dark:text-gray-300">{renderField(profile.formality)}</p>
                    </div>

                    {(() => {
                        const interests = renderInterests(profile.interests);
                        if (interests.length === 0) return null;
                        return (
                            <div className="md:col-span-2 p-4 border border-gray-200 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800">
                                <h4 className="text-sm font-semibold text-gray-900 dark:text-gray-100 mb-2">Inferred Interests</h4>
                                <div className="flex flex-wrap gap-2">
                                    {interests.map((interest, idx) => (
                                        <span key={idx} className="px-2 py-1 bg-blue-100 dark:bg-blue-900 text-blue-800 dark:text-blue-100 text-xs rounded-full">
                                            {interest}
                                        </span>
                                    ))}
                                </div>
                            </div>
                        );
                    })()}
                </div>
            )}
        </div>
    );
};
