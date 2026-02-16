import React, { useState } from 'react';
import { peopleService } from '../../services/people';
import {
  Users, Plus, RefreshCw, Linkedin, Github, Globe,
  Clock, ChevronRight, Loader2, Trash2
} from 'lucide-react';
import { Button } from '../shared/Button';

interface SocialLinks {
  [key: string]: string;
}

interface ActivityProfile {
  platform_frequency: Record<string, string>;
  platform_last_active: Record<string, string>;
  content_types: Record<string, string[]>;
  overall_frequency: string;
  overall_last_active: string;
  topics: string[];
  focus_summary: string;
  last_analyzed: string | null;
}

interface MemberSummary {
  id: string;
  name: string;
  headline: string;
  current_role: string;
  current_company: string;
  photo_url: string;
  social_links: SocialLinks;
  tags: string[];
  last_collected: Record<string, string>;
  updated_at: string;
  activity_profile?: ActivityProfile;
}

interface TeamMemberListProps {
  notebookId: string;
  members: MemberSummary[];
  onSelectMember: (memberId: string) => void;
  onAddMember: () => void;
  onRefresh: () => void;
  isCollecting: boolean;
}

const PLATFORM_ICONS: Record<string, React.ReactNode> = {
  linkedin: <Linkedin className="w-3.5 h-3.5" />,
  github: <Github className="w-3.5 h-3.5" />,
  twitter: <Globe className="w-3.5 h-3.5" />,
  instagram: <Globe className="w-3.5 h-3.5" />,
  personal_site: <Globe className="w-3.5 h-3.5" />,
};

const getInitials = (name: string) => {
  return name
    .split(' ')
    .map(n => n[0])
    .join('')
    .toUpperCase()
    .slice(0, 2);
};

const getTimeSince = (isoDate: string) => {
  if (!isoDate) return 'Never';
  const diff = Math.max(0, Date.now() - new Date(isoDate).getTime());
  const days = Math.floor(diff / (1000 * 60 * 60 * 24));
  if (days === 0) return 'Today';
  if (days === 1) return 'Yesterday';
  if (days < 7) return `${days}d ago`;
  if (days < 30) return `${Math.floor(days / 7)}w ago`;
  return `${Math.floor(days / 30)}mo ago`;
};

export const TeamMemberList: React.FC<TeamMemberListProps> = ({
  notebookId,
  members,
  onSelectMember,
  onAddMember,
  onRefresh,
}) => {
  const [collectingMember, setCollectingMember] = useState<string | null>(null);
  const [deletingMember, setDeletingMember] = useState<string | null>(null);

  const handleCollectMember = async (e: React.MouseEvent, memberId: string) => {
    e.stopPropagation();
    setCollectingMember(memberId);
    try {
      await peopleService.collectMember(notebookId, memberId);
      onRefresh();
    } catch (err) {
      console.error('Collection failed:', err);
    } finally {
      setCollectingMember(null);
    }
  };

  const handleDeleteMember = async (e: React.MouseEvent, memberId: string, memberName: string) => {
    e.stopPropagation();
    if (!window.confirm(`Remove ${memberName} from this notebook?`)) return;
    setDeletingMember(memberId);
    try {
      await peopleService.deleteMember(notebookId, memberId);
      onRefresh();
    } catch (err) {
      console.error('Delete failed:', err);
    } finally {
      setDeletingMember(null);
    }
  };

  if (members.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-12 text-center">
        <Users className="w-12 h-12 text-gray-300 dark:text-gray-600 mb-4" />
        <h3 className="text-lg font-medium text-gray-700 dark:text-gray-300 mb-2">
          No profiles yet
        </h3>
        <p className="text-sm text-gray-500 dark:text-gray-400 mb-4 max-w-sm">
          Add people to start tracking their public activity and building coaching profiles.
        </p>
        <Button onClick={onAddMember}>
          <Plus className="w-4 h-4 mr-1" /> Add Person
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {/* Member cards */}
      {members.map(member => {
        const lastUpdate = Object.values(member.last_collected || {}).sort().pop();
        const isMemberCollecting = collectingMember === member.id;

        return (
          <div
            key={member.id}
            onClick={() => onSelectMember(member.id)}
            className="flex items-center gap-3 p-3 rounded-lg border border-gray-200 dark:border-gray-700 hover:border-blue-300 dark:hover:border-blue-600 hover:bg-gray-50 dark:hover:bg-gray-800 cursor-pointer transition-colors group"
          >
            {/* Avatar */}
            <div className="w-10 h-10 rounded-full bg-gradient-to-br from-blue-400 to-purple-500 flex items-center justify-center text-white text-sm font-bold flex-shrink-0">
              {member.photo_url ? (
                <img
                  src={member.photo_url}
                  alt={member.name}
                  className="w-10 h-10 rounded-full object-cover"
                />
              ) : (
                getInitials(member.name)
              )}
            </div>

            {/* Info */}
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <span className="font-medium text-gray-900 dark:text-gray-100 text-sm truncate">
                  {member.name}
                </span>
                {member.tags.slice(0, 2).map(tag => (
                  <span
                    key={tag}
                    className="px-1.5 py-0.5 text-[10px] font-medium rounded-full bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300"
                  >
                    {tag}
                  </span>
                ))}
              </div>
              <p className="text-xs text-gray-500 dark:text-gray-400 truncate">
                {member.headline || member.current_role || 'No role set'}
              </p>
              <div className="flex items-center gap-2 mt-1">
                {/* Social platform icons */}
                <div className="flex gap-1">
                  {Object.keys(member.social_links || {})
                    .filter(k => member.social_links[k])
                    .map(platform => (
                      <span key={platform} className="text-gray-400 dark:text-gray-500">
                        {PLATFORM_ICONS[platform] || <Globe className="w-3.5 h-3.5" />}
                      </span>
                    ))}
                </div>
                {/* Last collected */}
                {lastUpdate && (
                  <span className="flex items-center gap-1 text-[10px] text-gray-400 dark:text-gray-500">
                    <Clock className="w-3 h-3" /> {getTimeSince(lastUpdate)}
                  </span>
                )}
                {/* Last active badge */}
                {member.activity_profile?.overall_last_active && (() => {
                  try {
                    const d = new Date(member.activity_profile.overall_last_active);
                    if (isNaN(d.getTime())) return null;
                    const diffDays = Math.floor(Math.max(0, Date.now() - d.getTime()) / (1000 * 60 * 60 * 24));
                    const label = diffDays === 0 ? 'Today' : diffDays === 1 ? 'Yesterday' : diffDays < 7 ? `${diffDays}d ago` : d.toLocaleDateString(undefined, { month: 'short', year: 'numeric' });
                    return (
                      <span className="text-[10px] px-1.5 py-0.5 rounded-full font-medium bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-300">
                        {label}
                      </span>
                    );
                  } catch { return null; }
                })()}
              </div>
              {/* Activity topics */}
              {member.activity_profile?.topics && member.activity_profile.topics.length > 0 && (
                <div className="flex gap-1 mt-0.5">
                  {member.activity_profile.topics.slice(0, 3).map(topic => (
                    <span key={topic} className="text-[10px] text-gray-400 dark:text-gray-500">
                      #{topic}
                    </span>
                  ))}
                </div>
              )}
            </div>

            {/* Actions */}
            <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
              <button
                onClick={(e) => handleCollectMember(e, member.id)}
                disabled={isMemberCollecting}
                className="p-1.5 text-gray-400 hover:text-blue-500 rounded"
                title="Collect now"
              >
                {isMemberCollecting ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <RefreshCw className="w-4 h-4" />
                )}
              </button>
              <button
                onClick={(e) => handleDeleteMember(e, member.id, member.name)}
                disabled={deletingMember === member.id}
                className="p-1.5 text-gray-400 hover:text-red-500 rounded"
                title="Remove person"
              >
                <Trash2 className="w-4 h-4" />
              </button>
              <ChevronRight className="w-4 h-4 text-gray-300 dark:text-gray-600" />
            </div>
          </div>
        );
      })}
    </div>
  );
};
