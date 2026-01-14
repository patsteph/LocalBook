import { useState, useEffect, useCallback } from 'react';
import {
  Activity,
  AlertTriangle,
  CheckCircle,
  XCircle,
  RefreshCw,
  Wrench,
  Download,
  Server,
  Database,
  Cpu,
  HardDrive,
  Zap,
  Box,
  Loader2,
  ChevronDown,
  ChevronRight,
  Copy,
  X
} from 'lucide-react';
import { API_BASE_URL } from '../services/api';

const API_BASE = API_BASE_URL;

interface HealthCheck {
  name: string;
  display: string;
  status: 'pass' | 'fail' | 'warn';
  details?: Record<string, unknown>;
  error?: string;
  repair?: string;
}

interface Issue {
  severity: 'critical' | 'high' | 'medium' | 'low';
  title: string;
  message: string;
  repair?: string;
  repair_params?: Record<string, unknown>;
}

interface HealthData {
  timestamp: string;
  overall: 'healthy' | 'degraded' | 'critical';
  checks: HealthCheck[];
  issues: Issue[];
  system: {
    os: string;
    os_version: string;
    arch: string;
    memory_total_gb: number;
    memory_available_gb: number;
    memory_percent_used: number;
    disk_total_gb: number;
    disk_free_gb: number;
    disk_percent_used: number;
    data_dir: string;
  };
  metrics: {
    queries_24h: number;
    avg_latency_ms: number;
    cache_hit_rate: number;
    error_rate: number;
  };
  cache?: {
    embedding: { hits: number; misses: number; size: number };
    answer: { hits: number; misses: number; size: number };
  };
}

interface LogEntry {
  timestamp: string;
  level: string;
  message: string;
  source: string;
}

const statusColors = {
  healthy: 'bg-green-500',
  degraded: 'bg-yellow-500',
  critical: 'bg-red-500',
};

const statusIcons = {
  pass: <CheckCircle className="w-5 h-5 text-green-500" />,
  fail: <XCircle className="w-5 h-5 text-red-500" />,
  warn: <AlertTriangle className="w-5 h-5 text-yellow-500" />,
};

const severityColors = {
  critical: 'border-red-500 bg-red-500/10',
  high: 'border-orange-500 bg-orange-500/10',
  medium: 'border-yellow-500 bg-yellow-500/10',
  low: 'border-blue-500 bg-blue-500/10',
};

interface SmokeScreenPortalProps {
  onClose: () => void;
}

export default function SmokeScreenPortal({ onClose }: SmokeScreenPortalProps) {
  const [healthData, setHealthData] = useState<HealthData | null>(null);
  const [loading, setLoading] = useState(true);
  const [repairing, setRepairing] = useState<string | null>(null);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [showLogs, setShowLogs] = useState(false);
  const [logFilter, setLogFilter] = useState('all');
  const [expandedChecks, setExpandedChecks] = useState<Set<string>>(new Set());

  const fetchHealth = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await fetch(`${API_BASE}/health/full`);
      const data = await resp.json();
      setHealthData(data);
    } catch (err) {
      console.error('Health check failed:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchLogs = useCallback(async () => {
    try {
      const resp = await fetch(`${API_BASE}/health/logs?limit=100`);
      const data = await resp.json();
      setLogs(data.logs || []);
    } catch (err) {
      console.error('Log fetch failed:', err);
    }
  }, []);

  useEffect(() => {
    fetchHealth();
    fetchLogs();
    const interval = setInterval(fetchLogs, 5000);
    return () => clearInterval(interval);
  }, [fetchHealth, fetchLogs]);

  const executeRepair = async (action: string, params?: Record<string, unknown>) => {
    setRepairing(action);
    try {
      const resp = await fetch(`${API_BASE}/health/repair`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action, params }),
      });
      const result = await resp.json();
      if (result.status === 'success') {
        setTimeout(fetchHealth, 1000);
      }
      fetchLogs();
    } catch (err) {
      console.error('Repair failed:', err);
    } finally {
      setRepairing(null);
    }
  };

  const exportDiagnostics = async () => {
    try {
      const resp = await fetch(`${API_BASE}/health/export`);
      const data = await resp.json();
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `localbook-diagnostics-${new Date().toISOString().split('T')[0]}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      console.error('Export failed:', err);
    }
  };

  const toggleCheck = (name: string) => {
    const newExpanded = new Set(expandedChecks);
    if (newExpanded.has(name)) {
      newExpanded.delete(name);
    } else {
      newExpanded.add(name);
    }
    setExpandedChecks(newExpanded);
  };

  const filteredLogs = logFilter === 'all' 
    ? logs 
    : logs.filter(l => l.level.toUpperCase() === logFilter.toUpperCase());

  const getCheckIcon = (name: string) => {
    switch (name) {
      case 'ollama_connection':
      case 'ollama_version':
        return <Zap className="w-5 h-5" />;
      case 'models':
        return <Box className="w-5 h-5" />;
      case 'backend':
        return <Server className="w-5 h-5" />;
      case 'database':
        return <Database className="w-5 h-5" />;
      case 'embeddings':
        return <Cpu className="w-5 h-5" />;
      default:
        return <Activity className="w-5 h-5" />;
    }
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white dark:bg-gray-900 rounded-xl shadow-2xl w-full max-w-4xl max-h-[90vh] overflow-hidden flex flex-col">
        {/* Header */}
        <div className="px-6 py-4 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Activity className="w-6 h-6 text-blue-500" />
            <h1 className="text-xl font-semibold text-gray-900 dark:text-white">
              LocalBook Smoke Screen Portal
            </h1>
          </div>
          <button
            onClick={onClose}
            className="p-2 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg transition-colors"
          >
            <X className="w-5 h-5 text-gray-500" />
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-6 space-y-6">
          {/* Status Banner */}
          {healthData && (
            <div className={`rounded-lg p-4 flex items-center justify-between ${
              healthData.overall === 'healthy' ? 'bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800' :
              healthData.overall === 'degraded' ? 'bg-yellow-50 dark:bg-yellow-900/20 border border-yellow-200 dark:border-yellow-800' :
              'bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800'
            }`}>
              <div className="flex items-center gap-3">
                <div className={`w-3 h-3 rounded-full ${statusColors[healthData.overall]} animate-pulse`} />
                <span className="font-medium text-gray-900 dark:text-white">
                  {healthData.overall === 'healthy' ? 'All Systems Operational' :
                   healthData.overall === 'degraded' ? `${healthData.issues.length} Issue${healthData.issues.length !== 1 ? 's' : ''} Detected` :
                   'Critical Issues Detected'}
                </span>
              </div>
              <div className="flex items-center gap-2">
                <button
                  onClick={fetchHealth}
                  disabled={loading}
                  className="flex items-center gap-2 px-3 py-1.5 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors text-sm"
                >
                  <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
                  Refresh
                </button>
                <button
                  onClick={exportDiagnostics}
                  className="flex items-center gap-2 px-3 py-1.5 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors text-sm"
                >
                  <Download className="w-4 h-4" />
                  Export
                </button>
              </div>
            </div>
          )}

          {/* Status Cards */}
          {healthData && (
            <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
              {healthData.checks.map((check) => (
                <div
                  key={check.name}
                  className="bg-gray-50 dark:bg-gray-800 rounded-lg p-4 cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
                  onClick={() => toggleCheck(check.name)}
                >
                  <div className="flex items-center justify-between mb-2">
                    <div className="flex items-center gap-2 text-gray-600 dark:text-gray-400">
                      {getCheckIcon(check.name)}
                      <span className="font-medium">{check.display}</span>
                    </div>
                    {statusIcons[check.status]}
                  </div>
                  
                  {expandedChecks.has(check.name) && check.details && (
                    <div className="mt-2 pt-2 border-t border-gray-200 dark:border-gray-600 text-xs text-gray-500 dark:text-gray-400 space-y-1">
                      {Object.entries(check.details).map(([key, value]) => (
                        <div key={key} className="flex justify-between">
                          <span>{key}:</span>
                          <span className="font-mono">{String(value)}</span>
                        </div>
                      ))}
                    </div>
                  )}
                  
                  {check.error && (
                    <div className="mt-2 text-xs text-red-500">
                      {check.error}
                    </div>
                  )}
                </div>
              ))}

              {/* System Stats Card */}
              <div className="bg-gray-50 dark:bg-gray-800 rounded-lg p-4">
                <div className="flex items-center gap-2 text-gray-600 dark:text-gray-400 mb-2">
                  <HardDrive className="w-5 h-5" />
                  <span className="font-medium">Storage</span>
                </div>
                <div className="text-2xl font-bold text-gray-900 dark:text-white">
                  {healthData.system.disk_free_gb?.toFixed(1)} GB
                </div>
                <div className="text-xs text-gray-500">
                  {(100 - healthData.system.disk_percent_used).toFixed(0)}% free
                </div>
                <div className="mt-2 h-2 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
                  <div 
                    className={`h-full ${
                      healthData.system.disk_percent_used > 90 ? 'bg-red-500' :
                      healthData.system.disk_percent_used > 80 ? 'bg-yellow-500' : 'bg-green-500'
                    }`}
                    style={{ width: `${healthData.system.disk_percent_used}%` }}
                  />
                </div>
              </div>

              {/* Memory Card */}
              <div className="bg-gray-50 dark:bg-gray-800 rounded-lg p-4">
                <div className="flex items-center gap-2 text-gray-600 dark:text-gray-400 mb-2">
                  <Cpu className="w-5 h-5" />
                  <span className="font-medium">Memory</span>
                </div>
                <div className="text-2xl font-bold text-gray-900 dark:text-white">
                  {healthData.system.memory_available_gb?.toFixed(1)} GB
                </div>
                <div className="text-xs text-gray-500">
                  of {healthData.system.memory_total_gb?.toFixed(0)} GB available
                </div>
                <div className="mt-2 h-2 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
                  <div 
                    className={`h-full ${
                      healthData.system.memory_percent_used > 90 ? 'bg-red-500' :
                      healthData.system.memory_percent_used > 80 ? 'bg-yellow-500' : 'bg-green-500'
                    }`}
                    style={{ width: `${healthData.system.memory_percent_used}%` }}
                  />
                </div>
              </div>
            </div>
          )}

          {/* Issues List */}
          {healthData && healthData.issues.length > 0 && (
            <div className="space-y-3">
              <h2 className="text-lg font-semibold text-gray-900 dark:text-white flex items-center gap-2">
                <AlertTriangle className="w-5 h-5 text-yellow-500" />
                Issues ({healthData.issues.length})
              </h2>
              
              {healthData.issues.map((issue, idx) => (
                <div
                  key={idx}
                  className={`border-l-4 rounded-lg p-4 ${severityColors[issue.severity]}`}
                >
                  <div className="flex items-start justify-between">
                    <div>
                      <div className="font-medium text-gray-900 dark:text-white">
                        {issue.title}
                      </div>
                      <div className="text-sm text-gray-600 dark:text-gray-400 mt-1">
                        {issue.message}
                      </div>
                    </div>
                    {issue.repair && (
                      <button
                        onClick={() => executeRepair(issue.repair!, issue.repair_params)}
                        disabled={repairing === issue.repair}
                        className="flex items-center gap-2 px-3 py-1.5 bg-blue-500 text-white rounded-lg hover:bg-blue-600 transition-colors text-sm shrink-0"
                      >
                        {repairing === issue.repair ? (
                          <Loader2 className="w-4 h-4 animate-spin" />
                        ) : (
                          <Wrench className="w-4 h-4" />
                        )}
                        Repair
                      </button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* Metrics */}
          {healthData?.metrics && healthData.metrics.queries_24h > 0 && (
            <div className="bg-gray-50 dark:bg-gray-800 rounded-lg p-4">
              <h2 className="text-lg font-semibold text-gray-900 dark:text-white mb-3">
                24h Performance
              </h2>
              <div className="grid grid-cols-4 gap-4 text-center">
                <div>
                  <div className="text-2xl font-bold text-gray-900 dark:text-white">
                    {healthData.metrics.queries_24h}
                  </div>
                  <div className="text-xs text-gray-500">Queries</div>
                </div>
                <div>
                  <div className="text-2xl font-bold text-gray-900 dark:text-white">
                    {(healthData.metrics.avg_latency_ms / 1000).toFixed(1)}s
                  </div>
                  <div className="text-xs text-gray-500">Avg Latency</div>
                </div>
                <div>
                  <div className="text-2xl font-bold text-gray-900 dark:text-white">
                    {(healthData.metrics.cache_hit_rate * 100).toFixed(0)}%
                  </div>
                  <div className="text-xs text-gray-500">Cache Hits</div>
                </div>
                <div>
                  <div className="text-2xl font-bold text-gray-900 dark:text-white">
                    {(healthData.metrics.error_rate * 100).toFixed(1)}%
                  </div>
                  <div className="text-xs text-gray-500">Error Rate</div>
                </div>
              </div>
            </div>
          )}

          {/* Logs Section */}
          <div className="border border-gray-200 dark:border-gray-700 rounded-lg overflow-hidden">
            <button
              onClick={() => setShowLogs(!showLogs)}
              className="w-full px-4 py-3 bg-gray-50 dark:bg-gray-800 flex items-center justify-between hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
            >
              <span className="font-medium text-gray-900 dark:text-white">System Logs</span>
              {showLogs ? <ChevronDown className="w-5 h-5" /> : <ChevronRight className="w-5 h-5" />}
            </button>
            
            {showLogs && (
              <div className="p-4 space-y-3">
                <div className="flex items-center gap-2">
                  <select
                    value={logFilter}
                    onChange={(e) => setLogFilter(e.target.value)}
                    className="px-3 py-1.5 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg text-sm"
                  >
                    <option value="all">All Levels</option>
                    <option value="ERROR">Errors</option>
                    <option value="WARN">Warnings</option>
                    <option value="INFO">Info</option>
                  </select>
                  <button
                    onClick={() => {
                      const text = filteredLogs.map(l => `${l.timestamp} [${l.level}] ${l.message}`).join('\n');
                      navigator.clipboard.writeText(text);
                    }}
                    className="flex items-center gap-1 px-3 py-1.5 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg text-sm hover:bg-gray-50 dark:hover:bg-gray-700"
                  >
                    <Copy className="w-4 h-4" />
                    Copy
                  </button>
                </div>
                
                <div className="bg-gray-900 rounded-lg p-3 h-48 overflow-y-auto font-mono text-xs">
                  {filteredLogs.length === 0 ? (
                    <div className="text-gray-500">No logs yet</div>
                  ) : (
                    filteredLogs.map((log, idx) => (
                      <div key={idx} className={`${
                        log.level === 'ERROR' ? 'text-red-400' :
                        log.level === 'WARN' ? 'text-yellow-400' : 'text-gray-300'
                      }`}>
                        <span className="text-gray-500">{log.timestamp.split('T')[1]?.split('.')[0]}</span>
                        {' '}
                        <span className={`${
                          log.level === 'ERROR' ? 'text-red-500' :
                          log.level === 'WARN' ? 'text-yellow-500' : 'text-blue-500'
                        }`}>[{log.level}]</span>
                        {' '}{log.message}
                      </div>
                    ))
                  )}
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Footer */}
        <div className="px-6 py-3 border-t border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 text-xs text-gray-500 flex items-center justify-between">
          <div>
            {healthData?.system?.os} {healthData?.system?.os_version} • {healthData?.system?.arch}
          </div>
          <div>
            Last checked: {healthData?.timestamp ? new Date(healthData.timestamp).toLocaleTimeString() : 'Never'}
          </div>
        </div>
      </div>
    </div>
  );
}
