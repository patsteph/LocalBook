import React, { useState, useEffect } from 'react';
import { Trash2 } from 'lucide-react';
import { collectorService } from '../services/collector';
import { sourceService } from '../services/sources';
import { peopleService } from '../services/people';
import { ApprovalQueue } from './collector/ApprovalQueue';
import { CollectorSetupWizard } from './collector/CollectorSetupWizard';
import { CollectorProfile } from './collector/CollectorProfile';
import { PeoplePanel } from './people/PeoplePanel';

interface CollectorConfig {
  name: string;
  intent: string;
  focus_areas: string[];
  collection_mode: string;
  approval_mode: string;
}

interface CollectorPanelProps {
  notebookId: string | null;
  notebookName?: string;
  refreshKey?: number;
  onSourcesRefresh?: () => void;
}

export const CollectorPanel: React.FC<CollectorPanelProps> = ({ 
  notebookId,
  notebookName = 'Notebook',
  refreshKey = 0,
  onSourcesRefresh
}) => {
  const [expanded, setExpanded] = useState(false);
  const [config, setConfig] = useState<CollectorConfig | null>(null);
  const [pendingCount, setPendingCount] = useState(0);
  const [loading, setLoading] = useState(false);
  const [showSetupWizard, setShowSetupWizard] = useState(false);
  const [showProfile, setShowProfile] = useState(false);
  const [lastCollection, setLastCollection] = useState<string | null>(null);
  const [collecting, setCollecting] = useState(false);
  const [collectResult, setCollectResult] = useState<{items: number; approved: number; pending: number; rejected: number; filtered: number; message?: string; auto_approved?: {id?: string; title: string; source: string; confidence: number}[]; filtered_items?: {title: string; source: string; confidence: number; reason?: string}[]} | null>(null);
  const [deletingSourceId, setDeletingSourceId] = useState<string | null>(null);
  const [hasPeopleConfig, setHasPeopleConfig] = useState(false);
  const [peopleMemberCount, setPeopleMemberCount] = useState(0);
  const [showSourceSection, setShowSourceSection] = useState(false);
  const [curatorFollowUp, setCuratorFollowUp] = useState<string | null>(null);

  useEffect(() => {
    // Reset all notebook-specific state when switching notebooks
    setConfig(null);
    setPendingCount(0);
    setCollectResult(null);
    setLastCollection(null);
    setCollecting(false);
    setExpanded(false);
    setShowSetupWizard(false);
    setHasPeopleConfig(false);
    setPeopleMemberCount(0);
    setShowSourceSection(false);
    setCuratorFollowUp(null);

    if (notebookId) {
      loadCollectorStatus();
    }
  }, [notebookId, refreshKey]);

  const loadCollectorStatus = async () => {
    if (!notebookId) return;
    
    setLoading(true);
    try {
      // Load config
      const configData = await collectorService.getConfig(notebookId);
      setConfig(configData as any);

      // Check for people profiler config
      try {
        const peopleData = await peopleService.getConfig(notebookId);
        setHasPeopleConfig(true);
        setPeopleMemberCount(peopleData.members?.length || 0);
      } catch {
        setHasPeopleConfig(false);
        setPeopleMemberCount(0);
      }

      // Load pending count
      const pendingData = await collectorService.getPendingApprovals(notebookId);
      const count = pendingData.total || pendingData.pending?.length || 0;
      setPendingCount(count);
      if (count > 0) {
        setExpanded(true);
      }
    } catch (err) {
      console.error('Error loading collector status:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleCollectNow = async () => {
    if (!notebookId) return;
    
    setCollecting(true);
    setCollectResult(null);
    
    try {
      const data: any = await collectorService.collectNow(notebookId);
      setLastCollection(new Date().toLocaleTimeString());
      setCollectResult({
        items: data.items_collected || 0,
        approved: data.items_approved || 0,
        pending: data.items_pending || 0,
        rejected: data.items_rejected || 0,
        filtered: data.items_filtered || 0,
        message: data.message,
        auto_approved: data.auto_approved || [],
        filtered_items: data.filtered || []
      });
      // Reload status after collection
      await loadCollectorStatus();
      // Tell parent to refresh sources list
      onSourcesRefresh?.();
    } catch (err) {
      console.error('Error triggering collection:', err);
      const errMsg = err instanceof Error ? err.message : 'Connection error';
      setCollectResult({ items: 0, approved: 0, pending: 0, rejected: 0, filtered: 0, message: `Collection error: ${errMsg}` });
    } finally {
      setCollecting(false);
    }
  };

  if (!notebookId) {
    return null;
  }

  const hasConfig = config && config.intent && config.intent.trim() !== '';

  return (
    <>
      <div className="border-b border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900">
        {/* Collapsed Header */}
        <button
          onClick={() => setExpanded(!expanded)}
          className="w-full px-4 py-2 flex items-center justify-between text-sm hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
        >
          <div className="flex items-center gap-3">
            <span className="text-lg">{hasPeopleConfig ? '\u{1F465}' : (() => {
              const intent = (config?.intent || '').toLowerCase();
              if (intent.includes('news') || intent.includes('financials') || intent.includes('company') || intent.includes('competitive')) return '\u{1F3E2}';
              if (intent.includes('industry') || intent.includes('market') || intent.includes('sector')) return '\u{1F4CA}';
              if (intent.includes('research') || intent.includes('papers') || intent.includes('academic')) return '\u{1F52C}';
              if (intent.includes('project') || intent.includes('archive') || intent.includes('deliverables')) return '\u{1F4C1}';
              if (intent.includes('tech') || intent.includes('software') || intent.includes('engineering')) return '\u{1F4BB}';
              return '\u{1F50D}';
            })()}</span>
            <span className="font-medium text-gray-700 dark:text-gray-300">
              Collector
            </span>
            {loading && (
              <svg className="animate-spin h-3 w-3 text-gray-400" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
              </svg>
            )}
            {hasPeopleConfig ? (
              <span className="text-xs text-gray-500 dark:text-gray-400">
                {peopleMemberCount} member{peopleMemberCount !== 1 ? 's' : ''}
              </span>
            ) : hasConfig ? (
              <span className="text-xs text-gray-500 dark:text-gray-400">
                {config.collection_mode} • {config.approval_mode}
              </span>
            ) : (
              <span className="text-xs text-amber-600 dark:text-amber-400">
                Not configured
              </span>
            )}
          </div>
          
          <div className="flex items-center gap-3">
            {pendingCount > 0 && (
              <span className="bg-blue-100 dark:bg-blue-900 text-blue-700 dark:text-blue-300 px-2 py-0.5 rounded-full text-xs font-medium">
                {pendingCount} pending
              </span>
            )}
            
            <svg 
              className={`w-4 h-4 text-gray-500 transition-transform ${expanded ? 'rotate-180' : ''}`}
              fill="none" 
              stroke="currentColor" 
              viewBox="0 0 24 24"
            >
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
            </svg>
          </div>
        </button>

        {/* Expanded Content */}
        {expanded && (
          <div className="px-4 pb-4 space-y-4 max-h-[calc(100vh-10rem)] overflow-y-auto">
            {/* Curator follow-up banner */}
            {curatorFollowUp && (
              <div className="bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-lg p-3 flex items-start gap-2">
                <span className="text-sm mt-0.5">💬</span>
                <p className="text-sm text-blue-800 dark:text-blue-200 flex-1">{curatorFollowUp}</p>
                <button
                  onClick={() => setCuratorFollowUp(null)}
                  className="text-blue-400 hover:text-blue-600 dark:hover:text-blue-300 text-xs ml-2"
                >
                  ✕
                </button>
              </div>
            )}

            {!hasConfig && !hasPeopleConfig ? (
              /* No Config at all - Show Setup Prompt */
              <div className="bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 rounded-lg p-4">
                <p className="text-sm text-amber-800 dark:text-amber-200 mb-3">
                  Set up your Collector to automatically find and curate content for this notebook.
                </p>
                <button
                  onClick={() => setShowSetupWizard(true)}
                  className="px-4 py-2 bg-amber-600 hover:bg-amber-700 text-white text-sm font-medium rounded-lg transition-colors"
                >
                  Configure Collector
                </button>
              </div>
            ) : hasPeopleConfig && notebookId ? (
              /* People notebook — PeoplePanel is primary */
              <>
                <PeoplePanel notebookId={notebookId} notebookName={notebookName} />

                {/* Compact source collection sub-section */}
                {hasConfig && (
                  <div className="border-t border-gray-200 dark:border-gray-700 pt-3">
                    <button
                      onClick={() => setShowSourceSection(!showSourceSection)}
                      className="flex items-center gap-2 text-xs text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300 mb-2"
                    >
                      <svg className={`w-3 h-3 transition-transform ${showSourceSection ? 'rotate-90' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                      </svg>
                      Source Collection
                      {lastCollection && <span className="text-gray-400">• Last: {lastCollection}</span>}
                    </button>

                    {showSourceSection && (
                      <div className="pl-5 space-y-2">
                        <div className="flex items-center gap-2">
                          <button
                            onClick={handleCollectNow}
                            disabled={collecting}
                            className={`px-3 py-1 text-white text-xs font-medium rounded transition-colors flex items-center gap-1.5 ${
                              collecting ? 'bg-blue-500 cursor-wait' : 'bg-blue-600 hover:bg-blue-700'
                            }`}
                          >
                            {collecting && (
                              <svg className="animate-spin h-3 w-3" viewBox="0 0 24 24">
                                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                              </svg>
                            )}
                            {collecting ? 'Collecting...' : 'Collect Sources'}
                          </button>
                          <button
                            onClick={() => setShowProfile(true)}
                            className="text-xs text-blue-600 dark:text-blue-400 hover:underline"
                          >
                            Profile
                          </button>
                          <button
                            onClick={() => setShowSetupWizard(true)}
                            className="text-xs text-blue-600 dark:text-blue-400 hover:underline"
                          >
                            Edit
                          </button>
                        </div>

                        {/* Compact collection result */}
                        {collectResult && !collecting && (
                          <p className={`text-xs ${
                            collectResult.approved > 0 ? 'text-green-600 dark:text-green-400' : 'text-amber-600 dark:text-amber-400'
                          }`}>
                            {collectResult.approved > 0
                              ? `Added ${collectResult.approved} source${collectResult.approved !== 1 ? 's' : ''} (${collectResult.items} found${(collectResult.rejected + collectResult.filtered) > 0 ? `, ${collectResult.rejected + collectResult.filtered} filtered` : ''})`
                              : (collectResult.message || 'No new items found')}
                          </p>
                        )}

                        {pendingCount > 0 && (
                          <div className="pt-2">
                            <h4 className="text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
                              Pending ({pendingCount})
                            </h4>
                            <ApprovalQueue notebookId={notebookId} onApprovalChange={loadCollectorStatus} />
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                )}
              </>
            ) : (
              /* Normal notebook — standard Collector layout */
              <>
                {/* Config Summary — whole card opens Profile, Edit stays separate */}
                <div
                  onClick={() => setShowProfile(true)}
                  className="bg-white dark:bg-gray-800 rounded-lg p-3 border border-gray-200 dark:border-gray-700 cursor-pointer hover:border-blue-400 dark:hover:border-blue-600 transition-colors group"
                >
                  <div className="flex items-start justify-between mb-2">
                    <h4 className="text-sm font-medium text-gray-900 dark:text-white group-hover:text-blue-600 dark:group-hover:text-blue-400 transition-colors">
                      {config?.name || 'Collector'}
                    </h4>
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-gray-400 dark:text-gray-500 group-hover:text-blue-500 transition-colors">
                        View Profile →
                      </span>
                      <span className="text-gray-300 dark:text-gray-600">|</span>
                      <button
                        onClick={(e) => { e.stopPropagation(); setShowSetupWizard(true); }}
                        className="text-xs text-blue-600 dark:text-blue-400 hover:underline"
                      >
                        Edit
                      </button>
                    </div>
                  </div>
                  
                  {config?.intent && (
                    <p className="text-xs text-gray-600 dark:text-gray-400 mb-2 line-clamp-2">
                      {config.intent}
                    </p>
                  )}
                  
                  {config?.focus_areas && config.focus_areas.length > 0 && (
                    <div className="flex flex-wrap gap-1">
                      {config.focus_areas.slice(0, 5).map((area, i) => (
                        <span 
                          key={i}
                          className="px-2 py-0.5 bg-blue-100 dark:bg-blue-900/50 text-blue-700 dark:text-blue-300 rounded text-xs"
                        >
                          {area}
                        </span>
                      ))}
                      {config.focus_areas.length > 5 && (
                        <span className="text-xs text-gray-500">
                          +{config.focus_areas.length - 5} more
                        </span>
                      )}
                    </div>
                  )}
                </div>

                {/* Actions */}
                <div className="flex items-center gap-2">
                  <button
                    onClick={handleCollectNow}
                    disabled={collecting}
                    className={`px-3 py-1.5 text-white text-sm font-medium rounded-lg transition-colors flex items-center gap-2 ${
                      collecting 
                        ? 'bg-blue-500 cursor-wait' 
                        : 'bg-blue-600 hover:bg-blue-700'
                    }`}
                  >
                    {collecting && (
                      <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24">
                        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                      </svg>
                    )}
                    {collecting ? 'Collecting...' : 'Collect Now'}
                  </button>
                  {lastCollection && !collecting && (
                    <span className="text-xs text-gray-500 dark:text-gray-400">
                      Last: {lastCollection}
                    </span>
                  )}
                </div>

                {/* Collection Progress */}
                {collecting && (
                  <div className="bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-lg p-3">
                    <div className="flex items-center gap-2 text-sm text-blue-700 dark:text-blue-300">
                      <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24">
                        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                      </svg>
                      Fetching from configured sources...
                    </div>
                    <p className="text-xs text-blue-600 dark:text-blue-400 mt-1">
                      Checking RSS feeds, news, and web sources. This may take a moment.
                    </p>
                  </div>
                )}

                {/* Collection Results */}
                {collectResult && !collecting && (
                  <div className={`border rounded-lg p-3 text-sm ${
                    collectResult.items > 0
                      ? 'bg-green-50 dark:bg-green-900/20 border-green-200 dark:border-green-800 text-green-700 dark:text-green-300'
                      : 'bg-amber-50 dark:bg-amber-900/20 border-amber-200 dark:border-amber-800 text-amber-700 dark:text-amber-300'
                  }`}>
                    {collectResult.approved > 0 ? (
                      <>
                        <p className="font-medium">Added {collectResult.approved} source{collectResult.approved !== 1 ? 's' : ''} to notebook</p>
                        <p className="text-xs mt-1 opacity-80">
                          {collectResult.items} found · {collectResult.approved} added
                          {collectResult.pending > 0 && ` · ${collectResult.pending} pending review`}
                          {(collectResult.rejected + collectResult.filtered) > 0 && ` · ${collectResult.rejected + collectResult.filtered} filtered out`}
                        </p>
                        {collectResult.auto_approved && collectResult.auto_approved.length > 0 && (
                          <div className="mt-2 pt-2 border-t border-green-200 dark:border-green-800">
                            <p className="text-xs font-medium mb-1">Added:</p>
                            <ul className="text-xs space-y-0.5">
                              {collectResult.auto_approved.map((item, i) => (
                                <li key={i} className="flex items-center gap-1 group/item">
                                  <span className="text-green-600 dark:text-green-400">✓</span>
                                  <span className="truncate flex-1">{item.title}</span>
                                  <span className="text-green-500 shrink-0">
                                    {Math.round(item.confidence * 100)}%
                                  </span>
                                  {item.id && notebookId && (
                                    <button
                                      onClick={async (e) => {
                                        e.stopPropagation();
                                        if (!window.confirm(`Remove "${item.title}" from sources?`)) return;
                                        setDeletingSourceId(item.id!);
                                        try {
                                          await sourceService.delete(notebookId, item.id!);
                                          setCollectResult(prev => prev ? {
                                            ...prev,
                                            approved: Math.max(0, prev.approved - 1),
                                            auto_approved: prev.auto_approved?.filter(a => a.id !== item.id),
                                          } : null);
                                          onSourcesRefresh?.();
                                        } catch (err) {
                                          console.error('Failed to delete source:', err);
                                        } finally {
                                          setDeletingSourceId(null);
                                        }
                                      }}
                                      disabled={deletingSourceId === item.id}
                                      className="opacity-0 group-hover/item:opacity-100 p-0.5 text-gray-400 hover:text-red-500 dark:hover:text-red-400 transition-all shrink-0"
                                      title="Remove source"
                                    >
                                      {deletingSourceId === item.id ? (
                                        <svg className="w-3 h-3 animate-spin" viewBox="0 0 24 24">
                                          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                                          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                                        </svg>
                                      ) : (
                                        <Trash2 size={12} />
                                      )}
                                    </button>
                                  )}
                                </li>
                              ))}
                            </ul>
                          </div>
                        )}
                        {collectResult.filtered_items && collectResult.filtered_items.length > 0 && (
                          <div className="mt-2 pt-2 border-t border-green-200 dark:border-green-800">
                            <p className="text-xs font-medium mb-1 text-gray-500 dark:text-gray-400">Filtered out (shallow/duplicate/low confidence):</p>
                            <ul className="text-xs space-y-0.5">
                              {collectResult.filtered_items.map((item, i) => (
                                <li key={i} className="flex items-center gap-1 text-gray-400 dark:text-gray-500">
                                  <span>✗</span>
                                  <span className="truncate">{item.title}</span>
                                  <span className="ml-auto shrink-0">
                                    {Math.round(item.confidence * 100)}%
                                  </span>
                                </li>
                              ))}
                            </ul>
                          </div>
                        )}
                      </>
                    ) : collectResult.items > 0 ? (
                      <p>Found {collectResult.items} items but none met quality threshold
                        {collectResult.pending > 0 && ` · ${collectResult.pending} pending review`}
                      </p>
                    ) : (
                      <p>{collectResult.message || 'No new items found from configured sources'}</p>
                    )}
                  </div>
                )}

                {/* Pending Items — prominent when items need review */}
                {pendingCount > 0 && (
                  <div className="bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 rounded-lg p-3">
                    <div className="flex items-center justify-between mb-2">
                      <div className="flex items-center gap-2">
                        <span className="text-base">&#x1F4CB;</span>
                        <h4 className="text-sm font-semibold text-amber-800 dark:text-amber-200">
                          {pendingCount} item{pendingCount !== 1 ? 's' : ''} awaiting your review
                        </h4>
                      </div>
                    </div>
                    <ApprovalQueue 
                      notebookId={notebookId} 
                      onApprovalChange={loadCollectorStatus}
                    />
                  </div>
                )}
              </>
            )}
          </div>
        )}
      </div>

      {/* Setup Wizard Modal */}
      {showSetupWizard && (
        <CollectorSetupWizard
          notebookId={notebookId}
          notebookName={notebookName}
          isOpen={showSetupWizard}
          onClose={() => setShowSetupWizard(false)}
          onComplete={(followUp?: string) => {
            setShowSetupWizard(false);
            loadCollectorStatus();
            // Auto-expand and auto-collect after wizard completes
            setExpanded(true);
            setTimeout(() => handleCollectNow(), 500);
            if (followUp) setCuratorFollowUp(followUp);
          }}
        />
      )}

      {/* Profile Modal */}
      {showProfile && notebookId && (
        <CollectorProfile
          notebookId={notebookId}
          isOpen={showProfile}
          onClose={() => setShowProfile(false)}
        />
      )}
    </>
  );
};

export default CollectorPanel;
