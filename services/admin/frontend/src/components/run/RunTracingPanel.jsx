import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { ArrowDown, ArrowUp, ArrowUpDown, ChevronDown, ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight, Eye, ExternalLink, Filter, FilterX, GripVertical, RefreshCw, X } from 'lucide-react';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { Checkbox } from '../ui/checkbox';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '../ui/collapsible';
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from '../ui/dropdown-menu';
import { Input } from '../ui/input';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../ui/table';
import { EmptyState } from '../shared/EmptyState';
import { SelectField } from '../shared/SelectField';
import {
  normalizeEventPayload,
} from '../../utils/governanceSignals';
import { asNumber, eventTypeOf, modelOf, truncate } from '../../utils/telemetryHelpers';
import { cn } from 'lib/utils';
import { RunTraceInspectorContent } from './RunTraceInspectorContent';

const QUERY_KEYS = {
  trace: 'trace',
  panel: 'panel',
  tab: 'tab',
  search: 'obs_q',
  idMode: 'obs_idmode',
  interval: 'obs_interval',
  window: 'obs_window',
  stream: 'obs_stream',
  agentList: 'obs_agents',
  agentText: 'obs_agent',
  modelList: 'obs_models',
  typeList: 'obs_types',
  stopList: 'obs_stops',
  tickMin: 'obs_tick_min',
  tickMax: 'obs_tick_max',
  costMin: 'obs_cost_min',
  costMax: 'obs_cost_max',
  round: 'obs_round',
  group: 'obs_group',
  page: 'obs_page',
  rows: 'obs_rows',
  rowHeight: 'obs_row_height',
  sort: 'obs_sort',
};

const COLUMN_CONFIG = [
  { key: 'timestamp', label: 'Timestamp' },
  { key: 'traceId', label: 'Trace ID' },
  { key: 'eventId', label: 'Event ID' },
  { key: 'name', label: 'Provider' },
  { key: 'eventType', label: 'Type' },
  { key: 'model', label: 'Model' },
  { key: 'agent', label: 'Agent' },
  { key: 'sessionId', label: 'Session ID' },
  { key: 'userId', label: 'User ID' },
  { key: 'environment', label: 'Environment' },
  { key: 'level', label: 'Level' },
  { key: 'tick', label: 'Heartbeat' },
  { key: 'round', label: 'Round' },
  { key: 'latency', label: 'Latency' },
  { key: 'status', label: 'Status' },
  { key: 'stopReason', label: 'Stop reason' },
  { key: 'version', label: 'Version' },
  { key: 'tags', label: 'Tags' },
  { key: 'input', label: 'Input' },
  { key: 'output', label: 'Output' },
  { key: 'actions', label: 'Actions' },
  { key: 'cost', label: 'Cost' },
  { key: 'tokens', label: 'Tokens' },
];
const DEFAULT_COLUMN_ORDER = COLUMN_CONFIG.map((column) => column.key);

const DEFAULT_VISIBLE_COLUMNS = {
  timestamp: true,
  traceId: false,
  eventId: false,
  name: false,
  eventType: true,
  model: true,
  agent: true,
  sessionId: false,
  userId: false,
  environment: false,
  level: false,
  tick: true,
  round: true,
  latency: true,
  status: false,
  stopReason: false,
  version: false,
  tags: false,
  input: true,
  output: true,
  actions: false,
  cost: true,
  tokens: true,
};

const VALID_INSPECTOR_TABS = new Set(['preview', 'log', 'metadata', 'json']);
const VALID_ROUND_FILTERS = new Set(['all', 'round0', 'round1plus']);
const VALID_GROUP_MODES = new Set(['flat', 'tick']);
const VALID_ROW_HEIGHTS = new Set(['small', 'medium', 'large']);
const SORTABLE_KEYS = new Set(['timestamp', 'name', 'agent', 'eventType', 'model', 'tick', 'cost']);
const SUPPORTED_DRILL_FILTERS = new Set(['cost']);
const VALID_ID_MODES = new Set(['ids_names', 'ids_only', 'names_only']);
const VALID_INTERVALS = new Set(['1h', '12h', '1d', '7d', '30d']);
const VALID_WINDOWS = new Set(['past_1h', 'past_12h', 'past_1d', 'past_7d', 'past_30d']);
const VALID_STREAM_MODES = new Set(['off', 'live']);
const COLUMN_WIDTH_BOUNDS = {
  selector: { min: 38, max: 38, default: 38 },
  timestamp: { min: 140, max: 220, default: 160 },
  traceId: { min: 120, max: 300, default: 160 },
  eventId: { min: 120, max: 300, default: 160 },
  name: { min: 100, max: 260, default: 140 },
  eventType: { min: 80, max: 180, default: 100 },
  model: { min: 100, max: 280, default: 140 },
  agent: { min: 80, max: 220, default: 110 },
  sessionId: { min: 100, max: 260, default: 140 },
  userId: { min: 100, max: 260, default: 140 },
  environment: { min: 80, max: 200, default: 120 },
  level: { min: 48, max: 120, default: 60 },
  tick: { min: 72, max: 140, default: 88 },
  round: { min: 48, max: 120, default: 56 },
  latency: { min: 64, max: 160, default: 84 },
  status: { min: 56, max: 160, default: 80 },
  stopReason: { min: 100, max: 260, default: 140 },
  version: { min: 80, max: 240, default: 120 },
  tags: { min: 100, max: 260, default: 140 },
  input: { min: 160, max: 600, default: 260 },
  output: { min: 160, max: 600, default: 260 },
  actions: { min: 48, max: 120, default: 56 },
  cost: { min: 56, max: 140, default: 72 },
  tokens: { min: 80, max: 200, default: 110 },
};
const FILTER_WIDTH_BOUNDS = { min: 200, max: 420, default: 252 };
const INSPECTOR_WIDTH_BOUNDS = { min: 420, max: 1320, default: 560 };
const ROW_HEIGHT_CONFIG = {
  small: {
    rowClassName: 'h-8',
    cellClassName: 'py-1',
    bodyTextClassName: 'text-[11px]',
    previewLineClassName: 'leading-4',
    previewLines: 1,
    previewCharsPerLine: 110,
  },
  medium: {
    rowClassName: 'h-12',
    cellClassName: 'py-2',
    bodyTextClassName: 'text-[11px]',
    previewLineClassName: 'leading-4',
    previewLines: 2,
    previewCharsPerLine: 150,
  },
  large: {
    rowClassName: 'h-[74px]',
    cellClassName: 'py-3',
    bodyTextClassName: 'text-[11px]',
    previewLineClassName: 'leading-4',
    previewLines: 4,
    previewCharsPerLine: 170,
  },
};

function normalizeIdMode(value) {
  const normalized = String(value || '').trim().toLowerCase();
  return VALID_ID_MODES.has(normalized) ? normalized : 'ids_names';
}

function normalizeInterval(value) {
  const normalized = String(value || '').trim().toLowerCase();
  return VALID_INTERVALS.has(normalized) ? normalized : '1d';
}

function normalizeWindow(value) {
  const normalized = String(value || '').trim().toLowerCase();
  return VALID_WINDOWS.has(normalized) ? normalized : 'past_1d';
}

function normalizeStream(value) {
  const normalized = String(value || '').trim().toLowerCase();
  return VALID_STREAM_MODES.has(normalized) ? normalized : 'off';
}

function normalizeRowHeight(value) {
  const normalized = String(value || '').trim().toLowerCase();
  return VALID_ROW_HEIGHTS.has(normalized) ? normalized : 'medium';
}

function normalizeGroupMode(value) {
  const normalized = String(value || '').trim().toLowerCase();
  return VALID_GROUP_MODES.has(normalized) ? normalized : 'flat';
}

function readableEventType(raw) {
  switch (raw) {
    case 'llm_io': return 'LLM Call';
    case 'action_attempt': return 'Action';
    case 'heartbeat_result': return 'Heartbeat';
    case 'prompt_part': return 'Prompt Part';
    default: return raw || 'Event';
  }
}

const EVENT_TYPE_BADGE_CLASS = {
  llm_io: 'border-blue-300 text-blue-700 dark:border-blue-700 dark:text-blue-400',
  action_attempt: 'border-amber-300 text-amber-700 dark:border-amber-700 dark:text-amber-400',
  heartbeat_result: 'border-emerald-300 text-emerald-700 dark:border-emerald-700 dark:text-emerald-400',
  prompt_part: 'border-purple-300 text-purple-700 dark:border-purple-700 dark:text-purple-400',
};

function parseRoundIndex(event) {
  const payload = normalizeEventPayload(event);
  const parsed = Number.parseInt(String(payload.round_index ?? ''), 10);
  return Number.isFinite(parsed) ? parsed : null;
}

function stopReasonOf(event) {
  const payload = normalizeEventPayload(event);
  const value = String(
    payload.stop_reason
    || payload.heartbeat_stop_reason
    || payload.parent_stop_reason
    || payload.termination_reason
    || ''
  ).trim();
  return value || 'unknown';
}

function normalizeDrillFilter(value) {
  const normalized = String(value || '').trim().toLowerCase();
  return SUPPORTED_DRILL_FILTERS.has(normalized) ? normalized : '';
}

function hasCostRelatedSignal(event, payload = normalizeEventPayload(event)) {
  const numericCostFields = [
    event?.llm_cost_usd,
    event?.total_cost_usd,
    payload?.llm_cost_usd,
    payload?.total_cost_usd,
    payload?.cost_usd,
    payload?.cost_total_usd,
    payload?.cost,
  ];

  if (numericCostFields.some((value) => (asNumber(value) ?? 0) > 0)) return true;

  const tokenFields = [
    event?.llm_tokens_input,
    event?.llm_tokens_output,
    event?.tokens_input,
    event?.tokens_output,
    payload?.llm_tokens_input,
    payload?.llm_tokens_output,
    payload?.tokens_input,
    payload?.tokens_output,
  ];
  if (tokenFields.some((value) => (asNumber(value) ?? 0) > 0)) return true;

  const eventType = String(event?.event_type || payload?.event_type || event?.type || '').toLowerCase();
  return eventType.includes('cost') || eventType === 'llm_io';
}

function hasUsageOrPricingMetadata(event) {
  const payload = normalizeEventPayload(event);
  const usage = payload?.usage && typeof payload.usage === 'object' ? payload.usage : {};

  const positiveSignals = [
    event?.llm_cost_usd,
    event?.total_cost_usd,
    event?.llm_tokens_input,
    event?.llm_tokens_output,
    payload?.llm_cost_usd,
    payload?.total_cost_usd,
    payload?.cost_usd,
    payload?.cost_total_usd,
    payload?.cost,
    payload?.llm_tokens_input,
    payload?.llm_tokens_output,
    payload?.tokens_input,
    payload?.tokens_output,
    usage?.total_cost,
    usage?.total_cost_usd,
    usage?.prompt_tokens,
    usage?.completion_tokens,
    usage?.input_tokens,
    usage?.output_tokens,
    usage?.total_tokens,
  ];

  if (positiveSignals.some((value) => (asNumber(value) ?? 0) > 0)) return true;
  if (usage?.has_usage === true) return true;
  return false;
}

function matchesDrillFilter(event, drill) {
  if (!drill) return true;
  if (drill !== 'cost') return true;
  const payload = normalizeEventPayload(event);
  return hasCostRelatedSignal(event, payload);
}

function inputOf(event) {
  const payload = normalizeEventPayload(event);
  return String(payload.user_message || payload.input || '').trim();
}

function outputOf(event) {
  const payload = normalizeEventPayload(event);
  const raw = payload.response_text || payload.output || payload.result || payload.message || payload.summary || '';
  if (typeof raw === 'object') {
    try { return JSON.stringify(raw, null, 2); } catch { return String(raw); }
  }
  return String(raw).trim();
}

function textField(...values) {
  for (const value of values) {
    const text = String(value || '').trim();
    if (text) return text;
  }
  return '';
}

function tagsOf(payload, event) {
  const raw = payload?.tags ?? event?.tags ?? payload?.labels ?? event?.labels;
  if (Array.isArray(raw)) {
    return raw.map((entry) => String(entry || '').trim()).filter(Boolean).join(', ');
  }
  return String(raw || '').trim();
}

function tickOf(telemetryTick, event) {
  const payload = normalizeEventPayload(event);
  const heartbeatRaw = payload.heartbeat_index ?? payload.heartbeatIndex;
  const heartbeatParsed = Number.parseInt(String(heartbeatRaw ?? ''), 10);
  if (Number.isFinite(heartbeatParsed)) return heartbeatParsed;
  const value = telemetryTick(event);
  return Number.isFinite(value) ? value : null;
}

function providerName(model) {
  const lower = String(model || '').toLowerCase();
  if (lower.includes('openrouter')) return 'OpenRouter Request';
  if (lower.includes('openai') || lower.includes('gpt')) return 'OpenAI Request';
  if (lower.includes('anthropic') || lower.includes('claude')) return 'Anthropic Request';
  if (lower.includes('dummy')) return 'Dummy Request';
  if (!model || model === '-') return 'LLM Request';
  return String(model).slice(0, 20);
}

function rowNameOf(eventType, model) {
  if (eventType === 'llm_io') return providerName(model);
  if (eventType === 'action_attempt') return 'Action Attempt';
  if (eventType === 'heartbeat_result') return 'Heartbeat';
  if (eventType === 'prompt_part') return 'Prompt Part';
  return eventType || 'Event';
}

function includesValue(list, value) {
  return Array.isArray(list) && list.includes(value);
}

function toggleValue(list, value) {
  const current = Array.isArray(list) ? list : [];
  return includesValue(current, value)
    ? current.filter((entry) => entry !== value)
    : current.concat(value);
}

function parseCsv(value) {
  return String(value || '')
    .split(',')
    .map((entry) => entry.trim())
    .filter(Boolean);
}

function joinCsv(values) {
  return Array.from(new Set((Array.isArray(values) ? values : []).map((entry) => String(entry || '').trim()).filter(Boolean)))
    .sort((a, b) => a.localeCompare(b))
    .join(',');
}

function parsePage(value, fallback = 1) {
  const parsed = Number.parseInt(String(value || ''), 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function parseRowsPerPage(value) {
  const parsed = Number.parseInt(String(value || ''), 10);
  if (!Number.isFinite(parsed)) return 50;
  if (parsed <= 50) return 50;
  if (parsed <= 100) return 100;
  return 500;
}

function parseSort(value) {
  const [keyRaw = 'timestamp', directionRaw = 'desc'] = String(value || '').split(':');
  const key = SORTABLE_KEYS.has(keyRaw) ? keyRaw : 'timestamp';
  const direction = directionRaw === 'asc' ? 'asc' : 'desc';
  return { key, direction };
}

function previewText(value, rowHeight = 'medium') {
  const text = String(value || '').trim();
  if (!text) return '-';
  const config = ROW_HEIGHT_CONFIG[normalizeRowHeight(rowHeight)] || ROW_HEIGHT_CONFIG.medium;
  const sourceLines = text
    .replace(/\r\n/g, '\n')
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean);

  if (!sourceLines.length) return '-';

  const clipped = sourceLines
    .slice(0, config.previewLines)
    .map((line) => (line.length > config.previewCharsPerLine ? `${line.slice(0, config.previewCharsPerLine)}...` : line));

  const hiddenLines = sourceLines.length > config.previewLines;
  return hiddenLines ? `${clipped.join('\n')}\n...` : clipped.join('\n');
}

function serializeSort(key, direction) {
  return `${SORTABLE_KEYS.has(key) ? key : 'timestamp'}:${direction === 'asc' ? 'asc' : 'desc'}`;
}

function columnBoundsFor(columnKey) {
  return COLUMN_WIDTH_BOUNDS[columnKey] || { min: 100, max: 360, default: 160 };
}

function sortValueForRow(row, sortKey) {
  if (!row) return null;
  switch (sortKey) {
    case 'timestamp': return row.timestamp;
    case 'traceId': return row.traceId || row.traceKey;
    case 'eventId': return row.eventId;
    case 'name': return row.provider;
    case 'eventType': return row.eventType;
    case 'model': return row.model;
    case 'agent': return row.agentId;
    case 'sessionId': return row.sessionId;
    case 'userId': return row.userId;
    case 'environment': return row.environment;
    case 'level': return row.level;
    case 'tick': return row.tick;
    case 'round': return row.round;
    case 'latency': return row.latencyMs;
    case 'status': return asNumber(row.statusCode) ?? row.statusCode;
    case 'stopReason': return row.stopReason;
    case 'version': return row.version;
    case 'tags': return row.tags;
    case 'input': return row.input;
    case 'output': return row.output;
    case 'actions': return row.actionCount;
    case 'cost': return row.costTotal;
    case 'tokens': return (Number(row.tokensInput || 0) + Number(row.tokensOutput || 0));
    default: return row.timestamp;
  }
}

function compareValues(a, b) {
  if (a == null && b == null) return 0;
  if (a == null) return 1;
  if (b == null) return -1;
  if (typeof a === 'number' && typeof b === 'number') return a - b;
  return String(a).localeCompare(String(b));
}

function readInitialQueryState(search) {
  const params = new URLSearchParams(search);
  const parsedSort = parseSort(params.get(QUERY_KEYS.sort));
  return {
    selectedEventId: String(params.get(QUERY_KEYS.trace) || '').trim(),
    inspectorOpen: params.get(QUERY_KEYS.panel) === '1',
    inspectorTab: VALID_INSPECTOR_TABS.has(params.get(QUERY_KEYS.tab)) ? params.get(QUERY_KEYS.tab) : 'preview',
    searchText: String(params.get(QUERY_KEYS.search) || '').trim(),
    idMode: normalizeIdMode(params.get(QUERY_KEYS.idMode)),
    timeInterval: normalizeInterval(params.get(QUERY_KEYS.interval)),
    timeWindow: normalizeWindow(params.get(QUERY_KEYS.window)),
    streamMode: normalizeStream(params.get(QUERY_KEYS.stream)),
    selectedAgents: parseCsv(params.get(QUERY_KEYS.agentList)),
    agentSearch: String(params.get(QUERY_KEYS.agentText) || '').trim(),
    selectedModels: parseCsv(params.get(QUERY_KEYS.modelList)),
    selectedTypes: parseCsv(params.get(QUERY_KEYS.typeList)),
    selectedStopReasons: parseCsv(params.get(QUERY_KEYS.stopList)),
    tickMin: String(params.get(QUERY_KEYS.tickMin) || '').trim(),
    tickMax: String(params.get(QUERY_KEYS.tickMax) || '').trim(),
    costMin: String(params.get(QUERY_KEYS.costMin) || '').trim(),
    costMax: String(params.get(QUERY_KEYS.costMax) || '').trim(),
    roundFilter: VALID_ROUND_FILTERS.has(params.get(QUERY_KEYS.round)) ? params.get(QUERY_KEYS.round) : 'all',
    groupMode: normalizeGroupMode(params.get(QUERY_KEYS.group)),
    page: parsePage(params.get(QUERY_KEYS.page), 1),
    rowsPerPage: parseRowsPerPage(params.get(QUERY_KEYS.rows)),
    rowHeight: normalizeRowHeight(params.get(QUERY_KEYS.rowHeight)),
    sortKey: parsedSort.key,
    sortDirection: parsedSort.direction,
  };
}

function setOrDeleteParam(params, key, value, defaultValue = '') {
  const text = String(value || '').trim();
  if (!text || text === String(defaultValue || '')) {
    params.delete(key);
  } else {
    params.set(key, text);
  }
}

function formatTracingDateTime(value) {
  if (!value) return 'N/A';
  const text = String(value).trim();
  if (!text) return 'N/A';
  const hasTimezone = /(?:Z|[+\-]\d{2}:\d{2})$/i.test(text);
  const date = new Date(hasTimezone ? text : `${text}Z`);
  if (Number.isNaN(date.getTime())) return 'N/A';
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Europe/Rome',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).formatToParts(date);
  const read = (name) => parts.find((part) => part.type === name)?.value || '';
  return `${read('year')}-${read('month')}-${read('day')} ${read('hour')}:${read('minute')}:${read('second')}`;
}

function SortableHead({
  label,
  sortKey,
  currentSortKey,
  currentDirection,
  onToggle,
  onResizeStart,
  sortable = true,
  className = '',
  style,
}) {
  const isSortable = sortable && SORTABLE_KEYS.has(sortKey);
  const isActive = isSortable && currentSortKey === sortKey;
  return (
    <TableHead className={`group relative transition-colors hover:bg-muted/40 ${className}`} style={style}>
      <div className="relative flex items-center pr-2">
        {isSortable ? (
          <button
            type="button"
            className="inline-flex w-full items-center gap-1 text-left text-[12px] font-semibold text-muted-foreground hover:text-foreground"
            onClick={() => onToggle(sortKey)}
          >
            <span>{label}</span>
            <ArrowUpDown className="h-3.5 w-3.5" />
            {isActive ? <span className="font-mono text-[9px]">{currentDirection === 'asc' ? 'ASC' : 'DESC'}</span> : null}
          </button>
        ) : (
          <span className="inline-flex w-full items-center text-[12px] font-semibold text-muted-foreground">{label}</span>
        )}
        {onResizeStart ? (
          <span
            role="separator"
            aria-orientation="vertical"
            className="absolute -right-2 top-0 z-10 flex h-full w-4 cursor-col-resize select-none touch-none items-center justify-center"
            title="Drag to resize column"
            onClick={(event) => event.stopPropagation()}
            onPointerDown={(event) => onResizeStart(event, sortKey)}
          >
            <span className="h-5 w-0.5 rounded-full bg-border transition-colors group-hover:bg-foreground/60" />
          </span>
        ) : null}
      </div>
    </TableHead>
  );
}

export function RunTracingPanel({
  telemetryTail,
  historyEvents = [],
  searchFilters,
  telemetryAgentOptions,
  telemetryEventType,
  telemetryTick,
  telemetryAgentId,
  extractCostUsd,
  extractTokens,
  promptPartBySha,
  fullPageTraceId = '',
  runBasePath = '',
  runPathQuery = '',
  formatDateTime,
  formatNumber,
  onRefresh,
}) {
  const navigate = useNavigate();
  const location = useLocation();
  const initializedRef = useRef(false);
  const lastSyncedSearchRef = useRef('');
  const lastAppliedRouteTraceRef = useRef('');
  const resizeStateRef = useRef(null);
  const filterResizeStateRef = useRef(null);
  const inspectorResizeStateRef = useRef(null);
  const contentLayoutRef = useRef(null);
  const legacyDrill = useMemo(() => normalizeDrillFilter(searchFilters?.drill), [searchFilters?.drill]);
  const legacyType = useMemo(() => String(searchFilters?.type || '').trim(), [searchFilters?.type]);

  const [showFilters, setShowFilters] = useState(true);
  const [filterWidth, setFilterWidth] = useState(FILTER_WIDTH_BOUNDS.default);
  const [selectedAgents, setSelectedAgents] = useState([]);
  const [agentSearch, setAgentSearch] = useState('');
  const [selectedModels, setSelectedModels] = useState([]);
  const [selectedTypes, setSelectedTypes] = useState([]);
  const [selectedStopReasons, setSelectedStopReasons] = useState([]);
  const [tickMin, setTickMin] = useState('');
  const [tickMax, setTickMax] = useState('');
  const [costMin, setCostMin] = useState('');
  const [costMax, setCostMax] = useState('');
  const [roundFilter, setRoundFilter] = useState('all');
  const [groupMode, setGroupMode] = useState('flat');
  const [page, setPage] = useState(1);
  const [rowsPerPage, setRowsPerPage] = useState(50);
  const [rowHeight, setRowHeight] = useState('medium');
  const [selectedEventId, setSelectedEventId] = useState('');
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [inspectorWidth, setInspectorWidth] = useState(INSPECTOR_WIDTH_BOUNDS.default);
  const [inspectorTab, setInspectorTab] = useState('preview');
  const [searchText, setSearchText] = useState('');
  const [idMode, setIdMode] = useState('ids_names');
  const [timeInterval, setTimeInterval] = useState('1d');
  const [timeWindow, setTimeWindow] = useState('past_1d');
  const [streamMode, setStreamMode] = useState('off');
  const [visibleColumns, setVisibleColumns] = useState(DEFAULT_VISIBLE_COLUMNS);
  const [sortKey, setSortKey] = useState('timestamp');
  const [sortDirection, setSortDirection] = useState('desc');
  const [collapsedTickGroups, setCollapsedTickGroups] = useState(() => new Set());
  const [selectedRowKeys, setSelectedRowKeys] = useState(() => new Set());
  const [columnWidths, setColumnWidths] = useState(() => ({}));
  const [columnOrder, setColumnOrder] = useState(DEFAULT_COLUMN_ORDER);
  const normalizedFullPageTraceId = String(fullPageTraceId || '').trim();
  const isFullPageDetail = Boolean(normalizedFullPageTraceId);

  const activeFilterCount = useMemo(() => {
    let count = 0;
    if (selectedTypes.length) count += 1;
    if (selectedAgents.length || agentSearch.trim()) count += 1;
    if (selectedModels.length) count += 1;
    if (tickMin.trim() || tickMax.trim()) count += 1;
    if (roundFilter !== 'all') count += 1;
    if (costMin.trim() || costMax.trim()) count += 1;
    if (selectedStopReasons.length) count += 1;
    return count;
  }, [selectedTypes, selectedAgents, agentSearch, selectedModels, tickMin, tickMax, roundFilter, costMin, costMax, selectedStopReasons]);

  useEffect(() => {
    if (initializedRef.current) return;
    const initial = readInitialQueryState(location.search);
    setSelectedEventId(initial.selectedEventId);
    setInspectorOpen(initial.inspectorOpen);
    setInspectorTab(initial.inspectorTab);
    setSearchText(initial.searchText);
    setIdMode(normalizeIdMode(initial.idMode));
    setTimeInterval(normalizeInterval(initial.timeInterval));
    setTimeWindow(normalizeWindow(initial.timeWindow));
    setStreamMode(normalizeStream(initial.streamMode));
    setSelectedAgents(initial.selectedAgents);
    setAgentSearch(initial.agentSearch || String(searchFilters?.agent || '').trim());
    setSelectedModels(initial.selectedModels);
    setSelectedTypes(initial.selectedTypes.length ? initial.selectedTypes : (legacyType ? [legacyType] : []));
    setSelectedStopReasons(initial.selectedStopReasons);
    setTickMin(initial.tickMin);
    setTickMax(initial.tickMax);
    setCostMin(initial.costMin);
    setCostMax(initial.costMax);
    setRoundFilter(initial.roundFilter);
    setGroupMode(normalizeGroupMode(initial.groupMode));
    setPage(initial.page);
    setRowsPerPage(initial.rowsPerPage);
    setRowHeight(normalizeRowHeight(initial.rowHeight));
    setSortKey(initial.sortKey);
    setSortDirection(initial.sortDirection);
    initializedRef.current = true;
  }, [legacyType, location.search, searchFilters?.agent]);

  useEffect(() => {
    if (!initializedRef.current) return;
    const currentSearch = location.search.startsWith('?') ? location.search.slice(1) : location.search;
    if (currentSearch === lastSyncedSearchRef.current) {
      lastSyncedSearchRef.current = '';
      return;
    }

    const routeState = readInitialQueryState(location.search);

    setInspectorTab((current) => (routeState.inspectorTab === current ? current : routeState.inspectorTab));
    setInspectorOpen((current) => {
      const next = routeState.inspectorOpen;
      return next === current ? current : next;
    });
    setSearchText((current) => (routeState.searchText === current ? current : routeState.searchText));
    setIdMode((current) => {
      const next = normalizeIdMode(routeState.idMode);
      return next === current ? current : next;
    });
    setTimeInterval((current) => {
      const next = normalizeInterval(routeState.timeInterval);
      return next === current ? current : next;
    });
    setTimeWindow((current) => {
      const next = normalizeWindow(routeState.timeWindow);
      return next === current ? current : next;
    });
    setStreamMode((current) => {
      const next = normalizeStream(routeState.streamMode);
      return next === current ? current : next;
    });
    setGroupMode((current) => {
      const next = normalizeGroupMode(routeState.groupMode);
      return next === current ? current : next;
    });
    setRowHeight((current) => {
      const next = normalizeRowHeight(routeState.rowHeight);
      return next === current ? current : next;
    });
  }, [location.pathname, location.search]);

  const sourceEvents = useMemo(() => {
    const merged = [];
    const seen = new Set();
    [...historyEvents, ...telemetryTail].forEach((event) => {
      const key = String(event?.event_id || event?.id || '');
      if (key && seen.has(key)) return;
      if (key) seen.add(key);
      merged.push(event);
    });
    return merged;
  }, [historyEvents, telemetryTail]);

  const rowSourceEvents = useMemo(() => {
    return sourceEvents.slice().sort((a, b) => String(b.timestamp || '').localeCompare(String(a.timestamp || '')));
  }, [sourceEvents]);

  const rows = useMemo(() => {
    const modelByTrace = new Map();
    const modelByAgentTick = new Map();

    rowSourceEvents.forEach((event) => {
      const payload = normalizeEventPayload(event);
      const model = modelOf(event);
      if (!model || model === '-') return;

      const traceId = String(payload.trace_id || event?.trace_id || '').trim();
      if (traceId && !modelByTrace.has(traceId)) {
        modelByTrace.set(traceId, model);
      }

      const agentId = telemetryAgentId(event);
      const tick = tickOf(telemetryTick, event);
      if (agentId && Number.isFinite(tick)) {
        const key = `${agentId}::${tick}`;
        if (!modelByAgentTick.has(key)) {
          modelByAgentTick.set(key, model);
        }
      }
    });

    return rowSourceEvents.map((event) => {
      const payload = normalizeEventPayload(event);
      const eventType = eventTypeOf(event, telemetryEventType);
      const tick = tickOf(telemetryTick, event);
      const round = parseRoundIndex(event);
      const agentId = telemetryAgentId(event);
      const traceId = String(payload.trace_id || event?.trace_id || '').trim();
      const eventId = String(event?.event_id || event?.id || '').trim();
      let model = modelOf(event);
      if (!model || model === '-') {
        if (traceId && modelByTrace.has(traceId)) {
          model = modelByTrace.get(traceId);
        } else if (agentId && Number.isFinite(tick)) {
          model = modelByAgentTick.get(`${agentId}::${tick}`) || model;
        }
      }
      const sessionId = textField(payload.session_id, payload.sessionId, event?.session_id, event?.sessionId);
      const userId = textField(payload.user_id, payload.userId, event?.user_id, event?.userId);
      const environment = textField(payload.environment_name, payload.environment, event?.environment_name, event?.environment);
      const level = textField(payload.level, payload.severity, event?.level, event?.severity);
      const version = textField(
        payload.release,
        payload.version,
        payload.prompt_contract_version,
        event?.release,
        event?.version
      );
      const latencyMs = asNumber(payload.elapsed_ms ?? payload.latency_ms ?? event?.elapsed_ms ?? event?.latency_ms);
      const statusCode = textField(payload.status_code, payload.status, event?.status_code, event?.status);
      const tags = tagsOf(payload, event);
      const stableFallbackKey = [
        String(event?.timestamp || ''),
        String(agentId || ''),
        String(eventType || ''),
        String(model || ''),
        String(payload.heartbeat_index ?? payload.heartbeatIndex ?? payload.tick ?? ''),
        String(payload.round_index ?? ''),
        String(payload.action_name || payload.action_type || payload.method || payload.url || ''),
        String(payload.user_message || payload.input || ''),
        String(payload.response_text || payload.output || ''),
      ].join('::');
      const traceKey = traceId || eventId || stableFallbackKey;
      const rowKey = eventId || stableFallbackKey;
      const cost = extractCostUsd(event);
      const tokens = extractTokens(event);
      const actionCount = payload.parsed_action_count ?? payload.actionable_action_count ?? 0;

      return {
        key: rowKey,
        traceKey,
        eventId,
        event,
        timestamp: event?.timestamp,
        model,
        provider: rowNameOf(eventType, model),
        eventType,
        agentId,
        tick,
        round,
        traceId,
        sessionId,
        userId,
        environment,
        level,
        version,
        latencyMs,
        statusCode,
        tags,
        input: inputOf(event),
        output: outputOf(event),
        costTotal: Number(cost?.total || 0),
        costKnown: hasUsageOrPricingMetadata(event),
        tokensInput: Number(tokens?.input || 0),
        tokensOutput: Number(tokens?.output || 0),
        stopReason: stopReasonOf(event),
        actionCount: Number(actionCount || 0),
      };
    });
  }, [extractCostUsd, extractTokens, rowSourceEvents, telemetryAgentId, telemetryEventType, telemetryTick]);

  const modelOptions = useMemo(
    () => Array.from(new Set(rows.map((row) => row.model).filter(Boolean))).sort((a, b) => a.localeCompare(b)),
    [rows]
  );

  const eventTypeOptions = useMemo(
    () => Array.from(new Set(rows.map((row) => row.eventType).filter(Boolean))).sort((a, b) => a.localeCompare(b)),
    [rows]
  );

  const stopReasonOptions = useMemo(
    () => Array.from(new Set(rows.map((row) => row.stopReason).filter(Boolean))).sort((a, b) => a.localeCompare(b)),
    [rows]
  );

  const filteredRows = useMemo(() => {
    const minTick = asNumber(tickMin);
    const maxTick = asNumber(tickMax);
    const minCost = asNumber(costMin);
    const maxCost = asNumber(costMax);
    const hasTickFilter = Number.isFinite(minTick) || Number.isFinite(maxTick);
    const globalSearchValue = String(searchText || '').trim().toLowerCase();
    const agentSearchValue = String(agentSearch || '').trim().toLowerCase();

    return rows.filter((row) => {
      if (selectedAgents.length && !selectedAgents.includes(row.agentId)) return false;
      if (agentSearchValue && !String(row.agentId || '').toLowerCase().includes(agentSearchValue)) return false;
      if (selectedModels.length && !selectedModels.includes(row.model)) return false;
      if (selectedTypes.length && !selectedTypes.includes(row.eventType)) return false;
      if (legacyType && row.eventType !== legacyType) return false;
      if (legacyDrill && !matchesDrillFilter(row.event, legacyDrill)) return false;
      if (selectedStopReasons.length && !selectedStopReasons.includes(row.stopReason)) return false;

      if (hasTickFilter && !Number.isFinite(row.tick)) return false;
      if (Number.isFinite(minTick) && row.tick < minTick) return false;
      if (Number.isFinite(maxTick) && row.tick > maxTick) return false;

      if (roundFilter === 'round0' && row.round !== 0) return false;
      if (roundFilter === 'round1plus' && (!Number.isFinite(row.round) || row.round < 1)) return false;

      if (Number.isFinite(minCost) && row.costTotal < minCost) return false;
      if (Number.isFinite(maxCost) && row.costTotal > maxCost) return false;
      if (globalSearchValue) {
        const haystack = [
          row.provider,
          row.eventType,
          row.agentId,
          row.model,
          row.traceKey,
          row.traceId,
          row.eventId,
          row.sessionId,
          row.userId,
          row.environment,
          row.level,
          row.statusCode,
          row.version,
          row.tags,
          row.input,
          row.output,
          row.stopReason,
        ]
          .map((value) => String(value || '').toLowerCase())
          .join('\n');
        if (!haystack.includes(globalSearchValue)) return false;
      }

      return true;
    });
  }, [
    searchText,
    agentSearch,
    costMax,
    costMin,
    roundFilter,
    rows,
    selectedAgents,
    selectedModels,
    legacyDrill,
    legacyType,
    selectedStopReasons,
    selectedTypes,
    tickMax,
    tickMin,
  ]);

  const sortedRows = useMemo(() => {
    const copy = filteredRows.slice();
    copy.sort((a, b) => {
      const aValue = sortValueForRow(a, sortKey);
      const bValue = sortValueForRow(b, sortKey);
      let comparison = compareValues(aValue, bValue);

      if (comparison === 0) {
        comparison = compareValues(a.key, b.key);
      }
      return sortDirection === 'asc' ? comparison : -comparison;
    });
    return copy;
  }, [filteredRows, sortDirection, sortKey]);

  const groupedTickRows = useMemo(() => {
    const groups = new Map();
    filteredRows.forEach((row) => {
      const tick = Number.isFinite(row.tick) ? row.tick : null;
      const key = Number.isFinite(tick) ? `tick:${tick}` : 'tick:none';
      if (!groups.has(key)) {
        groups.set(key, {
          key,
          tick,
          rows: [],
          latestTimestamp: '',
          agents: new Set(),
          models: new Set(),
          types: new Set(),
          costTotal: 0,
          pricingSignalCount: 0,
          llmIoCount: 0,
          tokensInput: 0,
          tokensOutput: 0,
        });
      }
      const group = groups.get(key);
      group.rows.push(row);
      const rowTs = String(row.timestamp || '');
      if (rowTs > group.latestTimestamp) {
        group.latestTimestamp = rowTs;
      }
      if (row.agentId) group.agents.add(row.agentId);
      if (row.model && row.model !== '-') group.models.add(row.model);
      if (row.eventType) group.types.add(row.eventType);
      group.costTotal += Number(row.costTotal || 0);
      if (row.eventType === 'llm_io') group.llmIoCount += 1;
      if (row.costKnown) group.pricingSignalCount += 1;
      group.tokensInput += Number(row.tokensInput || 0);
      group.tokensOutput += Number(row.tokensOutput || 0);
    });

    const summarized = Array.from(groups.values()).map((group) => {
      const rowsByTime = group.rows.slice().sort((a, b) => {
        const byTimestamp = compareValues(a.timestamp, b.timestamp);
        if (byTimestamp !== 0) return -byTimestamp;
        return compareValues(a.key, b.key);
      });
      return {
        key: group.key,
        tick: group.tick,
        tickLabel: Number.isFinite(group.tick) ? `Heartbeat ${group.tick}` : 'No heartbeat',
        latestTimestamp: group.latestTimestamp,
        eventCount: rowsByTime.length,
        agentCount: group.agents.size,
        modelCount: group.models.size,
        typeCount: group.types.size,
        costTotal: group.costTotal,
        pricingSignalCount: group.pricingSignalCount,
        llmIoCount: group.llmIoCount,
        tokensInput: group.tokensInput,
        tokensOutput: group.tokensOutput,
        rows: rowsByTime,
      };
    });

    summarized.sort((a, b) => {
      const aTick = Number.isFinite(a.tick);
      const bTick = Number.isFinite(b.tick);
      if (aTick && bTick) {
        if (a.tick !== b.tick) return b.tick - a.tick;
      } else if (aTick !== bTick) {
        return aTick ? -1 : 1;
      }
      const byTimestamp = compareValues(a.latestTimestamp, b.latestTimestamp);
      if (byTimestamp !== 0) return -byTimestamp;
      return compareValues(a.key, b.key);
    });

    return summarized;
  }, [filteredRows]);

  useEffect(() => {
    const available = new Set(groupedTickRows.map((group) => group.key));
    setCollapsedTickGroups((current) => {
      const next = new Set(Array.from(current).filter((key) => available.has(key)));
      return next;
    });
  }, [groupedTickRows]);

  useEffect(() => {
    const availableRowKeys = new Set(sortedRows.map((row) => row.key));
    setSelectedRowKeys((current) => {
      const next = new Set(Array.from(current).filter((rowKey) => availableRowKeys.has(rowKey)));
      return next;
    });
  }, [sortedRows]);

  const totalItems = groupMode === 'tick' ? groupedTickRows.length : sortedRows.length;
  const totalPages = Math.max(1, Math.ceil(totalItems / rowsPerPage));
  const noRowsLabel = rows.length ? 'No rows match current filters.' : 'No run events recorded yet.';

  useEffect(() => {
    setPage(1);
  }, [
    searchText,
    rowsPerPage,
    selectedAgents,
    agentSearch,
    selectedModels,
    selectedTypes,
    selectedStopReasons,
    tickMin,
    tickMax,
    costMin,
    costMax,
    roundFilter,
    groupMode,
    sortKey,
    sortDirection,
  ]);

  useEffect(() => {
    if (page > totalPages) setPage(totalPages);
  }, [page, totalPages]);

  const pagedRows = useMemo(() => {
    if (groupMode === 'tick') return [];
    const start = (page - 1) * rowsPerPage;
    return sortedRows.slice(start, start + rowsPerPage);
  }, [groupMode, sortedRows, page, rowsPerPage]);

  const pagedTickGroups = useMemo(() => {
    if (groupMode !== 'tick') return [];
    const start = (page - 1) * rowsPerPage;
    return groupedTickRows.slice(start, start + rowsPerPage);
  }, [groupMode, groupedTickRows, page, rowsPerPage]);

  const pagedSelectableRows = useMemo(() => {
    if (groupMode === 'tick') {
      return pagedTickGroups.flatMap((group) => group.rows);
    }
    return pagedRows;
  }, [groupMode, pagedRows, pagedTickGroups]);

  const allPageSelected = useMemo(() => {
    if (!pagedSelectableRows.length) return false;
    return pagedSelectableRows.every((row) => selectedRowKeys.has(row.key));
  }, [pagedSelectableRows, selectedRowKeys]);

  const somePageSelected = useMemo(() => {
    if (!pagedSelectableRows.length) return false;
    return !allPageSelected && pagedSelectableRows.some((row) => selectedRowKeys.has(row.key));
  }, [allPageSelected, pagedSelectableRows, selectedRowKeys]);

  const orderedColumns = useMemo(() => {
    const byKey = new Map(COLUMN_CONFIG.map((column) => [column.key, column]));
    const normalizedOrder = [];
    columnOrder.forEach((key) => {
      if (!byKey.has(key)) return;
      if (normalizedOrder.includes(key)) return;
      normalizedOrder.push(key);
    });
    COLUMN_CONFIG.forEach((column) => {
      if (!normalizedOrder.includes(column.key)) normalizedOrder.push(column.key);
    });
    return normalizedOrder.map((key) => byKey.get(key)).filter(Boolean);
  }, [columnOrder]);

  const visibleColumnKeys = useMemo(
    () => orderedColumns.filter((column) => visibleColumns[column.key]).map((column) => column.key),
    [orderedColumns, visibleColumns]
  );

  useEffect(() => {
    if (!initializedRef.current || !rows.length || isFullPageDetail) return;

    const routeTrace = String(new URLSearchParams(location.search).get(QUERY_KEYS.trace) || '').trim();
    if (!routeTrace) {
      lastAppliedRouteTraceRef.current = '';
      return;
    }
    if (routeTrace === lastAppliedRouteTraceRef.current) return;

    const matchByKey = rows.find((row) => row.key === routeTrace);
    if (matchByKey) {
      lastAppliedRouteTraceRef.current = routeTrace;
      setSelectedEventId(matchByKey.key);
      return;
    }

    const matchByTraceKey = rows.find((row) => row.traceKey === routeTrace);
    if (matchByTraceKey) {
      lastAppliedRouteTraceRef.current = routeTrace;
      setSelectedEventId(matchByTraceKey.key);
    }
  }, [isFullPageDetail, location.search, rows]);

  useEffect(() => {
    if (!initializedRef.current || !rows.length || !isFullPageDetail) return;
    if (!normalizedFullPageTraceId) return;

    const matchByKey = rows.find((row) => row.key === normalizedFullPageTraceId);
    const matchByTraceKey = rows.find((row) => row.traceKey === normalizedFullPageTraceId || row.traceId === normalizedFullPageTraceId);
    const target = matchByKey || matchByTraceKey;
    if (!target) return;
    if (target.key === selectedEventId) return;
    setSelectedEventId(target.key);
  }, [isFullPageDetail, normalizedFullPageTraceId, rows, selectedEventId]);

  useEffect(() => {
    if (!rows.length) {
      setSelectedEventId('');
      return;
    }

    if (!selectedEventId) {
      setSelectedEventId(rows[0].key);
      return;
    }

    if (rows.some((row) => row.key === selectedEventId)) return;

    const traceMatch = rows.find((row) => row.traceKey === selectedEventId);
    if (traceMatch) {
      setSelectedEventId(traceMatch.key);
      return;
    }

    setSelectedEventId(rows[0].key);
  }, [rows, selectedEventId]);

  const selectedRow = useMemo(() => {
    if (!selectedEventId) return rows[0] || null;
    return rows.find((row) => row.key === selectedEventId) || rows.find((row) => row.traceKey === selectedEventId) || rows[0] || null;
  }, [rows, selectedEventId]);

  const selectedSortedIndex = useMemo(() => {
    if (!selectedRow) return -1;
    return sortedRows.findIndex((row) => row.key === selectedRow.key);
  }, [selectedRow, sortedRows]);

  const previousSortedRow = selectedSortedIndex > 0 ? sortedRows[selectedSortedIndex - 1] : null;
  const nextSortedRow = selectedSortedIndex >= 0 && selectedSortedIndex < sortedRows.length - 1
    ? sortedRows[selectedSortedIndex + 1]
    : null;

  const relatedRounds = useMemo(() => {
    if (!selectedRow || !Number.isFinite(selectedRow.tick)) return [];
    return rows
      .filter((row) => row.agentId === selectedRow.agentId && row.tick === selectedRow.tick)
      .slice()
      .sort((a, b) => {
        const safeA = Number.isFinite(a.round) ? a.round : -1;
        const safeB = Number.isFinite(b.round) ? b.round : -1;
        return safeA - safeB;
      });
  }, [rows, selectedRow]);

  const setSort = useCallback((key) => {
    if (!SORTABLE_KEYS.has(key)) return;
    setSortKey((currentKey) => {
      if (currentKey === key) {
        setSortDirection((currentDirection) => (currentDirection === 'asc' ? 'desc' : 'asc'));
        return currentKey;
      }
      setSortDirection('desc');
      return key;
    });
  }, []);

  const startFilterResize = useCallback((event) => {
    if (event.button !== 0) return;
    event.preventDefault();
    filterResizeStateRef.current = {
      startX: event.clientX,
      startWidth: filterWidth,
    };
    if (typeof document !== 'undefined') {
      document.body.style.userSelect = 'none';
      document.body.style.cursor = 'col-resize';
    }
  }, [filterWidth]);

  useEffect(() => {
    function onMouseMove(event) {
      const active = filterResizeStateRef.current;
      if (!active) return;
      const delta = event.clientX - active.startX;
      const nextWidth = Math.min(
        FILTER_WIDTH_BOUNDS.max,
        Math.max(FILTER_WIDTH_BOUNDS.min, active.startWidth + delta)
      );
      setFilterWidth(nextWidth);
    }

    function stopResize() {
      if (!filterResizeStateRef.current) return;
      filterResizeStateRef.current = null;
      if (typeof document !== 'undefined') {
        document.body.style.userSelect = '';
        document.body.style.cursor = '';
      }
    }

    window.addEventListener('mousemove', onMouseMove);
    window.addEventListener('mouseup', stopResize);
    return () => {
      window.removeEventListener('mousemove', onMouseMove);
      window.removeEventListener('mouseup', stopResize);
      stopResize();
    };
  }, []);

  const startInspectorResize = useCallback((event) => {
    if (event.button !== 0) return;
    const container = contentLayoutRef.current;
    if (!container) return;
    event.preventDefault();
    const bounds = container.getBoundingClientRect();
    inspectorResizeStateRef.current = {
      startX: event.clientX,
      startWidth: inspectorWidth,
      containerWidth: bounds.width,
    };
    if (typeof document !== 'undefined') {
      document.body.style.userSelect = 'none';
      document.body.style.cursor = 'col-resize';
    }
  }, [inspectorWidth]);

  useEffect(() => {
    function onMouseMove(event) {
      const active = inspectorResizeStateRef.current;
      if (!active) return;
      const delta = active.startX - event.clientX;
      const maxWidth = Math.min(INSPECTOR_WIDTH_BOUNDS.max, Math.max(INSPECTOR_WIDTH_BOUNDS.min, active.containerWidth - 260));
      const nextWidth = Math.min(maxWidth, Math.max(INSPECTOR_WIDTH_BOUNDS.min, active.startWidth + delta));
      setInspectorWidth(nextWidth);
    }

    function stopResize() {
      if (!inspectorResizeStateRef.current) return;
      inspectorResizeStateRef.current = null;
      if (typeof document !== 'undefined') {
        document.body.style.userSelect = '';
        document.body.style.cursor = '';
      }
    }

    window.addEventListener('mousemove', onMouseMove);
    window.addEventListener('mouseup', stopResize);
    return () => {
      window.removeEventListener('mousemove', onMouseMove);
      window.removeEventListener('mouseup', stopResize);
      stopResize();
    };
  }, []);

  const moveColumn = useCallback((columnKey, delta) => {
    setColumnOrder((currentOrder) => {
      const currentIndex = currentOrder.indexOf(columnKey);
      if (currentIndex < 0) return currentOrder;
      const nextIndex = Math.max(0, Math.min(currentOrder.length - 1, currentIndex + delta));
      if (nextIndex === currentIndex) return currentOrder;
      const nextOrder = currentOrder.slice();
      const [entry] = nextOrder.splice(currentIndex, 1);
      nextOrder.splice(nextIndex, 0, entry);
      return nextOrder;
    });
  }, []);

  const restoreColumnDefaults = useCallback(() => {
    setVisibleColumns(DEFAULT_VISIBLE_COLUMNS);
    setColumnOrder(DEFAULT_COLUMN_ORDER);
    setColumnWidths({});
  }, []);

  const buildTraceDetailPath = useCallback((traceValue) => {
    const traceToken = String(traceValue || '').trim();
    if (!traceToken) return '';
    const base = runBasePath || String(location.pathname || '').replace(/\/traces(?:\/[^/?#]+)?$/, '');
    return `${base}/traces/${encodeURIComponent(traceToken)}`;
  }, [location.pathname, runBasePath]);

  const openFullPageForRow = useCallback((row) => {
    if (!row) return;
    const traceValue = row.traceId || row.traceKey || row.key;
    const pathname = buildTraceDetailPath(traceValue);
    if (!pathname) return;
    navigate({ pathname, search: location.search }, { replace: false });
  }, [buildTraceDetailPath, location.search, navigate]);

  const goBackToTraceList = useCallback(() => {
    if (!runBasePath) return;
    const querySuffix = location.search || runPathQuery || '';
    navigate(`${runBasePath}/traces${querySuffix}`, { replace: false });
  }, [location.search, navigate, runBasePath, runPathQuery]);

  useEffect(() => {
    if (!initializedRef.current) return;

    const params = new URLSearchParams(location.search);

    const queryTraceValue = !isFullPageDetail && inspectorOpen ? selectedEventId : '';
    const queryPanelValue = !isFullPageDetail && inspectorOpen ? '1' : '';
    const queryTabValue = isFullPageDetail || inspectorOpen ? inspectorTab : '';
    setOrDeleteParam(params, QUERY_KEYS.trace, queryTraceValue, '');
    setOrDeleteParam(params, QUERY_KEYS.panel, queryPanelValue, '');
    setOrDeleteParam(params, QUERY_KEYS.tab, queryTabValue, 'preview');
    setOrDeleteParam(params, QUERY_KEYS.search, searchText);
    setOrDeleteParam(params, QUERY_KEYS.idMode, idMode, 'ids_names');
    setOrDeleteParam(params, QUERY_KEYS.interval, timeInterval, '1d');
    setOrDeleteParam(params, QUERY_KEYS.window, timeWindow, 'past_1d');
    setOrDeleteParam(params, QUERY_KEYS.stream, streamMode, 'off');
    setOrDeleteParam(params, QUERY_KEYS.agentList, joinCsv(selectedAgents));
    setOrDeleteParam(params, QUERY_KEYS.agentText, agentSearch);
    setOrDeleteParam(params, QUERY_KEYS.modelList, joinCsv(selectedModels));
    setOrDeleteParam(params, QUERY_KEYS.typeList, joinCsv(selectedTypes));
    setOrDeleteParam(params, QUERY_KEYS.stopList, joinCsv(selectedStopReasons));
    setOrDeleteParam(params, QUERY_KEYS.tickMin, tickMin);
    setOrDeleteParam(params, QUERY_KEYS.tickMax, tickMax);
    setOrDeleteParam(params, QUERY_KEYS.costMin, costMin);
    setOrDeleteParam(params, QUERY_KEYS.costMax, costMax);
    setOrDeleteParam(params, QUERY_KEYS.round, roundFilter, 'all');
    setOrDeleteParam(params, QUERY_KEYS.group, groupMode, 'flat');
    setOrDeleteParam(params, QUERY_KEYS.page, String(page), '1');
    setOrDeleteParam(params, QUERY_KEYS.rows, String(rowsPerPage), '50');
    setOrDeleteParam(params, QUERY_KEYS.rowHeight, rowHeight, 'medium');
    setOrDeleteParam(params, QUERY_KEYS.sort, serializeSort(sortKey, sortDirection), 'timestamp:desc');

    const nextSearch = params.toString();
    const currentSearch = location.search.startsWith('?') ? location.search.slice(1) : location.search;
    if (nextSearch === currentSearch) return;

    lastSyncedSearchRef.current = nextSearch;
    navigate({ pathname: location.pathname, search: nextSearch ? `?${nextSearch}` : '' }, { replace: true });
  }, [
    agentSearch,
    searchText,
    idMode,
    timeInterval,
    timeWindow,
    streamMode,
    costMax,
    costMin,
    inspectorOpen,
    inspectorTab,
    location.pathname,
    location.search,
    navigate,
    page,
    roundFilter,
    groupMode,
    rowsPerPage,
    rowHeight,
    selectedAgents,
    selectedEventId,
    selectedModels,
    selectedStopReasons,
    selectedTypes,
    sortDirection,
    sortKey,
    tickMax,
    tickMin,
    isFullPageDetail,
  ]);

  const openTraceRow = useCallback((row) => {
    if (!row) return;
    setSelectedEventId(row.key);
    setInspectorOpen(true);
  }, []);

  const onRowKeyDown = useCallback((event, row) => {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    event.preventDefault();
    openTraceRow(row);
  }, [openTraceRow]);

  const selectionPositionLabel = selectedSortedIndex >= 0 ? `${selectedSortedIndex + 1} / ${sortedRows.length}` : `0 / ${sortedRows.length}`;
  const selectedTraceToken = selectedRow?.traceId || selectedRow?.traceKey || selectedRow?.key || '';
  const rowDensity = ROW_HEIGHT_CONFIG[rowHeight] || ROW_HEIGHT_CONFIG.medium;
  const bodyCellClassName = `${rowDensity.bodyTextClassName} ${rowDensity.cellClassName}`;
  const identifierCellClassName = `${bodyCellClassName} font-mono text-[11px]`;
  const timestampMinCh = useMemo(() => {
    const sample = sortedRows.slice(0, 120);
    let maxLen = 'Timestamp'.length;
    sample.forEach((row) => {
      const text = formatTracingDateTime(row.timestamp);
      maxLen = Math.max(maxLen, text.length);
    });
    return Math.min(Math.max(maxLen + 1, 18), 24);
  }, [sortedRows]);
  const heartbeatMinCh = useMemo(() => {
    const sample = sortedRows.slice(0, 120);
    let maxLen = 'Heartbeat'.length;
    sample.forEach((row) => {
      const text = Number.isFinite(row.tick) ? String(row.tick) : '-';
      maxLen = Math.max(maxLen, text.length);
    });
    return Math.min(Math.max(maxLen + 2, 10), 16);
  }, [sortedRows]);
  const selectionColumnStyle = useMemo(() => ({ width: '38px', minWidth: '38px', maxWidth: '38px' }), []);
  const columnStyleFor = useCallback((columnKey) => {
    const bounds = columnBoundsFor(columnKey);
    const timestampMinPx = Math.round(timestampMinCh * 8);
    const heartbeatMinPx = Math.round(heartbeatMinCh * 8);
    let dynamicMin = bounds.min;
    if (columnKey === 'timestamp') {
      dynamicMin = Math.max(bounds.min, timestampMinPx);
    } else if (columnKey === 'tick') {
      dynamicMin = Math.max(bounds.min, heartbeatMinPx);
    }
    const width = columnWidths[columnKey] ?? bounds.default;
    const clampedWidth = Math.min(bounds.max, Math.max(dynamicMin, width));
    return {
      width: `${clampedWidth}px`,
      minWidth: `${dynamicMin}px`,
      maxWidth: `${bounds.max}px`,
    };
  }, [columnWidths, heartbeatMinCh, timestampMinCh]);

  const startColumnResize = useCallback((event, columnKey) => {
    if (typeof event.button === 'number' && event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    const bounds = columnBoundsFor(columnKey);
    const timestampMinPx = Math.round(timestampMinCh * 8);
    const heartbeatMinPx = Math.round(heartbeatMinCh * 8);
    let min = bounds.min;
    if (columnKey === 'timestamp') {
      min = Math.max(bounds.min, timestampMinPx);
    } else if (columnKey === 'tick') {
      min = Math.max(bounds.min, heartbeatMinPx);
    }
    const startWidth = columnWidths[columnKey] ?? bounds.default;
    resizeStateRef.current = {
      columnKey,
      startX: event.clientX,
      startWidth,
      min,
      max: bounds.max,
    };
    if (typeof document !== 'undefined') {
      document.body.style.userSelect = 'none';
      document.body.style.cursor = 'col-resize';
    }
    if (typeof event.currentTarget?.setPointerCapture === 'function' && typeof event.pointerId === 'number') {
      try {
        event.currentTarget.setPointerCapture(event.pointerId);
      } catch {
        // Ignore browsers/contexts where pointer capture is unavailable.
      }
    }
  }, [columnWidths, heartbeatMinCh, timestampMinCh]);

  useEffect(() => {
    function onPointerMove(event) {
      const active = resizeStateRef.current;
      if (!active) return;
      const delta = event.clientX - active.startX;
      const nextWidth = Math.min(active.max, Math.max(active.min, active.startWidth + delta));
      setColumnWidths((current) => {
        if (current[active.columnKey] === nextWidth) return current;
        return { ...current, [active.columnKey]: nextWidth };
      });
    }

    function stopResize() {
      if (!resizeStateRef.current) return;
      resizeStateRef.current = null;
      if (typeof document !== 'undefined') {
        document.body.style.userSelect = '';
        document.body.style.cursor = '';
      }
    }

    window.addEventListener('pointermove', onPointerMove);
    window.addEventListener('pointerup', stopResize);
    window.addEventListener('pointercancel', stopResize);
    return () => {
      window.removeEventListener('pointermove', onPointerMove);
      window.removeEventListener('pointerup', stopResize);
      window.removeEventListener('pointercancel', stopResize);
      stopResize();
    };
  }, []);

  const toggleRowSelection = useCallback((rowKey, nextChecked) => {
    setSelectedRowKeys((current) => {
      const next = new Set(current);
      const shouldSelect = typeof nextChecked === 'boolean' ? nextChecked : !next.has(rowKey);
      if (shouldSelect) next.add(rowKey);
      else next.delete(rowKey);
      return next;
    });
  }, []);

  const toggleAllPageSelection = useCallback((nextChecked) => {
    const shouldSelect = nextChecked === true;
    const pageKeys = pagedSelectableRows.map((row) => row.key);
    setSelectedRowKeys((current) => {
      const next = new Set(current);
      pageKeys.forEach((key) => {
        if (shouldSelect) next.add(key);
        else next.delete(key);
      });
      return next;
    });
  }, [pagedSelectableRows]);

  const pageNumberOptions = useMemo(() => {
    if (totalPages <= 200) {
      return Array.from({ length: totalPages }, (_, index) => index + 1);
    }
    const halfWindow = 99;
    let start = Math.max(1, page - halfWindow);
    let end = Math.min(totalPages, start + 199);
    start = Math.max(1, end - 199);
    return Array.from({ length: (end - start) + 1 }, (_, index) => start + index);
  }, [page, totalPages]);

  const toggleTickGroup = useCallback((groupKey) => {
    setCollapsedTickGroups((current) => {
      const next = new Set(current);
      if (next.has(groupKey)) next.delete(groupKey);
      else next.add(groupKey);
      return next;
    });
  }, []);

  function onTickGroupKeyDown(event, groupKey) {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    event.preventDefault();
    toggleTickGroup(groupKey);
  }

  function renderEventCell(columnKey, row) {
    switch (columnKey) {
      case 'timestamp':
        return (
          <TableCell
            key={columnKey}
            className={`${bodyCellClassName} whitespace-nowrap`}
            style={columnStyleFor('timestamp')}
            title={formatTracingDateTime(row.timestamp)}
          >
            {formatTracingDateTime(row.timestamp)}
          </TableCell>
        );
      case 'traceId':
        return (
          <TableCell key={columnKey} className={identifierCellClassName} style={columnStyleFor('traceId')} title={row.traceId || row.traceKey}>
            {truncate(row.traceId || row.traceKey, 24)}
          </TableCell>
        );
      case 'eventId':
        return (
          <TableCell key={columnKey} className={identifierCellClassName} style={columnStyleFor('eventId')} title={row.eventId}>
            {truncate(row.eventId, 24)}
          </TableCell>
        );
      case 'name':
        return (
          <TableCell key={columnKey} className={bodyCellClassName} style={columnStyleFor('name')} title={row.model}>
            {row.provider}
          </TableCell>
        );
      case 'eventType':
        return (
          <TableCell key={columnKey} className={bodyCellClassName} style={columnStyleFor('eventType')}>
            <Badge variant="outline" className={cn('text-[10px] font-medium', EVENT_TYPE_BADGE_CLASS[row.eventType])}>
              {readableEventType(row.eventType)}
            </Badge>
          </TableCell>
        );
      case 'model':
        return (
          <TableCell key={columnKey} className={bodyCellClassName} style={columnStyleFor('model')} title={row.model}>
            {truncate(row.model, 30)}
          </TableCell>
        );
      case 'agent':
        return (
          <TableCell key={columnKey} className={bodyCellClassName} style={columnStyleFor('agent')} title={row.agentId}>
            {truncate(row.agentId, 30)}
          </TableCell>
        );
      case 'sessionId':
        return (
          <TableCell key={columnKey} className={identifierCellClassName} style={columnStyleFor('sessionId')} title={row.sessionId}>
            {truncate(row.sessionId, 30)}
          </TableCell>
        );
      case 'userId':
        return (
          <TableCell key={columnKey} className={identifierCellClassName} style={columnStyleFor('userId')} title={row.userId}>
            {truncate(row.userId, 30)}
          </TableCell>
        );
      case 'environment':
        return (
          <TableCell key={columnKey} className={bodyCellClassName} style={columnStyleFor('environment')} title={row.environment}>
            {truncate(row.environment, 24)}
          </TableCell>
        );
      case 'level':
        return <TableCell key={columnKey} className={bodyCellClassName} style={columnStyleFor('level')}>{row.level || '-'}</TableCell>;
      case 'tick':
        return <TableCell key={columnKey} className={bodyCellClassName} style={columnStyleFor('tick')}>{Number.isFinite(row.tick) ? row.tick : '-'}</TableCell>;
      case 'round':
        return <TableCell key={columnKey} className={bodyCellClassName} style={columnStyleFor('round')}>{Number.isFinite(row.round) ? row.round : '-'}</TableCell>;
      case 'latency':
        return (
          <TableCell key={columnKey} className={bodyCellClassName} style={columnStyleFor('latency')}>
            {Number.isFinite(row.latencyMs) ? `${formatNumber(row.latencyMs)} ms` : '-'}
          </TableCell>
        );
      case 'status':
        return <TableCell key={columnKey} className={bodyCellClassName} style={columnStyleFor('status')}>{row.statusCode || '-'}</TableCell>;
      case 'stopReason':
        return (
          <TableCell key={columnKey} className={bodyCellClassName} style={columnStyleFor('stopReason')} title={row.stopReason}>
            {truncate(row.stopReason, 24)}
          </TableCell>
        );
      case 'version':
        return (
          <TableCell key={columnKey} className={bodyCellClassName} style={columnStyleFor('version')} title={row.version}>
            {truncate(row.version, 24)}
          </TableCell>
        );
      case 'tags':
        return (
          <TableCell key={columnKey} className={bodyCellClassName} style={columnStyleFor('tags')} title={row.tags}>
            {truncate(row.tags, 32)}
          </TableCell>
        );
      case 'input':
        return (
          <TableCell key={columnKey} className={`${rowDensity.cellClassName} bg-blue-50/40 dark:bg-blue-950/20`} style={columnStyleFor('input')}>
            <span
              className={`block whitespace-pre-wrap break-words font-mono ${rowDensity.bodyTextClassName} ${rowDensity.previewLineClassName}`}
              title={row.input || '-'}
            >
              {previewText(row.input, rowHeight)}
            </span>
          </TableCell>
        );
      case 'output':
        return (
          <TableCell key={columnKey} className={`${rowDensity.cellClassName} bg-emerald-50/40 dark:bg-emerald-950/20`} style={columnStyleFor('output')}>
            <span
              className={`block whitespace-pre-wrap break-words font-mono ${rowDensity.bodyTextClassName} ${rowDensity.previewLineClassName}`}
              title={row.output || '-'}
            >
              {previewText(row.output, rowHeight)}
            </span>
            {row.actionCount ? (
              <span className="mt-1 block text-[10px] text-muted-foreground">
                actions={formatNumber(row.actionCount)}
              </span>
            ) : null}
          </TableCell>
        );
      case 'actions':
        return <TableCell key={columnKey} className={bodyCellClassName} style={columnStyleFor('actions')}>{formatNumber(row.actionCount)}</TableCell>;
      case 'cost':
        return (
          <TableCell key={columnKey} className={bodyCellClassName} style={columnStyleFor('cost')}>
            {row.eventType === 'llm_io' && !row.costKnown && row.costTotal <= 0 ? (
              <span className="text-muted-foreground">n/a</span>
            ) : (
              <>${formatNumber(row.costTotal)}</>
            )}
          </TableCell>
        );
      case 'tokens':
        return (
          <TableCell key={columnKey} className={bodyCellClassName} style={columnStyleFor('tokens')}>
            {formatNumber(row.tokensInput)} / {formatNumber(row.tokensOutput)}
          </TableCell>
        );
      default:
        return null;
    }
  }

  function renderTickGroupCell(columnKey, group, collapsed) {
    switch (columnKey) {
      case 'timestamp':
        return (
          <TableCell key={columnKey} className={`${bodyCellClassName} whitespace-nowrap font-semibold`} style={columnStyleFor('timestamp')}>
            {group.latestTimestamp ? formatTracingDateTime(group.latestTimestamp) : '-'}
          </TableCell>
        );
      case 'name':
        return (
          <TableCell key={columnKey} className={`${bodyCellClassName} font-semibold`} style={columnStyleFor('name')}>
            <span className="inline-flex items-center gap-1">
              <ChevronDown className={`h-3 w-3 transition-transform ${collapsed ? '-rotate-90' : ''}`} />
              {group.tickLabel}
            </span>
          </TableCell>
        );
      case 'eventType':
        return (
          <TableCell key={columnKey} className={`${bodyCellClassName} font-semibold`} style={columnStyleFor('eventType')}>
            {formatNumber(group.eventCount)} events
          </TableCell>
        );
      case 'model':
        return (
          <TableCell key={columnKey} className={`${bodyCellClassName} font-semibold`} style={columnStyleFor('model')}>
            {formatNumber(group.modelCount)} models
          </TableCell>
        );
      case 'agent':
        return (
          <TableCell key={columnKey} className={`${bodyCellClassName} font-semibold`} style={columnStyleFor('agent')}>
            {formatNumber(group.agentCount)} agents
          </TableCell>
        );
      case 'tick':
        return (
          <TableCell key={columnKey} className={`${bodyCellClassName} font-semibold`} style={columnStyleFor('tick')}>
            {Number.isFinite(group.tick) ? group.tick : '-'}
          </TableCell>
        );
      case 'actions':
        return (
          <TableCell key={columnKey} className={`${bodyCellClassName} font-semibold`} style={columnStyleFor('actions')}>
            {formatNumber(group.rows.reduce((total, row) => total + (Number(row.actionCount) || 0), 0))}
          </TableCell>
        );
      case 'cost':
        return (
          <TableCell key={columnKey} className={`${bodyCellClassName} font-semibold`} style={columnStyleFor('cost')}>
            {group.llmIoCount > 0 && group.pricingSignalCount === 0 && group.costTotal <= 0 ? (
              <span className="text-muted-foreground">n/a</span>
            ) : (
              <>${formatNumber(group.costTotal)}</>
            )}
          </TableCell>
        );
      case 'tokens':
        return (
          <TableCell key={columnKey} className={`${bodyCellClassName} font-semibold`} style={columnStyleFor('tokens')}>
            {formatNumber(group.tokensInput)} / {formatNumber(group.tokensOutput)}
          </TableCell>
        );
      case 'input':
      case 'output':
        return <TableCell key={columnKey} className={`${rowDensity.cellClassName} bg-muted/30`} style={columnStyleFor(columnKey)} />;
      default:
        return <TableCell key={columnKey} className={`${bodyCellClassName} text-muted-foreground`} style={columnStyleFor(columnKey)}>-</TableCell>;
    }
  }

  function renderEventRow(row, options = {}) {
    const selected = selectedEventId && row.key === selectedEventId;
    const indented = Boolean(options.indented);
    const rowChecked = selectedRowKeys.has(row.key);
    return (
      <TableRow
        key={row.key}
        data-state={selected ? 'selected' : undefined}
        onClick={() => openTraceRow(row)}
        onKeyDown={(event) => onRowKeyDown(event, row)}
        tabIndex={0}
        aria-label={`Open trace row ${row.key}`}
        className={`cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${rowDensity.rowClassName} ${indented ? '[&>td:first-child]:pl-6' : ''}`}
      >
        <TableCell className={`${rowDensity.cellClassName} px-2`} style={selectionColumnStyle}>
          <Checkbox
            aria-label={`Select row ${row.key}`}
            checked={rowChecked}
            onClick={(event) => event.stopPropagation()}
            onCheckedChange={(checked) => toggleRowSelection(row.key, checked === true)}
          />
        </TableCell>
        {visibleColumnKeys.map((columnKey) => renderEventCell(columnKey, row))}
      </TableRow>
    );
  }

  return (
    <div className="flex h-full min-h-0 min-w-0 flex-col bg-background">
      {!isFullPageDetail ? (
      <div className="flex flex-wrap items-center justify-between gap-1.5 border-b bg-muted/20 px-2.5 py-2">
          <div className="flex items-center gap-1.5">
            {!isFullPageDetail ? (
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="h-8 px-3 text-xs"
                onClick={() => setShowFilters((current) => !current)}
              >
                {showFilters ? <FilterX className="h-4 w-4" /> : <Filter className="h-4 w-4" />}
                {showFilters ? 'Hide filters' : 'Filters'}
                {activeFilterCount > 0 ? (
                  <Badge variant="secondary" className="ml-1 h-4 min-w-4 px-1 text-[10px]">{activeFilterCount}</Badge>
                ) : null}
              </Button>
            ) : null}
            <Input
              value={searchText}
              onChange={(event) => setSearchText(event.target.value)}
              placeholder="Search..."
              className="h-8 w-[220px] text-xs"
            />
            <SelectField
              ariaLabel="IDs and names"
              value={idMode}
              onChange={(value) => setIdMode(normalizeIdMode(value))}
              options={[
                { value: 'ids_names', label: 'IDs / Names' },
                { value: 'ids_only', label: 'IDs only' },
                { value: 'names_only', label: 'Names only' },
              ]}
              triggerClassName="h-8 w-[140px] text-xs"
            />
            <SelectField
              ariaLabel="Grouping mode"
              value={groupMode}
              onChange={(value) => setGroupMode(normalizeGroupMode(value))}
              options={[
                { value: 'flat', label: 'Flat' },
                { value: 'tick', label: 'By tick' },
              ]}
              triggerClassName="h-8 w-[108px] text-xs"
            />
            <div className="inline-flex items-center overflow-hidden rounded-md border bg-background">
              <SelectField
                ariaLabel="Time window"
                value={timeWindow}
                onChange={(value) => {
                  const nextWindow = normalizeWindow(value);
                  setTimeWindow(nextWindow);
                  const mappedInterval = nextWindow === 'past_1h'
                    ? '1h'
                    : nextWindow === 'past_12h'
                      ? '12h'
                      : nextWindow === 'past_7d'
                        ? '7d'
                        : nextWindow === 'past_30d'
                          ? '30d'
                          : '1d';
                  setTimeInterval(normalizeInterval(mappedInterval));
                }}
                options={[
                  { value: 'past_1h', label: 'Past 1 hour' },
                  { value: 'past_12h', label: 'Past 12 hours' },
                  { value: 'past_1d', label: 'Past 1 day' },
                  { value: 'past_7d', label: 'Past 7 days' },
                  { value: 'past_30d', label: 'Past 30 days' },
                ]}
                triggerClassName="h-8 w-[154px] border-0 rounded-none bg-transparent text-xs shadow-none ring-0 focus:ring-0"
              />
            </div>
            <div className="inline-flex items-center overflow-hidden rounded-md border bg-background">
              <Button
                type="button"
                size="sm"
                variant="ghost"
                className="h-8 rounded-none px-2 text-muted-foreground hover:text-foreground"
                onClick={() => onRefresh?.()}
                title="Refresh traces"
              >
                <RefreshCw className="h-4 w-4" />
              </Button>
              <div className="h-6 w-px bg-border" />
              <SelectField
                ariaLabel="Stream mode"
                value={streamMode}
                onChange={(value) => setStreamMode(normalizeStream(value))}
                options={[
                  { value: 'off', label: 'Off' },
                  { value: 'live', label: 'Live' },
                ]}
                triggerClassName="h-8 w-[84px] border-0 rounded-none bg-transparent text-xs shadow-none ring-0 focus:ring-0"
              />
            </div>
          </div>

          <div className="flex items-center gap-1.5">
            {!isFullPageDetail ? (
              <>
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button type="button" size="sm" variant="outline" className="h-8 px-3 text-xs">
                      Row height
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end" className="w-40">
                    <DropdownMenuCheckboxItem
                      checked={rowHeight === 'small'}
                      onCheckedChange={() => setRowHeight('small')}
                    >
                      Small
                    </DropdownMenuCheckboxItem>
                    <DropdownMenuCheckboxItem
                      checked={rowHeight === 'medium'}
                      onCheckedChange={() => setRowHeight('medium')}
                    >
                      Medium
                    </DropdownMenuCheckboxItem>
                    <DropdownMenuCheckboxItem
                      checked={rowHeight === 'large'}
                      onCheckedChange={() => setRowHeight('large')}
                    >
                      Large
                    </DropdownMenuCheckboxItem>
                  </DropdownMenuContent>
                </DropdownMenu>
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button type="button" size="sm" variant="outline" className="h-8 px-3 text-xs">
                      <Eye className="h-4 w-4" />
                      Columns {visibleColumnKeys.length}/{COLUMN_CONFIG.length}
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end" className="w-[340px] p-0">
                    <div className="flex items-center justify-between border-b px-3 py-2">
                      <span className="text-sm font-semibold">Column Visibility</span>
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        className="h-7 px-2 text-xs"
                        onClick={restoreColumnDefaults}
                      >
                        Restore Defaults
                      </Button>
                    </div>
                    <div className="flex items-center justify-between border-b px-3 py-2 text-xs text-muted-foreground">
                      <span>Select All Columns</span>
                      <span className="font-medium text-foreground">{visibleColumnKeys.length}/{COLUMN_CONFIG.length}</span>
                    </div>
                    <div className="max-h-[380px] overflow-y-auto px-2 py-2">
                      {orderedColumns.map((column, index) => (
                        <div key={column.key} className="flex items-center justify-between rounded-sm px-1 py-1 hover:bg-muted/60">
                          <label className="flex min-w-0 flex-1 items-center gap-2 text-xs font-medium">
                            <Checkbox
                              checked={visibleColumns[column.key]}
                              onCheckedChange={(checked) => {
                                setVisibleColumns((current) => {
                                  const next = { ...current, [column.key]: Boolean(checked) };
                                  return Object.values(next).some(Boolean) ? next : current;
                                });
                              }}
                            />
                            <span className="truncate">{column.label}</span>
                          </label>
                          <div className="flex items-center gap-0.5">
                            <Button
                              type="button"
                              size="icon"
                              variant="ghost"
                              className="h-6 w-6"
                              disabled={index === 0}
                              onClick={() => moveColumn(column.key, -1)}
                            >
                              <ArrowUp className="h-3.5 w-3.5" />
                            </Button>
                            <Button
                              type="button"
                              size="icon"
                              variant="ghost"
                              className="h-6 w-6"
                              disabled={index === orderedColumns.length - 1}
                              onClick={() => moveColumn(column.key, 1)}
                            >
                              <ArrowDown className="h-3.5 w-3.5" />
                            </Button>
                            <GripVertical className="h-3.5 w-3.5 text-muted-foreground" />
                          </div>
                        </div>
                      ))}
                    </div>
                  </DropdownMenuContent>
                </DropdownMenu>
              </>
            ) : null}
          </div>
        </div>
      ) : null}

      <div className="flex min-h-0 flex-1 overflow-hidden">
          {showFilters && !isFullPageDetail ? (
            <>
              <aside
                className="h-full min-h-0 overflow-y-auto border-r bg-muted/10 p-2"
                style={{ width: `${filterWidth}px`, minWidth: `${FILTER_WIDTH_BOUNDS.min}px`, maxWidth: `${FILTER_WIDTH_BOUNDS.max}px` }}
                data-testid="traces-filters-pane"
              >
              <div className="mb-3 flex items-center justify-between">
                <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Filters</span>
                {activeFilterCount > 0 ? (
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    className="h-6 px-2 text-[10px] text-muted-foreground"
                    onClick={() => {
                      setSelectedAgents([]);
                      setAgentSearch(String(searchFilters?.agent || '').trim());
                      setSelectedModels([]);
                      setSelectedTypes(legacyType ? [legacyType] : []);
                      setSelectedStopReasons([]);
                      setTickMin('');
                      setTickMax('');
                      setCostMin('');
                      setCostMax('');
                      setRoundFilter('all');
                      setPage(1);
                    }}
                  >
                    Reset all
                  </Button>
                ) : null}
              </div>
              <div className="space-y-1 text-xs">
                <Collapsible defaultOpen>
                  <CollapsibleTrigger className="flex w-full items-center justify-between py-1.5 text-xs uppercase tracking-wide text-muted-foreground hover:text-foreground">
                    <span>Type</span>
                    <div className="flex items-center gap-1">
                      {selectedTypes.length ? (
                        <span role="button" tabIndex={0} className="text-muted-foreground hover:text-foreground" onClick={(e) => { e.stopPropagation(); setSelectedTypes(legacyType ? [legacyType] : []); setPage(1); }} onKeyDown={(e) => { if (e.key === 'Enter') { e.stopPropagation(); setSelectedTypes(legacyType ? [legacyType] : []); setPage(1); } }} title="Clear type filter"><X className="h-3 w-3" /></span>
                      ) : null}
                      <ChevronDown className="h-3 w-3 transition-transform [[data-state=closed]>&]:rotate-[-90deg]" />
                    </div>
                  </CollapsibleTrigger>
                  <CollapsibleContent>
                    <div className="max-h-[120px] space-y-1 overflow-auto rounded-md border bg-background p-2">
                      {eventTypeOptions.length ? eventTypeOptions.map((type) => (
                        <label key={type} className="flex items-center gap-2 text-xs">
                          <Checkbox
                            checked={includesValue(selectedTypes, type)}
                            onCheckedChange={() => setSelectedTypes((current) => toggleValue(current, type))}
                          />
                          <span>{type}</span>
                        </label>
                      )) : <p className="text-xs text-muted-foreground">No event types.</p>}
                    </div>
                  </CollapsibleContent>
                </Collapsible>

                <Collapsible defaultOpen>
                  <CollapsibleTrigger className="flex w-full items-center justify-between py-1.5 text-xs uppercase tracking-wide text-muted-foreground hover:text-foreground">
                    <span>Agent</span>
                    <div className="flex items-center gap-1">
                      {(selectedAgents.length || agentSearch.trim()) ? (
                        <span role="button" tabIndex={0} className="text-muted-foreground hover:text-foreground" onClick={(e) => { e.stopPropagation(); setSelectedAgents([]); setAgentSearch(String(searchFilters?.agent || '').trim()); setPage(1); }} onKeyDown={(e) => { if (e.key === 'Enter') { e.stopPropagation(); setSelectedAgents([]); setAgentSearch(String(searchFilters?.agent || '').trim()); setPage(1); } }} title="Clear agent filter"><X className="h-3 w-3" /></span>
                      ) : null}
                      <ChevronDown className="h-3 w-3 transition-transform [[data-state=closed]>&]:rotate-[-90deg]" />
                    </div>
                  </CollapsibleTrigger>
                  <CollapsibleContent>
                    <div className="space-y-2">
                      <Input
                        value={agentSearch}
                        onChange={(event) => setAgentSearch(event.target.value)}
                        placeholder="Search agents..."
                        className="h-7 text-xs"
                      />
                      <div className="max-h-[150px] space-y-1 overflow-auto rounded-md border bg-background p-2">
                        {telemetryAgentOptions.filter((option) => option.value).map((option) => (
                          <label key={option.value} className="flex items-center gap-2 text-xs">
                            <Checkbox
                              checked={includesValue(selectedAgents, option.value)}
                              onCheckedChange={() => setSelectedAgents((current) => toggleValue(current, option.value))}
                            />
                            <span>{option.label}</span>
                          </label>
                        ))}
                      </div>
                    </div>
                  </CollapsibleContent>
                </Collapsible>

                <Collapsible defaultOpen>
                  <CollapsibleTrigger className="flex w-full items-center justify-between py-1.5 text-xs uppercase tracking-wide text-muted-foreground hover:text-foreground">
                    <span>Model</span>
                    <div className="flex items-center gap-1">
                      {selectedModels.length ? (
                        <span role="button" tabIndex={0} className="text-muted-foreground hover:text-foreground" onClick={(e) => { e.stopPropagation(); setSelectedModels([]); setPage(1); }} onKeyDown={(e) => { if (e.key === 'Enter') { e.stopPropagation(); setSelectedModels([]); setPage(1); } }} title="Clear model filter"><X className="h-3 w-3" /></span>
                      ) : null}
                      <ChevronDown className="h-3 w-3 transition-transform [[data-state=closed]>&]:rotate-[-90deg]" />
                    </div>
                  </CollapsibleTrigger>
                  <CollapsibleContent>
                    <div className="max-h-[150px] space-y-1 overflow-auto rounded-md border bg-background p-2">
                      {modelOptions.map((model) => (
                        <label key={model} className="flex items-center gap-2 text-xs">
                          <Checkbox
                            checked={includesValue(selectedModels, model)}
                            onCheckedChange={() => setSelectedModels((current) => toggleValue(current, model))}
                          />
                          <span title={model}>{truncate(model, 38)}</span>
                        </label>
                      ))}
                    </div>
                  </CollapsibleContent>
                </Collapsible>

                <Collapsible>
                  <CollapsibleTrigger className="flex w-full items-center justify-between py-1.5 text-xs uppercase tracking-wide text-muted-foreground hover:text-foreground">
                    <span>Heartbeat range</span>
                    <div className="flex items-center gap-1">
                      {(tickMin.trim() || tickMax.trim()) ? (
                        <span role="button" tabIndex={0} className="text-muted-foreground hover:text-foreground" onClick={(e) => { e.stopPropagation(); setTickMin(''); setTickMax(''); setPage(1); }} onKeyDown={(e) => { if (e.key === 'Enter') { e.stopPropagation(); setTickMin(''); setTickMax(''); setPage(1); } }} title="Clear heartbeat filter"><X className="h-3 w-3" /></span>
                      ) : null}
                      <ChevronDown className="h-3 w-3 transition-transform [[data-state=closed]>&]:rotate-[-90deg]" />
                    </div>
                  </CollapsibleTrigger>
                  <CollapsibleContent>
                    <div className="grid grid-cols-2 gap-2">
                      <Input value={tickMin} onChange={(event) => setTickMin(event.target.value)} placeholder="Min" type="number" className="h-7 text-xs" />
                      <Input value={tickMax} onChange={(event) => setTickMax(event.target.value)} placeholder="Max" type="number" className="h-7 text-xs" />
                    </div>
                  </CollapsibleContent>
                </Collapsible>

                <Collapsible>
                  <CollapsibleTrigger className="flex w-full items-center justify-between py-1.5 text-xs uppercase tracking-wide text-muted-foreground hover:text-foreground">
                    <span>Round</span>
                    <div className="flex items-center gap-1">
                      {roundFilter !== 'all' ? (
                        <span role="button" tabIndex={0} className="text-muted-foreground hover:text-foreground" onClick={(e) => { e.stopPropagation(); setRoundFilter('all'); setPage(1); }} onKeyDown={(e) => { if (e.key === 'Enter') { e.stopPropagation(); setRoundFilter('all'); setPage(1); } }} title="Clear round filter"><X className="h-3 w-3" /></span>
                      ) : null}
                      <ChevronDown className="h-3 w-3 transition-transform [[data-state=closed]>&]:rotate-[-90deg]" />
                    </div>
                  </CollapsibleTrigger>
                  <CollapsibleContent>
                    <SelectField
                      ariaLabel="Round filter"
                      value={roundFilter}
                      onChange={setRoundFilter}
                      options={[
                        { value: 'all', label: 'All rounds' },
                        { value: 'round0', label: 'Round 0 only' },
                        { value: 'round1plus', label: 'Round 1+' },
                      ]}
                      triggerClassName="h-7 text-xs"
                    />
                  </CollapsibleContent>
                </Collapsible>

                <Collapsible>
                  <CollapsibleTrigger className="flex w-full items-center justify-between py-1.5 text-xs uppercase tracking-wide text-muted-foreground hover:text-foreground">
                    <span>Cost range</span>
                    <div className="flex items-center gap-1">
                      {(costMin.trim() || costMax.trim()) ? (
                        <span role="button" tabIndex={0} className="text-muted-foreground hover:text-foreground" onClick={(e) => { e.stopPropagation(); setCostMin(''); setCostMax(''); setPage(1); }} onKeyDown={(e) => { if (e.key === 'Enter') { e.stopPropagation(); setCostMin(''); setCostMax(''); setPage(1); } }} title="Clear cost filter"><X className="h-3 w-3" /></span>
                      ) : null}
                      <ChevronDown className="h-3 w-3 transition-transform [[data-state=closed]>&]:rotate-[-90deg]" />
                    </div>
                  </CollapsibleTrigger>
                  <CollapsibleContent>
                    <div className="grid grid-cols-2 gap-2">
                      <Input value={costMin} onChange={(event) => setCostMin(event.target.value)} placeholder="Min $" type="number" className="h-7 text-xs" />
                      <Input value={costMax} onChange={(event) => setCostMax(event.target.value)} placeholder="Max $" type="number" className="h-7 text-xs" />
                    </div>
                  </CollapsibleContent>
                </Collapsible>

                <Collapsible>
                  <CollapsibleTrigger className="flex w-full items-center justify-between py-1.5 text-xs uppercase tracking-wide text-muted-foreground hover:text-foreground">
                    <span>Stop reason</span>
                    <div className="flex items-center gap-1">
                      {selectedStopReasons.length ? (
                        <span role="button" tabIndex={0} className="text-muted-foreground hover:text-foreground" onClick={(e) => { e.stopPropagation(); setSelectedStopReasons([]); setPage(1); }} onKeyDown={(e) => { if (e.key === 'Enter') { e.stopPropagation(); setSelectedStopReasons([]); setPage(1); } }} title="Clear stop reason filter"><X className="h-3 w-3" /></span>
                      ) : null}
                      <ChevronDown className="h-3 w-3 transition-transform [[data-state=closed]>&]:rotate-[-90deg]" />
                    </div>
                  </CollapsibleTrigger>
                  <CollapsibleContent>
                    <div className="max-h-[150px] space-y-1 overflow-auto rounded-md border bg-background p-2">
                      {stopReasonOptions.map((reason) => (
                        <label key={reason} className="flex items-center gap-2 text-xs">
                          <Checkbox
                            checked={includesValue(selectedStopReasons, reason)}
                            onCheckedChange={() => setSelectedStopReasons((current) => toggleValue(current, reason))}
                          />
                          <span title={reason}>{truncate(reason, 40)}</span>
                        </label>
                      ))}
                    </div>
                  </CollapsibleContent>
                </Collapsible>
              </div>
              </aside>
              <div
                role="separator"
                aria-label="Resize filters panel"
                aria-orientation="vertical"
                className="w-px cursor-col-resize bg-border/40 transition-colors hover:bg-border"
                onMouseDown={startFilterResize}
                data-testid="traces-filters-resizer"
              />
            </>
          ) : null}

          <section className="min-h-0 min-w-0 h-full flex-1">
            {isFullPageDetail ? (
              <div className="flex h-full flex-col">
                <div className="flex flex-wrap items-center justify-between gap-3 border-b px-4 py-3">
                  <div className="flex min-w-0 items-center gap-2">
                    <Button type="button" size="sm" variant="outline" onClick={goBackToTraceList}>
                      <ChevronLeft className="h-4 w-4" />
                      Back to traces
                    </Button>
                    <span className="truncate font-mono text-xs text-muted-foreground" title={selectedTraceToken}>
                      {selectedTraceToken || 'No trace selected'}
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      disabled={!previousSortedRow}
                      onClick={() => openFullPageForRow(previousSortedRow)}
                    >
                      <ChevronLeft className="h-4 w-4" />
                      Prev
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      disabled={!nextSortedRow}
                      onClick={() => openFullPageForRow(nextSortedRow)}
                    >
                      Next
                      <ChevronRight className="h-4 w-4" />
                    </Button>
                    <span className="font-mono text-xs text-muted-foreground">{selectionPositionLabel}</span>
                  </div>
                </div>
                <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
                  {selectedRow ? (
                    <RunTraceInspectorContent
                      selectedRow={selectedRow}
                      relatedRounds={relatedRounds}
                      telemetryTail={sourceEvents}
                      telemetryEventType={telemetryEventType}
                      telemetryTick={telemetryTick}
                      telemetryAgentId={telemetryAgentId}
                      promptPartBySha={promptPartBySha}
                      inspectorTab={inspectorTab}
                      onInspectorTabChange={(next) => setInspectorTab(VALID_INSPECTOR_TABS.has(next) ? next : 'preview')}
                      onSelectRoundRow={(nextRowKey) => {
                        const row = rows.find((candidate) => candidate.key === nextRowKey);
                        if (!row) return;
                        setSelectedEventId(row.key);
                        openFullPageForRow(row);
                      }}
                      formatDateTime={formatDateTime}
                      formatNumber={formatNumber}
                    />
                  ) : (
                    <EmptyState description="Trace not found for the current route and filters." />
                  )}
                </div>
              </div>
            ) : (
            <div
              ref={contentLayoutRef}
              className={`${inspectorOpen ? 'grid h-full xl:grid-cols-[minmax(0,1fr)_var(--inspector-width)]' : 'h-full'}`}
              style={inspectorOpen ? { '--inspector-width': `${inspectorWidth}px` } : undefined}
            >
              <div className="min-h-0 min-w-0 h-full overflow-hidden">
                <div className="flex h-full min-h-0 flex-col overflow-hidden">
                  <div className="min-h-0 flex-1 overflow-auto">
                    <Table className="w-full min-w-[900px] table-fixed text-[11px]">
		                        <TableHeader>
		                          <TableRow>
		                            <TableHead className="px-2" style={selectionColumnStyle}>
		                              <Checkbox
		                                aria-label="Select all rows on page"
		                                checked={allPageSelected ? true : (somePageSelected ? 'indeterminate' : false)}
		                                onCheckedChange={(checked) => toggleAllPageSelection(checked)}
		                              />
		                            </TableHead>
                                {visibleColumnKeys.map((columnKey) => {
                                  const column = COLUMN_CONFIG.find((entry) => entry.key === columnKey);
                                  if (!column) return null;
                                  return (
                                    <SortableHead
                                      key={columnKey}
                                      label={column.label}
                                      sortKey={columnKey}
                                      currentSortKey={sortKey}
                                      currentDirection={sortDirection}
                                      onToggle={setSort}
                                      onResizeStart={startColumnResize}
                                      className={columnKey === 'timestamp' ? 'whitespace-nowrap' : ''}
                                      style={columnStyleFor(columnKey)}
                                    />
                                  );
                                })}
		                          </TableRow>
		                        </TableHeader>
                        <TableBody>
                          {groupMode === 'tick' ? (
		                            pagedTickGroups.length ? (
		                              pagedTickGroups.map((group) => {
                                const collapsed = collapsedTickGroups.has(group.key);
                                const groupAllSelected = group.rows.every((row) => selectedRowKeys.has(row.key));
                                const groupSomeSelected = !groupAllSelected && group.rows.some((row) => selectedRowKeys.has(row.key));
                                return (
                                  <React.Fragment key={group.key}>
                                    <TableRow
                                      className="cursor-pointer border-t-2 bg-muted/30 hover:bg-muted/40"
                                      tabIndex={0}
                                      aria-label={`Toggle ${group.tickLabel}`}
                                      onClick={() => toggleTickGroup(group.key)}
                                      onKeyDown={(event) => onTickGroupKeyDown(event, group.key)}
                                    >
	                                      <TableCell className={`${rowDensity.cellClassName} px-2`} style={selectionColumnStyle}>
	                                        <Checkbox
	                                          aria-label={`Select ${group.tickLabel}`}
	                                          checked={groupAllSelected ? true : (groupSomeSelected ? 'indeterminate' : false)}
                                          onClick={(event) => event.stopPropagation()}
                                          onCheckedChange={(checked) => {
                                            const shouldSelect = checked === true;
                                            setSelectedRowKeys((current) => {
                                              const next = new Set(current);
                                              group.rows.forEach((row) => {
                                                if (shouldSelect) next.add(row.key);
                                                else next.delete(row.key);
                                              });
                                              return next;
                                            });
	                                          }}
	                                        />
	                                      </TableCell>
                                      {visibleColumnKeys.map((columnKey) => renderTickGroupCell(columnKey, group, collapsed))}
	                                    </TableRow>
                                    {!collapsed ? group.rows.map((row) => renderEventRow(row, { indented: true })) : null}
                                  </React.Fragment>
                                );
                              })
		                            ) : (
		                              <TableRow>
		                                <TableCell className="py-6 text-center text-[11px] text-muted-foreground" colSpan={(visibleColumnKeys.length || 1) + 1}>
		                                  {noRowsLabel}
		                                </TableCell>
		                              </TableRow>
		                            )
                          ) : (
		                            pagedRows.length ? (
		                              pagedRows.map((row) => renderEventRow(row))
		                            ) : (
		                              <TableRow>
		                                <TableCell className="py-6 text-center text-[11px] text-muted-foreground" colSpan={(visibleColumnKeys.length || 1) + 1}>
		                                  {noRowsLabel}
		                                </TableCell>
		                              </TableRow>
		                            )
		                          )}
		                        </TableBody>
                    </Table>
                  </div>

                  <div className="flex items-center justify-between border-t px-4 py-2 text-[11px] text-muted-foreground">
                    <span>{formatNumber(totalItems)} {groupMode === 'tick' ? 'ticks' : 'rows'}</span>
                    <div className="flex items-center gap-2">
                      <span>Rows per page</span>
                      <SelectField
                        ariaLabel="Rows per page"
                        value={String(rowsPerPage)}
                        onChange={(value) => setRowsPerPage(parseRowsPerPage(value))}
                        options={[
                          { value: '50', label: '50' },
                          { value: '100', label: '100' },
                          { value: '500', label: '500' },
                        ]}
                        triggerClassName="h-8 w-[72px] text-[11px]"
                      />
                      <Button
                        type="button"
                        size="icon"
                        variant="outline"
                        className="h-8 w-8"
                        disabled={page <= 1}
                        onClick={() => setPage(1)}
                        title="First page"
                      >
                        <ChevronsLeft className="h-4 w-4" />
                      </Button>
                      <Button
                        type="button"
                        size="icon"
                        variant="outline"
                        className="h-8 w-8"
                        disabled={page <= 1}
                        onClick={() => setPage((current) => Math.max(1, current - 1))}
                        title="Previous page"
                      >
                        <ChevronLeft className="h-4 w-4" />
                      </Button>
                      <span>Page</span>
                      <SelectField
                        ariaLabel="Page"
                        value={String(page)}
                        onChange={(value) => setPage(parsePage(value, page))}
                        options={pageNumberOptions.map((pageNumber) => ({ value: String(pageNumber), label: String(pageNumber) }))}
                        triggerClassName="h-8 w-[76px] text-[11px]"
                      />
                      <span>of {totalPages}</span>
                      <Button
                        type="button"
                        size="icon"
                        variant="outline"
                        className="h-8 w-8"
                        disabled={page >= totalPages}
                        onClick={() => setPage((current) => Math.min(totalPages, current + 1))}
                        title="Next page"
                      >
                        <ChevronRight className="h-4 w-4" />
                      </Button>
                      <Button
                        type="button"
                        size="icon"
                        variant="outline"
                        className="h-8 w-8"
                        disabled={page >= totalPages}
                        onClick={() => setPage(totalPages)}
                        title="Last page"
                      >
                        <ChevronsRight className="h-4 w-4" />
                      </Button>
                    </div>
                  </div>
                </div>
              </div>

	              {inspectorOpen ? (
	                <aside className="relative flex min-h-0 flex-col border-t bg-background xl:border-t-0 xl:border-l">
                    <div
                      role="separator"
                      aria-label="Resize trace inspector"
                      aria-orientation="vertical"
                      className="absolute left-0 top-0 z-20 hidden h-full w-1 -translate-x-1/2 cursor-col-resize bg-border/40 transition-colors hover:bg-border xl:block"
                      onMouseDown={startInspectorResize}
                    />
	                  <div className="flex items-start justify-between gap-3 border-b px-4 py-3">
	                    <div>
	                      <h2 className="text-sm font-semibold">Trace details</h2>
                      <p className="text-xs text-muted-foreground">Select any table row to inspect details without leaving the list.</p>
                    </div>
                    <div className="flex items-center gap-2">
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        disabled={!selectedRow}
                        onClick={() => openFullPageForRow(selectedRow)}
                      >
                        <ExternalLink className="h-4 w-4" />
                        Full page
                      </Button>
	                      <Button type="button" size="sm" variant="ghost" onClick={() => setInspectorOpen(false)}>
	                        Close
	                      </Button>
	                    </div>
	                  </div>
	                  <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
	                    <RunTraceInspectorContent
                      selectedRow={selectedRow}
                      relatedRounds={relatedRounds}
                      telemetryTail={sourceEvents}
                      telemetryEventType={telemetryEventType}
                      telemetryTick={telemetryTick}
                      telemetryAgentId={telemetryAgentId}
                      promptPartBySha={promptPartBySha}
                      inspectorTab={inspectorTab}
                      onInspectorTabChange={(next) => setInspectorTab(VALID_INSPECTOR_TABS.has(next) ? next : 'preview')}
                      onSelectRoundRow={setSelectedEventId}
                      formatDateTime={formatDateTime}
                      formatNumber={formatNumber}
                    />
                  </div>
                </aside>
              ) : null}
            </div>
            )}
          </section>
      </div>
    </div>
  );
}
