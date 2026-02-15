import React, { useState, useEffect, useCallback } from 'react';
import { peopleService } from '../../services/people';
import { TeamMemberList } from './TeamMemberList';
import { PersonProfileView } from './PersonProfileView';
import { PeopleSetupWizard } from './PeopleSetupWizard';
import { Button } from '../shared/Button';
import { RefreshCw, GraduationCap } from 'lucide-react';

interface PeoplePanelProps {
  notebookId: string;
  notebookName: string;
}

export const PeoplePanel: React.FC<PeoplePanelProps> = ({
  notebookId,
  notebookName,
}) => {
  const [config, setConfig] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [hasConfig, setHasConfig] = useState(false);
  const [coachingEnabled, setCoachingEnabled] = useState(false);
  const [selectedMemberId, setSelectedMemberId] = useState<string | null>(null);
  const [showWizard, setShowWizard] = useState(false);
  const [showAddMember, setShowAddMember] = useState(false);
  const [isCollecting, setIsCollecting] = useState(false);

  // New member form
  const [newMemberName, setNewMemberName] = useState('');
  const [addingMember, setAddingMember] = useState(false);

  const loadConfig = useCallback(async () => {
    try {
      const data = await peopleService.getConfig(notebookId);
      setConfig(data);
      setHasConfig(true);
      setCoachingEnabled(data.coaching_enabled === true);
    } catch (e) {
      console.error('Failed to load people config:', e);
    } finally {
      setLoading(false);
    }
  }, [notebookId]);

  useEffect(() => {
    loadConfig();
  }, [loadConfig]);

  const handleCollectAll = async () => {
    setIsCollecting(true);
    try {
      await peopleService.collectAll(notebookId);
      await loadConfig();
    } catch (e) {
      console.error('Collect all failed:', e);
    } finally {
      setIsCollecting(false);
    }
  };

  const handleAddMember = async () => {
    if (!newMemberName.trim()) return;
    setAddingMember(true);
    try {
      await peopleService.getMembers(notebookId);
      setNewMemberName('');
      setShowAddMember(false);
      await loadConfig();
    } catch (e) {
      console.error('Failed to add member:', e);
    } finally {
      setAddingMember(false);
    }
  };

  const handleWizardComplete = () => {
    setShowWizard(false);
    loadConfig();
  };

  // =========================================================================
  // If viewing a specific member profile
  // =========================================================================

  if (selectedMemberId) {
    return (
      <PersonProfileView
        notebookId={notebookId}
        memberId={selectedMemberId}
        coachingEnabled={coachingEnabled}
        onBack={() => {
          setSelectedMemberId(null);
          loadConfig();
        }}
      />
    );
  }

  // =========================================================================
  // Loading or no config — render nothing.
  // The parent (CollectorPanel) gates rendering behind hasPeopleConfig,
  // but guard here too for safety.
  // =========================================================================

  if (loading || !hasConfig) {
    return null;
  }

  // =========================================================================
  // Main panel — has config + members
  // =========================================================================

  const members = config?.members || [];

  return (
    <div>
      {/* Compact action bar */}
      <div className="flex items-center justify-between mb-3">
        <span className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide">
          {members.length === 1 ? 'Profile' : `Profiles (${members.length})`}
        </span>
        <div className="flex items-center gap-2">
          <button
            onClick={async () => {
              try {
                const next = !coachingEnabled;
                await peopleService.toggleCoaching(notebookId, next);
                setCoachingEnabled(next);
              } catch (e) { console.error('Toggle coaching failed:', e); }
            }}
            className={`p-1 rounded transition-colors ${
              coachingEnabled
                ? 'text-purple-500 hover:text-purple-600'
                : 'text-gray-400 hover:text-purple-500'
            }`}
            title={coachingEnabled ? 'Coaching mode ON — click to disable' : 'Enable coaching mode'}
          >
            <GraduationCap className="w-4 h-4" />
          </button>
          <button
            onClick={handleCollectAll}
            disabled={isCollecting || members.length === 0}
            className="p-1 text-gray-400 hover:text-blue-500 rounded transition-colors"
            title="Collect all profiles"
          >
            <RefreshCw className={`w-4 h-4 ${isCollecting ? 'animate-spin text-blue-500' : ''}`} />
          </button>
          <button
            onClick={() => setShowAddMember(true)}
            className="p-1 text-gray-400 hover:text-green-500 rounded transition-colors"
            title="Add team member"
          >
            <span className="text-lg leading-none">+</span>
          </button>
        </div>
      </div>

      <TeamMemberList
        notebookId={notebookId}
        members={members}
        onSelectMember={setSelectedMemberId}
        onAddMember={() => setShowAddMember(true)}
        onRefresh={loadConfig}
        isCollecting={isCollecting}
      />

      {/* Quick add member */}
      {showAddMember && (
        <div className="mt-3 p-3 border border-blue-200 dark:border-blue-800 rounded-lg bg-blue-50/50 dark:bg-blue-900/10">
          <input
            type="text"
            value={newMemberName}
            onChange={e => setNewMemberName(e.target.value)}
            placeholder="Person's name"
            className="w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 focus:ring-2 focus:ring-blue-500 outline-none"
            autoFocus
            onKeyDown={e => e.key === 'Enter' && handleAddMember()}
          />
          <div className="flex justify-end gap-2 mt-2">
            <Button size="sm" variant="secondary" onClick={() => setShowAddMember(false)}>
              Cancel
            </Button>
            <Button size="sm" onClick={handleAddMember} disabled={addingMember || !newMemberName.trim()}>
              {addingMember ? 'Adding...' : 'Add'}
            </Button>
          </div>
        </div>
      )}

      {showWizard && (
        <PeopleSetupWizard
          notebookId={notebookId}
          notebookName={notebookName}
          isOpen={showWizard}
          onClose={() => setShowWizard(false)}
          onComplete={handleWizardComplete}
        />
      )}
    </div>
  );
};
