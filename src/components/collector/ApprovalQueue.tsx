import React, { useState, useEffect } from 'react';
import { Button } from '../shared/Button';
import { collectorService } from '../../services/collector';
import './ApprovalQueue.css';

interface PendingItem {
  item_id: string;
  title: string;
  preview: string;
  source: string;
  confidence: number;
  confidence_reasons: string[];
  queued_at: string;
  expires_at: string;
  days_until_expiry: number;
  // Temporal Intelligence (Enhancement #6)
  delta_summary?: string | null;
  is_new_topic?: boolean;
  temporal_context?: string | null;
  knowledge_overlap?: number;
  related_titles?: string[];
}

interface ApprovalQueueProps {
  notebookId: string;
  onApprovalChange?: () => void;
}

const REJECTION_REASONS = [
  { id: 'wrong_topic', label: 'Wrong topic' },
  { id: 'too_old', label: 'Too old' },
  { id: 'bad_source', label: 'Bad source' },
  { id: 'already_knew', label: 'Already knew this' },
];

export const ApprovalQueue: React.FC<ApprovalQueueProps> = ({ 
  notebookId,
  onApprovalChange 
}) => {
  const [pending, setPending] = useState<PendingItem[]>([]);
  const [expiringCount, setExpiringCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [showRejectModal, setShowRejectModal] = useState<string | null>(null);
  const [rejectReason, setRejectReason] = useState('');
  const [rejectType, setRejectType] = useState<string | null>(null);

  useEffect(() => {
    fetchPending();
  }, [notebookId]);

  const fetchPending = async () => {
    try {
      const data = await collectorService.getPendingApprovals(notebookId);
      setPending(data.pending || []);
      setExpiringCount(data.expiring_soon || 0);
    } catch (err) {
      console.error('Error fetching pending approvals:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleApprove = async (itemId: string) => {
    setActionLoading(itemId);
    try {
      await collectorService.approveItem(notebookId, itemId);
      setPending(prev => prev.filter(p => p.item_id !== itemId));
      onApprovalChange?.();
    } catch (err) {
      console.error('Error approving item:', err);
    } finally {
      setActionLoading(null);
    }
  };

  const handleApproveAll = async () => {
    setActionLoading('all');
    try {
      const itemIds = pending.map(p => p.item_id);
      await collectorService.approveBatch(notebookId, itemIds);
      setPending([]);
      onApprovalChange?.();
    } catch (err) {
      console.error('Error approving all items:', err);
    } finally {
      setActionLoading(null);
    }
  };

  const handleReject = async (itemId: string) => {
    setActionLoading(itemId);
    try {
      await collectorService.rejectItem(notebookId, itemId, rejectReason || 'User rejected', rejectType as any);
      setPending(prev => prev.filter(p => p.item_id !== itemId));
      setShowRejectModal(null);
      setRejectReason('');
      setRejectType(null);
      onApprovalChange?.();
    } catch (err) {
      console.error('Error rejecting item:', err);
    } finally {
      setActionLoading(null);
    }
  };

  const getConfidenceClass = (confidence: number) => {
    if (confidence >= 0.8) return 'approval-queue__confidence--high';
    if (confidence >= 0.5) return 'approval-queue__confidence--medium';
    return 'approval-queue__confidence--low';
  };

  if (loading) {
    return (
      <div className="approval-queue approval-queue--loading">
        <div className="approval-queue__skeleton"></div>
        <div className="approval-queue__skeleton"></div>
      </div>
    );
  }

  if (pending.length === 0) {
    return (
      <div className="approval-queue approval-queue--empty">
        <span className="approval-queue__empty-icon">✓</span>
        <p>No items pending approval</p>
      </div>
    );
  }

  return (
    <div className="approval-queue">
      <div className="approval-queue__header">
        <h3 className="approval-queue__title">
          Pending Approval
          <span className="approval-queue__count">{pending.length}</span>
        </h3>
        {pending.length > 1 && (
          <Button 
            variant="secondary" 
            onClick={handleApproveAll}
            disabled={actionLoading === 'all'}
          >
            Approve All
          </Button>
        )}
      </div>

      {expiringCount > 0 && (
        <div className="approval-queue__warning">
          ⚠️ {expiringCount} item{expiringCount > 1 ? 's' : ''} expiring in 3 days
        </div>
      )}

      <div className="approval-queue__list">
        {pending.map(item => (
          <div key={item.item_id} className="approval-queue__item">
            <div className="approval-queue__item-header">
              <h4 className="approval-queue__item-title">{item.title}</h4>
              <div className={`approval-queue__confidence ${getConfidenceClass(item.confidence)}`}>
                {Math.round(item.confidence * 100)}%
              </div>
            </div>
            
            <p className="approval-queue__item-preview">{item.preview}</p>
            
            <div className="approval-queue__item-meta">
              <span className="approval-queue__item-source">📰 {item.source}</span>
              {item.days_until_expiry <= 3 && (
                <span className="approval-queue__item-expiry">
                  Expires in {item.days_until_expiry}d
                </span>
              )}
            </div>

            {item.delta_summary && (
              <div className={`approval-queue__delta ${item.is_new_topic ? 'approval-queue__delta--new' : 'approval-queue__delta--update'}`}>
                <span className="approval-queue__delta-badge">
                  {item.is_new_topic ? 'New Topic' : 'Updates Existing'}
                </span>
                <span className="approval-queue__delta-summary">{item.delta_summary}</span>
                {item.temporal_context && (
                  <span className="approval-queue__delta-temporal">{item.temporal_context}</span>
                )}
                {item.related_titles && item.related_titles.length > 0 && (
                  <span className="approval-queue__delta-related">
                    Connects to: {item.related_titles.slice(0, 2).join(', ')}
                  </span>
                )}
              </div>
            )}

            {item.confidence_reasons.length > 0 && (
              <div className="approval-queue__reasons">
                <span className="approval-queue__reasons-label">Why I found this:</span>
                {item.confidence_reasons.map((reason, i) => (
                  <span key={i} className="approval-queue__reason">{reason}</span>
                ))}
              </div>
            )}

            <div className="approval-queue__item-actions">
              <Button
                variant="primary"
                onClick={() => handleApprove(item.item_id)}
                disabled={actionLoading === item.item_id}
              >
                ✓ Approve
              </Button>
              <Button
                variant="secondary"
                onClick={() => setShowRejectModal(item.item_id)}
                disabled={actionLoading === item.item_id}
              >
                ✗ Reject
              </Button>
            </div>
          </div>
        ))}
      </div>

      {showRejectModal && (
        <div className="approval-queue__modal-overlay" onClick={() => setShowRejectModal(null)}>
          <div className="approval-queue__modal" onClick={e => e.stopPropagation()}>
            <h4>Why wasn't this relevant?</h4>
            <div className="approval-queue__modal-options">
              {REJECTION_REASONS.map(reason => (
                <button
                  key={reason.id}
                  className={`approval-queue__modal-option ${rejectType === reason.id ? 'approval-queue__modal-option--selected' : ''}`}
                  onClick={() => setRejectType(reason.id)}
                >
                  {reason.label}
                </button>
              ))}
            </div>
            <textarea
              className="approval-queue__modal-input"
              placeholder="Other reason (optional)"
              value={rejectReason}
              onChange={e => setRejectReason(e.target.value)}
            />
            <div className="approval-queue__modal-actions">
              <Button variant="secondary" onClick={() => setShowRejectModal(null)}>
                Cancel
              </Button>
              <Button 
                variant="danger" 
                onClick={() => handleReject(showRejectModal)}
                disabled={actionLoading === showRejectModal}
              >
                Reject
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default ApprovalQueue;
