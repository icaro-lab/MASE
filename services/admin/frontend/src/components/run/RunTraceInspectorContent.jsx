import React, { useMemo, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '../ui/collapsible';
import { ScrollArea } from '../ui/scroll-area';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../ui/table';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../ui/tabs';
import { CollapsibleCodeBlock } from '../shared/CollapsibleCodeBlock';
import { EmptyState } from '../shared/EmptyState';
import { normalizeEventPayload } from '../../utils/governanceSignals';
import { asNumber, eventTypeOf, parseTimestampMs, truncate } from '../../utils/telemetryHelpers';
import { cn } from 'lib/utils';

function estimateTokens(text) {
  if (!text) return 0;
  return Math.ceil(String(text).length / 4);
}

function looksLikeMarkdown(text) {
  if (!text) return false;
  return /^#{1,6}\s|^\*\*|^- |\[.*\]\(|^>\s|^```/m.test(text);
}

function eventSummary(event, type) {
  const payload = normalizeEventPayload(event);
  if (type === 'action_attempt') {
    const method = payload.method || payload.http_method || payload.verb || '';
    const url = payload.url || payload.path || '';
    const status = payload.status_code ?? payload.status ?? '';
    const error = payload.error_code || '';
    return [method, url, status ? `status=${status}` : '', error ? `error=${error}` : ''].filter(Boolean).join(' ');
  }
  if (type === 'heartbeat_result') {
    return [
      payload.heartbeat_status ? `status=${payload.heartbeat_status}` : '',
      payload.stop_reason ? `stop=${payload.stop_reason}` : '',
      payload.rounds_executed != null ? `rounds=${payload.rounds_executed}` : '',
      payload.elapsed_ms != null ? `${payload.elapsed_ms}ms` : '',
    ].filter(Boolean).join(' ');
  }
  if (type === 'llm_io') {
    return [
      payload.model ? `model=${payload.model}` : '',
      payload.round_index != null ? `round=${payload.round_index}` : '',
      payload.parsed_action_count != null ? `actions=${payload.parsed_action_count}` : '',
    ].filter(Boolean).join(' ');
  }
  return String(payload.summary || payload.message || payload.reason || payload.response_text || type || 'event');
}

function sectionTone(type) {
  if (type === 'action_attempt') return 'warning';
  if (type === 'llm_io') return 'healthy';
  if (type === 'heartbeat_result') return 'info';
  return 'other';
}

const STEP_COLORS = {
  observe: 'bg-purple-500',
  think: 'bg-blue-500',
  act: 'bg-amber-500',
  result: 'bg-emerald-500',
  heartbeat: 'bg-slate-500',
};

function DecisionLoopStep({ stepNumber, stepType, title, subtitle, children, defaultOpen = true }) {
  const [open, setOpen] = useState(defaultOpen);
  const dotColor = STEP_COLORS[stepType] || 'bg-muted-foreground';

  return (
    <div className="relative pl-8">
      <div className="absolute left-0 top-0 flex h-6 w-6 items-center justify-center">
        <span className={cn('h-3 w-3 rounded-full', dotColor)} />
      </div>
      {children ? (
        <div className="absolute left-[11px] top-6 bottom-0 w-px bg-border" />
      ) : null}
      <div className="pb-4">
        <button
          type="button"
          className="flex w-full items-center gap-2 text-left"
          onClick={() => setOpen((c) => !c)}
        >
          <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">{stepNumber}. {stepType}</span>
          <span className="text-sm font-medium">{title}</span>
          {subtitle ? <span className="font-mono text-[11px] text-muted-foreground">{subtitle}</span> : null}
          {children ? (
            open ? <ChevronDown className="ml-auto h-3.5 w-3.5 text-muted-foreground" /> : <ChevronRight className="ml-auto h-3.5 w-3.5 text-muted-foreground" />
          ) : null}
        </button>
        {children && open ? (
          <div className="mt-2">
            {children}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function TraceTreeNode({ node, depth = 0 }) {
  const hasChildren = Array.isArray(node.children) && node.children.length > 0;
  const [open, setOpen] = useState(depth < 2);

  return (
    <div className={cn(depth > 0 ? 'ml-4 border-l pl-3' : '')}>
      <div className="flex items-start gap-2 py-1">
        {hasChildren ? (
          <button
            type="button"
            className="mt-[2px] rounded-sm border bg-background p-0.5 text-muted-foreground hover:text-foreground"
            onClick={() => setOpen((current) => !current)}
            aria-label={`${open ? 'Collapse' : 'Expand'} ${node.label}`}
          >
            {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
          </button>
        ) : (
          <span className="mt-[7px] h-1.5 w-1.5 rounded-full bg-muted-foreground/70" />
        )}

        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium text-sm leading-5">{node.label}</span>
            {node.badge ? <Badge variant="outline" className="font-mono text-[10px]">{node.badge}</Badge> : null}
            {node.meta ? <span className="font-mono text-[11px] text-muted-foreground">{node.meta}</span> : null}
            {node.duration != null ? <span className="font-mono text-[10px] text-muted-foreground">{node.duration}ms</span> : null}
            {node.cost != null && node.cost > 0 ? <span className="font-mono text-[10px] text-muted-foreground">${node.cost.toFixed(4)}</span> : null}
          </div>
          {node.description ? (
            <p className="font-mono text-[11px] leading-4 text-muted-foreground" title={node.description}>
              {truncate(node.description, 180)}
            </p>
          ) : null}
        </div>
      </div>

      {hasChildren && open ? (
        <div className="pb-1">
          {node.children.map((child) => (
            <TraceTreeNode key={child.id} node={child} depth={depth + 1} />
          ))}
        </div>
      ) : null}
    </div>
  );
}

function buildTree({ selectedRow, tickEvents, promptParts, relatedRounds, formatNumber }) {
  if (!selectedRow) return null;

  const llmNode = {
    id: `llm-${selectedRow.key}`,
    label: 'llm-response',
    badge: Number.isFinite(selectedRow.round) ? `round ${selectedRow.round}` : 'round -',
    meta: `${formatNumber(selectedRow.tokensInput)} -> ${formatNumber(selectedRow.tokensOutput)} tokens`,
    description: selectedRow.output,
    duration: selectedRow.latencyMs,
    cost: selectedRow.costTotal,
    children: [],
  };

  const promptChildren = promptParts.map((part, idx) => ({
    id: `prompt-${idx}`,
    label: part?.name || part?.kind || 'context-part',
    badge: part?.dynamic ? 'dynamic' : 'static',
    meta: part?.sha256 ? `${String(part.sha256).slice(0, 6)}...${String(part.sha256).slice(-4)}` : '',
    description: part?.content_tail || '',
    children: [],
  }));

  const actionChildren = tickEvents
    .filter((event) => event.type === 'action_attempt')
    .map((event, index) => ({
      id: `action-${index}-${event.key}`,
      label: event.method || event.actionName || 'action-attempt',
      badge: event.status ? `status ${event.status}` : '',
      duration: event.duration,
      meta: '',
      description: event.summary,
      children: [],
    }));

  const heartbeatEvent = tickEvents.find((event) => event.type === 'heartbeat_result');

  const rootChildren = [];
  if (promptChildren.length) {
    rootChildren.push({
      id: 'prompt-group',
      label: 'context-encoding',
      badge: `${promptChildren.length} parts`,
      children: promptChildren,
    });
  }

  rootChildren.push(llmNode);

  if (actionChildren.length) {
    rootChildren.push({
      id: 'postprocessing-group',
      label: 'postprocessing',
      badge: `${actionChildren.length} actions`,
      children: actionChildren,
    });
  }

  if (heartbeatEvent) {
    rootChildren.push({
      id: 'response-sent',
      label: 'response-sent',
      badge: heartbeatEvent.status || 'heartbeat',
      duration: heartbeatEvent.duration,
      meta: '',
      description: heartbeatEvent.summary,
      children: [],
    });
  }

  return {
    id: `trace-${selectedRow.key}`,
    label: selectedRow.provider || 'trace',
    badge: `tick ${Number.isFinite(selectedRow.tick) ? selectedRow.tick : '-'}`,
    meta: `${relatedRounds.length || 1} round(s)`,
    children: rootChildren,
  };
}

const HARDCODED_METADATA_KEYS = new Set([
  'run_id', 'trace_id', 'agent_id', 'tick', 'round_index', 'model',
  'environment_name', 'environment_url', 'stop_reason', 'interaction_mode',
  'memory_mode', 'memory_turns_loaded', 'prompt_contract_version',
]);

const PREVIEW_EXCLUDED_KEYS = new Set([
  'user_message', 'input', 'response_text', 'output', 'actions',
  'system_prompt_parts', 'event_type', 'payload',
]);

function metadataRowsForPayload(payload, selectedRow) {
  const rows = [
    ['run_id', payload.run_id || selectedRow?.event?.run_id || ''],
    ['trace_id', payload.trace_id || selectedRow?.event?.trace_id || selectedRow?.key || ''],
    ['agent_id', payload.agent_id || selectedRow?.agentId || ''],
    ['heartbeat_index', payload.heartbeat_index ?? payload.heartbeatIndex ?? selectedRow?.tick ?? ''],
    ['tick', payload.tick ?? selectedRow?.tick ?? ''],
    ['round_index', payload.round_index ?? selectedRow?.round ?? ''],
    ['model', payload.model || selectedRow?.model || ''],
    ['environment_name', payload.environment_name || ''],
    ['environment_url', payload.environment_url || ''],
    ['stop_reason', payload.stop_reason || ''],
    ['interaction_mode', payload.interaction_mode || ''],
    ['memory_mode', payload.memory_mode || ''],
    ['memory_turns_loaded', payload.memory_turns_loaded ?? ''],
    ['prompt_contract_version', payload.prompt_contract_version || ''],
  ];

  // Add dynamic fields not already covered
  if (payload && typeof payload === 'object') {
    Object.entries(payload).forEach(([key, value]) => {
      if (HARDCODED_METADATA_KEYS.has(key)) return;
      if (PREVIEW_EXCLUDED_KEYS.has(key)) return;
      if (value == null) return;
      const stringValue = typeof value === 'object' ? JSON.stringify(value) : String(value);
      if (!stringValue.trim()) return;
      rows.push([key, stringValue]);
    });
  }

  return rows.filter(([, value]) => String(value || '').trim() !== '');
}

function PromptPartCollapsible({ label, badge, tokens, content, defaultMarkdown = false }) {
  const [renderMd, setRenderMd] = useState(defaultMarkdown);
  if (!content && !label) return null;
  return (
    <Collapsible defaultOpen={false} className="rounded-md border border-purple-200/60 bg-purple-50/30 dark:border-purple-800/40 dark:bg-purple-950/20">
      <CollapsibleTrigger className="flex w-full items-center gap-2 p-2.5 text-left">
        <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform duration-200 [[data-state=open]>&]:rotate-90" />
        <Badge variant="outline" className="text-[10px] border-purple-300 text-purple-700 dark:border-purple-700 dark:text-purple-400">
          {badge}
        </Badge>
        <span className="flex-1 truncate text-xs font-medium">{label}</span>
        {tokens > 0 ? <span className="shrink-0 text-[10px] text-muted-foreground">~{tokens.toLocaleString()} tokens</span> : null}
      </CollapsibleTrigger>
      <CollapsibleContent>
        <div className="px-2.5 pb-2.5">
          {content ? (
            <>
              <div className="mb-1.5 flex justify-end">
                <button
                  type="button"
                  className="text-[10px] text-muted-foreground hover:text-foreground"
                  onClick={(e) => { e.stopPropagation(); setRenderMd((v) => !v); }}
                >
                  {renderMd ? 'Raw' : 'Rendered'}
                </button>
              </div>
              {renderMd ? (
                <div className="prose prose-sm dark:prose-invert max-w-none max-h-[400px] overflow-auto rounded-md border border-border/30 bg-background/80 p-2.5 text-[11px] leading-relaxed">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
                </div>
              ) : (
                <pre className="max-h-[400px] overflow-auto whitespace-pre-wrap break-words rounded-md border border-border/30 bg-background/80 p-2.5 font-mono text-[11px] leading-relaxed">
                  {content}
                </pre>
              )}
            </>
          ) : null}
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}

export function RunTraceInspectorContent({
  selectedRow,
  relatedRounds,
  telemetryTail,
  telemetryEventType,
  telemetryTick,
  telemetryAgentId,
  promptPartBySha,
  inspectorTab,
  onInspectorTabChange,
  onSelectRoundRow,
  formatDateTime,
  formatNumber,
}) {
  const selectedPayload = normalizeEventPayload(selectedRow?.event);
  const selectedPromptParts = Array.isArray(selectedPayload.system_prompt_parts)
    ? selectedPayload.system_prompt_parts
    : [];

  const tickEvents = useMemo(() => {
    if (!selectedRow || !Number.isFinite(selectedRow.tick)) return [];

    const rows = telemetryTail
      .filter((event) => {
        if (telemetryAgentId(event) !== selectedRow.agentId) return false;
        const tick = telemetryTick(event);
        return Number.isFinite(tick) && tick === selectedRow.tick;
      })
      .map((event, index) => {
        const payload = normalizeEventPayload(event);
        const type = eventTypeOf(event, telemetryEventType);
        const method = String(payload.method || payload.http_method || payload.verb || '').trim();
        const status = payload.status_code ?? payload.status ?? '';
        const duration = asNumber(payload.elapsed_ms);
        const cost = asNumber(payload.llm_cost_usd ?? payload.cost_usd ?? payload.total_cost_usd);
        return {
          key: String(event?.event_id || event?.id || `${event?.timestamp || ''}-${index}`),
          event,
          type,
          timestamp: event?.timestamp,
          method,
          status,
          duration,
          cost,
          actionName: String(payload.action_name || payload.action_type || type),
          summary: eventSummary(event, type),
        };
      })
      .sort((a, b) => parseTimestampMs(a.timestamp) - parseTimestampMs(b.timestamp));

    return rows;
  }, [selectedRow, telemetryAgentId, telemetryEventType, telemetryTail, telemetryTick]);

  const actionEvents = useMemo(() => tickEvents.filter((e) => e.type === 'action_attempt'), [tickEvents]);
  const heartbeatEvent = useMemo(() => tickEvents.find((e) => e.type === 'heartbeat_result'), [tickEvents]);
  const heartbeatPayload = heartbeatEvent ? normalizeEventPayload(heartbeatEvent.event) : null;

  const treeRoot = useMemo(
    () => buildTree({ selectedRow, tickEvents, promptParts: selectedPromptParts, relatedRounds, formatNumber }),
    [formatNumber, relatedRounds, selectedPromptParts, selectedRow, tickEvents]
  );

  const metadataRows = useMemo(() => metadataRowsForPayload(selectedPayload, selectedRow), [selectedPayload, selectedRow]);

  if (!selectedRow) {
    return <EmptyState description="Select a trace row to inspect details." />;
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="secondary" className="text-xs font-semibold">{selectedRow.agentId || 'agent'}</Badge>
        <Badge variant="secondary">{selectedRow.provider}</Badge>
        <Badge variant="outline">tick {Number.isFinite(selectedRow.tick) ? selectedRow.tick : '-'}</Badge>
        <Badge variant="outline">round {Number.isFinite(selectedRow.round) ? selectedRow.round : '-'}</Badge>
        <Badge variant="outline">${formatNumber(selectedRow.costTotal)}</Badge>
        {Number.isFinite(selectedRow.latencyMs) ? (
          <Badge variant="outline">{formatNumber(selectedRow.latencyMs)} ms</Badge>
        ) : null}
        <Badge variant="outline">
          {formatNumber(selectedRow.tokensInput)} / {formatNumber(selectedRow.tokensOutput)} tokens
        </Badge>
      </div>

      {relatedRounds.length > 1 ? (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-[10px] uppercase tracking-wide text-muted-foreground">Round:</span>
          {relatedRounds.map((row) => (
            <Button
              key={row.key}
              type="button"
              size="sm"
              variant={row.key === selectedRow.key ? 'default' : 'outline'}
              className="h-6 px-2 text-[11px]"
              onClick={() => onSelectRoundRow(row.key)}
            >
              {Number.isFinite(row.round) ? row.round : '-'}
            </Button>
          ))}
        </div>
      ) : null}

      <Tabs value={inspectorTab} onValueChange={onInspectorTabChange} className="w-full">
        <TabsList>
          <TabsTrigger value="preview">Preview</TabsTrigger>
          <TabsTrigger value="log">Log View</TabsTrigger>
          <TabsTrigger value="metadata">Metadata</TabsTrigger>
          <TabsTrigger value="json">JSON</TabsTrigger>
        </TabsList>

        <TabsContent value="preview" className="space-y-1 pt-3">
          <DecisionLoopStep
            stepNumber={1}
            stepType="observe"
            title="Context Assembly"
            subtitle={selectedPromptParts.length ? `${selectedPromptParts.length} prompt parts` : 'input'}
          >
            {selectedPromptParts.length ? (
              <div className="space-y-1.5">
                {selectedPromptParts.map((part, index) => {
                  const sha = String(part?.sha256 || '');
                  const staticPart = sha && promptPartBySha?.get ? promptPartBySha.get(sha) : null;
                  const staticPayload = normalizeEventPayload(staticPart);
                  const partContent = part?.dynamic && part?.content_tail
                    ? String(part.content_tail)
                    : staticPayload.content_preview || staticPayload.content
                      ? String(staticPayload.content_preview ?? staticPayload.content)
                      : null;
                  const tokens = estimateTokens(partContent);
                  const isMd = looksLikeMarkdown(partContent);
                  return (
                    <PromptPartCollapsible
                      key={sha || index}
                      label={part?.name || part?.kind || 'prompt_part'}
                      badge={part?.dynamic ? 'dynamic' : 'static'}
                      tokens={tokens}
                      content={partContent}
                      defaultMarkdown={isMd}
                    />
                  );
                })}
              </div>
            ) : null}
            <div className="rounded-md border border-blue-200/60 bg-blue-50/30 dark:border-blue-800/40 dark:bg-blue-950/20 p-2.5">
              <div className="flex items-center gap-2 mb-2">
                <Badge variant="outline" className="text-[10px] border-blue-300 text-blue-700 dark:border-blue-700 dark:text-blue-400">input</Badge>
                <span className="text-xs font-medium">User message</span>
              </div>
              <pre className="max-h-[300px] overflow-auto whitespace-pre-wrap break-words rounded-md border border-border/30 bg-background/80 p-2.5 font-mono text-[11px] leading-relaxed">
                {selectedPayload.user_message || selectedPayload.input || '-'}
              </pre>
            </div>
          </DecisionLoopStep>

          <DecisionLoopStep
            stepNumber={2}
            stepType="think"
            title="LLM Inference"
            subtitle={[
              selectedRow.model,
              `${formatNumber(selectedRow.tokensInput)} in / ${formatNumber(selectedRow.tokensOutput)} out`,
              `$${formatNumber(selectedRow.costTotal)}`,
              Number.isFinite(selectedRow.latencyMs) ? `${formatNumber(selectedRow.latencyMs)}ms` : '',
            ].filter(Boolean).join(' · ')}
          >
            <div className="rounded-md border border-emerald-200/60 bg-emerald-50/30 dark:border-emerald-800/40 dark:bg-emerald-950/20 p-2.5">
              <div className="flex items-center gap-2 mb-2">
                <Badge variant="outline" className="text-[10px] border-emerald-300 text-emerald-700 dark:border-emerald-700 dark:text-emerald-400">output</Badge>
                <span className="text-xs font-medium">Response</span>
              </div>
              <pre className="max-h-[400px] overflow-auto whitespace-pre-wrap break-words rounded-md border border-border/30 bg-background/80 p-2.5 font-mono text-[11px] leading-relaxed">
                {selectedPayload.response_text || selectedPayload.output || '-'}
              </pre>
            </div>
          </DecisionLoopStep>

          {(selectedPayload.actions && (Array.isArray(selectedPayload.actions) ? selectedPayload.actions.length : true)) || actionEvents.length ? (
            <DecisionLoopStep
              stepNumber={3}
              stepType="act"
              title="Parsed Actions"
              subtitle={actionEvents.length ? `${actionEvents.length} action(s)` : ''}
            >
              <CollapsibleCodeBlock
                title="Actions"
                value={selectedPayload.actions || []}
                defaultOpen
                preClassName="rounded-md border border-border/30 bg-background/80 p-2.5 text-[11px] leading-relaxed"
              />
            </DecisionLoopStep>
          ) : null}

          {actionEvents.length ? (
            <DecisionLoopStep
              stepNumber={4}
              stepType="result"
              title="Action Outcomes"
              subtitle={`${actionEvents.length} attempt(s)`}
            >
              <div className="space-y-1.5">
                {actionEvents.map((ae) => (
                  <div key={ae.key} className="flex items-center gap-2 rounded-md border border-border/40 bg-background/60 p-2 font-mono text-[11px]">
                    <Badge variant="outline" className="text-[10px]">{ae.method || 'action'}</Badge>
                    <span className="min-w-0 flex-1 truncate text-muted-foreground">{ae.summary}</span>
                    {ae.status ? <Badge variant="outline" className="text-[10px]">status {ae.status}</Badge> : null}
                    {ae.duration != null ? <span className="text-muted-foreground">{ae.duration}ms</span> : null}
                  </div>
                ))}
              </div>
            </DecisionLoopStep>
          ) : null}

          {heartbeatEvent ? (
            <DecisionLoopStep
              stepNumber={actionEvents.length ? 5 : 3}
              stepType="heartbeat"
              title="Heartbeat Resolution"
              subtitle={[
                heartbeatPayload?.stop_reason ? `stop=${heartbeatPayload.stop_reason}` : '',
                heartbeatPayload?.rounds_executed != null ? `rounds=${heartbeatPayload.rounds_executed}` : '',
                heartbeatEvent.duration != null ? `${heartbeatEvent.duration}ms` : '',
              ].filter(Boolean).join(' · ')}
              defaultOpen={false}
            >
              <pre className="whitespace-pre-wrap break-words rounded border border-border/40 bg-muted/20 p-2 font-mono text-[11px]">
                {heartbeatEvent.summary}
              </pre>
            </DecisionLoopStep>
          ) : null}
        </TabsContent>

        <TabsContent value="log" className="space-y-3 pt-3">
          <section className="rounded-xl border border-border/50 bg-muted/5 p-4">
            <h4 className="text-sm font-medium">Agent Trace Tree (tick-level)</h4>
            <div className="mt-3">
              {treeRoot ? <TraceTreeNode node={treeRoot} /> : <EmptyState description="No tree data for selected trace." />}
            </div>
          </section>

          <section className="rounded-xl border border-border/50 bg-muted/5 p-4">
            <h4 className="text-sm font-medium">Timeline Events</h4>
            <div className="mt-3">
              {tickEvents.length ? (
                <ScrollArea className="max-h-[320px] rounded-md border border-border/40">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead className="w-[170px]">Time</TableHead>
                        <TableHead className="w-[160px]">Type</TableHead>
                        <TableHead>Summary</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {tickEvents.map((row) => (
                        <TableRow key={row.key}>
                          <TableCell className="font-mono text-xs">{formatDateTime(row.timestamp)}</TableCell>
                          <TableCell>
                            <Badge variant="outline" className="text-[10px]">
                              {eventTypeOf(row.event, telemetryEventType)}
                            </Badge>
                          </TableCell>
                          <TableCell className="font-mono text-xs text-muted-foreground" title={row.summary}>
                            {truncate(row.summary, 220)}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </ScrollArea>
              ) : (
                <EmptyState description="No timeline events for selected tick." />
              )}
            </div>
          </section>
        </TabsContent>

        <TabsContent value="metadata" className="space-y-3 pt-3">
          <section className="rounded-xl border border-border/50 bg-muted/5 p-4">
            <h4 className="text-sm font-medium">Metadata</h4>
            <div className="mt-3">
              {metadataRows.length ? (
                <div className="rounded-md border border-border/40">
                  <Table className="table-fixed">
                    <TableHeader>
                      <TableRow>
                        <TableHead className="w-[200px]">Path</TableHead>
                        <TableHead>Value</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {metadataRows.map(([path, value]) => (
                        <TableRow key={path}>
                          <TableCell className="align-top font-mono text-xs break-all">{path}</TableCell>
                          <TableCell className="whitespace-pre-wrap break-words font-mono text-xs">{String(value)}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              ) : (
                <EmptyState description="No metadata fields available for selected trace." />
              )}
            </div>
          </section>
        </TabsContent>

        <TabsContent value="json" className="space-y-4 pt-3">
          <CollapsibleCodeBlock title="Raw payload" value={selectedPayload} defaultOpen preClassName="max-h-[420px]" />
          <CollapsibleCodeBlock title="Raw event" value={selectedRow.event} preClassName="max-h-[420px]" />
        </TabsContent>
      </Tabs>

      {tickEvents.length ? (
        <div className="flex flex-wrap gap-2">
          {Array.from(new Set(tickEvents.map((event) => event.type))).map((type) => (
            <Badge key={type} variant="secondary" className="font-mono text-[10px]" data-tone={sectionTone(type)}>
              {type}
            </Badge>
          ))}
        </div>
      ) : null}
    </div>
  );
}
