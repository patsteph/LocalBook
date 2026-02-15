import React, { useState, useEffect } from 'react';
import { 
  Sun, 
  Moon,
  Inbox,
  TrendingUp,
  Clock,
  ChevronRight,
  Sparkles,
  Loader2,
  RefreshCw
} from 'lucide-react';
import { curatorService } from '../../services/curatorApi';

interface NotebookSummary {
  notebook_id: string;
  name: string;
  items_added: number;
  flagged_important: number;
  pending_approval: number;
  top_finding: string | null;
}

interface BriefData {
  away_duration: string;
  notebooks: NotebookSummary[];
  cross_notebook_insight: string | null;
  generated_at: string;
}

interface MorningBriefProps {
  isOpen: boolean;
  onClose: () => void;
  onNavigateToNotebook?: (notebookId: string) => void;
  onShowHighlights?: () => void;
}

export const MorningBrief: React.FC<MorningBriefProps> = ({
  isOpen,
  onClose,
  onNavigateToNotebook,
  onShowHighlights,
}) => {
  const [briefData, setBriefData] = useState<BriefData | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (isOpen) {
      fetchBrief();
    }
  }, [isOpen]);

  const fetchBrief = async () => {
    setIsLoading(true);
    setError(null);
    
    try {
      // Calculate hours since last seen (stored in localStorage)
      const lastSeen = localStorage.getItem('localbook_last_seen');
      const now = Date.now();
      let hoursAway = 8; // Default
      
      if (lastSeen) {
        hoursAway = Math.floor((now - parseInt(lastSeen)) / (1000 * 60 * 60));
        hoursAway = Math.max(1, Math.min(hoursAway, 168)); // Between 1 hour and 1 week
      }
      
      const data = await curatorService.getMorningBrief(hoursAway);
      setBriefData(data);
      
      // Update last seen
      localStorage.setItem('localbook_last_seen', now.toString());
      
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load brief');
    } finally {
      setIsLoading(false);
    }
  };

  const getGreeting = () => {
    const hour = new Date().getHours();
    if (hour < 12) return 'Good morning';
    if (hour < 17) return 'Good afternoon';
    return 'Good evening';
  };

  const getIcon = () => {
    const hour = new Date().getHours();
    if (hour >= 6 && hour < 18) {
      return <Sun className="w-6 h-6 text-amber-500" />;
    }
    return <Moon className="w-6 h-6 text-indigo-400" />;
  };

  const hasActivity = briefData && (
    briefData.notebooks.length > 0 || 
    briefData.cross_notebook_insight
  );

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-white dark:bg-gray-800 rounded-xl max-w-lg w-full mx-4 overflow-hidden shadow-2xl">
        {/* Header */}
        <div className="bg-gradient-to-r from-blue-500 to-purple-600 p-6 text-white">
          <div className="flex items-center gap-3 mb-2">
            {getIcon()}
            <h2 className="text-xl font-semibold">{getGreeting()}</h2>
          </div>
          {briefData && (
            <p className="text-white/80 text-sm flex items-center gap-1">
              <Clock className="w-4 h-4" />
              You've been away for {briefData.away_duration}
            </p>
          )}
        </div>

        {/* Content */}
        <div className="p-4 max-h-[60vh] overflow-y-auto">
          {isLoading ? (
            <div className="py-8 text-center">
              <Loader2 className="w-8 h-8 animate-spin mx-auto text-blue-500 mb-2" />
              <p className="text-gray-500">Loading your brief...</p>
            </div>
          ) : error ? (
            <div className="py-8 text-center">
              <p className="text-red-500 mb-2">{error}</p>
              <button 
                onClick={fetchBrief}
                className="text-blue-600 hover:text-blue-700 flex items-center gap-1 mx-auto"
              >
                <RefreshCw className="w-4 h-4" />
                Try again
              </button>
            </div>
          ) : !hasActivity ? (
            <div className="py-8 text-center">
              <Sparkles className="w-12 h-12 mx-auto text-gray-300 mb-3" />
              <p className="text-gray-500">No new activity while you were away</p>
              <p className="text-gray-400 text-sm mt-1">Your Collectors are monitoring your sources</p>
            </div>
          ) : (
            <div className="space-y-4">
              {/* Cross-notebook insight */}
              {briefData?.cross_notebook_insight && (
                <div className="bg-purple-50 dark:bg-purple-900/20 rounded-lg p-3">
                  <div className="flex items-start gap-2">
                    <Sparkles className="w-5 h-5 text-purple-500 mt-0.5" />
                    <div>
                      <p className="text-sm font-medium text-purple-700 dark:text-purple-300">
                        Cross-Notebook Insight
                      </p>
                      <p className="text-sm text-gray-600 dark:text-gray-400 mt-1">
                        {briefData.cross_notebook_insight}
                      </p>
                    </div>
                  </div>
                </div>
              )}

              {/* Notebook summaries */}
              {briefData?.notebooks.map((notebook) => (
                <div 
                  key={notebook.notebook_id}
                  className="border dark:border-gray-700 rounded-lg p-3 hover:border-blue-300 dark:hover:border-blue-600 transition-colors cursor-pointer"
                  onClick={() => onNavigateToNotebook?.(notebook.notebook_id)}
                >
                  <div className="flex items-center justify-between mb-2">
                    <h3 className="font-medium truncate">{notebook.name}</h3>
                    <ChevronRight className="w-4 h-4 text-gray-400" />
                  </div>
                  
                  <div className="flex gap-4 text-sm text-gray-500 dark:text-gray-400">
                    {notebook.items_added > 0 && (
                      <span className="flex items-center gap-1">
                        <TrendingUp className="w-4 h-4 text-green-500" />
                        {notebook.items_added} new
                      </span>
                    )}
                    {notebook.pending_approval > 0 && (
                      <span className="flex items-center gap-1">
                        <Inbox className="w-4 h-4 text-amber-500" />
                        {notebook.pending_approval} pending
                      </span>
                    )}
                    {notebook.flagged_important > 0 && (
                      <span className="flex items-center gap-1 text-blue-600 dark:text-blue-400">
                        <Sparkles className="w-4 h-4" />
                        {notebook.flagged_important} important
                      </span>
                    )}
                  </div>
                  
                  {notebook.top_finding && (
                    <p className="text-sm text-gray-600 dark:text-gray-400 mt-2 line-clamp-2">
                      📌 {notebook.top_finding}
                    </p>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="p-4 border-t dark:border-gray-700 flex justify-end gap-3">
          <button
            onClick={onClose}
            className="px-4 py-2 text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg transition-colors"
          >
            Got it
          </button>
          {onShowHighlights && (
            <button
              onClick={() => {
                onShowHighlights();
                onClose();
              }}
              className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors flex items-center gap-2"
            >
              <Sparkles className="w-4 h-4" />
              Show highlights
            </button>
          )}
        </div>
      </div>
    </div>
  );
};

export default MorningBrief;
