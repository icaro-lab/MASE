import React, { useEffect, useMemo, useState } from 'react';
import { AlertTriangle } from 'lucide-react';
import { Area, AreaChart, Bar, BarChart, CartesianGrid, XAxis, YAxis } from 'recharts';
import { Alert, AlertDescription } from '../ui/alert';
import { Card, CardContent, CardHeader, CardTitle } from '../ui/card';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../ui/table';
import { ChartContainer, ChartTooltip, ChartTooltipContent } from '../ui/chart';
import { EmptyState } from '../shared/EmptyState';
import { SelectField } from '../shared/SelectField';
import { StatusBadge } from '../shared/StatusBadge';
import { TablePagination } from '../shared/TablePagination';
import { modelOf, parseTimestampMs } from '../../utils/telemetryHelpers';

const PAGE_SIZE = 20;

function eventPayload(event) {
  let payload = event?.payload;
  let depth = 0;
  while (
    payload
    && typeof payload === 'object'
    && payload.payload
    && typeof payload.payload === 'object'
    && depth < 3
  ) {
    payload = payload.payload;
    depth += 1;
  }
  return payload && typeof payload === 'object' ? payload : {};
}

function telemetryRoundIndex(event) {
  const payload = eventPayload(event);
  const parsed = Number.parseInt(String(payload.round_index ?? ''), 10);
  return Number.isFinite(parsed) ? parsed : null;
}

function minuteBucket(value) {
  const ts = parseTimestampMs(value);
  if (Number.isNaN(ts)) return '';
  return new Date(ts).toISOString().slice(0, 16);
}

function minuteLabel(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '--:--';
  return new Intl.DateTimeFormat('it-IT', {
    timeZone: 'Europe/Rome',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date);
}

function shortenModel(value, max = 28) {
  const text = String(value || '-');
  return text.length > max ? `${text.slice(0, max - 3)}...` : text;
}

function Stat({ label, value }) {
  return (
    <div className="space-y-0.5">
      <p className="text-[10px] uppercase tracking-wider text-muted-foreground">{label}</p>
      <p className="text-lg font-semibold tracking-tight">{value}</p>
    </div>
  );
}

function useElapsedTime(run) {
  const [, setTick] = useState(0);
  const isActive = run && !run.ended_at;

  useEffect(() => {
    if (!isActive) return undefined;
    const id = setInterval(() => setTick((t) => t + 1), 1000);
    return () => clearInterval(id);
  }, [isActive]);

  if (!run?.started_at) return null;
  const start = parseTimestampMs(run.started_at);
  if (Number.isNaN(start)) return null;
  const end = run.ended_at ? parseTimestampMs(run.ended_at) : Date.now();
  const seconds = Math.max(0, Math.floor((end - start) / 1000));
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (h > 0) return `${h}h ${m}m ${s}s`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

export function RunLivePanel({
  run,
  eventCount,
  eventTypeCounts,
  agentCount,
  costTotals,
  pricingUnavailable,
  actionSeries,
  costSeries,
  tokenSeries,
  terminalFilters,
  onTerminalFiltersChange,
  telemetryTypeOptions,
  telemetryAgentOptions,
  filteredTelemetryTail,
  telemetryTail,
  historyEvents,
  formatDateTime,
  formatNumber,
  formatRunDuration,
  formatTime,
  telemetryEventType,
  telemetryTick,
  telemetryAgentId,
  buildTelemetrySummary,
  extractCostUsd,
  extractTokens,
}) {
  const [terminalPage, setTerminalPage] = useState(1);
  const elapsed = useElapsedTime(run);

  // For stopped runs the SSE buffer (telemetryTail) is empty. Fall back to historyEvents.
  const effectiveSource = useMemo(
    () => (telemetryTail.length ? telemetryTail : (historyEvents || [])),
    [telemetryTail, historyEvents],
  );

  const telemetryRows = useMemo(
    () =>
      filteredTelemetryTail.map((evt, idx) => {
        const type = telemetryEventType(evt);
        const cost = extractCostUsd(evt);
        const tokens = extractTokens(evt);
        return {
          key: evt.event_id || evt.id || `${evt.timestamp || ''}-${idx}`,
          evt,
          type,
          tick: telemetryTick(evt),
          round: telemetryRoundIndex(evt),
          agent: telemetryAgentId(evt),
          summary: buildTelemetrySummary(evt),
          cost,
          tokens,
          timestamp: evt.timestamp,
          model: modelOf(evt),
        };
      }),
    [
      buildTelemetrySummary,
      extractCostUsd,
      extractTokens,
      filteredTelemetryTail,
      telemetryAgentId,
      telemetryEventType,
      telemetryTick,
    ]
  );

  const terminalTotalPages = Math.max(1, Math.ceil(telemetryRows.length / PAGE_SIZE));
  const pagedTelemetryRows = useMemo(() => {
    const start = (terminalPage - 1) * PAGE_SIZE;
    return telemetryRows.slice(start, start + PAGE_SIZE);
  }, [terminalPage, telemetryRows]);

  useEffect(() => {
    if (terminalPage > terminalTotalPages) setTerminalPage(terminalTotalPages);
  }, [terminalPage, terminalTotalPages]);

  useEffect(() => {
    setTerminalPage(1);
  }, [terminalFilters.agent, terminalFilters.type]);

  const eventBreakdownSub = useMemo(() => {
    const entries = Object.entries(eventTypeCounts)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 3);
    if (!entries.length) return 'No event types observed yet';
    return entries.map(([type, count]) => `${type}: ${formatNumber(count)}`).join(' | ');
  }, [eventTypeCounts, formatNumber]);

  const eventTypeChartData = useMemo(() => {
    return Object.entries(eventTypeCounts)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 6)
      .map(([type, count]) => ({ type: shortenModel(type, 20), count }));
  }, [eventTypeCounts]);

  const costRateSub = useMemo(() => {
    if (!run?.started_at || !costTotals.total) {
      return `in=${formatNumber(costTotals.tokensIn)} out=${formatNumber(costTotals.tokensOut)}`;
    }
    const start = parseTimestampMs(run.started_at);
    const end = run.ended_at ? parseTimestampMs(run.ended_at) : Date.now();
    const minutes = Math.max(1, (end - start) / 60000);
    const rate = costTotals.total / minutes;
    return `$${formatNumber(rate)}/min | in=${formatNumber(costTotals.tokensIn)} out=${formatNumber(costTotals.tokensOut)}`;
  }, [costTotals, formatNumber, run]);

  const tracesByTimeSeries = useMemo(() => {
    const buckets = new Map();
    effectiveSource.forEach((evt) => {
      const key = minuteBucket(evt.timestamp);
      if (!key) return;
      buckets.set(key, (buckets.get(key) || 0) + 1);
    });

    const keys = Array.from(buckets.keys()).sort((a, b) => a.localeCompare(b));
    return keys.slice(Math.max(0, keys.length - 60)).map((key) => ({
      bucket: key,
      label: minuteLabel(`${key}:00Z`),
      traces: buckets.get(key) || 0,
    }));
  }, [effectiveSource]);

  const modelUsageRows = useMemo(() => {
    const byModel = new Map();

    effectiveSource.forEach((evt) => {
      const type = telemetryEventType(evt);
      const model = modelOf(evt);
      const tokens = extractTokens(evt);
      const cost = extractCostUsd(evt);
      const hasSignal = (tokens.input || tokens.output || cost.total);
      if (!hasSignal && type !== 'llm_io') return;

      const current = byModel.get(model) || {
        model,
        requests: 0,
        tokensIn: 0,
        tokensOut: 0,
        totalTokens: 0,
        cost: 0,
      };
      current.requests += 1;
      current.tokensIn += Number(tokens.input || 0);
      current.tokensOut += Number(tokens.output || 0);
      current.totalTokens += Number(tokens.input || 0) + Number(tokens.output || 0);
      current.cost += Number(cost.total || 0);
      byModel.set(model, current);
    });

    return Array.from(byModel.values())
      .sort((a, b) => b.cost - a.cost || b.totalTokens - a.totalTokens || a.model.localeCompare(b.model))
      .slice(0, 8);
  }, [effectiveSource, extractCostUsd, extractTokens, telemetryEventType]);

  const modelUsageChartData = useMemo(() => {
    return modelUsageRows.slice(0, 6).map((row) => ({
      model: shortenModel(row.model, 22),
      cost: row.cost,
    }));
  }, [modelUsageRows]);

  const agentStatsRows = useMemo(() => {
    const byAgent = new Map();

    effectiveSource.forEach((evt) => {
      const agent = telemetryAgentId(evt);
      const cost = extractCostUsd(evt);
      const tokens = extractTokens(evt);

      const current = byAgent.get(agent) || { agent, events: 0, cost: 0, tokensIn: 0, tokensOut: 0 };
      current.events += 1;
      current.cost += Number(cost.total || 0);
      current.tokensIn += Number(tokens.input || 0);
      current.tokensOut += Number(tokens.output || 0);
      byAgent.set(agent, current);
    });

    return Array.from(byAgent.values())
      .sort((a, b) => b.cost - a.cost || b.events - a.events || a.agent.localeCompare(b.agent))
      .slice(0, 8);
  }, [effectiveSource, extractCostUsd, extractTokens, telemetryAgentId]);

  const eventsPerMinute = useMemo(() => {
    if (!run?.started_at || !eventCount) return null;
    const start = parseTimestampMs(run.started_at);
    const end = run.ended_at ? parseTimestampMs(run.ended_at) : Date.now();
    const minutes = Math.max(1, (end - start) / 60000);
    return eventCount / minutes;
  }, [eventCount, run]);

  return (
    <div className="space-y-4">
      {pricingUnavailable ? (
        <Alert>
          <AlertTriangle className="h-4 w-4" />
          <AlertDescription>
            Provider pricing metadata was not recorded for this run, so cost values in traces are unavailable.
          </AlertDescription>
        </Alert>
      ) : null}
      {/* Section 1: Timeline Charts — 2-col (3:2) */}
      <div className="grid items-start gap-4 xl:grid-cols-5">
        <Card className="xl:col-span-3">
          <CardHeader className="border-b pb-4">
            <CardTitle className="text-base">Traces by time</CardTitle>
          </CardHeader>
          <CardContent className="pt-6">
            {tracesByTimeSeries.length > 1 ? (
              <ChartContainer
                className="h-[240px] w-full"
                config={{
                  traces: {
                    label: 'Traces',
                    color: 'hsl(221.2 83.2% 53.3%)',
                  },
                }}
              >
                <AreaChart data={tracesByTimeSeries} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
                  <XAxis dataKey="label" minTickGap={26} />
                  <YAxis allowDecimals={false} />
                  <ChartTooltip cursor={false} content={<ChartTooltipContent />} />
                  <Area
                    type="monotone"
                    dataKey="traces"
                    stroke="var(--color-traces)"
                    fill="var(--color-traces)"
                    fillOpacity={0.12}
                    strokeWidth={1.8}
                  />
                </AreaChart>
              </ChartContainer>
            ) : (
              <EmptyState description="Not enough traces yet to chart a trend." />
            )}
          </CardContent>
        </Card>
        <Card className="xl:col-span-2">
          <CardHeader className="border-b pb-4">
            <CardTitle className="text-base">Cost over time</CardTitle>
          </CardHeader>
          <CardContent className="pt-6">
            {costSeries.length > 1 ? (
              <ChartContainer
                className="h-[240px] w-full"
                config={{
                  cumulative_usd: {
                    label: 'Cumulative USD',
                    color: 'hsl(173.4 80.4% 40%)',
                  },
                }}
              >
                <AreaChart data={costSeries} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
                  <XAxis dataKey="label" minTickGap={26} />
                  <YAxis />
                  <ChartTooltip
                    cursor={false}
                    content={<ChartTooltipContent formatter={(value) => `$${formatNumber(value || 0)}`} />}
                  />
                  <Area
                    type="monotone"
                    dataKey="cumulative_usd"
                    stroke="var(--color-cumulative_usd)"
                    fill="var(--color-cumulative_usd)"
                    fillOpacity={0.12}
                    strokeWidth={1.8}
                  />
                </AreaChart>
              </ChartContainer>
            ) : (
              <EmptyState description={pricingUnavailable ? 'No pricing metadata was recorded for this run.' : 'No cost series yet.'} />
            )}
          </CardContent>
        </Card>
      </div>

      {/* Section 3: Breakdowns — 3-col equal */}
      <div className="grid items-start gap-4 xl:grid-cols-3">
        <Card>
          <CardHeader className="border-b pb-4">
            <CardTitle className="text-base">Event types</CardTitle>
          </CardHeader>
          <CardContent className="pt-6">
            {eventTypeChartData.length ? (
              <ChartContainer
                className="h-[200px] w-full"
                config={{
                  count: {
                    label: 'Count',
                    color: 'hsl(221.2 83.2% 53.3%)',
                  },
                }}
              >
                <BarChart data={eventTypeChartData} layout="vertical" margin={{ top: 0, right: 4, left: 60, bottom: 0 }}>
                  <XAxis type="number" hide />
                  <YAxis type="category" dataKey="type" width={56} tick={{ fontSize: 11 }} />
                  <Bar dataKey="count" fill="var(--color-count)" radius={[0, 4, 4, 0]} />
                </BarChart>
              </ChartContainer>
            ) : (
              <EmptyState description="No traces to chart yet." />
            )}
            <p className="mt-2 text-xs text-muted-foreground">{eventBreakdownSub}</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="border-b pb-4">
            <CardTitle className="text-base">Agents</CardTitle>
          </CardHeader>
          <CardContent className="pt-6">
            {agentStatsRows.length ? (
              <div className="overflow-hidden rounded-md border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Agent</TableHead>
                      <TableHead className="w-[70px] text-right">Events</TableHead>
                      <TableHead className="w-[80px] text-right">USD</TableHead>
                      <TableHead className="w-[100px] text-right">Tokens</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {agentStatsRows.slice(0, 8).map((row) => (
                      <TableRow key={row.agent}>
                        <TableCell className="font-mono text-xs">{row.agent}</TableCell>
                        <TableCell className="text-right font-mono text-xs">{formatNumber(row.events)}</TableCell>
                        <TableCell className="text-right font-mono text-xs">${formatNumber(row.cost)}</TableCell>
                        <TableCell className="text-right font-mono text-xs">
                          {formatNumber(row.tokensIn + row.tokensOut)}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            ) : (
              <EmptyState description="No agent data yet." />
            )}
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="border-b pb-4">
            <CardTitle className="text-base">Models & tokens</CardTitle>
          </CardHeader>
          <CardContent className="pt-6 space-y-4">
            {modelUsageChartData.length ? (
              <ChartContainer
                className="h-[140px] w-full"
                config={{
                  cost: {
                    label: 'USD',
                    color: 'hsl(173.4 80.4% 40%)',
                  },
                }}
              >
                <BarChart data={modelUsageChartData} margin={{ top: 0, right: 6, left: 0, bottom: 0 }}>
                  <XAxis dataKey="model" tick={{ fontSize: 11 }} interval={0} height={52} angle={-20} textAnchor="end" />
                  <YAxis allowDecimals={false} />
                  <ChartTooltip
                    cursor={false}
                    content={<ChartTooltipContent formatter={(value) => `$${formatNumber(value || 0)}`} />}
                  />
                  <Bar dataKey="cost" fill="var(--color-cost)" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ChartContainer>
            ) : (
              <EmptyState description="No model usage data yet." />
            )}
            {tokenSeries.length ? (
              <ChartContainer
                className="h-[140px] w-full"
                config={{
                  input: {
                    label: 'Input',
                    color: 'hsl(221.2 83.2% 53.3%)',
                  },
                  output: {
                    label: 'Output',
                    color: 'hsl(173.4 80.4% 40%)',
                  },
                }}
              >
                <BarChart data={tokenSeries} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
                  <XAxis dataKey="label" minTickGap={26} />
                  <YAxis allowDecimals={false} />
                  <ChartTooltip cursor={false} content={<ChartTooltipContent />} />
                  <Bar dataKey="input" stackId="tokens" fill="var(--color-input)" radius={[0, 0, 0, 0]} />
                  <Bar dataKey="output" stackId="tokens" fill="var(--color-output)" radius={[3, 3, 0, 0]} />
                </BarChart>
              </ChartContainer>
            ) : (
              <EmptyState description="No token usage yet." />
            )}
          </CardContent>
        </Card>
      </div>

      {/* Section 4: Terminal */}
      <Card>
        <CardHeader className="flex flex-col gap-3 border-b pb-4">
          <div className="space-y-1">
            <CardTitle className="text-xl">Terminal</CardTitle>
            <p className="text-xs text-muted-foreground">
              Recent telemetry log stream for this run. Use filters to focus by type or agent.
            </p>
          </div>
          <div className="grid gap-2 sm:grid-cols-2">
            <SelectField
              id="run-stats-terminal-type"
              ariaLabel="Terminal type filter"
              value={terminalFilters.type}
              options={telemetryTypeOptions}
              onChange={(value) => onTerminalFiltersChange({ ...terminalFilters, type: value })}
            />
            <SelectField
              id="run-stats-terminal-agent"
              ariaLabel="Terminal agent filter"
              value={terminalFilters.agent}
              options={telemetryAgentOptions}
              onChange={(value) => onTerminalFiltersChange({ ...terminalFilters, agent: value })}
            />
          </div>
        </CardHeader>
        <CardContent className="pt-6">
          <div className="overflow-hidden rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-[110px]">Time</TableHead>
                  <TableHead className="w-[170px]">Agent</TableHead>
                  <TableHead className="w-[180px]">Model</TableHead>
                  <TableHead className="w-[130px]">Type</TableHead>
                  <TableHead className="w-[70px]">Tick</TableHead>
                  <TableHead className="w-[70px]">Round</TableHead>
                  <TableHead>Summary</TableHead>
                  <TableHead className="w-[120px]">Cost</TableHead>
                  <TableHead className="w-[130px]">Tokens</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {pagedTelemetryRows.length ? (
                  pagedTelemetryRows.map((row) => (
                    <TableRow key={row.key}>
                      <TableCell>
                        <span className="font-mono text-xs">{formatTime(row.timestamp)}</span>
                      </TableCell>
                      <TableCell>
                        <span className="font-mono text-xs">{row.agent}</span>
                      </TableCell>
                      <TableCell>
                        <span className="font-mono text-xs">{row.model}</span>
                      </TableCell>
                      <TableCell>
                        <StatusBadge tone="info" label={row.type} className="normal-case" />
                      </TableCell>
                      <TableCell>
                        <span className="font-mono text-xs">{row.tick ?? '-'}</span>
                      </TableCell>
                      <TableCell>
                        <span className="font-mono text-xs">{row.round ?? '-'}</span>
                      </TableCell>
                      <TableCell>
                        <span className="font-mono text-xs">{String(row.summary || '-')}</span>
                      </TableCell>
                      <TableCell>
                        <span className="font-mono text-xs">
                          {row.cost?.total ? `$${formatNumber(row.cost.total)}` : '-'}
                        </span>
                      </TableCell>
                      <TableCell>
                        <span className="font-mono text-xs">
                          {row.tokens?.input || row.tokens?.output
                            ? `${formatNumber(row.tokens.input)} / ${formatNumber(row.tokens.output)}`
                            : '-'}
                        </span>
                      </TableCell>
                    </TableRow>
                  ))
                ) : (
                  <TableRow>
                    <TableCell className="py-6 text-center text-sm text-muted-foreground" colSpan={9}>
                      {telemetryTail.length ? 'No rows match your filters.' : 'No telemetry yet for this run.'}
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>

            <TablePagination page={terminalPage} totalPages={terminalTotalPages} onPageChange={setTerminalPage} />
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
