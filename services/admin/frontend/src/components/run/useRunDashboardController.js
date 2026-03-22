import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import { toast } from 'sonner';
import { eventsApi, runApi } from '../../api';
import { formatNumber } from '../../utils/formatters';
import {
  normalizeEventPayload,
} from '../../utils/governanceSignals';
import { asNumber } from '../../utils/telemetryHelpers';

const ACTIVE_RUN_STATUSES = ['running', 'pending', 'starting', 'setup', 'warmup'];
const SUPPORTED_DRILL_FILTERS = new Set(['cost']);
const HISTORY_PAGE_LIMIT = 1000;
const HISTORY_MAX_EVENTS = 10000;

function isActiveStatus(status) {
  const value = String(status || '').toLowerCase();
  return (
    ACTIVE_RUN_STATUSES.includes(value)
    || value.includes('run')
    || value.includes('start')
    || value.includes('warmup')
  );
}

function isPausedStatus(status) {
  return String(status || '').toLowerCase().includes('pause');
}

function isStoppedStatus(status) {
  const value = String(status || '').toLowerCase();
  return (
    value.includes('stop')
    || value.includes('cancel')
    || value.includes('complete')
    || value.includes('timeout')
    || value.includes('timed_out')
    || value.includes('fail')
    || value.includes('error')
  );
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

function historyMatchesDrill(event, drill) {
  if (!drill) return true;

  const payload = normalizeEventPayload(event);
  if (drill === 'cost') return hasCostRelatedSignal(event, payload);

  return true;
}

function formatSchedulerUptime(value) {
  const seconds = asNumber(value);
  if (seconds == null || seconds <= 0) return '0s';

  const rounded = Math.floor(seconds);
  const hours = Math.floor(rounded / 3600);
  const minutes = Math.floor((rounded % 3600) / 60);
  const secs = rounded % 60;

  if (hours > 0) return `${hours}h ${minutes}m`;
  if (minutes > 0) return `${minutes}m ${secs}s`;
  return `${secs}s`;
}

function shortId(value) {
  const text = String(value || '');
  if (text.length <= 12) return text;
  return `${text.slice(0, 6)}...${text.slice(-4)}`;
}

function parseAgentTickKey(value) {
  const text = String(value || '');
  const idx = text.lastIndexOf(':');
  if (idx < 0) return { agentId: text, tick: null };
  const agentId = text.slice(0, idx);
  const tick = Number.parseInt(text.slice(idx + 1), 10);
  return { agentId, tick: Number.isFinite(tick) ? tick : null };
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

function telemetryEventType(event) {
  const payload = normalizeEventPayload(event);
  return payload.event_type || event?.event_type || event?.type || event?.action_type || 'telemetry';
}

function telemetryTick(event) {
  const payload = normalizeEventPayload(event);
  const raw = payload.tick;
  if (typeof raw === 'number' && Number.isFinite(raw)) return raw;
  const parsed = Number.parseInt(String(raw || ''), 10);
  return Number.isFinite(parsed) ? parsed : null;
}

function telemetryHeartbeatIndex(event) {
  const payload = normalizeEventPayload(event);
  const raw = payload.heartbeat_index ?? payload.heartbeatIndex ?? payload.tick;
  if (typeof raw === 'number' && Number.isFinite(raw)) return raw;
  const parsed = Number.parseInt(String(raw || ''), 10);
  return Number.isFinite(parsed) ? parsed : null;
}

function telemetryAgentId(event) {
  return String(event?.agent_id || event?.agentId || event?.agent || event?.actor || '<system>');
}

function extractTokens(event) {
  const payload = normalizeEventPayload(event);
  const input = asNumber(event?.llm_tokens_input ?? event?.tokens_input ?? payload?.llm_tokens_input ?? payload?.tokens_input);
  const output = asNumber(event?.llm_tokens_output ?? event?.tokens_output ?? payload?.llm_tokens_output ?? payload?.tokens_output);
  return {
    input: input ?? 0,
    output: output ?? 0,
  };
}

function extractCostUsd(event) {
  const payload = normalizeEventPayload(event);
  const llm = asNumber(event?.llm_cost_usd ?? payload?.llm_cost_usd) ?? 0;
  return { llm, total: llm };
}

function eventHasUsageOrPricing(event) {
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

export function buildTelemetrySummary(event) {
  const eventType = telemetryEventType(event);
  const payload = normalizeEventPayload(event);

  if (eventType === 'action_attempt') {
    const method = payload.method || payload.http_method || payload.verb || '';
    const target = payload.url || payload.path || '';
    const statusCode = payload.status_code ?? payload.status ?? '';
    const errorCode = payload.error_code || '';
    const preview = payload.response_preview ? String(payload.response_preview) : '';
    const left = [method, target].filter(Boolean).join(' ');
    const right = [statusCode ? `status=${statusCode}` : '', errorCode ? `error=${errorCode}` : '']
      .filter(Boolean)
      .join(' ');
    return [left, right, preview ? `preview=${preview}` : ''].filter(Boolean).join(' | ') || 'action_attempt';
  }

  if (eventType === 'llm_io') {
    const heartbeatIndex = payload.heartbeat_index ?? payload.heartbeatIndex ?? payload.tick ?? '';
    const tick = payload.tick ?? '';
    const parsedRound = Number.parseInt(String(payload.round_index ?? ''), 10);
    const roundIndex = Number.isFinite(parsedRound) ? parsedRound : '';
    const model = payload.model || event?.model || '';
    const parsedCount = payload.parsed_action_count ?? payload.actionable_action_count ?? '';
    return [
      `hb=${heartbeatIndex}`,
      tick !== '' ? `tick=${tick}` : '',
      roundIndex !== '' ? `r=${roundIndex}` : '',
      model ? `model=${model}` : '',
      parsedCount !== '' ? `actions=${parsedCount}` : '',
    ]
      .filter(Boolean)
      .join(' ');
  }

  if (eventType === 'prompt_part') {
    const name = payload.part_name || payload.part_kind || 'prompt_part';
    const sha = payload.part_sha256 ? shortId(payload.part_sha256) : '';
    return sha ? `${name} (${sha})` : String(name || 'prompt_part');
  }

  if (eventType === 'heartbeat_result') {
    const heartbeatIndex = payload.heartbeat_index ?? payload.heartbeatIndex ?? payload.tick ?? '';
    const tick = payload.tick ?? '';
    const actions = payload.actions_executed ?? payload.actions ?? '';
    const stopReason = payload.stop_reason || '';
    const heartbeatStatus = payload.heartbeat_status || '';
    const rounds = payload.rounds_executed ?? '';
    const elapsedMs = payload.elapsed_ms ?? '';
    const error = payload.error_code || payload.error || '';
    return [
      `hb=${heartbeatIndex}`,
      tick !== '' ? `tick=${tick}` : '',
      actions !== '' ? `actions=${actions}` : '',
      heartbeatStatus ? `status=${heartbeatStatus}` : '',
      stopReason ? `stop=${stopReason}` : '',
      rounds !== '' ? `rounds=${rounds}` : '',
      elapsedMs !== '' ? `${elapsedMs}ms` : '',
      error ? `error=${error}` : '',
    ]
      .filter(Boolean)
      .join(' ');
  }

  return (
    payload.summary
    || payload.message
    || payload.reason
    || payload.response_text
    || payload.user_message
    || event?.error_message
    || eventType
  );
}

function copyToClipboard(text) {
  const value = String(text || '');
  if (!value) return Promise.resolve(false);
  if (typeof navigator === 'undefined' || !navigator.clipboard?.writeText) {
    return Promise.resolve(false);
  }
  return navigator.clipboard
    .writeText(value)
    .then(() => true)
    .catch(() => false);
}

function normalizeRunEventRows(response) {
  if (Array.isArray(response?.events)) return response.events;
  if (Array.isArray(response)) return response;
  return [];
}

function eventIdentity(event, index) {
  const explicit = String(event?.event_id || event?.id || '').trim();
  if (explicit) return explicit;
  return [
    String(event?.timestamp || ''),
    String(event?.agent_id || event?.agentId || ''),
    String(event?.event_type || event?.type || ''),
    String(event?.action_name || event?.action_type || ''),
    String(index),
  ].join('::');
}

function dedupeEvents(events = []) {
  const seen = new Set();
  const deduped = [];
  events.forEach((event, index) => {
    const key = eventIdentity(event, index);
    if (seen.has(key)) return;
    seen.add(key);
    deduped.push(event);
  });
  return deduped;
}

function byTimestampDesc(a, b) {
  return String(b?.timestamp || '').localeCompare(String(a?.timestamp || ''));
}

export function useRunDashboardController({ mode = 'live' }) {
  const location = useLocation();
  const navigate = useNavigate();
  const { run_id } = useParams();

  const [run, setRun] = useState(null);
  const [eventCount, setEventCount] = useState(0);
  const [eventTypeCounts, setEventTypeCounts] = useState({});

  const [runCost, setRunCost] = useState(null);
  const [runCostError, setRunCostError] = useState('');
  const [schedulerStatus, setSchedulerStatus] = useState(null);
  const [schedulerStatusError, setSchedulerStatusError] = useState('');

  const [historyEvents, setHistoryEvents] = useState([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState('');
  const [selectedHistoryEvent, setSelectedHistoryEvent] = useState(null);
  const historyEventsRef = useRef([]);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [autoRefresh, setAutoRefresh] = useState(true);

  const [actionBusy, setActionBusy] = useState('');
  const [stopping, setStopping] = useState(false);
  const confirmResolverRef = useRef(null);
  const [confirmDialog, setConfirmDialog] = useState({
    open: false,
    title: '',
    description: '',
    confirmLabel: 'Confirm',
    destructive: false,
  });

  const [filters, setFilters] = useState({ type: '', agent: '' });
  const [streamState, setStreamState] = useState('inactive');
  const [streamLastAt, setStreamLastAt] = useState(null);
  const streamRefreshRef = useRef(0);
  const missingRunHandledRef = useRef(false);

  const telemetryRef = useRef({
    buffer: [],
    seen: new Set(),
    promptPartBySha: new Map(),
    llmIoByAgentTick: new Map(),
    llmIoRoundsByAgentTick: new Map(),
    actionAttemptsByAgentTick: new Map(),
    heartbeatByAgentTick: new Map(),
    lastFlushAt: 0,
    flushPending: false,
  });
  const [telemetryVersion, setTelemetryVersion] = useState(0);
  const [selectedTelemetryEvent, setSelectedTelemetryEvent] = useState(null);
  const selectedTelemetryEventRef = useRef(null);

  const [terminalFilters, setTerminalFilters] = useState({ type: '', agent: '' });

  const [selectedTickKey, setSelectedTickKey] = useState('');
  const [selectedRoundIndex, setSelectedRoundIndex] = useState(0);
  const [selectedPromptPartSha, setSelectedPromptPartSha] = useState('');
  const [copyStatus, setCopyStatus] = useState('');

  const runPathQuery = useMemo(() => location.search || '', [location.search]);

  const runBasePath = useMemo(() => {
    const encodedRunId = encodeURIComponent(run_id || '');
    return `/runs/${encodedRunId}`;
  }, [run_id]);

  const searchFilters = useMemo(() => {
    const params = new URLSearchParams(location.search);
    return {
      drill: normalizeDrillFilter(params.get('drill')),
      type: String(params.get('type') || '').trim(),
      agent: String(params.get('agent') || '').trim(),
    };
  }, [location.search]);

  const runsBackPath = '/runs';

  const runIsActive = useMemo(() => isActiveStatus(run?.status), [run?.status]);
  const runIsPaused = useMemo(() => isPausedStatus(run?.status), [run?.status]);
  const environmentActionMode = useMemo(
    () => ((runIsActive || runIsPaused) ? 'open' : 'relaunch'),
    [runIsActive, runIsPaused]
  );

  const environmentPreviewUrl = useMemo(() => {
    const url = String(run?.frontend_url || '');
    return url.startsWith('http') ? url : '';
  }, [run?.frontend_url]);

  const loadBaseData = useCallback(async () => {
    try {
      setError(null);
      setRunCostError('');
      setSchedulerStatusError('');

      const [runData, countData, costData, schedulerData] = await Promise.all([
        runApi.get(run_id),
        eventsApi.getRunEventCount(run_id),
        runApi.getCost(run_id).catch((err) => {
          setRunCostError(err.message || 'Failed to load run cost');
          return null;
        }),
        runApi.getSchedulerStatus(run_id).catch((err) => {
          setSchedulerStatusError(err.message || 'Failed to load scheduler status');
          return null;
        }),
      ]);

      setRun(runData);
      setEventCount(countData?.count || 0);
      setEventTypeCounts(countData?.event_types || {});
      setRunCost(costData || null);
      setSchedulerStatus(schedulerData || null);
      missingRunHandledRef.current = false;
    } catch (err) {
      if (err?.status === 404) {
        if (!missingRunHandledRef.current) {
          missingRunHandledRef.current = true;
          toast.info(`Run ${run_id} no longer exists. Returning to runs.`);
          navigate(runsBackPath, { replace: true });
        }
        return;
      }
      setRun(null);
      setRunCost(null);
      setSchedulerStatus(null);
      setError(err.message || 'Failed to load run');
    } finally {
      setLoading(false);
    }
  }, [navigate, run_id, runsBackPath]);

  const loadHistory = useCallback(async ({ silent = false } = {}) => {
    if (!run_id) return;
    try {
      if (!silent) {
        setHistoryLoading(true);
      }
      setHistoryError('');

      // Silent refresh: fetch only the newest page and merge it into existing history.
      if (silent && historyEventsRef.current.length) {
        const response = await eventsApi.getRunEvents(run_id, undefined, undefined, HISTORY_PAGE_LIMIT, 0);
        const latestRows = normalizeRunEventRows(response);
        setHistoryEvents((current) => {
          const merged = dedupeEvents(latestRows.concat(current))
            .sort(byTimestampDesc)
            .slice(0, HISTORY_MAX_EVENTS);
          return merged;
        });
        return;
      }

      // Initial/full refresh: hydrate all available events (paginated) so tracing matches backend history.
      const collected = [];
      let offset = 0;
      let total = null;

      while (offset < HISTORY_MAX_EVENTS) {
        const response = await eventsApi.getRunEvents(run_id, undefined, undefined, HISTORY_PAGE_LIMIT, offset);
        const pageRows = normalizeRunEventRows(response);
        if (!pageRows.length) break;

        collected.push(...pageRows);
        offset += pageRows.length;

        if (Number.isFinite(Number(response?.total))) {
          total = Number(response.total);
        }
        if (pageRows.length < HISTORY_PAGE_LIMIT) break;
        if (Number.isFinite(total) && offset >= total) break;
      }

      const rows = dedupeEvents(collected)
        .sort(byTimestampDesc)
        .slice(0, HISTORY_MAX_EVENTS);
      setHistoryEvents(rows);
      setSelectedHistoryEvent((current) => current || (rows.length ? rows[0] : null));
    } catch (err) {
      setHistoryEvents([]);
      setHistoryError(err.message || 'Failed to load run events');
    } finally {
      if (!silent) {
        setHistoryLoading(false);
      }
    }
  }, [run_id]);

  const refreshAll = useCallback(async () => {
    await Promise.all([loadBaseData(), loadHistory()]);
  }, [loadBaseData, loadHistory]);

  useEffect(() => {
    setLoading(true);
    setError(null);
    setRun(null);
    setRunCost(null);
    setRunCostError('');
    setSchedulerStatus(null);
    setSchedulerStatusError('');
    setEventCount(0);
    setEventTypeCounts({});
    setHistoryEvents([]);
    setSelectedHistoryEvent(null);
    setHistoryError('');
    setSelectedTelemetryEvent(null);
    setTerminalFilters({ type: '', agent: '' });
    setFilters({ type: '', agent: '' });
    setSelectedTickKey('');
    setSelectedPromptPartSha('');
    setStreamState('inactive');
    setStreamLastAt(null);
    missingRunHandledRef.current = false;
    streamRefreshRef.current = 0;
    telemetryRef.current = {
      buffer: [],
      seen: new Set(),
      promptPartBySha: new Map(),
      llmIoByAgentTick: new Map(),
      llmIoRoundsByAgentTick: new Map(),
      actionAttemptsByAgentTick: new Map(),
      heartbeatByAgentTick: new Map(),
      lastFlushAt: 0,
      flushPending: false,
    };
    setTelemetryVersion((v) => v + 1);
  }, [run_id]);

  useEffect(() => {
    if (mode !== 'events') return;
    setFilters((current) => {
      if (current.type === searchFilters.type && current.agent === searchFilters.agent) {
        return current;
      }
      return { type: searchFilters.type, agent: searchFilters.agent };
    });
  }, [mode, searchFilters.agent, searchFilters.type]);

  useEffect(() => {
    if (mode !== 'events') return;
    loadHistory();
  }, [loadHistory, mode]);

  useEffect(() => {
    loadBaseData();
  }, [loadBaseData]);

  useEffect(() => {
    loadHistory();
  }, [loadHistory]);

  useEffect(() => {
    selectedTelemetryEventRef.current = selectedTelemetryEvent;
  }, [selectedTelemetryEvent]);

  useEffect(() => {
    historyEventsRef.current = historyEvents;
  }, [historyEvents]);

  useEffect(() => {
    if (!autoRefresh || !runIsActive || !run_id) {
      setStreamState('offline');
      return undefined;
    }

    let pollTimer = null;
    let stream = null;
    const streamPaths = [
      `/api/v1/runs/${encodeURIComponent(run_id)}/stream?history=120`,
      `/api/v1/stream/runs/${encodeURIComponent(run_id)}?history=120`,
    ];
    let streamPathIndex = 0;

    const flushTelemetry = () => {
      const ref = telemetryRef.current;
      const now = Date.now();
      if (now - ref.lastFlushAt < 250) {
        if (!ref.flushPending) {
          ref.flushPending = true;
          window.setTimeout(() => {
            ref.flushPending = false;
            flushTelemetry();
          }, 260);
        }
        return;
      }
      ref.lastFlushAt = now;
      setTelemetryVersion((v) => v + 1);
    };

    const handleTelemetryEvent = (raw) => {
      if (!raw) return;
      let parsed = null;
      try {
        parsed = typeof raw === 'string' ? JSON.parse(raw) : raw;
      } catch (_) {
        return;
      }
      if (!parsed || typeof parsed !== 'object') return;

      const eventId = String(parsed.event_id || parsed.id || '');
      if (!eventId) return;

      const ref = telemetryRef.current;
      if (ref.seen.has(eventId)) return;
      ref.seen.add(eventId);

      ref.buffer.push(parsed);
      if (ref.buffer.length > 1200) {
        const overflow = ref.buffer.length - 1000;
        const removed = ref.buffer.splice(0, overflow);
        removed.forEach((evt) => {
          const rid = String(evt?.event_id || evt?.id || '');
          if (rid) ref.seen.delete(rid);
        });
      }

      const eventType = telemetryEventType(parsed);
      const heartbeatIndex = telemetryHeartbeatIndex(parsed);
      const agentId = telemetryAgentId(parsed);
      const payload = normalizeEventPayload(parsed);

      if (eventType === 'prompt_part') {
        const sha = payload.part_sha256 || payload.partSha256 || payload.sha256;
        if (sha) ref.promptPartBySha.set(String(sha), parsed);
      }

      if (eventType === 'llm_io' && heartbeatIndex !== null) {
        const key = `${agentId}:${heartbeatIndex}`;
        const roundIdxRaw = payload.round_index;
        const parsedRoundIdx = Number.parseInt(String(roundIdxRaw ?? ''), 10);
        const roundIndex = Number.isFinite(parsedRoundIdx) ? parsedRoundIdx : 0;
        const rounds = ref.llmIoRoundsByAgentTick.get(key) || [];
        const existingIdx = rounds.findIndex((evt) => {
          const evtPayload = normalizeEventPayload(evt);
          const evtRoundRaw = evtPayload.round_index;
          const evtRound = Number.parseInt(String(evtRoundRaw ?? ''), 10);
          return Number.isFinite(evtRound) ? evtRound === roundIndex : false;
        });
        if (existingIdx >= 0) {
          rounds.splice(existingIdx, 1, parsed);
        } else {
          rounds.push(parsed);
        }
        rounds.sort((a, b) => {
          const aPayload = normalizeEventPayload(a);
          const bPayload = normalizeEventPayload(b);
          const aRound = Number.parseInt(String(aPayload.round_index ?? ''), 10);
          const bRound = Number.parseInt(String(bPayload.round_index ?? ''), 10);
          const safeA = Number.isFinite(aRound) ? aRound : 0;
          const safeB = Number.isFinite(bRound) ? bRound : 0;
          return safeA - safeB;
        });
        if (rounds.length > 20) rounds.splice(0, rounds.length - 20);
        ref.llmIoRoundsByAgentTick.set(key, rounds);

        const current = ref.llmIoByAgentTick.get(key);
        if (!current) {
          ref.llmIoByAgentTick.set(key, parsed);
        } else {
          const currentPayload = normalizeEventPayload(current);
          const currentRound = Number.parseInt(String(currentPayload.round_index ?? ''), 10);
          const safeCurrentRound = Number.isFinite(currentRound) ? currentRound : 0;
          if (roundIndex >= safeCurrentRound) {
            ref.llmIoByAgentTick.set(key, parsed);
          }
        }
      }

      if (eventType === 'heartbeat_result' && heartbeatIndex !== null) {
        ref.heartbeatByAgentTick.set(`${agentId}:${heartbeatIndex}`, parsed);
      }

      if (eventType === 'action_attempt' && heartbeatIndex !== null) {
        const key = `${agentId}:${heartbeatIndex}`;
        const list = ref.actionAttemptsByAgentTick.get(key) || [];
        list.push(parsed);
        if (list.length > 60) list.splice(0, list.length - 60);
        ref.actionAttemptsByAgentTick.set(key, list);
      }

      if (!selectedTelemetryEventRef.current && (eventType === 'action_attempt' || eventType === 'llm_io')) {
        selectedTelemetryEventRef.current = parsed;
        setSelectedTelemetryEvent(parsed);
      }

      flushTelemetry();
    };

    const refreshRunViews = () => {
      loadBaseData();
      loadHistory({ silent: true });
    };

    const startPolling = (nextState = 'polling') => {
      if (pollTimer) return;
      setStreamState(nextState);
      refreshRunViews();
      pollTimer = setInterval(refreshRunViews, 5000);
    };

    if (typeof window === 'undefined' || typeof window.EventSource === 'undefined') {
      startPolling('polling');
      return () => {
        if (pollTimer) clearInterval(pollTimer);
      };
    }

    const handleStreamActivity = () => {
      setStreamState('healthy');
      setStreamLastAt(Date.now());

      const now = Date.now();
      if (now - streamRefreshRef.current > 5000) {
        streamRefreshRef.current = now;
        refreshRunViews();
      }
    };

    const openStream = () => {
      if (streamPathIndex >= streamPaths.length) {
        startPolling('degraded');
        return;
      }

      setStreamState(streamPathIndex === 0 ? 'connecting' : 'polling');
      stream = new window.EventSource(streamPaths[streamPathIndex]);
      let streamOpened = false;

      stream.onopen = () => {
        streamOpened = true;
        setStreamState('healthy');
        refreshRunViews();
      };

      stream.onmessage = handleStreamActivity;
      stream.addEventListener('connected', handleStreamActivity);
      stream.addEventListener('telemetry', (event) => {
        handleStreamActivity();
        handleTelemetryEvent(event?.data);
      });
      stream.addEventListener('history', (event) => {
        handleStreamActivity();
        handleTelemetryEvent(event?.data);
      });
      stream.addEventListener('heartbeat', () => {
        setStreamLastAt(Date.now());
      });

      stream.onerror = () => {
        if (stream) {
          stream.close();
          stream = null;
        }

        const canTryAlternatePath = !streamOpened && streamPathIndex + 1 < streamPaths.length;
        if (canTryAlternatePath) {
          streamPathIndex += 1;
          openStream();
          return;
        }

        startPolling('degraded');
      };
    };

    openStream();

    return () => {
      if (stream) {
        stream.close();
        stream = null;
      }
      if (pollTimer) clearInterval(pollTimer);
    };
  }, [autoRefresh, loadBaseData, loadHistory, runIsActive, run_id]);

  const streamIndicator = useMemo(() => {
    switch (streamState) {
      case 'healthy':
        return {
          tone: 'healthy',
          label: streamLastAt ? `SSE live ${new Date(streamLastAt).toLocaleTimeString()}` : 'SSE live',
        };
      case 'connecting':
        return { tone: 'polling', label: 'Stream connecting' };
      case 'degraded':
        return { tone: 'degraded', label: 'SSE degraded, polling' };
      case 'polling':
        return { tone: 'polling', label: 'Polling fallback' };
      default:
        return { tone: 'offline', label: 'Stream offline' };
    }
  }, [streamLastAt, streamState]);

  const schedulerIndicator = useMemo(() => {
    if (schedulerStatusError) {
      return {
        tone: 'degraded',
        label: 'unavailable',
        detail: schedulerStatusError,
      };
    }

    const state = String(schedulerStatus?.state || '').toLowerCase();
    if (!state) {
      return {
        tone: 'unknown',
        label: 'unknown',
        detail: 'No scheduler status available yet.',
      };
    }

    const tickCount = asNumber(schedulerStatus?.tick_count);
    const tickSuffix = tickCount == null ? '' : ` · tick ${formatNumber(tickCount)}`;

    let tone = 'unknown';
    if (state.includes('run')) tone = 'healthy';
    else if (state.includes('pause')) tone = 'paused';
    else if (state.includes('stop')) tone = 'offline';
    else if (state.includes('error') || state.includes('fail')) tone = 'failed';
    else if (state.includes('start') || state.includes('pending')) tone = 'polling';

    const detail = String(schedulerStatus?.message || '').trim()
      || `Uptime ${formatSchedulerUptime(schedulerStatus?.uptime_seconds)}`;
    return {
      tone,
      label: `${state}${tickSuffix}`,
      detail,
    };
  }, [schedulerStatus, schedulerStatusError]);

  const runToggleAction = isActiveStatus(run?.status) ? 'pause' : isPausedStatus(run?.status) ? 'resume' : '';
  const canPauseResume = Boolean(runToggleAction);
  const canStopRun = isActiveStatus(run?.status) || isPausedStatus(run?.status);
  const canDeleteRun = Boolean(run?.run_id) && isStoppedStatus(run?.status) && !isPausedStatus(run?.status);
  const canOpenEnvironment = Boolean(run?.run_id) && (Boolean(environmentPreviewUrl) || environmentActionMode === 'relaunch');

  const openConfirmDialog = useCallback(
    ({ title, description, confirmLabel = 'Confirm', destructive = false }) =>
      new Promise((resolve) => {
        confirmResolverRef.current = resolve;
        setConfirmDialog({
          open: true,
          title,
          description,
          confirmLabel,
          destructive,
        });
      }),
    []
  );

  const closeConfirmDialog = useCallback((confirmed) => {
    const resolve = confirmResolverRef.current;
    confirmResolverRef.current = null;
    setConfirmDialog({
      open: false,
      title: '',
      description: '',
      confirmLabel: 'Confirm',
      destructive: false,
    });
    resolve?.(confirmed);
  }, []);

  const handleStopRun = useCallback(async () => {
    if (!run) return;
    const confirmed = await openConfirmDialog({
      title: 'Stop run',
      description: `Stop run ${run.run_id}?`,
      confirmLabel: 'Stop',
      destructive: true,
    });
    if (!confirmed) return;
    setStopping(true);
    setError(null);
    try {
      await runApi.stop(run.run_id);
      toast.success('Run stopped');
      await refreshAll();
    } catch (err) {
      setError(err.message || 'Failed to stop run');
    } finally {
      setStopping(false);
    }
  }, [openConfirmDialog, refreshAll, run]);

  const handlePauseResume = useCallback(async () => {
    if (!run || !runToggleAction) return;
    const confirmed = await openConfirmDialog({
      title: runToggleAction === 'pause' ? 'Pause run' : 'Resume run',
      description: runToggleAction === 'pause' ? `Pause run ${run.run_id}?` : `Resume run ${run.run_id}?`,
      confirmLabel: runToggleAction === 'pause' ? 'Pause' : 'Resume',
      destructive: true,
    });
    if (!confirmed) return;
    setActionBusy(`run:${runToggleAction}`);
    setError(null);
    try {
      if (runToggleAction === 'pause') {
        await runApi.pause(run.run_id);
        toast.success('Run paused');
      } else {
        await runApi.resume(run.run_id);
        toast.success('Run resumed');
      }
      await refreshAll();
    } catch (err) {
      setError(err.message || `Failed to ${runToggleAction} run`);
    } finally {
      setActionBusy('');
    }
  }, [openConfirmDialog, refreshAll, run, runToggleAction]);

  const handleDeleteRun = useCallback(async () => {
    if (!run) return;
    const confirmed = await openConfirmDialog({
      title: 'Delete run',
      description: `Delete run ${run.run_id}? This will tear down run resources.`,
      confirmLabel: 'Delete',
      destructive: true,
    });
    if (!confirmed) return;
    setActionBusy('run:delete');
    setError(null);
    try {
      await runApi.delete(run.run_id);
      toast.success('Run deleted');
      navigate(runsBackPath, { replace: true });
    } catch (err) {
      setError(err.message || 'Failed to delete run');
    } finally {
      setActionBusy('');
    }
  }, [navigate, openConfirmDialog, run, runsBackPath]);

  const handleEnvironmentAction = useCallback(async () => {
    if (!run?.run_id) return;

    if (environmentActionMode === 'open') {
      if (!environmentPreviewUrl) {
        setError('Environment frontend URL is unavailable for this run');
        return;
      }
      window.open(environmentPreviewUrl, '_blank', 'noopener,noreferrer');
      return;
    }

    let previewWindow = null;
    if (typeof window !== 'undefined') {
      previewWindow = window.open('', '_blank');
      if (previewWindow && !previewWindow.closed) {
        previewWindow.document.write('<title>MASE Environment</title><body style="font-family: ui-sans-serif, system-ui; padding: 24px;">Restarting environment stack for inspection...</body>');
      }
    }

    setActionBusy('run:restart-stack');
    setError(null);
    try {
      const response = await runApi.restartStack(run.run_id);
      const nextRun = response?.run || null;
      if (nextRun) {
        setRun(nextRun);
      }
      const nextUrl = String(
        response?.service_urls?.environment_frontend
        || nextRun?.frontend_url
        || run.frontend_url
        || ''
      );

      if (!nextUrl) {
        previewWindow?.close?.();
        throw new Error('Environment frontend URL is unavailable after stack relaunch');
      }

      if (previewWindow && !previewWindow.closed) {
        previewWindow.location.replace(nextUrl);
        previewWindow.focus?.();
      } else {
        window.open(nextUrl, '_blank', 'noopener,noreferrer');
      }

      toast.success(
        response?.stack_status === 'already_running'
          ? 'Environment opened'
          : 'Environment relaunched for inspection'
      );
      await refreshAll();
    } catch (err) {
      previewWindow?.close?.();
      setError(err.message || 'Failed to relaunch environment stack');
    } finally {
      setActionBusy('');
    }
  }, [environmentActionMode, environmentPreviewUrl, refreshAll, run]);

  const costTotals = useMemo(() => {
    const totals = runCost?.totals || {};
    return {
      total: asNumber(totals.total_cost_usd) ?? asNumber(totals.total) ?? 0,
      llm: asNumber(totals.llm_cost_usd) ?? 0,
      tokensIn: asNumber(totals.llm_tokens_input) ?? 0,
      tokensOut: asNumber(totals.llm_tokens_output) ?? 0,
    };
  }, [runCost]);

  const pricingUnavailable = useMemo(() => {
    const llmIoCount = Number(eventTypeCounts?.llm_io || 0);
    if (llmIoCount <= 0) return false;
    if ((costTotals.total || 0) > 0) return false;
    if ((costTotals.tokensIn || 0) > 0 || (costTotals.tokensOut || 0) > 0) return false;
    return !historyEvents.some((event) => telemetryEventType(event) === 'llm_io' && eventHasUsageOrPricing(event));
  }, [costTotals, eventTypeCounts, historyEvents]);

  const agentCount = useMemo(() => {
    const configured = Number.parseInt(String(run?.agent_count ?? ''), 10);
    if (Number.isFinite(configured) && configured > 0) return configured;
    const seen = new Set();
    telemetryRef.current.buffer.forEach((evt) => {
      const agentId = telemetryAgentId(evt);
      if (agentId && agentId !== '<system>') seen.add(agentId);
    });
    return seen.size || null;
  }, [run?.agent_count, telemetryVersion]);

  const costSeries = useMemo(() => {
    const series = Array.isArray(runCost?.series_minute) ? runCost.series_minute : [];
    let cumulative = 0;
    return series.map((row) => {
      const total = asNumber(row?.total_cost_usd) ?? 0;
      cumulative += total;
      return {
        ...row,
        label: minuteLabel(row.bucket_start),
        cumulative_usd: cumulative,
      };
    });
  }, [runCost]);

  const telemetryTail = useMemo(() => {
    const buffer = telemetryRef.current.buffer;
    const slice = buffer.slice(Math.max(0, buffer.length - 200));
    return slice.slice().sort((a, b) => String(b.timestamp || '').localeCompare(String(a.timestamp || '')));
  }, [telemetryVersion]);

  const telemetryEventTypeCounts = useMemo(() => {
    const counts = {};
    telemetryTail.forEach((evt) => {
      const type = telemetryEventType(evt);
      counts[type] = (counts[type] || 0) + 1;
    });
    return counts;
  }, [telemetryTail]);

  const telemetryAgentOptions = useMemo(() => {
    const seen = new Set();
    telemetryRef.current.buffer.forEach((evt) => {
      const agent = telemetryAgentId(evt);
      if (agent) seen.add(agent);
    });
    return [{ value: '', label: 'All agents' }].concat(
      Array.from(seen)
        .sort((a, b) => a.localeCompare(b))
        .map((agent) => ({ value: agent, label: agent }))
    );
  }, [telemetryVersion]);

  const telemetryTypeOptions = useMemo(
    () =>
      [{ value: '', label: 'All event types' }].concat(
        Object.keys(telemetryEventTypeCounts)
          .sort((a, b) => a.localeCompare(b))
          .map((type) => ({ value: type, label: type }))
      ),
    [telemetryEventTypeCounts]
  );

  const filteredTelemetryTail = useMemo(
    () =>
      telemetryTail.filter((evt) => {
        if (terminalFilters.type && telemetryEventType(evt) !== terminalFilters.type) return false;
        if (terminalFilters.agent && telemetryAgentId(evt) !== terminalFilters.agent) return false;
        return true;
      }),
    [telemetryTail, terminalFilters.agent, terminalFilters.type]
  );

  const heartbeatSummaryRows = useMemo(() => {
    const rows = [];
    telemetryRef.current.heartbeatByAgentTick.forEach((evt, key) => {
      const payload = normalizeEventPayload(evt);
      const { agentId, tick } = parseAgentTickKey(key);
      const rounds = asNumber(payload.rounds_executed);
      const modelCalls = asNumber(payload.total_model_calls);
      const elapsedMs = asNumber(payload.elapsed_ms);
      const stopReason = String(payload.stop_reason || '').trim() || '-';
      const heartbeatStatus = String(payload.heartbeat_status || '').trim() || '-';
      const gate = payload.gate && typeof payload.gate === 'object' ? payload.gate : null;
      const gateSummary = gate
        ? String(gate.gate || gate.reason || gate.summary || '').trim()
        : '';
      rows.push({
        key: String(evt?.event_id || `${key}:${evt?.timestamp || ''}`),
        timestamp: evt?.timestamp,
        agentId,
        tick: Number.isFinite(tick) ? tick : null,
        stopReason,
        heartbeatStatus,
        rounds: rounds ?? 0,
        modelCalls: modelCalls ?? 0,
        elapsedMs: elapsedMs ?? 0,
        gateSummary,
      });
    });
    rows.sort((a, b) => {
      const aTick = Number.isFinite(a.tick) ? a.tick : -1;
      const bTick = Number.isFinite(b.tick) ? b.tick : -1;
      if (aTick !== bTick) return bTick - aTick;
      return String(b.timestamp || '').localeCompare(String(a.timestamp || ''));
    });
    return rows.slice(0, 60);
  }, [telemetryVersion]);

  const actionSeries = useMemo(() => {
    const buckets = new Map();
    const source = telemetryRef.current.buffer.length ? telemetryRef.current.buffer : [];

    source.forEach((evt) => {
      const type = telemetryEventType(evt);
      if (type !== 'action_attempt') return;
      const ts = new Date(evt.timestamp || 0);
      if (Number.isNaN(ts.getTime())) return;
      const key = ts.toISOString().slice(0, 16);
      buckets.set(key, (buckets.get(key) || 0) + 1);
    });

    if (!source.length) {
      historyEvents.forEach((evt) => {
        if (String(evt.event_type || '') !== 'action_attempt') return;
        const ts = new Date(evt.timestamp || 0);
        if (Number.isNaN(ts.getTime())) return;
        const key = ts.toISOString().slice(0, 16);
        buckets.set(key, (buckets.get(key) || 0) + 1);
      });
    }

    const sortedKeys = Array.from(buckets.keys()).sort((a, b) => a.localeCompare(b));
    return sortedKeys.slice(Math.max(0, sortedKeys.length - 30)).map((key) => {
      const label = minuteLabel(`${key}:00Z`);
      return { bucket: key, label, actions: buckets.get(key) || 0 };
    });
  }, [historyEvents, telemetryVersion]);

  const tokenSeries = useMemo(() => {
    const buckets = new Map();
    const source = telemetryRef.current.buffer.length ? telemetryRef.current.buffer : [];

    source.forEach((evt) => {
      const tokens = extractTokens(evt);
      if (!tokens.input && !tokens.output) return;
      const ts = new Date(evt.timestamp || 0);
      if (Number.isNaN(ts.getTime())) return;
      const key = ts.toISOString().slice(0, 16);
      const existing = buckets.get(key) || { input: 0, output: 0 };
      existing.input += tokens.input;
      existing.output += tokens.output;
      buckets.set(key, existing);
    });

    if (!source.length) {
      historyEvents.forEach((evt) => {
        const tokens = extractTokens(evt);
        if (!tokens.input && !tokens.output) return;
        const ts = new Date(evt.timestamp || 0);
        if (Number.isNaN(ts.getTime())) return;
        const key = ts.toISOString().slice(0, 16);
        const existing = buckets.get(key) || { input: 0, output: 0 };
        existing.input += tokens.input;
        existing.output += tokens.output;
        buckets.set(key, existing);
      });
    }

    const sortedKeys = Array.from(buckets.keys()).sort((a, b) => a.localeCompare(b));
    return sortedKeys.slice(Math.max(0, sortedKeys.length - 30)).map((key) => {
      const entry = buckets.get(key);
      return { label: minuteLabel(`${key}:00Z`), input: entry.input, output: entry.output };
    });
  }, [historyEvents, telemetryVersion]);

  const filteredHistoryEvents = useMemo(() => {
    const typeFilter = String(filters.type || '').trim();
    const agentFilter = String(filters.agent || '').trim().toLowerCase();

    return historyEvents.filter((event) => {
      const eventType = String(event?.event_type || '').trim();
      const agentId = String(event?.agent_id || '<system>').trim().toLowerCase();

      if (typeFilter && eventType !== typeFilter) return false;
      if (agentFilter && !agentId.includes(agentFilter)) return false;
      if (!historyMatchesDrill(event, searchFilters.drill)) return false;
      return true;
    });
  }, [filters.agent, filters.type, historyEvents, searchFilters.drill]);

  useEffect(() => {
    if (!filteredHistoryEvents.length) {
      setSelectedHistoryEvent(null);
      return;
    }

    setSelectedHistoryEvent((current) => {
      if (!current) return filteredHistoryEvents[0];
      const selectedId = String(current?.event_id || '');
      if (selectedId && filteredHistoryEvents.some((event) => String(event?.event_id || '') === selectedId)) {
        return current;
      }
      return filteredHistoryEvents[0];
    });
  }, [filteredHistoryEvents]);

  const tickOptions = useMemo(() => {
    const keySet = new Set([
      ...Array.from(telemetryRef.current.llmIoByAgentTick.keys()),
      ...Array.from(telemetryRef.current.heartbeatByAgentTick.keys()),
    ]);

      const options = [];
    keySet.forEach((key) => {
      const { agentId, tick } = parseAgentTickKey(key);
      if (!Number.isFinite(tick)) return;
      const heartbeatEvt = telemetryRef.current.heartbeatByAgentTick.get(key);
      const heartbeatPayload = normalizeEventPayload(heartbeatEvt);
      const globalTick = heartbeatEvt ? telemetryTick(heartbeatEvt) : null;
      const stopReason = String(heartbeatPayload.stop_reason || '').trim();
      const status = String(heartbeatPayload.heartbeat_status || '').trim();
      const suffix = stopReason || status ? ` · ${stopReason || status}` : '';
      options.push({
        value: key,
        tick,
        agentId,
        label: `${agentId} / hb ${tick}${globalTick !== null ? ` · tick ${globalTick}` : ''}${suffix}`,
      });
    });

    options.sort((a, b) => b.tick - a.tick || a.agentId.localeCompare(b.agentId));
    return options.map(({ value, label }) => ({ value, label }));
  }, [telemetryVersion]);

  useEffect(() => {
    if (!tickOptions.length) {
      setSelectedTickKey('');
      setSelectedRoundIndex(0);
      return;
    }
    if (selectedTickKey && tickOptions.some((opt) => opt.value === selectedTickKey)) return;
    setSelectedTickKey(tickOptions[0].value);
    setSelectedRoundIndex(0);
  }, [selectedTickKey, tickOptions]);

  const selectedTickParts = selectedTickKey ? parseAgentTickKey(selectedTickKey) : { agentId: '', tick: null };
  const selectedTickAgent = selectedTickParts.agentId || '';
  const selectedTickValue = selectedTickParts.tick;

  const selectedTickHeartbeatEvent = useMemo(() => {
    if (!selectedTickKey) return null;
    return telemetryRef.current.heartbeatByAgentTick.get(selectedTickKey) || null;
  }, [selectedTickKey, telemetryVersion]);

  const selectedTickLlmIoRounds = useMemo(() => {
    if (!selectedTickKey) return [];
    const rounds = telemetryRef.current.llmIoRoundsByAgentTick.get(selectedTickKey) || [];
    if (rounds.length) return rounds;
    const fallbackRound = telemetryRef.current.llmIoByAgentTick.get(selectedTickKey);
    return fallbackRound ? [fallbackRound] : [];
  }, [selectedTickKey, telemetryVersion]);

  useEffect(() => {
    setSelectedRoundIndex((current) => {
      if (!selectedTickLlmIoRounds.length) return 0;
      if (current < 0 || current >= selectedTickLlmIoRounds.length) return 0;
      return current;
    });
  }, [selectedTickLlmIoRounds]);

  const selectedLlmIoEvent = useMemo(() => {
    if (!selectedTickLlmIoRounds.length) return null;
    return selectedTickLlmIoRounds[selectedRoundIndex] || selectedTickLlmIoRounds[0] || null;
  }, [selectedRoundIndex, selectedTickLlmIoRounds]);

  const selectedTickAllActionAttempts = useMemo(() => {
    if (!selectedTickKey) return [];
    return telemetryRef.current.actionAttemptsByAgentTick.get(selectedTickKey) || [];
  }, [selectedTickKey, telemetryVersion]);

  const selectedTickActionAttempts = useMemo(() => {
    const attempts = selectedTickAllActionAttempts;
    if (!attempts.length) return [];
    if (!selectedLlmIoEvent) return attempts;

    const roundPayload = normalizeEventPayload(selectedLlmIoEvent);
    const parsedRound = Number.parseInt(String(roundPayload.round_index ?? ''), 10);
    const hasSelectedRound = Number.isFinite(parsedRound);

    if (hasSelectedRound) {
      const attemptsWithRound = attempts.filter((attempt) => {
        const payload = normalizeEventPayload(attempt);
        const round = Number.parseInt(String(payload.round_index ?? ''), 10);
        return Number.isFinite(round);
      });
      if (attemptsWithRound.length) {
        return attempts.filter((attempt) => {
          const payload = normalizeEventPayload(attempt);
          const round = Number.parseInt(String(payload.round_index ?? ''), 10);
          return Number.isFinite(round) ? round === parsedRound : false;
        });
      }
    }

    const roundStartMs = Date.parse(String(selectedLlmIoEvent.timestamp || ''));
    if (!Number.isFinite(roundStartMs)) return attempts;

    const nextRoundEvent = selectedTickLlmIoRounds[selectedRoundIndex + 1] || null;
    const nextRoundMs = Date.parse(String(nextRoundEvent?.timestamp || ''));
    const heartbeatMs = Date.parse(String(selectedTickHeartbeatEvent?.timestamp || ''));
    const roundEndMs = Number.isFinite(nextRoundMs)
      ? nextRoundMs
      : Number.isFinite(heartbeatMs)
        ? heartbeatMs
        : null;

    const filtered = attempts.filter((attempt) => {
      const timestampMs = Date.parse(String(attempt?.timestamp || ''));
      if (!Number.isFinite(timestampMs)) return false;
      if (timestampMs < roundStartMs) return false;
      if (Number.isFinite(roundEndMs) && timestampMs >= roundEndMs) return false;
      return true;
    });

    return filtered.length ? filtered : attempts;
  }, [
    selectedLlmIoEvent,
    selectedRoundIndex,
    selectedTickAllActionAttempts,
    selectedTickHeartbeatEvent,
    selectedTickLlmIoRounds,
  ]);

  const selectedPromptPartEvent = useMemo(() => {
    if (!selectedPromptPartSha) return null;
    return telemetryRef.current.promptPartBySha.get(selectedPromptPartSha) || null;
  }, [selectedPromptPartSha, telemetryVersion]);

  const reconstructedPromptText = useMemo(() => {
    const evt = selectedLlmIoEvent;
    if (!evt) return '';
    const payload = normalizeEventPayload(evt);
    const parts = Array.isArray(payload.system_prompt_parts) ? payload.system_prompt_parts : [];
    const lines = [];

    lines.push(`# Run ${run_id}`);
    lines.push(`# Agent ${selectedTickAgent || telemetryAgentId(evt)}`);
    lines.push(`# Heartbeat ${String(payload.heartbeat_index ?? payload.heartbeatIndex ?? selectedTickValue ?? '-')}`);
    if (payload.tick != null) {
      lines.push(`# Global Tick ${String(payload.tick)}`);
    }
    if (payload.environment_name || payload.environment_url) {
      lines.push(`# Environment ${String(payload.environment_name || '')} ${String(payload.environment_url || '')}`.trim());
    }
    lines.push('');

    parts.forEach((part) => {
      const name = part?.name || part?.kind || 'prompt_part';
      const sha = part?.sha256 ? String(part.sha256) : '';
      const dynamic = Boolean(part?.dynamic);
      lines.push(`## ${name}${sha ? ` (${sha})` : ''}${dynamic ? ' [dynamic]' : ''}`);

      if (dynamic && part?.content_tail) {
        lines.push(String(part.content_tail));
      } else if (sha && telemetryRef.current.promptPartBySha.has(sha)) {
        const staticEvt = telemetryRef.current.promptPartBySha.get(sha);
        const staticPayload = normalizeEventPayload(staticEvt);
        lines.push(String(staticPayload.content_preview ?? staticPayload.content ?? ''));
      } else {
        lines.push('[static part content not available in session buffer]');
      }

      lines.push('');
    });

    lines.push('## USER_MESSAGE');
    lines.push(String(payload.user_message || ''));

    return lines.join('\n');
  }, [run_id, selectedLlmIoEvent, selectedTickAgent, selectedTickValue, telemetryVersion]);

  const handleCopy = useCallback(async (kind, text) => {
    setCopyStatus('');
    const ok = await copyToClipboard(text);
    setCopyStatus(ok ? `${kind} copied` : 'Copy failed');
    window.setTimeout(() => setCopyStatus(''), 1500);
  }, []);

  const eventTypeOptions = useMemo(() => {
    const options = [{ value: '', label: 'All event types' }].concat(
      Object.keys(eventTypeCounts)
        .sort((a, b) => a.localeCompare(b))
        .map((type) => ({ value: type, label: type }))
    );
    if (filters.type && !options.some((option) => option.value === filters.type)) {
      options.push({ value: filters.type, label: filters.type });
    }
    return options;
  }, [eventTypeCounts, filters.type]);

  const openEnvironmentPreview = useCallback(() => {
    handleEnvironmentAction();
  }, [handleEnvironmentAction]);

  const pageTitle = `Run ${shortId(run_id)}`;
  const pageDescription = run_id;

  return {
    pageTitle,
    pageDescription,
    runBasePath,
    runPathQuery,
    loading,
    run,
    error,
    runCostError,
    schedulerStatusError,
    streamIndicator,
    schedulerIndicator,
    autoRefresh,
    setAutoRefresh,
    refreshAll,
    environmentPreviewUrl,
    openEnvironmentPreview,
    canOpenEnvironment,
    environmentActionMode,
    pricingUnavailable,
    canPauseResume,
    runToggleAction,
    canStopRun,
    canDeleteRun,
    actionBusy,
    stopping,
    handlePauseResume,
    handleStopRun,
    handleDeleteRun,
    searchFilters,
    confirmDialog,
    closeConfirmDialog,
    livePanelProps: {
      run,
      eventCount,
      eventTypeCounts,
      heartbeatSummaryRows,
      agentCount,
      costTotals,
      pricingUnavailable,
      actionSeries,
      costSeries,
      tokenSeries,
      terminalFilters,
      onTerminalFiltersChange: setTerminalFilters,
      telemetryTypeOptions,
      telemetryAgentOptions,
      filteredTelemetryTail,
      telemetryTail,
      historyEvents,
      selectedTelemetryEvent,
      onSelectTelemetryEvent: setSelectedTelemetryEvent,
      telemetryEventType,
      telemetryTick,
      telemetryAgentId,
      buildTelemetrySummary,
      extractCostUsd,
      extractTokens,
    },
    eventsPanelProps: {
      searchFilters,
      filters,
      onFiltersChange: setFilters,
      eventTypeOptions,
      onRefresh: loadHistory,
      historyLoading,
      historyError,
      filteredHistoryEvents,
      historyEvents,
      selectedHistoryEvent,
      onSelectHistoryEvent: setSelectedHistoryEvent,
    },
    tracingPanelProps: {
      runBasePath,
      runPathQuery,
      historyEvents,
      searchFilters,
      telemetryTail,
      telemetryAgentOptions,
      telemetryEventType,
      telemetryTick,
      telemetryAgentId,
      extractCostUsd,
      extractTokens,
      promptPartBySha: telemetryRef.current.promptPartBySha,
    },
    helpers: {
      formatNumber,
    },
  };
}
