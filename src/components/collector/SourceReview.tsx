import React, { useState, useEffect } from 'react';
import { 
  Rss, 
  Globe, 
  FileText, 
  Youtube, 
  BookOpen, 
  Newspaper,
  Check,
  ChevronDown,
  ChevronUp,
  Loader2,
  Sparkles,
  Building2,
  TrendingUp,
  AlertCircle
} from 'lucide-react';
import { sourceDiscoveryService } from '../../services/sourceDiscovery';
import { PeopleSetupWizard } from '../people/PeopleSetupWizard';

interface DiscoveredSource {
  id: string;
  source_type: string;
  name: string;
  url?: string;
  description: string;
  confidence: number;
  auto_approve: boolean;
  metadata: Record<string, any>;
  validated: boolean;
  validation_error?: string;
  has_rss: boolean;
  rss_url?: string;
  curator_recommendation?: 'auto_approve' | 'suggest' | 'skip';
  curator_reason?: string;
}

interface IntentAnalysis {
  primary_topic: string;
  notebook_purpose: string;
  purpose_confidence: number;
  is_company_research: boolean;
  company_name?: string;
  company_ticker?: string;
  company_is_private?: boolean;
  needs_company_clarification?: boolean;
  product_name?: string;
  person_name?: string;
  skill_name?: string;
  industry?: string;
  competitors: string[];
  keywords: string[];
  time_sensitivity: string;
  research_depth: string;
}

const PURPOSE_OPTIONS = [
  { value: 'company_research', label: 'Company Research', description: 'Track a specific company (news, filings, competitors)' },
  { value: 'topic_research', label: 'Topic Research', description: 'Explore a broad topic (AI, Leadership, etc.)' },
  { value: 'product_research', label: 'Product/Technology', description: 'Research a product or technology' },
  { value: 'skill_development', label: 'Skill Development', description: 'Learn a new skill with tutorials and courses' },
  { value: 'person_tracking', label: 'Coaching / People', description: 'Track team members, coaching profiles, personal development' },
  { value: 'industry_monitoring', label: 'Industry Monitoring', description: 'Monitor an industry sector' },
  { value: 'personal_interests', label: 'Personal Interests', description: 'Hobbies and personal interests' },
];

interface CompanyProfile {
  name: string;
  ticker?: string;
  industry?: string;
  sector?: string;
  competitors: string[];
  official_website?: string;
  news_page?: string;
  investor_relations?: string;
}

interface SourceReviewProps {
  notebookId: string;
  subject: string;
  intent: string;
  focusAreas: string[];
  onComplete: (sourcesAdded: number) => void;
  onCancel: () => void;
}

const SOURCE_TYPE_ICONS: Record<string, React.ReactNode> = {
  rss_feed: <Rss className="w-4 h-4" />,
  RSS_FEED: <Rss className="w-4 h-4" />,
  web_page: <Globe className="w-4 h-4" />,
  WEB_PAGE: <Globe className="w-4 h-4" />,
  company_news: <Building2 className="w-4 h-4" />,
  COMPANY_NEWS: <Building2 className="w-4 h-4" />,
  sec_filing: <FileText className="w-4 h-4" />,
  SEC_FILING: <FileText className="w-4 h-4" />,
  youtube_keyword: <Youtube className="w-4 h-4" />,
  YOUTUBE_KEYWORD: <Youtube className="w-4 h-4" />,
  youtube_channel: <Youtube className="w-4 h-4" />,
  YOUTUBE_CHANNEL: <Youtube className="w-4 h-4" />,
  arxiv_category: <BookOpen className="w-4 h-4" />,
  ARXIV_CATEGORY: <BookOpen className="w-4 h-4" />,
  news_keyword: <Newspaper className="w-4 h-4" />,
  NEWS_KEYWORD: <Newspaper className="w-4 h-4" />,
};

const SOURCE_TYPE_LABELS: Record<string, string> = {
  rss_feed: 'RSS Feed',
  RSS_FEED: 'RSS Feed',
  web_page: 'Web Page',
  WEB_PAGE: 'Web Page',
  company_news: 'Company News',
  COMPANY_NEWS: 'Company News',
  sec_filing: 'SEC Filing',
  SEC_FILING: 'SEC Filing',
  youtube_keyword: 'YouTube',
  YOUTUBE_KEYWORD: 'YouTube',
  youtube_channel: 'YouTube Channel',
  YOUTUBE_CHANNEL: 'YouTube Channel',
  arxiv_category: 'arXiv',
  ARXIV_CATEGORY: 'arXiv',
  news_keyword: 'News',
  NEWS_KEYWORD: 'News',
};

export const SourceReview: React.FC<SourceReviewProps> = ({
  notebookId,
  subject,
  intent,
  focusAreas,
  onComplete,
  onCancel,
}) => {
  const [isLoading, setIsLoading] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sources, setSources] = useState<DiscoveredSource[]>([]);
  const [intentAnalysis, setIntentAnalysis] = useState<IntentAnalysis | null>(null);
  const [companyProfile, setCompanyProfile] = useState<CompanyProfile | null>(null);
  const [selectedSources, setSelectedSources] = useState<Set<string>>(new Set());
  const [expandedSource, setExpandedSource] = useState<string | null>(null);
  const [discoveryTime, setDiscoveryTime] = useState<number>(0);
  const [showPurposeClarification, setShowPurposeClarification] = useState(false);
  const [overridePurpose, setOverridePurpose] = useState<string | null>(null);
  const [showCompanyClarification, setShowCompanyClarification] = useState(false);
  const [companyDetails, setCompanyDetails] = useState<{name: string; ticker?: string; industry?: string} | null>(null);
  const [localCompanyName, setLocalCompanyName] = useState('');
  const [localTicker, setLocalTicker] = useState('');
  const [localIndustry, setLocalIndustry] = useState('');
  const [showPeopleWizard, setShowPeopleWizard] = useState(false);

  useEffect(() => {
    discoverSources();
  }, [notebookId, intent, focusAreas]);

  const discoverSources = async () => {
    setIsLoading(true);
    setError(null);
    
    try {
      const data = await sourceDiscoveryService.discover(notebookId, {
        subject,
        intent,
        focus_areas: focusAreas,
        override_purpose: overridePurpose,
        company_details: companyDetails,
      });
      setSources(data.sources || []);
      setIntentAnalysis(data.intent_analysis);
      setCompanyProfile(data.company_profile);
      setDiscoveryTime(data.discovery_time_ms || 0);
      
      // Check if this is a people/coaching notebook — branch to People Profiler
      const purpose = data.intent_analysis?.notebook_purpose;
      if (purpose === 'person_tracking' && !overridePurpose) {
        setIsLoading(false);
        setShowPeopleWizard(true);
        return;
      }
      
      // Check if purpose confidence is low - show clarification
      const confidence = data.intent_analysis?.purpose_confidence ?? 1.0;
      if (confidence < 0.7 && !overridePurpose) {
        setShowPurposeClarification(true);
        setIsLoading(false);
        return; // Wait for user to clarify
      }
      
      // Check if company lookup failed and needs clarification
      if (data.intent_analysis?.needs_company_clarification && !companyDetails) {
        setShowCompanyClarification(true);
        setIsLoading(false);
        return; // Wait for user to provide company details
      }
      
      // Auto-select sources with auto_approve or suggest recommendation
      const autoSelected = new Set<string>();
      data.sources?.forEach((source: DiscoveredSource) => {
        if (source.curator_recommendation === 'auto_approve' || 
            source.curator_recommendation === 'suggest') {
          autoSelected.add(source.id);
        }
      });
      setSelectedSources(autoSelected);
      
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Discovery failed');
    } finally {
      setIsLoading(false);
    }
  };

  const toggleSource = (sourceId: string) => {
    setSelectedSources(prev => {
      const newSet = new Set(prev);
      if (newSet.has(sourceId)) {
        newSet.delete(sourceId);
      } else {
        newSet.add(sourceId);
      }
      return newSet;
    });
  };

  const selectAll = () => {
    setSelectedSources(new Set(sources.map(s => s.id)));
  };

  const deselectAll = () => {
    setSelectedSources(new Set());
  };

  const handleApprove = async () => {
    setIsSubmitting(true);
    setError(null);
    
    try {
      const approved = sources.filter(s => selectedSources.has(s.id));
      
      const result = await sourceDiscoveryService.approve(notebookId, approved);
      onComplete(result.sources_added);
      
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save sources');
    } finally {
      setIsSubmitting(false);
    }
  };

  const renderSourceCard = (source: DiscoveredSource) => {
    const isSelected = selectedSources.has(source.id);
    const isExpanded = expandedSource === source.id;
    const isAutoApproved = source.curator_recommendation === 'auto_approve';
    const isSkipped = source.curator_recommendation === 'skip';
    
    return (
      <div
        key={source.id}
        className={`
          border rounded-lg p-3 transition-all cursor-pointer
          ${isSelected 
            ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/20' 
            : isSkipped
              ? 'border-gray-200 dark:border-gray-700 opacity-60'
              : 'border-gray-200 dark:border-gray-700 hover:border-gray-300'
          }
        `}
        onClick={() => toggleSource(source.id)}
      >
        <div className="flex items-start gap-3">
          <div className={`
            w-5 h-5 rounded border-2 flex items-center justify-center flex-shrink-0 mt-0.5
            ${isSelected 
              ? 'border-blue-500 bg-blue-500 text-white' 
              : 'border-gray-300 dark:border-gray-600'
            }
          `}>
            {isSelected && <Check className="w-3 h-3" />}
          </div>
          
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <span className="text-gray-500 dark:text-gray-400">
                {SOURCE_TYPE_ICONS[source.source_type] || <Globe className="w-4 h-4" />}
              </span>
              <span className="font-medium truncate text-gray-900 dark:text-gray-100">{source.name}</span>
              {isAutoApproved && (
                <span className="text-xs bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400 px-1.5 py-0.5 rounded">
                  Recommended
                </span>
              )}
              {isSkipped && (
                <span className="text-xs bg-gray-100 text-gray-500 dark:bg-gray-800 px-1.5 py-0.5 rounded">
                  Lower relevance
                </span>
              )}
            </div>
            
            <p className="text-sm text-gray-500 dark:text-gray-400 mt-1 line-clamp-2">
              {source.description}
            </p>
            
            {source.curator_reason && (
              <p className="text-xs text-blue-600 dark:text-blue-400 mt-1 flex items-center gap-1">
                <Sparkles className="w-3 h-3" />
                {source.curator_reason}
              </p>
            )}
            
            {/* Expandable details */}
            <button
              onClick={(e) => {
                e.stopPropagation();
                setExpandedSource(isExpanded ? null : source.id);
              }}
              className="text-xs text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 mt-2 flex items-center gap-1"
            >
              {isExpanded ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
              {isExpanded ? 'Less details' : 'More details'}
            </button>
            
            {isExpanded && (
              <div className="mt-2 text-xs space-y-1 text-gray-500 dark:text-gray-400">
                <p><span className="font-medium">Type:</span> {SOURCE_TYPE_LABELS[source.source_type] || source.source_type}</p>
                {source.url && (
                  <p className="truncate"><span className="font-medium">URL:</span> {source.url}</p>
                )}
                <p><span className="font-medium">Confidence:</span> {Math.round(source.confidence * 100)}%</p>
                {source.validated && !source.validation_error && (
                  <p className="text-green-600 dark:text-green-400">✓ URL validated</p>
                )}
                {source.validation_error && (
                  <p className="text-amber-600 dark:text-amber-400">⚠ {source.validation_error}</p>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    );
  };

  // Handler for purpose clarification selection
  const handlePurposeSelect = (purpose: string) => {
    setOverridePurpose(purpose);
    setShowPurposeClarification(false);
    // Re-run discovery with specified purpose
    discoverSources();
  };

  if (isLoading) {
    return (
      <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
        <div className="bg-white dark:bg-gray-800 rounded-xl p-4 max-w-lg w-full mx-4 text-center">
          <Loader2 className="w-12 h-12 animate-spin mx-auto text-blue-500 mb-4" />
          <h3 className="text-base font-semibold mb-2 text-gray-900 dark:text-gray-100">Discovering Sources...</h3>
          <p className="text-gray-500 dark:text-gray-400">
            Analyzing your intent and finding relevant sources
          </p>
          <div className="mt-4 text-sm text-gray-400">
            <Sparkles className="w-4 h-4 inline mr-1" />
            The Curator is validating each source for you
          </div>
        </div>
      </div>
    );
  }

// People Profiler branching — shown when intent is coaching/person_tracking
if (showPeopleWizard) {
  return (
    <PeopleSetupWizard
      notebookId={notebookId}
      notebookName={subject || 'Team'}
      isOpen={true}
      onClose={onCancel}
      onComplete={() => onComplete(0)}
    />
  );
}

// Purpose clarification UI - shown when intent is ambiguous
if (showPurposeClarification) {
  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-white dark:bg-gray-800 rounded-xl p-4 max-w-lg w-full mx-4">
        <h3 className="text-base font-semibold mb-2 flex items-center gap-2 text-gray-900 dark:text-gray-100">
          <Sparkles className="w-5 h-5 text-blue-500" />
          Help us understand your goal
        </h3>
        <p className="text-gray-500 dark:text-gray-400 mb-4">
          What best describes what you want to do with "{intentAnalysis?.primary_topic}"?
        </p>
        
        <div className="space-y-2">
          {PURPOSE_OPTIONS.map((option) => (
            <button
              key={option.value}
              onClick={() => handlePurposeSelect(option.value)}
              className="w-full text-left p-3 rounded-lg border border-gray-200 dark:border-gray-700 hover:border-blue-500 hover:bg-blue-50 dark:hover:bg-blue-900/20 transition-colors"
            >
              <span className="font-medium text-gray-900 dark:text-gray-100">{option.label}</span>
              <p className="text-sm text-gray-500 dark:text-gray-400">{option.description}</p>
            </button>
          ))}
        </div>
        
        <div className="mt-4 flex justify-between">
          <button
            onClick={onCancel}
            className="text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300"
          >
            Cancel
          </button>
          <button
            onClick={() => {
              setShowPurposeClarification(false);
              // Proceed with best guess
            }}
            className="text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-500"
          >
            Use best guess →
          </button>
        </div>
      </div>
    </div>
  );
}

// Company clarification UI - shown when company lookup failed
if (showCompanyClarification) {
  const handleCompanySubmit = () => {
    setCompanyDetails({
      name: localCompanyName || intentAnalysis?.company_name || '',
      ticker: localTicker || undefined,
      industry: localIndustry || undefined
    });
    setShowCompanyClarification(false);
    discoverSources(); // Re-run with company details
  };
  
  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-white dark:bg-gray-800 rounded-xl p-4 max-w-lg w-full mx-4">
        <h3 className="text-base font-semibold mb-2 flex items-center gap-2 text-gray-900 dark:text-gray-100">
          <Building2 className="w-5 h-5 text-blue-500" />
          Help us find this company
        </h3>
        <p className="text-gray-500 dark:text-gray-400 mb-4">
          We couldn't find "{intentAnalysis?.company_name}" in our search. Can you provide more details?
        </p>
        
        <div className="space-y-3">
          <div>
            <label className="block text-sm font-medium mb-1 text-gray-700 dark:text-gray-300">Company Name</label>
            <input
              type="text"
              value={localCompanyName}
              onChange={(e) => setLocalCompanyName(e.target.value)}
              className="w-full px-3 py-2 border rounded-lg dark:bg-gray-700 dark:border-gray-600"
              placeholder="e.g., Costco Wholesale Corporation"
            />
          </div>
          <div>
            <label className="block text-sm font-medium mb-1 text-gray-700 dark:text-gray-300">Stock Ticker (optional)</label>
            <input
              type="text"
              value={localTicker}
              onChange={(e) => setLocalTicker(e.target.value.toUpperCase())}
              className="w-full px-3 py-2 border rounded-lg dark:bg-gray-700 dark:border-gray-600"
              placeholder="e.g., COST (leave blank if private company)"
            />
          </div>
          <div>
            <label className="block text-sm font-medium mb-1 text-gray-700 dark:text-gray-300">Industry (optional)</label>
            <input
              type="text"
              value={localIndustry}
              onChange={(e) => setLocalIndustry(e.target.value)}
              className="w-full px-3 py-2 border rounded-lg dark:bg-gray-700 dark:border-gray-600"
              placeholder="e.g., Retail, Technology, Healthcare"
            />
          </div>
        </div>
        
        <div className="mt-4 flex justify-between">
          <button
            onClick={onCancel}
            className="text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300"
          >
            Cancel
          </button>
          <button
            onClick={handleCompanySubmit}
            disabled={!localCompanyName.trim()}
            className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 dark:hover:bg-blue-800 disabled:opacity-50"
          >
            Find Sources →
          </button>
        </div>
      </div>
    </div>
  );
}

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-white dark:bg-gray-800 rounded-xl max-w-2xl w-full mx-4 max-h-[90vh] flex flex-col">
        {/* Header */}
        <div className="p-4 border-b dark:border-gray-700">
          <div className="flex items-center justify-between">
            <h2 className="text-base font-semibold flex items-center gap-2 text-gray-900 dark:text-gray-100">
              <Sparkles className="w-5 h-5 text-blue-500" />
              Discovered Sources
            </h2>
            <span className="text-sm text-gray-500 dark:text-gray-400">
              {discoveryTime > 0 && `Found in ${(discoveryTime / 1000).toFixed(1)}s`}
            </span>
          </div>
          
          {/* Intent Analysis Summary */}
          {intentAnalysis && (
            <div className="mt-3 p-3 bg-gray-50 dark:bg-gray-900/50 rounded-lg text-sm">
              <div className="flex items-start gap-2">
                <TrendingUp className="w-4 h-4 text-blue-500 mt-0.5" />
                <div>
                  <p className="font-medium text-gray-900 dark:text-gray-100">{intentAnalysis.primary_topic}</p>
                  {intentAnalysis.is_company_research && companyProfile && (
                    <p className="text-gray-500 dark:text-gray-400 mt-1">
                      Company: {companyProfile.name}
                      {companyProfile.ticker && ` (${companyProfile.ticker})`}
                      {companyProfile.industry && ` • ${companyProfile.industry}`}
                    </p>
                  )}
                  {intentAnalysis.keywords.length > 0 && (
                    <div className="flex flex-wrap gap-1 mt-2">
                      {intentAnalysis.keywords.slice(0, 5).map((kw, i) => (
                        <span key={i} className="px-2 py-0.5 bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300 rounded text-xs">
                          {kw}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}
        </div>
        
        {/* Error Display */}
        {error && (
          <div className="mx-4 mt-4 p-3 bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400 rounded-lg flex items-center gap-2">
            <AlertCircle className="w-4 h-4" />
            {error}
          </div>
        )}
        
        {/* Source List */}
        <div className="flex-1 overflow-y-auto p-4">
          {sources.length === 0 ? (
            <div className="text-center py-8 text-gray-500">
              <Globe className="w-12 h-12 mx-auto mb-3 opacity-30" />
              <p>No sources discovered</p>
              <p className="text-sm">Try adjusting your intent or focus areas</p>
            </div>
          ) : (
            <>
              {/* Selection Controls */}
              <div className="flex items-center justify-between mb-3">
                <span className="text-sm text-gray-500">
                  {selectedSources.size} of {sources.length} selected
                </span>
                <div className="flex gap-2">
                  <button
                    onClick={selectAll}
                    className="text-sm text-blue-600 hover:text-blue-700"
                  >
                    Select all
                  </button>
                  <span className="text-gray-300">|</span>
                  <button
                    onClick={deselectAll}
                    className="text-sm text-gray-500 hover:text-gray-700"
                  >
                    Clear
                  </button>
                </div>
              </div>
              
              {/* Source Cards */}
              <div className="space-y-2">
                {sources.map(renderSourceCard)}
              </div>
            </>
          )}
        </div>
        
        {/* Footer Actions */}
        <div className="p-4 border-t dark:border-gray-700 flex items-center justify-between">
          <button
            onClick={onCancel}
            className="px-4 py-2 text-gray-600 dark:text-gray-400 hover:text-gray-800 dark:hover:text-gray-200"
          >
            Cancel
          </button>
          
          <div className="flex gap-2">
            <button
              onClick={discoverSources}
              disabled={isSubmitting}
              className="px-4 py-2 border border-gray-300 dark:border-gray-600 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700"
            >
              Re-discover
            </button>
            <button
              onClick={handleApprove}
              disabled={isSubmitting || selectedSources.size === 0}
              className={`
                px-4 py-2 rounded-lg flex items-center gap-2
                ${selectedSources.size > 0
                  ? 'bg-blue-600 text-white hover:bg-blue-700'
                  : 'bg-gray-100 text-gray-400 cursor-not-allowed'
                }
              `}
            >
              {isSubmitting ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  Saving...
                </>
              ) : (
                <>
                  <Check className="w-4 h-4" />
                  Add {selectedSources.size} Source{selectedSources.size !== 1 ? 's' : ''}
                </>
              )}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

export default SourceReview;
