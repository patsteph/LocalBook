import { useState, useEffect, useCallback, useRef } from 'react';
import {
  Loader2,
  CheckCircle,
  XCircle,
  AlertCircle,
  Clock,
  X,
  ChevronDown,
  ChevronUp
} from 'lucide-react';
import { API_BASE_URL, WS_BASE_URL } from '../services/api';

interface JobProgress {
  percent: number;
  message: string;
  current_step: number;
  total_steps: number;
  details: Record<string, unknown>;
}

interface JobStatus {
  id: string;
  job_type: string;
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  progress: JobProgress;
  error: string | null;
  params: Record<string, unknown>;
  notebook_id: string | null;
  duration_seconds: number | null;
  result?: unknown;
}

interface JobProgressProps {
  jobId: string;
  onClose?: () => void;
  onComplete?: (result: unknown) => void;
  showDetails?: boolean;
  compact?: boolean;
}

const statusConfig = {
  pending: { icon: Clock, color: 'text-yellow-500', bg: 'bg-yellow-500/10', label: 'Pending' },
  running: { icon: Loader2, color: 'text-blue-500', bg: 'bg-blue-500/10', label: 'Running' },
  completed: { icon: CheckCircle, color: 'text-green-500', bg: 'bg-green-500/10', label: 'Completed' },
  failed: { icon: XCircle, color: 'text-red-500', bg: 'bg-red-500/10', label: 'Failed' },
  cancelled: { icon: AlertCircle, color: 'text-gray-500', bg: 'bg-gray-500/10', label: 'Cancelled' }
};

const jobTypeLabels: Record<string, string> = {
  topic_rebuild: 'Rebuilding Topics',
  document_ingest: 'Ingesting Document',
  batch_ingest: 'Batch Ingest',
  rlm_query: 'Deep Research Query',
  contradiction_scan: 'Scanning for Contradictions',
  timeline_extract: 'Extracting Timeline',
  export: 'Exporting Data',
  custom: 'Processing'
};

export function JobProgress({ 
  jobId, 
  onClose, 
  onComplete,
  showDetails = true,
  compact = false 
}: JobProgressProps) {
  const [job, setJob] = useState<JobStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const completedRef = useRef(false);

  const fetchStatus = useCallback(async () => {
    try {
      const response = await fetch(`${API_BASE_URL}/jobs/${jobId}`);
      if (!response.ok) {
        throw new Error('Job not found');
      }
      const data = await response.json();
      setJob(data);
      
      if (data.status === 'completed' && !completedRef.current) {
        completedRef.current = true;
        onComplete?.(data.result);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch job status');
    }
  }, [jobId, onComplete]);

  const cancelJob = async () => {
    try {
      await fetch(`${API_BASE_URL}/jobs/${jobId}/cancel`, { method: 'POST' });
      fetchStatus();
    } catch (err) {
      console.error('Failed to cancel job:', err);
    }
  };

  useEffect(() => {
    fetchStatus();

    const ws = new WebSocket(`${WS_BASE_URL}/jobs/ws/${jobId}`);
    wsRef.current = ws;

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type === 'connected') {
          setJob(data.job);
        } else if (data.id) {
          setJob(data);
          if (data.status === 'completed' && !completedRef.current) {
            completedRef.current = true;
            onComplete?.(data.result);
          }
        }
      } catch (err) {
        console.error('WS parse error:', err);
      }
    };

    ws.onerror = () => {
      const interval = setInterval(fetchStatus, 2000);
      return () => clearInterval(interval);
    };

    ws.onclose = () => {
      fetchStatus();
    };

    return () => {
      ws.close();
    };
  }, [jobId, fetchStatus, onComplete]);

  if (error) {
    return (
      <div className="flex items-center gap-2 p-3 bg-red-500/10 rounded-lg text-red-400">
        <XCircle className="w-4 h-4" />
        <span className="text-sm">{error}</span>
        {onClose && (
          <button onClick={onClose} className="ml-auto p-1 hover:bg-white/10 rounded">
            <X className="w-4 h-4" />
          </button>
        )}
      </div>
    );
  }

  if (!job) {
    return (
      <div className="flex items-center gap-2 p-3 bg-white/5 rounded-lg">
        <Loader2 className="w-4 h-4 animate-spin text-blue-400" />
        <span className="text-sm text-gray-400">Loading job status...</span>
      </div>
    );
  }

  const config = statusConfig[job.status];
  const StatusIcon = config.icon;
  const isActive = job.status === 'pending' || job.status === 'running';
  const jobLabel = jobTypeLabels[job.job_type] || job.job_type;

  if (compact) {
    return (
      <div className={`flex items-center gap-2 p-2 ${config.bg} rounded-lg`}>
        <StatusIcon className={`w-4 h-4 ${config.color} ${job.status === 'running' ? 'animate-spin' : ''}`} />
        <span className="text-sm text-gray-300">{job.progress.message || config.label}</span>
        {job.progress.percent > 0 && job.progress.percent < 100 && (
          <span className="text-xs text-gray-500">{job.progress.percent}%</span>
        )}
        {isActive && (
          <button 
            onClick={cancelJob}
            className="ml-auto p-1 hover:bg-white/10 rounded text-gray-400 hover:text-white"
            title="Cancel"
          >
            <X className="w-3 h-3" />
          </button>
        )}
      </div>
    );
  }

  return (
    <div className={`${config.bg} rounded-lg overflow-hidden`}>
      <div className="p-3">
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <StatusIcon className={`w-5 h-5 ${config.color} ${job.status === 'running' ? 'animate-spin' : ''}`} />
            <span className="font-medium text-gray-200">{jobLabel}</span>
          </div>
          <div className="flex items-center gap-1">
            {isActive && (
              <button 
                onClick={cancelJob}
                className="p-1 hover:bg-white/10 rounded text-gray-400 hover:text-white"
                title="Cancel job"
              >
                <X className="w-4 h-4" />
              </button>
            )}
            {onClose && (
              <button 
                onClick={onClose}
                className="p-1 hover:bg-white/10 rounded text-gray-400 hover:text-white"
                title="Dismiss"
              >
                <X className="w-4 h-4" />
              </button>
            )}
          </div>
        </div>

        <p className="text-sm text-gray-400 mb-2">
          {job.progress.message || config.label}
        </p>

        {isActive && (
          <div className="relative h-2 bg-white/10 rounded-full overflow-hidden">
            <div 
              className="absolute inset-y-0 left-0 bg-blue-500 transition-all duration-300"
              style={{ width: `${job.progress.percent}%` }}
            />
          </div>
        )}

        {job.status === 'failed' && job.error && (
          <p className="text-sm text-red-400 mt-2">{job.error}</p>
        )}

        {job.duration_seconds !== null && !isActive && (
          <p className="text-xs text-gray-500 mt-2">
            Completed in {job.duration_seconds.toFixed(1)}s
          </p>
        )}
      </div>

      {showDetails && (
        <>
          <button
            onClick={() => setExpanded(!expanded)}
            className="w-full flex items-center justify-center gap-1 py-1 text-xs text-gray-500 hover:text-gray-300 hover:bg-white/5"
          >
            {expanded ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
            {expanded ? 'Hide details' : 'Show details'}
          </button>

          {expanded && (
            <div className="px-3 pb-3 text-xs text-gray-500 space-y-1 border-t border-white/5 pt-2">
              <div className="flex justify-between">
                <span>Job ID:</span>
                <span className="font-mono">{job.id}</span>
              </div>
              <div className="flex justify-between">
                <span>Status:</span>
                <span className={config.color}>{config.label}</span>
              </div>
              {job.progress.current_step > 0 && (
                <div className="flex justify-between">
                  <span>Step:</span>
                  <span>{job.progress.current_step} / {job.progress.total_steps}</span>
                </div>
              )}
              {Object.entries(job.progress.details || {}).map(([key, value]) => (
                <div key={key} className="flex justify-between">
                  <span>{key.replace(/_/g, ' ')}:</span>
                  <span>{String(value)}</span>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

interface JobListProps {
  notebookId?: string;
  limit?: number;
}

export function JobList({ notebookId, limit = 10 }: JobListProps) {
  const [jobs, setJobs] = useState<JobStatus[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchJobs = useCallback(async () => {
    try {
      const params = new URLSearchParams();
      if (notebookId) params.set('notebook_id', notebookId);
      params.set('limit', String(limit));
      
      const response = await fetch(`${API_BASE_URL}/jobs?${params}`);
      const data = await response.json();
      setJobs(data.jobs || []);
    } catch (err) {
      console.error('Failed to fetch jobs:', err);
    } finally {
      setLoading(false);
    }
  }, [notebookId, limit]);

  useEffect(() => {
    fetchJobs();
    const interval = setInterval(fetchJobs, 5000);
    return () => clearInterval(interval);
  }, [fetchJobs]);

  if (loading) {
    return (
      <div className="flex items-center justify-center p-4">
        <Loader2 className="w-5 h-5 animate-spin text-gray-400" />
      </div>
    );
  }

  if (jobs.length === 0) {
    return (
      <div className="text-center p-4 text-gray-500 text-sm">
        No recent jobs
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {jobs.map((job) => (
        <JobProgress key={job.id} jobId={job.id} compact showDetails={false} />
      ))}
    </div>
  );
}

export function useJobTracker() {
  const [activeJobs, setActiveJobs] = useState<Map<string, JobStatus>>(new Map());

  const trackJob = useCallback((jobId: string) => {
    setActiveJobs(prev => {
      const next = new Map(prev);
      next.set(jobId, { id: jobId, status: 'pending' } as JobStatus);
      return next;
    });
  }, []);

  const updateJob = useCallback((job: JobStatus) => {
    setActiveJobs(prev => {
      const next = new Map(prev);
      if (job.status === 'completed' || job.status === 'failed' || job.status === 'cancelled') {
        setTimeout(() => {
          setActiveJobs(p => {
            const n = new Map(p);
            n.delete(job.id);
            return n;
          });
        }, 5000);
      }
      next.set(job.id, job);
      return next;
    });
  }, []);

  const dismissJob = useCallback((jobId: string) => {
    setActiveJobs(prev => {
      const next = new Map(prev);
      next.delete(jobId);
      return next;
    });
  }, []);

  return {
    activeJobs: Array.from(activeJobs.values()),
    trackJob,
    updateJob,
    dismissJob
  };
}

export default JobProgress;
