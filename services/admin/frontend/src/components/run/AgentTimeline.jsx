import React, { useMemo } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '../ui/card';
import { EmptyState } from '../shared/EmptyState';

const EVENT_TYPE_FILL = {
  action_attempt: '#f59e0b',
  llm_io: '#10b981',
  prompt_part: '#3b82f6',
  heartbeat_result: '#6b7280',
};

const LANE_HEIGHT = 32;
const DOT_RADIUS = 4;
const Y_PADDING = 8;
const LEFT_LABEL_WIDTH = 120;

const AGENT_DOT_COLORS = [
  '#3b82f6',
  '#10b981',
  '#f59e0b',
  '#8b5cf6',
  '#f43f5e',
  '#06b6d4',
  '#f97316',
  '#ec4899',
];

export function AgentTimeline({
  telemetryTail,
  telemetryAgentOptions,
  telemetryEventType,
  telemetryAgentId,
  onSelectTelemetryEvent,
  agentColorMap,
  formatTime,
}) {
  const agents = useMemo(
    () => telemetryAgentOptions.filter((opt) => opt.value).map((opt) => opt.value),
    [telemetryAgentOptions]
  );

  const { events, minTime, maxTime } = useMemo(() => {
    if (!telemetryTail.length) return { events: [], minTime: 0, maxTime: 0 };
    let min = Infinity;
    let max = -Infinity;
    const parsed = [];

    telemetryTail.forEach((evt) => {
      const ts = new Date(evt.timestamp || 0).getTime();
      if (Number.isNaN(ts)) return;
      if (ts < min) min = ts;
      if (ts > max) max = ts;
      parsed.push({
        agent: telemetryAgentId(evt),
        time: ts,
        type: telemetryEventType(evt),
        evt,
      });
    });

    return { events: parsed, minTime: min, maxTime: max };
  }, [telemetryTail, telemetryAgentId, telemetryEventType]);

  const timeSpan = maxTime - minTime || 1;
  const svgHeight = agents.length * LANE_HEIGHT + Y_PADDING * 2;

  const tickMarks = useMemo(() => {
    if (!events.length) return [];
    const count = Math.min(6, Math.max(2, Math.floor(timeSpan / 60000)));
    const marks = [];
    for (let i = 0; i <= count; i++) {
      const t = minTime + (timeSpan * i) / count;
      marks.push({ time: t, pct: (i / count) * 100 });
    }
    return marks;
  }, [events.length, minTime, timeSpan]);

  if (!agents.length || !events.length) {
    return (
      <Card>
        <CardHeader className="border-b pb-4">
          <CardTitle className="text-base">Agent Timeline</CardTitle>
        </CardHeader>
        <CardContent className="pt-6">
          <EmptyState description="No agent activity recorded yet." />
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader className="border-b pb-4">
        <CardTitle className="text-base">Agent Timeline</CardTitle>
      </CardHeader>
      <CardContent className="pt-6">
        <div className="overflow-x-auto">
          <div className="flex min-w-[500px]">
            {/* Agent labels */}
            <div className="shrink-0" style={{ width: LEFT_LABEL_WIDTH }}>
              <div style={{ height: Y_PADDING }} />
              {agents.map((agent) => {
                const colorIdx = agentColorMap?.get(agent);
                const dotColor = colorIdx != null ? AGENT_DOT_COLORS[colorIdx] : '#6b7280';
                return (
                  <div
                    key={agent}
                    className="flex items-center gap-1.5 pr-2"
                    style={{ height: LANE_HEIGHT }}
                  >
                    <span
                      className="inline-block h-2 w-2 shrink-0 rounded-full"
                      style={{ backgroundColor: dotColor }}
                    />
                    <span className="truncate font-mono text-xs text-muted-foreground">
                      {agent}
                    </span>
                  </div>
                );
              })}
            </div>

            {/* SVG timeline area */}
            <div className="relative flex-1">
              <svg
                width="100%"
                height={svgHeight}
                className="block"
                style={{ minHeight: svgHeight }}
              >
                {/* Lane grid lines */}
                {agents.map((_, idx) => (
                  <line
                    key={`lane-${idx}`}
                    x1="0"
                    x2="100%"
                    y1={Y_PADDING + idx * LANE_HEIGHT + LANE_HEIGHT / 2}
                    y2={Y_PADDING + idx * LANE_HEIGHT + LANE_HEIGHT / 2}
                    stroke="hsl(var(--border))"
                    strokeWidth={1}
                    strokeDasharray="4 4"
                    opacity={0.4}
                  />
                ))}

                {/* Tick marks */}
                {tickMarks.map((mark, idx) => (
                  <line
                    key={`tick-${idx}`}
                    x1={`${mark.pct}%`}
                    x2={`${mark.pct}%`}
                    y1={0}
                    y2={svgHeight}
                    stroke="hsl(var(--border))"
                    strokeWidth={1}
                    strokeDasharray="2 3"
                    opacity={0.3}
                  />
                ))}

                {/* Event dots */}
                {events.map((e, idx) => {
                  const agentIdx = agents.indexOf(e.agent);
                  if (agentIdx < 0) return null;
                  const xPct = ((e.time - minTime) / timeSpan) * 100;
                  const cy = Y_PADDING + agentIdx * LANE_HEIGHT + LANE_HEIGHT / 2;
                  const fill = EVENT_TYPE_FILL[e.type] || '#6b7280';
                  const payload = e?.evt?.payload && typeof e.evt.payload === 'object' ? e.evt.payload : {};
                  const stopReason = String(payload.stop_reason || '').toLowerCase();
                  const isSkippedHeartbeat = e.type === 'heartbeat_result'
                    && (stopReason.includes('gated') || stopReason.includes('skip'));
                  return (
                    <circle
                      key={`${e.evt.event_id || idx}`}
                      cx={`${xPct}%`}
                      cy={cy}
                      r={DOT_RADIUS}
                      fill={isSkippedHeartbeat ? 'none' : fill}
                      stroke={isSkippedHeartbeat ? fill : undefined}
                      strokeWidth={isSkippedHeartbeat ? 1.5 : undefined}
                      opacity={0.8}
                      className="cursor-pointer hover:opacity-100"
                      onClick={() => onSelectTelemetryEvent(e.evt)}
                    >
                      <title>{`${e.type} @ ${formatTime(e.evt.timestamp)}`}</title>
                    </circle>
                  );
                })}
              </svg>

              {/* Time axis labels */}
              <div className="relative h-5" style={{ marginTop: -2 }}>
                {tickMarks.map((mark, idx) => (
                  <span
                    key={`label-${idx}`}
                    className="absolute font-mono text-[10px] text-muted-foreground -translate-x-1/2"
                    style={{ left: `${mark.pct}%` }}
                  >
                    {formatTime(new Date(mark.time).toISOString())}
                  </span>
                ))}
              </div>
            </div>
          </div>

          {/* Legend */}
          <div className="mt-3 flex flex-wrap gap-3">
            {Object.entries(EVENT_TYPE_FILL).map(([type, color]) => (
              <div key={type} className="flex items-center gap-1.5">
                <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ backgroundColor: color }} />
                <span className="text-xs text-muted-foreground">{type}</span>
              </div>
            ))}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
