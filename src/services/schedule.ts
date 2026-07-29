/**
 * Schedule Service — the Unified Schedule Viewer (2.2.0).
 *
 * Reads the merged schedule payload (registry defaults ⊕ overrides ⊕
 * per-notebook Collector schedules ⊕ live worker state) and edits the
 * live-editable ones. Only Collector per-notebook rows are editable today;
 * everything else is read-only until Rung C.
 */
import { API_BASE_URL, localFetch } from './api';

export interface ScheduleRow {
  id: string;
  name: string;
  agent: string;                 // curator | collector | correspondent | research | memory | system
  category: string;              // agents | email | memory | infrastructure
  cadence_kind: string;          // interval | per_notebook | gated | reactive | env
  editable: boolean;             // live-editable in this build (Collector rows only)
  enabled: boolean;
  interval_seconds?: number | null;
  default_seconds?: number | null;
  min_seconds?: number | null;
  max_seconds?: number | null;
  overridden?: boolean;
  rung_c_candidate?: boolean;
  module_const?: string;
  note?: string;
  tier?: string | null;
  env_var?: string | null;
  managed_in?: string | null;
  advanced?: boolean;
  // Collector per-notebook rows:
  notebook_id?: string;
  frequency?: string;
  max_items_per_run?: number | null;
  frequency_options?: string[];
  last_run?: string | null;
  next_due?: string | null;
  overdue?: boolean | null;
}

export interface LiveSchedule {
  queue_depth?: number | null;
  current_job?: string | null;
  tier?: string | null;
  memory_pressure?: boolean | null;
  last_run?: string | null;
  last_run_label?: string | null;
}

export interface SchedulesPayload {
  schedules: ScheduleRow[];
  live: LiveSchedule;
  generated_at: string;
}

export interface ScheduleUpdate {
  interval_seconds?: number;
  enabled?: boolean;
  frequency?: string;
  max_items_per_run?: number;
}

class ScheduleService {
  async getSchedules(): Promise<SchedulesPayload> {
    const response = await localFetch(`${API_BASE_URL}/system/schedules`);
    if (!response.ok) throw new Error('Failed to fetch schedules');
    return response.json();
  }

  async updateSchedule(id: string, update: ScheduleUpdate): Promise<any> {
    const response = await localFetch(
      `${API_BASE_URL}/system/schedules/${encodeURIComponent(id)}`,
      {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(update),
      },
    );
    if (!response.ok) {
      let detail = 'Failed to update schedule';
      try {
        const body = await response.json();
        detail = body?.detail || detail;
      } catch {
        /* keep default */
      }
      throw new Error(detail);
    }
    return response.json();
  }
}

export const scheduleService = new ScheduleService();
