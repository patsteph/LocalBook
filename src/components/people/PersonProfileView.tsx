import React, { useState, useEffect } from 'react';
import { peopleService } from '../../services/people';
import {
  ArrowLeft, RefreshCw, Github,
  MapPin, Briefcase, Clock, Plus,
  Target, Loader2, ExternalLink,
  Sparkles, Trash2
} from 'lucide-react';
import { Button } from '../shared/Button';

interface PersonProfileViewProps {
  notebookId: string;
  memberId: string;
  coachingEnabled?: boolean;
  onBack: () => void;
}

type ProfileTab = 'overview' | 'activity' | 'coaching' | 'sources';

export const PersonProfileView: React.FC<PersonProfileViewProps> = ({
  notebookId,
  memberId,
  coachingEnabled = false,
  onBack,
}) => {
  const [profile, setProfile] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<ProfileTab>('overview');
  const [collecting, setCollecting] = useState(false);
  const [newNote, setNewNote] = useState('');
  const [noteCategory, setNoteCategory] = useState('general');
  const [addingNote, setAddingNote] = useState(false);
  const [showNoteForm, setShowNoteForm] = useState(false);
  const [newGoal, setNewGoal] = useState('');
  const [showGoalForm, setShowGoalForm] = useState(false);
  const [addingGoal, setAddingGoal] = useState(false);

  useEffect(() => {
    loadProfile();
  }, [memberId]);

  const loadProfile = async () => {
    setLoading(true);
    try {
      const data = await peopleService.getMember(notebookId, memberId);
      setProfile(data);
    } catch (e) {
      console.error('Failed to load profile:', e);
    } finally {
      setLoading(false);
    }
  };

  const handleCollect = async () => {
    setCollecting(true);
    try {
      await peopleService.collectMember(notebookId, memberId);
      await loadProfile();
    } catch (e) {
      console.error('Collection failed:', e);
    } finally {
      setCollecting(false);
    }
  };

  const handleAddNote = async () => {
    if (!newNote.trim()) return;
    setAddingNote(true);
    try {
      await peopleService.addNote(notebookId, memberId, newNote.trim(), noteCategory);
      setNewNote('');
      setShowNoteForm(false);
      await loadProfile();
    } catch (e) {
      console.error('Failed to add note:', e);
    } finally {
      setAddingNote(false);
    }
  };

  const handleAddGoal = async () => {
    if (!newGoal.trim()) return;
    setAddingGoal(true);
    try {
      await peopleService.addGoal(notebookId, memberId, newGoal.trim());
      setNewGoal('');
      setShowGoalForm(false);
      await loadProfile();
    } catch (e) {
      console.error('Failed to add goal:', e);
    } finally {
      setAddingGoal(false);
    }
  };

  const handleDeleteNote = async (noteId: string) => {
    try {
      await peopleService.deleteNote(notebookId, memberId, noteId);
      await loadProfile();
    } catch (e) {
      console.error('Failed to delete note:', e);
    }
  };

  const handleDeleteGoal = async (goalId: string) => {
    try {
      await peopleService.deleteGoal(notebookId, memberId, goalId);
      await loadProfile();
    } catch (e) {
      console.error('Failed to delete goal:', e);
    }
  };

  const getInitials = (name: string) =>
    name.split(' ').map(n => n[0]).join('').toUpperCase().slice(0, 2);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="w-8 h-8 animate-spin text-blue-500" />
      </div>
    );
  }

  if (!profile) {
    return (
      <div className="text-center py-12 text-gray-500">
        Profile not found.
        <Button variant="secondary" onClick={onBack} className="ml-2">Back</Button>
      </div>
    );
  }

  const TABS: { key: ProfileTab; label: string }[] = [
    { key: 'overview', label: 'Overview' },
    { key: 'activity', label: 'Activity' },
    ...(coachingEnabled ? [{ key: 'coaching' as ProfileTab, label: 'Coaching' }] : []),
    { key: 'sources', label: 'Sources' },
  ];

  const NOTE_CATEGORIES = [
    { value: 'general', label: 'General' },
    { value: 'strength', label: 'Strength' },
    { value: 'growth_area', label: 'Growth Area' },
    { value: 'goal', label: 'Goal' },
    { value: 'observation', label: 'Observation' },
  ];

  // =========================================================================
  // Tab renderers
  // =========================================================================

  const renderOverview = () => (
    <div className="space-y-6">
      {/* Bio */}
      {profile.bio && (
        <div>
          <h4 className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide mb-2">About</h4>
          <p className="text-sm text-gray-700 dark:text-gray-300 leading-relaxed">{profile.bio}</p>
        </div>
      )}

      {/* Experience */}
      {profile.experience?.length > 0 && (() => {
        // Deduplicate by title text and separate role titles from descriptions
        const seen = new Set<string>();
        const deduped = profile.experience.filter((exp: any) => {
          const key = (exp.title || '').trim();
          if (!key || seen.has(key)) return false;
          seen.add(key);
          return true;
        });
        return (
          <div>
            <h4 className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide mb-2">Experience</h4>
            <div className="space-y-2">
              {deduped.slice(0, 5).map((exp: any, i: number) => {
                const isDescription = (exp.title || '').length > 120;
                return (
                  <div key={i} className="flex gap-3 p-2 rounded-lg bg-gray-50 dark:bg-gray-800">
                    <Briefcase className="w-4 h-4 text-gray-400 mt-0.5 flex-shrink-0" />
                    <div>
                      {isDescription ? (
                        <p className="text-sm text-gray-600 dark:text-gray-400 leading-relaxed">{exp.title}</p>
                      ) : (
                        <p className="text-sm font-medium text-gray-800 dark:text-gray-200">{exp.title}</p>
                      )}
                      {exp.company && <p className="text-xs text-gray-500">{exp.company}</p>}
                      {exp.dates && <p className="text-xs text-gray-400">{exp.dates}</p>}
                      {exp.description && <p className="text-xs text-gray-500 dark:text-gray-400 mt-1 leading-relaxed">{exp.description}</p>}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        );
      })()}

      {/* Skills */}
      {profile.skills?.length > 0 && (
        <div>
          <h4 className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide mb-2">Skills</h4>
          <div className="flex flex-wrap gap-1.5">
            {profile.skills.map((skill: string, i: number) => (
              <span
                key={i}
                className="px-2 py-0.5 text-xs bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300 rounded-full"
              >
                {skill}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Coaching Insights */}
      {profile.coaching_insights && Object.keys(profile.coaching_insights).length > 0 && (() => {
        const ci = typeof profile.coaching_insights === 'string'
          ? (() => { try { return JSON.parse(profile.coaching_insights); } catch { return null; } })()
          : profile.coaching_insights;
        if (!ci) return null;
        return (
          <div className="space-y-4">
            <div className="flex items-center gap-2">
              <Sparkles className="w-4 h-4 text-amber-500" />
              <h4 className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide">Coaching Insights</h4>
              {ci.generated_at && (
                <span className="text-[10px] text-gray-400 ml-auto">
                  {new Date(ci.generated_at).toLocaleDateString()}
                </span>
              )}
            </div>

            {/* Overall Summary */}
            {ci.overall_summary && (
              <div className="p-3 rounded-lg bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800/30">
                <p className="text-sm text-blue-800 dark:text-blue-200 leading-relaxed">{ci.overall_summary}</p>
              </div>
            )}

            {/* Strengths */}
            {ci.strengths?.length > 0 && (
              <div>
                <h5 className="text-xs font-medium text-green-700 dark:text-green-400 mb-1.5 flex items-center gap-1">
                  <span className="text-green-500">+</span> Strengths
                </h5>
                <ul className="space-y-1">
                  {ci.strengths.map((s: string, i: number) => (
                    <li key={i} className="text-sm text-gray-700 dark:text-gray-300 pl-4 relative before:content-[''] before:absolute before:left-1 before:top-2 before:w-1.5 before:h-1.5 before:bg-green-400 before:rounded-full">
                      {s}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {/* Growth Areas */}
            {ci.growth_areas?.length > 0 && (
              <div>
                <h5 className="text-xs font-medium text-orange-700 dark:text-orange-400 mb-1.5 flex items-center gap-1">
                  <span className="text-orange-500">&#x25B2;</span> Growth Areas
                </h5>
                <ul className="space-y-1">
                  {ci.growth_areas.map((g: string, i: number) => (
                    <li key={i} className="text-sm text-gray-700 dark:text-gray-300 pl-4 relative before:content-[''] before:absolute before:left-1 before:top-2 before:w-1.5 before:h-1.5 before:bg-orange-400 before:rounded-full">
                      {g}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {/* Topic Trends */}
            {ci.topic_trends?.length > 0 && (
              <div>
                <h5 className="text-xs font-medium text-purple-700 dark:text-purple-400 mb-1.5">Trending Topics</h5>
                <div className="flex flex-wrap gap-1.5">
                  {ci.topic_trends.map((t: string, i: number) => (
                    <span key={i} className="px-2 py-0.5 text-xs bg-purple-100 dark:bg-purple-900/30 text-purple-700 dark:text-purple-300 rounded-full">
                      {t}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {/* Conversation Starters */}
            {ci.conversation_starters?.length > 0 && (
              <div>
                <h5 className="text-xs font-medium text-gray-600 dark:text-gray-400 mb-1.5">Conversation Starters</h5>
                <div className="space-y-1.5">
                  {ci.conversation_starters.map((q: string, i: number) => (
                    <div key={i} className="p-2 rounded bg-gray-50 dark:bg-gray-800 text-sm text-gray-700 dark:text-gray-300 italic">
                      &ldquo;{q}&rdquo;
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        );
      })()}
    </div>
  );

  const renderActivity = () => {
    const allActivity: { platform: string; text: string; date?: string }[] = [];

    (profile.linkedin_posts || []).forEach((p: any) =>
      allActivity.push({ platform: 'LinkedIn', text: p.text, date: p.date })
    );
    (profile.tweets || []).forEach((t: any) =>
      allActivity.push({ platform: 'Twitter', text: t.text, date: t.date })
    );
    (profile.blog_posts || []).forEach((b: any) =>
      allActivity.push({ platform: 'Blog', text: b.text || b.title, date: b.date })
    );

    const actProfile = profile.activity_profile;
    const ghActivity = profile.github_activity || {};

    if (allActivity.length === 0 && !ghActivity?.pinned_repos?.length && !actProfile?.recent_items?.length) {
      return (
        <div className="text-center py-8 text-gray-500 dark:text-gray-400 text-sm">
          No activity collected yet. Click "Collect" to pull recent posts.
        </div>
      );
    }

    const formatLastActive = (isoDate: string) => {
      try {
        const d = new Date(isoDate);
        if (isNaN(d.getTime())) return null;
        const now = new Date();
        const diffMs = now.getTime() - d.getTime();
        const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));
        if (diffDays === 0) return 'Today';
        if (diffDays === 1) return 'Yesterday';
        if (diffDays < 7) return `${diffDays}d ago`;
        return d.toLocaleDateString(undefined, { month: 'short', year: 'numeric' });
      } catch { return null; }
    };

    return (
      <div className="space-y-5">
        {/* Activity Summary */}
        {actProfile && (actProfile.overall_last_active || actProfile.platform_last_active && Object.keys(actProfile.platform_last_active).length > 0 || actProfile.topics?.length > 0) && (
          <div className="p-3 rounded-lg bg-gray-50 dark:bg-gray-800 border border-gray-200 dark:border-gray-700">
            <div className="flex items-center gap-2 flex-wrap">
              {actProfile.overall_last_active && formatLastActive(actProfile.overall_last_active) && (
                <>
                  <span className="text-xs text-gray-500 dark:text-gray-400">Last active:</span>
                  <span className="text-[10px] font-medium px-2 py-0.5 rounded-full bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-300">
                    {formatLastActive(actProfile.overall_last_active)}
                  </span>
                </>
              )}
              {actProfile.topics?.length > 0 && (
                <>
                  <span className="text-gray-300 dark:text-gray-600">|</span>
                  {actProfile.topics.map((t: string, i: number) => (
                    <span key={i} className="text-[10px] px-1.5 py-0.5 rounded bg-purple-100 dark:bg-purple-900/30 text-purple-700 dark:text-purple-300">
                      #{t}
                    </span>
                  ))}
                </>
              )}
            </div>
            {/* Per-platform last active */}
            {actProfile.platform_last_active && Object.keys(actProfile.platform_last_active).length > 0 && (
              <div className="flex gap-3 mt-2 flex-wrap">
                {Object.entries(actProfile.platform_last_active).map(([platform, dateStr]: [string, any]) => {
                  const formatted = formatLastActive(dateStr as string);
                  if (!formatted) return null;
                  return (
                    <div key={platform} className="flex items-center gap-1">
                      <span className="text-[10px] capitalize text-gray-500 dark:text-gray-400">{platform.replace('_', ' ')}:</span>
                      <span className="text-[10px] font-medium text-gray-600 dark:text-gray-300">
                        {formatted}
                      </span>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}

        {/* LLM Activity Insights */}
        {actProfile?.focus_summary && (
          <div className="p-3 rounded-lg bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800/30">
            <div className="flex items-center gap-2 mb-1">
              <Sparkles className="w-3.5 h-3.5 text-blue-500" />
              <h5 className="text-xs font-medium text-blue-700 dark:text-blue-300">Activity Insights</h5>
            </div>
            <p className="text-sm text-blue-800 dark:text-blue-200 leading-relaxed">{actProfile.focus_summary}</p>
          </div>
        )}

        {/* GitHub */}
        {ghActivity?.pinned_repos?.length > 0 && (
          <div>
            <h4 className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide mb-2">
              GitHub
              {ghActivity.contributions_text && (
                <span className="ml-2 normal-case font-normal text-gray-400">
                  {ghActivity.contributions_text}
                </span>
              )}
            </h4>
            <div className="space-y-2">
              {ghActivity.pinned_repos.map((repo: any, i: number) => (
                <div key={i} className="p-3 rounded-lg bg-gray-50 dark:bg-gray-800 border border-gray-200 dark:border-gray-700">
                  <div className="flex items-center gap-2">
                    <Github className="w-3.5 h-3.5 text-gray-500 dark:text-gray-400 flex-shrink-0" />
                    <span className="text-sm font-medium text-gray-800 dark:text-gray-200">{repo.name}</span>
                    {repo.language && (
                      <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-gray-200 dark:bg-gray-700 text-gray-600 dark:text-gray-300 ml-auto">
                        {repo.language}
                      </span>
                    )}
                  </div>
                  {repo.description && (
                    <p className="text-xs text-gray-500 dark:text-gray-400 mt-1 ml-5.5">{repo.description}</p>
                  )}
                  {(repo.stars > 0 || repo.forks > 0) && (
                    <div className="flex gap-3 mt-1 ml-5.5 text-[10px] text-gray-400">
                      {repo.stars > 0 && <span>&#9733; {repo.stars}</span>}
                      {repo.forks > 0 && <span>&#x2442; {repo.forks}</span>}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Posts timeline */}
        {allActivity.length > 0 && (
          <div>
            <h4 className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide mb-2">
              Recent Posts
            </h4>
            <div className="space-y-2">
              {allActivity.slice(0, 20).map((item, i) => (
                <div key={i} className="p-3 rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-[10px] font-medium px-1.5 py-0.5 rounded-full bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300">
                      {item.platform}
                    </span>
                    {item.date && (
                      <span className="text-[10px] text-gray-400">{item.date}</span>
                    )}
                  </div>
                  <p className="text-sm text-gray-700 dark:text-gray-300 line-clamp-3">
                    {item.text}
                  </p>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Activity profile recent items (from analyzer) */}
        {actProfile?.recent_items?.length > 0 && allActivity.length === 0 && (
          <div>
            <h4 className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide mb-2">
              Recent Activity
            </h4>
            <div className="space-y-2">
              {actProfile.recent_items.map((item: any, i: number) => (
                <div key={i} className="p-3 rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-[10px] font-medium px-1.5 py-0.5 rounded-full bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300 capitalize">
                      {item.platform}
                    </span>
                    <span className="text-[10px] text-gray-400 capitalize">{item.content_type?.replace('_', ' ')}</span>
                    {item.date && (
                      <span className="text-[10px] text-gray-400 ml-auto">{item.date}</span>
                    )}
                  </div>
                  <p className="text-sm text-gray-700 dark:text-gray-300 line-clamp-2">
                    {item.title || item.summary}
                  </p>
                  {item.engagement && (item.engagement.likes > 0 || item.engagement.comments > 0) && (
                    <div className="flex gap-3 mt-1 text-[10px] text-gray-400">
                      {item.engagement.likes > 0 && <span>&#x2764; {item.engagement.likes}</span>}
                      {item.engagement.comments > 0 && <span>&#x1F4AC; {item.engagement.comments}</span>}
                      {item.engagement.shares > 0 && <span>&#x21BB; {item.engagement.shares}</span>}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    );
  };

  const renderCoaching = () => (
    <div className="space-y-6">
      {/* Notes */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <h4 className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide">
            Coaching Notes ({profile.coaching_notes?.length || 0})
          </h4>
          <button
            onClick={() => setShowNoteForm(!showNoteForm)}
            className="flex items-center gap-1 text-blue-600 dark:text-blue-400 text-xs font-medium hover:text-blue-700"
          >
            <Plus className="w-3 h-3" /> Add Note
          </button>
        </div>

        {showNoteForm && (
          <div className="mb-4 p-3 border border-blue-200 dark:border-blue-800 rounded-lg bg-blue-50/50 dark:bg-blue-900/10">
            <select
              value={noteCategory}
              onChange={e => setNoteCategory(e.target.value)}
              className="w-full mb-2 px-2 py-1.5 text-xs border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100"
            >
              {NOTE_CATEGORIES.map(c => (
                <option key={c.value} value={c.value}>{c.label}</option>
              ))}
            </select>
            <textarea
              value={newNote}
              onChange={e => setNewNote(e.target.value)}
              placeholder="Write a coaching note..."
              rows={3}
              className="w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 resize-none focus:ring-2 focus:ring-blue-500 outline-none"
              autoFocus
            />
            <div className="flex justify-end gap-2 mt-2">
              <Button size="sm" variant="secondary" onClick={() => setShowNoteForm(false)}>Cancel</Button>
              <Button size="sm" onClick={handleAddNote} disabled={addingNote || !newNote.trim()}>
                {addingNote ? 'Saving...' : 'Save Note'}
              </Button>
            </div>
          </div>
        )}

        <div className="space-y-2">
          {(profile.coaching_notes || []).length === 0 && !showNoteForm && (
            <p className="text-sm text-gray-400 dark:text-gray-500 text-center py-4">
              No notes yet. Click "Add Note" to start.
            </p>
          )}
          {(profile.coaching_notes || [])
            .slice()
            .reverse()
            .map((note: any) => (
              <div key={note.id} className="group p-3 rounded-lg bg-gray-50 dark:bg-gray-800 border border-gray-200 dark:border-gray-700">
                <div className="flex items-center gap-2 mb-1">
                  <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded-full ${
                    note.category === 'strength' ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-300' :
                    note.category === 'growth_area' ? 'bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-300' :
                    note.category === 'goal' ? 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300' :
                    'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-300'
                  }`}>
                    {note.category?.replace('_', ' ')}
                  </span>
                  <span className="text-[10px] text-gray-400">
                    {note.created_at ? new Date(note.created_at).toLocaleDateString() : ''}
                  </span>
                  <button
                    onClick={() => handleDeleteNote(note.id)}
                    className="ml-auto opacity-0 group-hover:opacity-100 transition-opacity text-gray-400 hover:text-red-500"
                    title="Delete note"
                  >
                    <Trash2 className="w-3 h-3" />
                  </button>
                </div>
                <p className="text-sm text-gray-700 dark:text-gray-300">{note.text}</p>
              </div>
            ))}
        </div>
      </div>

      {/* Goals */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <h4 className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide">
            Goals ({profile.goals?.length || 0})
          </h4>
          <button
            onClick={() => setShowGoalForm(!showGoalForm)}
            className="flex items-center gap-1 text-blue-600 dark:text-blue-400 text-xs font-medium hover:text-blue-700"
          >
            <Target className="w-3 h-3" /> Add Goal
          </button>
        </div>

        {showGoalForm && (
          <div className="mb-4 p-3 border border-blue-200 dark:border-blue-800 rounded-lg bg-blue-50/50 dark:bg-blue-900/10">
            <input
              type="text"
              value={newGoal}
              onChange={e => setNewGoal(e.target.value)}
              placeholder="What should this person work toward?"
              className="w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 focus:ring-2 focus:ring-blue-500 outline-none"
              autoFocus
            />
            <div className="flex justify-end gap-2 mt-2">
              <Button size="sm" variant="secondary" onClick={() => setShowGoalForm(false)}>Cancel</Button>
              <Button size="sm" onClick={handleAddGoal} disabled={addingGoal || !newGoal.trim()}>
                {addingGoal ? 'Saving...' : 'Add Goal'}
              </Button>
            </div>
          </div>
        )}

        <div className="space-y-2">
          {(profile.goals || []).length === 0 && !showGoalForm && (
            <p className="text-sm text-gray-400 dark:text-gray-500 text-center py-4">
              No goals set. Click "Add Goal" to start.
            </p>
          )}
          {(profile.goals || []).map((goal: any) => (
            <div key={goal.id} className="group flex items-start gap-2 p-3 rounded-lg bg-gray-50 dark:bg-gray-800">
              <Target className={`w-4 h-4 mt-0.5 flex-shrink-0 ${
                goal.status === 'completed' ? 'text-green-500' :
                goal.status === 'paused' ? 'text-yellow-500' :
                'text-blue-500'
              }`} />
              <div className="flex-1">
                <p className="text-sm text-gray-700 dark:text-gray-300">{goal.goal}</p>
                <span className={`text-[10px] font-medium ${
                  goal.status === 'completed' ? 'text-green-600' :
                  goal.status === 'paused' ? 'text-yellow-600' :
                  'text-blue-600'
                }`}>
                  {goal.status}
                </span>
              </div>
              <button
                onClick={() => handleDeleteGoal(goal.id)}
                className="opacity-0 group-hover:opacity-100 transition-opacity text-gray-400 hover:text-red-500 mt-0.5"
                title="Delete goal"
              >
                <Trash2 className="w-3 h-3" />
              </button>
            </div>
          ))}
        </div>
      </div>
    </div>
  );

  const renderSources = () => (
    <div className="space-y-4">
      {/* Connected platforms */}
      <div>
        <h4 className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide mb-2">
          Social Links
        </h4>
        <div className="space-y-1.5">
          {Object.entries(profile.social_links || {})
            .filter(([_, url]) => url)
            .map(([platform, url]) => (
              <a
                key={platform}
                href={url as string}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-2 p-2 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-800 text-sm text-blue-600 dark:text-blue-400"
              >
                <ExternalLink className="w-3.5 h-3.5" />
                <span className="capitalize">{platform.replace('_', ' ')}</span>
                <span className="text-xs text-gray-400 truncate ml-auto">{url as string}</span>
              </a>
            ))}
        </div>
      </div>

      {/* Collection history */}
      <div>
        <h4 className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide mb-2">
          Collection History
        </h4>
        {(profile.sources || []).length === 0 ? (
          <p className="text-sm text-gray-400 text-center py-4">No collections yet</p>
        ) : (
          <div className="space-y-1.5">
            {(profile.sources || []).slice(-10).reverse().map((src: any, i: number) => (
              <div key={i} className="flex items-center justify-between p-2 rounded bg-gray-50 dark:bg-gray-800 text-xs">
                <div className="flex items-center gap-2">
                  <span className={`w-2 h-2 rounded-full ${src.success ? 'bg-green-400' : 'bg-red-400'}`} />
                  <span className="capitalize text-gray-700 dark:text-gray-300">
                    {src.platform?.replace('_', ' ')}
                  </span>
                </div>
                <span className="text-gray-400">
                  {src.captured_at ? new Date(src.captured_at).toLocaleDateString() : ''}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Schedule */}
      <div className="p-3 rounded-lg bg-gray-50 dark:bg-gray-800">
        <div className="flex items-center gap-2 text-sm">
          <Clock className="w-4 h-4 text-gray-400" />
          <span className="text-gray-600 dark:text-gray-300">
            Collection schedule: <strong className="capitalize">{profile.collection_schedule || 'weekly'}</strong>
          </span>
        </div>
      </div>
    </div>
  );

  return (
    <div className="flex flex-col">
      {/* Header */}
      <div className="p-4 border-b border-gray-200 dark:border-gray-700">
        <div className="flex items-center gap-3">
          <button
            onClick={onBack}
            className="p-1 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 rounded"
          >
            <ArrowLeft className="w-5 h-5" />
          </button>

          <div className="w-12 h-12 rounded-full bg-gradient-to-br from-blue-400 to-purple-500 flex items-center justify-center text-white font-bold flex-shrink-0">
            {profile.photo_url ? (
              <img src={profile.photo_url} alt={profile.name} className="w-12 h-12 rounded-full object-cover" />
            ) : (
              getInitials(profile.name || '??')
            )}
          </div>

          <div className="flex-1 min-w-0">
            <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100 truncate">
              {profile.name}
            </h2>
            <p className="text-sm text-gray-500 dark:text-gray-400 truncate">
              {profile.headline || profile.current_role || ''}
              {profile.current_company && profile.current_role ? ` at ${profile.current_company}` : profile.current_company || ''}
            </p>
            {profile.location && (
              <p className="text-xs text-gray-400 flex items-center gap-1 mt-0.5">
                <MapPin className="w-3 h-3" /> {profile.location}
              </p>
            )}
          </div>

          <Button
            size="sm"
            variant="secondary"
            onClick={handleCollect}
            disabled={collecting}
          >
            {collecting ? (
              <><Loader2 className="w-3 h-3 animate-spin mr-1" /> Collecting</>
            ) : (
              <><RefreshCw className="w-3 h-3 mr-1" /> Collect</>
            )}
          </Button>
        </div>

        {/* Tags */}
        {profile.tags?.length > 0 && (
          <div className="flex gap-1.5 mt-2 ml-[76px]">
            {profile.tags.map((tag: string) => (
              <span
                key={tag}
                className="px-2 py-0.5 text-[10px] font-medium rounded-full bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300"
              >
                {tag}
              </span>
            ))}
          </div>
        )}
      </div>

      {/* Tabs */}
      <div className="flex border-b border-gray-200 dark:border-gray-700 px-4">
        {TABS.map(tab => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className={`px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${
              activeTab === tab.key
                ? 'text-blue-600 dark:text-blue-400 border-blue-600 dark:border-blue-400'
                : 'text-gray-500 dark:text-gray-400 border-transparent hover:text-gray-700 dark:hover:text-gray-300'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div className="flex-1 overflow-y-auto p-4">
        {activeTab === 'overview' && renderOverview()}
        {activeTab === 'activity' && renderActivity()}
        {activeTab === 'coaching' && renderCoaching()}
        {activeTab === 'sources' && renderSources()}
      </div>
    </div>
  );
};
