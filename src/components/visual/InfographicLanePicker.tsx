/**
 * InfographicLanePicker — the lane-override control (plan §3.4).
 *
 * "The UI exposes the four lanes as explicit buttons alongside 'auto'. This is
 * the single most important debugging affordance: a router mistake becomes a
 * one-click correction instead of a bug report. Ship the override BEFORE the
 * classifier."
 *
 * Builds L1 (chart) + L2 (diagram) + L3 (hand-drawn scene, Phase-4) + L4
 * (decorative Klein art). All four lanes are selectable; "auto" routes on
 * content shape.
 */

export type InfographicLane = 'auto' | 'L1' | 'L2' | 'L3' | 'L4';

interface LaneDef {
  id: InfographicLane;
  label: string;
  hint: string;
  disabled?: boolean;
}

const LANES: LaneDef[] = [
  { id: 'auto', label: 'Auto', hint: 'Route on content shape' },
  { id: 'L1', label: 'Chart', hint: 'Annotated chart' },
  { id: 'L2', label: 'Diagram', hint: 'Structured diagram' },
  { id: 'L3', label: 'Scene', hint: 'Hand-drawn scene' },
  { id: 'L4', label: 'Art', hint: 'Decorative image (Klein)' },
];

interface Props {
  value: InfographicLane;
  onChange: (lane: InfographicLane) => void;
  className?: string;
}

export const InfographicLanePicker = ({ value, onChange, className = '' }: Props) => {
  return (
    <div className={`flex flex-wrap gap-1.5 ${className}`} role="group" aria-label="Infographic lane">
      {LANES.map((lane) => {
        const active = value === lane.id;
        return (
          <button
            key={lane.id}
            type="button"
            disabled={lane.disabled}
            title={lane.hint}
            aria-pressed={active}
            onClick={() => !lane.disabled && onChange(lane.id)}
            className={[
              'px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors',
              lane.disabled
                ? 'opacity-40 cursor-not-allowed border-gray-200 dark:border-gray-700 text-gray-400'
                : active
                ? 'border-[#e0503a] bg-[#e0503a]/10 text-[#e0503a]'
                : 'border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300 hover:border-[#e0503a]/50',
            ].join(' ')}
          >
            {lane.label}
          </button>
        );
      })}
    </div>
  );
};

export default InfographicLanePicker;
