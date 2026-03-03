/**
 * ChartRenderer.tsx - Renders data-driven charts using Recharts
 * 
 * Accepts a JSON chart configuration and maps it to Recharts components.
 * Supports: line, bar, area, composed (mixed), scatter, and pie charts.
 * 
 * The LLM/backend generates a simple JSON config, and this component
 * handles all the rendering complexity.
 */

import React, { useMemo } from 'react';
import {
  ResponsiveContainer,
  LineChart, Line,
  BarChart, Bar,
  AreaChart, Area,
  ComposedChart,
  ScatterChart, Scatter,
  PieChart, Pie, Cell,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend,
} from 'recharts';

// ─── Chart Configuration Interface ───────────────────────────────────────────

export interface ChartSeries {
  key: string;
  label?: string;
  color?: string;
  type?: 'line' | 'bar' | 'area';      // For composed charts
  strokeDasharray?: string;              // e.g. "5 5" for dashed lines
  yAxisId?: 'left' | 'right';
}

export interface ChartConfig {
  chart_type: 'line' | 'bar' | 'area' | 'composed' | 'scatter' | 'pie';
  title?: string;
  x_axis?: { label?: string; key?: string };
  y_axis?: { label?: string; domain?: [number | string, number | string] };
  y_axis_right?: { label?: string };
  series: ChartSeries[];
  data: Record<string, any>[];
  show_grid?: boolean;
  show_legend?: boolean;
  show_tooltip?: boolean;
  stacked?: boolean;
}

// ─── Default Color Palette ───────────────────────────────────────────────────

const CHART_COLORS = [
  '#6366f1', // indigo
  '#22c55e', // green
  '#f59e0b', // amber
  '#ef4444', // red
  '#8b5cf6', // purple
  '#06b6d4', // cyan
  '#ec4899', // pink
  '#14b8a6', // teal
];

const PIE_COLORS = [
  '#6366f1', '#22c55e', '#f59e0b', '#ef4444',
  '#8b5cf6', '#06b6d4', '#ec4899', '#14b8a6',
];

// ─── Custom Tooltip ──────────────────────────────────────────────────────────

const CustomTooltip = ({ active, payload, label }: any) => {
  if (!active || !payload?.length) return null;
  return (
    <div className="bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 shadow-lg">
      <p className="text-xs text-gray-400 mb-1">{label}</p>
      {payload.map((entry: any, i: number) => (
        <p key={i} className="text-xs" style={{ color: entry.color }}>
          {entry.name}: <span className="font-semibold">{
            typeof entry.value === 'number'
              ? entry.value.toLocaleString()
              : entry.value
          }</span>
        </p>
      ))}
    </div>
  );
};

// ─── Main Component ──────────────────────────────────────────────────────────

interface ChartRendererProps {
  config: ChartConfig;
  className?: string;
  height?: number;
  darkMode?: boolean;
}

export const ChartRenderer: React.FC<ChartRendererProps> = ({
  config,
  className = '',
  height = 350,
  darkMode = true,
}) => {
  const {
    chart_type,
    data,
    series,
    x_axis,
    y_axis,
    y_axis_right,
    show_grid = true,
    show_legend = true,
    show_tooltip = true,
    stacked = false,
  } = config;

  const xKey = x_axis?.key || 'name';

  // Assign colors to series that don't have explicit colors
  const coloredSeries = useMemo(() => 
    series.map((s, i) => ({
      ...s,
      color: s.color || CHART_COLORS[i % CHART_COLORS.length],
    })),
    [series]
  );

  const axisStyle = {
    fontSize: 11,
    fill: darkMode ? '#9ca3af' : '#6b7280',
  };

  const gridColor = darkMode ? '#374151' : '#e5e7eb';

  // Shared axis components
  const renderAxes = (hasDualY = false) => (
    <>
      {show_grid && <CartesianGrid strokeDasharray="3 3" stroke={gridColor} />}
      <XAxis 
        dataKey={xKey} 
        tick={axisStyle}
        axisLine={{ stroke: gridColor }}
        tickLine={{ stroke: gridColor }}
        label={x_axis?.label ? { 
          value: x_axis.label, 
          position: 'insideBottom', 
          offset: -5,
          style: { ...axisStyle, fontSize: 12 }
        } : undefined}
      />
      <YAxis 
        yAxisId={hasDualY ? 'left' : undefined}
        tick={axisStyle}
        axisLine={{ stroke: gridColor }}
        tickLine={{ stroke: gridColor }}
        domain={y_axis?.domain as any}
        tickFormatter={(v: number) => {
          if (Math.abs(v) >= 1e9) return `${(v / 1e9).toFixed(1)}B`;
          if (Math.abs(v) >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
          if (Math.abs(v) >= 1e3) return `${(v / 1e3).toFixed(1)}K`;
          return v.toString();
        }}
        label={y_axis?.label ? { 
          value: y_axis.label, 
          angle: -90, 
          position: 'insideLeft',
          style: { ...axisStyle, fontSize: 12 }
        } : undefined}
      />
      {hasDualY && y_axis_right && (
        <YAxis 
          yAxisId="right"
          orientation="right"
          tick={axisStyle}
          axisLine={{ stroke: gridColor }}
          tickLine={{ stroke: gridColor }}
          label={y_axis_right.label ? { 
            value: y_axis_right.label, 
            angle: 90, 
            position: 'insideRight',
            style: { ...axisStyle, fontSize: 12 }
          } : undefined}
        />
      )}
      {show_tooltip && <Tooltip content={<CustomTooltip />} />}
      {show_legend && (
        <Legend 
          wrapperStyle={{ fontSize: 11, color: darkMode ? '#d1d5db' : '#374151' }}
        />
      )}
    </>
  );

  const hasDualY = coloredSeries.some(s => s.yAxisId === 'right');

  // ─── Line Chart ────────────────────────────────────────────────────────────

  if (chart_type === 'line') {
    return (
      <div className={`chart-renderer ${className}`}>
        <ResponsiveContainer width="100%" height={height}>
          <LineChart data={data} margin={{ top: 10, right: 30, left: 10, bottom: 5 }}>
            {renderAxes(hasDualY)}
            {coloredSeries.map((s) => (
              <Line
                key={s.key}
                type="monotone"
                dataKey={s.key}
                name={s.label || s.key}
                stroke={s.color}
                strokeWidth={2}
                strokeDasharray={s.strokeDasharray}
                yAxisId={hasDualY ? (s.yAxisId || 'left') : undefined}
                dot={{ r: 3, fill: s.color }}
                activeDot={{ r: 5 }}
                connectNulls
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
    );
  }

  // ─── Bar Chart ─────────────────────────────────────────────────────────────

  if (chart_type === 'bar') {
    return (
      <div className={`chart-renderer ${className}`}>
        <ResponsiveContainer width="100%" height={height}>
          <BarChart data={data} margin={{ top: 10, right: 30, left: 10, bottom: 5 }}>
            {renderAxes(hasDualY)}
            {coloredSeries.map((s) => (
              <Bar
                key={s.key}
                dataKey={s.key}
                name={s.label || s.key}
                fill={s.color}
                yAxisId={hasDualY ? (s.yAxisId || 'left') : undefined}
                stackId={stacked ? 'stack' : undefined}
                radius={[4, 4, 0, 0]}
              />
            ))}
          </BarChart>
        </ResponsiveContainer>
      </div>
    );
  }

  // ─── Area Chart ────────────────────────────────────────────────────────────

  if (chart_type === 'area') {
    return (
      <div className={`chart-renderer ${className}`}>
        <ResponsiveContainer width="100%" height={height}>
          <AreaChart data={data} margin={{ top: 10, right: 30, left: 10, bottom: 5 }}>
            {renderAxes(hasDualY)}
            {coloredSeries.map((s) => (
              <Area
                key={s.key}
                type="monotone"
                dataKey={s.key}
                name={s.label || s.key}
                stroke={s.color}
                fill={s.color}
                fillOpacity={0.15}
                strokeWidth={2}
                yAxisId={hasDualY ? (s.yAxisId || 'left') : undefined}
                stackId={stacked ? 'stack' : undefined}
                connectNulls
              />
            ))}
          </AreaChart>
        </ResponsiveContainer>
      </div>
    );
  }

  // ─── Composed Chart (mixed bar + line + area) ──────────────────────────────

  if (chart_type === 'composed') {
    return (
      <div className={`chart-renderer ${className}`}>
        <ResponsiveContainer width="100%" height={height}>
          <ComposedChart data={data} margin={{ top: 10, right: 30, left: 10, bottom: 5 }}>
            {renderAxes(hasDualY)}
            {coloredSeries.map((s) => {
              const seriesType = s.type || 'line';
              if (seriesType === 'bar') {
                return (
                  <Bar
                    key={s.key}
                    dataKey={s.key}
                    name={s.label || s.key}
                    fill={s.color}
                    yAxisId={hasDualY ? (s.yAxisId || 'left') : undefined}
                    radius={[4, 4, 0, 0]}
                  />
                );
              }
              if (seriesType === 'area') {
                return (
                  <Area
                    key={s.key}
                    type="monotone"
                    dataKey={s.key}
                    name={s.label || s.key}
                    stroke={s.color}
                    fill={s.color}
                    fillOpacity={0.15}
                    strokeWidth={2}
                    yAxisId={hasDualY ? (s.yAxisId || 'left') : undefined}
                    connectNulls
                  />
                );
              }
              return (
                <Line
                  key={s.key}
                  type="monotone"
                  dataKey={s.key}
                  name={s.label || s.key}
                  stroke={s.color}
                  strokeWidth={2}
                  strokeDasharray={s.strokeDasharray}
                  yAxisId={hasDualY ? (s.yAxisId || 'left') : undefined}
                  dot={{ r: 3, fill: s.color }}
                  connectNulls
                />
              );
            })}
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    );
  }

  // ─── Scatter Chart ─────────────────────────────────────────────────────────

  if (chart_type === 'scatter') {
    return (
      <div className={`chart-renderer ${className}`}>
        <ResponsiveContainer width="100%" height={height}>
          <ScatterChart margin={{ top: 10, right: 30, left: 10, bottom: 5 }}>
            {show_grid && <CartesianGrid strokeDasharray="3 3" stroke={gridColor} />}
            <XAxis 
              type="number" 
              dataKey={coloredSeries[0]?.key || 'x'} 
              name={x_axis?.label}
              tick={axisStyle}
            />
            <YAxis 
              type="number" 
              dataKey={coloredSeries[1]?.key || 'y'} 
              name={y_axis?.label}
              tick={axisStyle}
            />
            {show_tooltip && <Tooltip content={<CustomTooltip />} />}
            {show_legend && <Legend wrapperStyle={{ fontSize: 11 }} />}
            <Scatter 
              name={config.title || 'Data'} 
              data={data} 
              fill={coloredSeries[0]?.color || CHART_COLORS[0]} 
            />
          </ScatterChart>
        </ResponsiveContainer>
      </div>
    );
  }

  // ─── Pie Chart ─────────────────────────────────────────────────────────────

  if (chart_type === 'pie') {
    const dataKey = coloredSeries[0]?.key || 'value';
    const nameKey = x_axis?.key || 'name';
    return (
      <div className={`chart-renderer ${className}`}>
        <ResponsiveContainer width="100%" height={height}>
          <PieChart>
            {show_tooltip && <Tooltip content={<CustomTooltip />} />}
            {show_legend && (
              <Legend 
                wrapperStyle={{ fontSize: 11, color: darkMode ? '#d1d5db' : '#374151' }}
              />
            )}
            <Pie
              data={data}
              dataKey={dataKey}
              nameKey={nameKey}
              cx="50%"
              cy="50%"
              outerRadius={height * 0.35}
              label={({ name, percent }: any) => `${name} ${(percent * 100).toFixed(0)}%`}
              labelLine={{ stroke: darkMode ? '#6b7280' : '#9ca3af' }}
            >
              {data.map((_: any, i: number) => (
                <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />
              ))}
            </Pie>
          </PieChart>
        </ResponsiveContainer>
      </div>
    );
  }

  // ─── Fallback ──────────────────────────────────────────────────────────────

  return (
    <div className={`flex items-center justify-center p-4 bg-gray-800 rounded-lg ${className}`}>
      <span className="text-sm text-gray-400">Unsupported chart type: {chart_type}</span>
    </div>
  );
};

export default ChartRenderer;
