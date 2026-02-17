import React, { useState, useEffect } from 'react';
import { timelineService, TimelineEvent } from '../services/timeline';
import { Button } from './shared/Button';
import { LoadingSpinner } from './shared/LoadingSpinner';
import { ErrorMessage } from './shared/ErrorMessage';

interface TimelineProps {
  notebookId: string | null;
  sourcesRefreshTrigger?: number;  // Increments when sources are added/removed
}

export const Timeline: React.FC<TimelineProps> = ({ notebookId, sourcesRefreshTrigger = 0 }) => {
  const [events, setEvents] = useState<TimelineEvent[]>([]);
  const [filteredEvents, setFilteredEvents] = useState<TimelineEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [extracting, setExtracting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedEvent, setSelectedEvent] = useState<TimelineEvent | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [sourceFilter, setSourceFilter] = useState<string>('all');
  const [extractionProgress, setExtractionProgress] = useState({ current: 0, total: 0, message: '' });
  const [lastExtractedAt, setLastExtractedAt] = useState<number>(0);  // Track when timeline was last extracted
  const [sourcesChangedSinceExtract, setSourcesChangedSinceExtract] = useState(false);

  useEffect(() => {
    if (notebookId) {
      loadTimeline();
    }
  }, [notebookId]);

  // Detect when sources change after timeline was extracted
  useEffect(() => {
    if (lastExtractedAt > 0 && sourcesRefreshTrigger > 0) {
      setSourcesChangedSinceExtract(true);
    }
  }, [sourcesRefreshTrigger]);

  useEffect(() => {
    filterEvents();
  }, [events, searchQuery, sourceFilter]);

  const loadTimeline = async () => {
    if (!notebookId) return;

    setLoading(true);
    setError(null);

    try {
      const data = await timelineService.getTimeline(notebookId);
      setEvents(data);
      // Don't set error for empty timeline - UI handles this gracefully
    } catch (err: any) {
      console.error('Failed to load timeline:', err);
      // Only show error for actual failures, not empty results
      if (err.response?.status !== 404) {
        setError(err.response?.data?.detail || err.message || 'Failed to load timeline');
      }
    } finally {
      setLoading(false);
    }
  };

  const handleExtract = async () => {
    if (!notebookId) return;

    setExtracting(true);
    setError(null);

    try {
      await timelineService.extractTimeline(notebookId);

      // Poll for progress
      const pollInterval = setInterval(async () => {
        try {
          const progress = await timelineService.getExtractionProgress(notebookId);
          setExtractionProgress(progress);

          if (progress.status === 'complete') {
            clearInterval(pollInterval);
            setExtracting(false);
            setLastExtractedAt(Date.now());
            setSourcesChangedSinceExtract(false);
            loadTimeline();
          } else if (progress.status === 'error') {
            clearInterval(pollInterval);
            setExtracting(false);
            setError('Timeline extraction failed. Please try again.');
          }
        } catch (err) {
          console.error('Failed to get progress:', err);
        }
      }, 1000);

      // Timeout after 10 minutes
      setTimeout(() => {
        clearInterval(pollInterval);
        if (extracting) {
          setExtracting(false);
          setError('Extraction timeout. Please try again.');
        }
      }, 600000);

    } catch (err: any) {
      console.error('Failed to start extraction:', err);
      setError(err.response?.data?.detail || err.message || 'Failed to start extraction');
      setExtracting(false);
    }
  };

  const filterEvents = () => {
    let filtered = [...events];

    // Filter by search query
    if (searchQuery) {
      const query = searchQuery.toLowerCase();
      filtered = filtered.filter(event =>
        event.event_text.toLowerCase().includes(query) ||
        event.date_string.toLowerCase().includes(query) ||
        event.context.toLowerCase().includes(query)
      );
    }

    // Filter by source
    if (sourceFilter !== 'all') {
      filtered = filtered.filter(event => event.source_id === sourceFilter);
    }

    setFilteredEvents(filtered);
  };

  const formatDate = (timestamp: number): string => {
    const date = new Date(timestamp * 1000);
    return date.toLocaleDateString('en-US', {
      year: 'numeric',
      month: 'short',
      day: 'numeric'
    });
  };

  const getUniqueS = () => {
    const sources = new Map<string, string>();
    events.forEach(event => {
      if (event.filename) {
        sources.set(event.source_id, event.filename);
      }
    });
    return Array.from(sources.entries());
  };

  const getTimelineScale = () => {
    if (filteredEvents.length === 0) return { min: 0, max: 0, range: 0 };

    const timestamps = filteredEvents.map(e => e.date_timestamp);
    let min = Math.min(...timestamps);
    let max = Math.max(...timestamps);

    // If all events are at the same timestamp (or very close), pad by ±3 months
    if (max - min < 86400 * 30) {
      const mid = (min + max) / 2;
      min = mid - 86400 * 90;  // 3 months before
      max = mid + 86400 * 90;  // 3 months after
    }

    const range = max - min;
    return { min, max, range };
  };


  if (!notebookId) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-center text-gray-500 dark:text-gray-400">
          <svg className="w-16 h-16 mx-auto mb-4 text-gray-400 dark:text-gray-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
          </svg>
          <p className="text-base font-medium">No Notebook Selected</p>
          <p className="text-sm mt-2">Select a notebook to view its timeline</p>
        </div>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <LoadingSpinner />
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full bg-white dark:bg-gray-800">
      {/* Header */}
      <div className="p-3 border-b dark:border-gray-700">
        <div className="flex items-center justify-between mb-3">
          <div>
            <h2 className="text-base font-semibold text-gray-900 dark:text-white">Timeline</h2>
            <p className="text-sm text-gray-600 dark:text-gray-400">
              {filteredEvents.length} {filteredEvents.length === 1 ? 'event' : 'events'}
              {events.length !== filteredEvents.length && ` (filtered from ${events.length})`}
            </p>
          </div>
          <Button
            onClick={handleExtract}
            disabled={extracting}
            size="sm"
            variant={sourcesChangedSinceExtract ? 'primary' : 'secondary'}
          >
            {extracting ? 'Extracting...' : 
              sourcesChangedSinceExtract ? '⚠️ Update Timeline (New Sources)' :
              events.length > 0 ? '🔄 Refresh Timeline' : '📅 Extract Timeline'}
          </Button>
        </div>

        {/* Sources changed notification */}
        {sourcesChangedSinceExtract && !extracting && (
          <div className="mb-4 p-3 bg-yellow-50 dark:bg-yellow-900/20 border border-yellow-200 dark:border-yellow-800 rounded-lg">
            <p className="text-sm text-yellow-800 dark:text-yellow-200">
              📢 New sources have been added since the timeline was last extracted. Click "Update Timeline" to include them.
            </p>
          </div>
        )}

        {error && <ErrorMessage message={error} onDismiss={() => setError(null)} />}

        {/* Extraction Progress */}
        {extracting && (
          <div className="mb-4 p-3 bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-lg">
            <div className="space-y-2">
              <div className="flex items-center justify-between text-sm">
                <span className="font-medium text-blue-900 dark:text-blue-100">
                  {extractionProgress.message || 'Extracting timeline events...'}
                </span>
                {extractionProgress.total > 0 && (
                  <span className="text-blue-700 dark:text-blue-300">
                    {extractionProgress.current} / {extractionProgress.total}
                  </span>
                )}
              </div>
              {extractionProgress.total > 0 && (
                <div className="w-full bg-blue-200 dark:bg-blue-900 rounded-full h-2">
                  <div
                    className="bg-blue-600 h-2 rounded-full transition-all duration-300"
                    style={{ width: `${(extractionProgress.current / extractionProgress.total) * 100}%` }}
                  />
                </div>
              )}
            </div>
          </div>
        )}

        {/* Filters */}
        {events.length > 0 && (
          <div className="flex gap-3">
            <input
              type="text"
              placeholder="Search events..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="flex-1 px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-gray-700 text-gray-900 dark:text-white placeholder-gray-400 dark:placeholder-gray-500"
            />
            <select
              value={sourceFilter}
              onChange={(e) => setSourceFilter(e.target.value)}
              className="px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
            >
              <option value="all">All Sources</option>
              {getUniqueS().map(([id, name]) => (
                <option key={id} value={id}>{name}</option>
              ))}
            </select>
          </div>
        )}
      </div>

      {/* Timeline Visualization */}
      {filteredEvents.length > 0 ? (
        <div className="flex-1 overflow-y-auto p-4">
          {/* Horizontal Timeline - Scrollable */}
          <div className="mb-8 p-4 bg-gray-50 dark:bg-gray-900 rounded-lg">
            {/* Timeline info */}
            {(() => {
              const scale = getTimelineScale();
              const startYear = new Date(scale.min * 1000).getFullYear();
              const endYear = new Date(scale.max * 1000).getFullYear();
              const yearSpan = endYear - startYear;
              return (
                <div className="flex items-center justify-between mb-3 text-sm text-gray-600 dark:text-gray-400">
                  <span>{startYear} — {endYear} ({yearSpan === 0 ? '<1 year' : `${yearSpan} year${yearSpan !== 1 ? 's' : ''}`})</span>
                  <span className="text-xs">← Scroll to navigate →</span>
                </div>
              );
            })()}
            
            {/* Scrollable timeline container */}
            <div className="overflow-x-auto pb-4">
              {(() => {
                const scale = getTimelineScale();
                const startDate = new Date(scale.min * 1000);
                const endDate = new Date(scale.max * 1000);
                const startYear = startDate.getFullYear();
                const endYear = endDate.getFullYear();
                const yearSpan = endYear - startYear + 1;
                const monthSpan = (endYear - startYear) * 12 + (endDate.getMonth() - startDate.getMonth()) + 1;

                // Ensure comfortable spacing: at least 800px, or 120px per year, or 60px per month
                const timelineWidth = Math.max(
                  800,
                  yearSpan * 120,
                  monthSpan * 60
                );

                // Pre-compute positions and stagger overlapping dots into rows
                const DOT_PROXIMITY_PX = 18; // dots closer than this get staggered
                const positions = filteredEvents.map((event) => {
                  const pct = scale.range > 0
                    ? ((event.date_timestamp - scale.min) / scale.range) * 100
                    : 50;
                  return Math.max(1, Math.min(99, pct));
                });

                // Assign rows: if a dot is too close to one already placed in the same row, bump it down
                const rows: number[] = [];
                const rowRightEdges: number[][] = []; // for each row, list of occupied x-pixels
                positions.forEach((pct) => {
                  const xPx = (pct / 100) * timelineWidth;
                  let assignedRow = 0;
                  for (let r = 0; r < rowRightEdges.length; r++) {
                    const conflict = rowRightEdges[r].some(ox => Math.abs(ox - xPx) < DOT_PROXIMITY_PX);
                    if (!conflict) { assignedRow = r; break; }
                    assignedRow = r + 1;
                  }
                  if (!rowRightEdges[assignedRow]) rowRightEdges[assignedRow] = [];
                  rowRightEdges[assignedRow].push(xPx);
                  rows.push(assignedRow);
                });

                const maxRow = Math.max(0, ...rows);
                const timelineHeight = 50 + maxRow * 22 + 30; // axis + staggered rows + padding
                
                return (
                  <div className="relative" style={{ width: `${timelineWidth}px`, minHeight: `${timelineHeight}px` }}>
                    {/* Timeline axis */}
                    <div className="absolute left-0 right-0 top-10 h-1 bg-gray-300 dark:bg-gray-600"></div>

                    {/* Time markers — months when span ≤ 2 years, otherwise years */}
                    {(() => {
                      if (scale.range === 0) return null;
                      const markers: React.ReactNode[] = [];
                      const monthNames = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

                      if (yearSpan <= 2) {
                        // Month markers
                        const cursor = new Date(startYear, startDate.getMonth(), 1);
                        while (cursor <= endDate) {
                          const ts = cursor.getTime() / 1000;
                          const pos = ((ts - scale.min) / scale.range) * 100;
                          if (pos >= 0 && pos <= 100) {
                            const label = cursor.getFullYear() === startYear && cursor.getMonth() === startDate.getMonth()
                              ? `${monthNames[cursor.getMonth()]} ${cursor.getFullYear()}`
                              : cursor.getMonth() === 0
                                ? `${monthNames[0]} ${cursor.getFullYear()}`
                                : monthNames[cursor.getMonth()];
                            markers.push(
                              <div
                                key={`m-${cursor.getFullYear()}-${cursor.getMonth()}`}
                                className="absolute flex flex-col items-center"
                                style={{ left: `${pos}%`, transform: 'translateX(-50%)' }}
                              >
                                <span className="text-xs text-gray-600 dark:text-gray-400 font-medium whitespace-nowrap">
                                  {label}
                                </span>
                                <div className="w-px h-2 bg-gray-400 dark:bg-gray-500 mt-1"></div>
                              </div>
                            );
                          }
                          cursor.setMonth(cursor.getMonth() + 1);
                        }
                      } else {
                        // Year markers
                        const yearStep = yearSpan > 20 ? 5 : yearSpan > 10 ? 2 : 1;
                        for (let year = startYear; year <= endYear; year += yearStep) {
                          const yearTimestamp = new Date(year, 0, 1).getTime() / 1000;
                          const pos = ((yearTimestamp - scale.min) / scale.range) * 100;
                          markers.push(
                            <div
                              key={year}
                              className="absolute flex flex-col items-center"
                              style={{ left: `${pos}%`, transform: 'translateX(-50%)' }}
                            >
                              <span className="text-xs text-gray-600 dark:text-gray-400 font-medium whitespace-nowrap">
                                {year}
                              </span>
                              <div className="w-px h-2 bg-gray-400 dark:bg-gray-500 mt-1"></div>
                            </div>
                          );
                        }
                      }
                      return markers;
                    })()}

                    {/* Event markers — staggered vertically to avoid overlap */}
                    {filteredEvents.map((event, i) => {
                      const pct = positions[i];
                      const row = rows[i];
                      const topPx = 36 + row * 22; // each row offsets 22px down

                      return (
                        <div
                          key={event.event_id}
                          className="absolute"
                          style={{
                            left: `${pct}%`,
                            top: `${topPx}px`,
                            transform: 'translateX(-50%)'
                          }}
                        >
                          <button
                            onClick={() => setSelectedEvent(event)}
                            className={`w-4 h-4 rounded-full transition-all hover:scale-125 shadow-md ${
                              selectedEvent?.event_id === event.event_id
                                ? 'bg-blue-600 ring-4 ring-blue-200 dark:ring-blue-800 scale-125'
                                : 'bg-blue-500 hover:bg-blue-600'
                            }`}
                            title={`${formatDate(event.date_timestamp)}: ${event.event_text.substring(0, 50)}...`}
                          />
                          {/* Connector line to axis for staggered dots */}
                          {row > 0 && (
                            <div
                              className="absolute left-1/2 -translate-x-1/2 w-px bg-blue-300 dark:bg-blue-700"
                              style={{ top: `-${row * 22 - 4}px`, height: `${row * 22 - 4}px` }}
                            />
                          )}
                        </div>
                      );
                    })}
                  </div>
                );
              })()}
            </div>
          </div>

          {/* Event Details */}
          {selectedEvent && (
            <div className="mb-6 p-4 bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-lg">
              <div className="flex items-start justify-between mb-3">
                <div className="flex-1">
                  <div className="flex items-center gap-2 mb-2">
                    <span className="text-2xl">📅</span>
                    <div>
                      <div className="font-semibold text-gray-900 dark:text-white">
                        {formatDate(selectedEvent.date_timestamp)}
                      </div>
                      <div className="text-sm text-gray-600 dark:text-gray-400">
                        {selectedEvent.date_string}
                      </div>
                    </div>
                  </div>
                  <div className="text-sm text-gray-600 dark:text-gray-400 mb-2">
                    📄 {selectedEvent.filename || 'Unknown source'}
                  </div>
                </div>
                <button
                  onClick={() => setSelectedEvent(null)}
                  className="text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300"
                >
                  <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </div>
              <p className="text-gray-700 dark:text-gray-300 mb-3 italic">
                "{selectedEvent.context}"
              </p>
              <div className="flex items-center gap-2 text-xs text-gray-500 dark:text-gray-400">
                <span className="px-2 py-1 bg-gray-200 dark:bg-gray-700 rounded">
                  Confidence: {(selectedEvent.confidence * 100).toFixed(0)}%
                </span>
                <span className="px-2 py-1 bg-gray-200 dark:bg-gray-700 rounded capitalize">
                  {selectedEvent.date_type}
                </span>
              </div>
            </div>
          )}

          {/* Event List */}
          <div className="space-y-3">
            <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-3">
              All Events
            </h3>
            {filteredEvents.map((event) => (
              <button
                key={event.event_id}
                onClick={() => setSelectedEvent(event)}
                className={`w-full text-left p-3 rounded-lg border transition-colors ${
                  selectedEvent?.event_id === event.event_id
                    ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/20'
                    : 'border-gray-200 dark:border-gray-700 hover:border-blue-300 dark:hover:border-blue-700 bg-white dark:bg-gray-900'
                }`}
              >
                <div className="flex items-start gap-3">
                  <div className="flex-shrink-0 w-3 h-3 mt-1 rounded-full bg-blue-500"></div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="font-medium text-gray-900 dark:text-white">
                        {formatDate(event.date_timestamp)}
                      </span>
                      <span className="text-xs text-gray-500 dark:text-gray-400">
                        ({event.date_string})
                      </span>
                    </div>
                    <p className="text-sm text-gray-700 dark:text-gray-300 line-clamp-2 mb-1">
                      {event.event_text}
                    </p>
                    <div className="text-xs text-gray-500 dark:text-gray-400">
                      📄 {event.filename || 'Unknown source'}
                    </div>
                  </div>
                </div>
              </button>
            ))}
          </div>
        </div>
      ) : !extracting && events.length === 0 && !error ? (
        <div className="flex-1 flex items-center justify-center">
          <div className="text-center text-gray-500 dark:text-gray-400 max-w-md">
            <svg className="w-20 h-20 mx-auto mb-4 text-gray-300 dark:text-gray-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
            </svg>
            <p className="text-base font-medium mb-2">No Timeline Yet</p>
            <p className="text-sm mb-4">
              Extract dates from your documents to create an interactive timeline
            </p>
            <Button onClick={handleExtract}>Extract Timeline</Button>
          </div>
        </div>
      ) : filteredEvents.length === 0 && events.length > 0 ? (
        <div className="flex-1 flex items-center justify-center">
          <div className="text-center text-gray-500 dark:text-gray-400">
            <p className="text-base font-medium">No events match your filters</p>
            <p className="text-sm mt-2">Try adjusting your search or source filter</p>
          </div>
        </div>
      ) : null}
    </div>
  );
};
