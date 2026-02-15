import React, { useState, useEffect } from 'react';
import { Button } from '../shared/Button';
import { collectorService } from '../../services/collector';
import './CollectorStatus.css';

interface CollectorConfig {
  name: string;
  intent: string;
  focus_areas: string[];
  collection_mode: string;
  approval_mode: string;
}

interface SourceHealth {
  source_id: string;
  url: string;
  health: string;
  failure_count: number;
  items_collected: number;
}

interface CollectorStatusProps {
  notebookId: string;
  onConfigureClick?: () => void;
  onViewApprovals?: () => void;
}

export const CollectorStatus: React.FC<CollectorStatusProps> = ({
  notebookId,
  onConfigureClick,
  onViewApprovals
}) => {
  const [config, setConfig] = useState<CollectorConfig | null>(null);
  const [sourceHealth, setSourceHealth] = useState<SourceHealth[]>([]);
  const [pendingCount, setPendingCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [collecting, setCollecting] = useState(false);

  useEffect(() => {
    fetchStatus();
  }, [notebookId]);

  const fetchStatus = async () => {
    try {
      const [configData, healthData, pendingData] = await Promise.all([
        collectorService.getConfig(notebookId),
        collectorService.getSourceHealth(notebookId),
        collectorService.getPendingApprovals(notebookId)
      ]);

      setConfig(configData as any);
      setSourceHealth(healthData.sources || []);
      setPendingCount(pendingData.total || 0);
    } catch (err) {
      console.error('Error fetching collector status:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleCollectNow = async () => {
    setCollecting(true);
    try {
      await collectorService.collectNow(notebookId);
      await fetchStatus();
    } catch (err) {
      console.error('Error triggering collection:', err);
    } finally {
      setCollecting(false);
    }
  };

  const getHealthIcon = (health: string) => {
    switch (health) {
      case 'healthy': return '🟢';
      case 'degraded': return '🟡';
      case 'failing': return '🟠';
      case 'dead': return '🔴';
      default: return '⚪';
    }
  };

  const getModeLabel = (mode: string) => {
    switch (mode) {
      case 'manual': return 'Manual only';
      case 'automatic': return 'Automatic';
      case 'hybrid': return 'Hybrid (manual + auto)';
      default: return mode;
    }
  };

  if (loading) {
    return (
      <div className="collector-status collector-status--loading">
        <div className="collector-status__skeleton"></div>
      </div>
    );
  }

  if (!config) {
    return (
      <div className="collector-status collector-status--unconfigured">
        <span className="collector-status__icon">🔍</span>
        <p>Collector not configured</p>
        <Button variant="primary" onClick={onConfigureClick}>
          Set up Collector
        </Button>
      </div>
    );
  }

  return (
    <div className="collector-status">
      <div className="collector-status__header">
        <div className="collector-status__name">
          <span className="collector-status__icon">🤖</span>
          <span>{config.name || 'Scout'}</span>
        </div>
        <span className={`collector-status__mode collector-status__mode--${config.collection_mode}`}>
          {getModeLabel(config.collection_mode)}
        </span>
      </div>

      {config.intent && (
        <p className="collector-status__intent">"{config.intent}"</p>
      )}

      {config.focus_areas.length > 0 && (
        <div className="collector-status__focus">
          <span className="collector-status__focus-label">Watching for:</span>
          <div className="collector-status__focus-tags">
            {config.focus_areas.slice(0, 5).map((area, i) => (
              <span key={i} className="collector-status__focus-tag">{area}</span>
            ))}
            {config.focus_areas.length > 5 && (
              <span className="collector-status__focus-more">
                +{config.focus_areas.length - 5} more
              </span>
            )}
          </div>
        </div>
      )}

      {sourceHealth.length > 0 && (
        <div className="collector-status__sources">
          <span className="collector-status__sources-label">Sources:</span>
          <div className="collector-status__sources-list">
            {sourceHealth.slice(0, 3).map(source => (
              <span key={source.source_id} className="collector-status__source">
                {getHealthIcon(source.health)}
                <span className="collector-status__source-items">
                  {source.items_collected}
                </span>
              </span>
            ))}
            {sourceHealth.length > 3 && (
              <span className="collector-status__sources-more">
                +{sourceHealth.length - 3}
              </span>
            )}
          </div>
        </div>
      )}

      <div className="collector-status__actions">
        {pendingCount > 0 && (
          <Button variant="primary" onClick={onViewApprovals}>
            Review {pendingCount} pending
          </Button>
        )}
        <Button 
          variant="secondary" 
          onClick={handleCollectNow}
          disabled={collecting}
        >
          {collecting ? 'Collecting...' : 'Collect now'}
        </Button>
        <Button variant="secondary" onClick={onConfigureClick}>
          ⚙️
        </Button>
      </div>
    </div>
  );
};

export default CollectorStatus;
