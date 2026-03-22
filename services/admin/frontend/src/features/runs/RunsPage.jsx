import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Activity,
  AlertCircle,
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  Eye,
  PauseCircle,
  PlayCircle,
  Plus,
  RefreshCw,
  Search,
  Square,
  Trash2,
  X,
} from 'lucide-react';
import { Link, useSearchParams } from 'react-router-dom';
import { toast } from 'sonner';
import { platformApi, runApi } from '@/api';
import { LoadingTableRows } from '@/components/shared/LoadingState';
import { PageHeader } from '@/components/shared/PageHeader';
import { StatusBadge, statusTone } from '@/components/shared/StatusBadge';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Field, FieldLabel } from '@/components/ui/field';
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from '@/components/ui/hover-card';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';
import { formatDateTime, formatNumber, formatRunDuration } from '@/utils/formatters';

const SORT_ICON = { asc: ArrowUp, desc: ArrowDown };
const ACTIVE_TONES = new Set(['running', 'paused']);

function shortenValue(value, head = 12, tail = 6) {
  const text = String(value || '').trim();
  if (!text) return 'N/A';
  if (text.length <= head + tail + 3) return text;
  return `${text.slice(0, head)}...${text.slice(-tail)}`;
}

function compareMaybeStrings(left, right) {
  return String(left || '').localeCompare(String(right || ''));
}

function compareMaybeDates(left, right) {
  return new Date(left || 0).getTime() - new Date(right || 0).getTime();
}

function compareMaybeNumbers(left, right) {
  return Number(left || 0) - Number(right || 0);
}

function SortableHeader({ label, sortKey, currentKey, currentDir, onToggle, className }) {
  const active = currentKey === sortKey;
  const Icon = active ? SORT_ICON[currentDir] || ArrowUpDown : ArrowUpDown;

  return (
    <Button
      type="button"
      variant="ghost"
      size="sm"
      className={cn('-ml-3 h-8 gap-1 px-3 text-xs font-semibold uppercase tracking-wide text-muted-foreground', className)}
      onClick={() => onToggle(sortKey)}
    >
      {label}
      <Icon className="h-3.5 w-3.5" />
    </Button>
  );
}

function RowMeta({ value, href, label, secondary }) {
  const text = String(value || '').trim() || label;

  return (
    <div className="space-y-1">
      <HoverCard openDelay={120}>
        <HoverCardTrigger asChild>
          <Link
            className="inline-flex max-w-[220px] truncate font-medium text-primary hover:underline"
            to={href}
          >
            {text}
          </Link>
        </HoverCardTrigger>
        <HoverCardContent align="start" className="w-80 space-y-2">
          <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{label}</div>
          <div className="break-all text-sm text-foreground">{text}</div>
          {secondary ? <div className="text-xs text-muted-foreground">{secondary}</div> : null}
        </HoverCardContent>
      </HoverCard>
      {secondary ? <div className="text-xs text-muted-foreground">{secondary}</div> : null}
    </div>
  );
}

function SnapshotMeta({ snapshotHash }) {
  const fullValue = String(snapshotHash || '').trim();
  const truncatedValue = shortenValue(fullValue, 14, 6);

  return (
    <HoverCard openDelay={120}>
      <HoverCardTrigger asChild>
        <button
          type="button"
          className="max-w-[150px] truncate text-left text-xs text-muted-foreground"
        >
          {truncatedValue}
        </button>
      </HoverCardTrigger>
      <HoverCardContent align="start" className="w-96 space-y-2">
        <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Snapshot</div>
        <div className="break-all font-mono text-xs text-foreground">{fullValue || 'N/A'}</div>
      </HoverCardContent>
    </HoverCard>
  );
}

function InspectActions({ runId }) {
  return (
    <div className="flex items-center justify-end gap-1">
      <Tooltip>
        <TooltipTrigger asChild>
          <Button asChild variant="ghost" size="icon" className="h-8 w-8">
            <Link
              aria-label={`Open dashboard for ${runId}`}
              to={`/runs/${encodeURIComponent(runId)}/stats`}
            >
              <Eye className="h-4 w-4" />
            </Link>
          </Button>
        </TooltipTrigger>
        <TooltipContent>Dashboard</TooltipContent>
      </Tooltip>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button asChild variant="ghost" size="icon" className="h-8 w-8">
            <Link
              aria-label={`Open traces for ${runId}`}
              to={`/runs/${encodeURIComponent(runId)}/traces`}
            >
              <Activity className="h-4 w-4" />
            </Link>
          </Button>
        </TooltipTrigger>
        <TooltipContent>Traces</TooltipContent>
      </Tooltip>
    </div>
  );
}

function RunActions({ run, busyAction, onAction }) {
  const tone = statusTone(run?.status);
  const isRunning = tone === 'running';
  const isPaused = tone === 'paused';
  const canStop = ['running', 'paused'].includes(tone);
  const isBusy = (action) => busyAction === `${action}:${run.run_id}`;

  return (
    <div className="flex items-center justify-end gap-1">
      <Tooltip>
        <TooltipTrigger asChild>
          <span>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="h-8 w-8"
              disabled={!isRunning || !!busyAction}
              aria-label={`Pause ${run.run_id}`}
              onClick={() => onAction({ action: 'pause', fn: runApi.pause, run })}
            >
              <PauseCircle className="h-4 w-4" />
            </Button>
          </span>
        </TooltipTrigger>
        <TooltipContent>Pause</TooltipContent>
      </Tooltip>
      <Tooltip>
        <TooltipTrigger asChild>
          <span>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="h-8 w-8"
              disabled={!isPaused || !!busyAction}
              aria-label={`Resume ${run.run_id}`}
              onClick={() => onAction({ action: 'resume', fn: runApi.resume, run })}
            >
              <PlayCircle className="h-4 w-4" />
            </Button>
          </span>
        </TooltipTrigger>
        <TooltipContent>Resume</TooltipContent>
      </Tooltip>
      <Tooltip>
        <TooltipTrigger asChild>
          <span>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="h-8 w-8"
              disabled={!canStop || !!busyAction}
              aria-label={`Stop ${run.run_id}`}
              onClick={() => onAction({ action: 'stop', fn: runApi.stop, run })}
            >
              <Square className="h-4 w-4" />
            </Button>
          </span>
        </TooltipTrigger>
        <TooltipContent>Stop</TooltipContent>
      </Tooltip>
      <Tooltip>
        <TooltipTrigger asChild>
          <span>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="h-8 w-8 text-destructive hover:text-destructive"
              disabled={!!busyAction}
              aria-label={`Delete ${run.run_id}`}
              onClick={() => onAction({ action: 'delete', fn: runApi.delete, run, requiresConfirm: true })}
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          </span>
        </TooltipTrigger>
        <TooltipContent>Delete</TooltipContent>
      </Tooltip>
      {busyAction && busyAction.endsWith(`:${run.run_id}`) ? (
        <RefreshCw className="ml-1 h-3.5 w-3.5 animate-spin text-muted-foreground" />
      ) : null}
    </div>
  );
}

export default function RunsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [runs, setRuns] = useState([]);
  const [environments, setEnvironments] = useState([]);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('__all__');
  const [busyAction, setBusyAction] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [sortKey, setSortKey] = useState('started_at');
  const [sortDir, setSortDir] = useState('desc');

  const environmentFilter = String(searchParams.get('environment_id') || '').trim();

  const load = useCallback(async () => {
    try {
      setLoading(true);
      const [runsPayload, environmentsPayload] = await Promise.all([
        platformApi.listRuns(environmentFilter ? { environment_id: environmentFilter } : {}),
        platformApi.listEnvironments(),
      ]);
      setRuns(Array.isArray(runsPayload) ? runsPayload : []);
      setEnvironments(Array.isArray(environmentsPayload) ? environmentsPayload : []);
      setError('');
    } catch (err) {
      setError(err.message || 'Failed to load runs.');
    } finally {
      setLoading(false);
    }
  }, [environmentFilter]);

  useEffect(() => {
    load();
  }, [load]);

  const filteredRuns = useMemo(() => {
    const normalizedSearch = search.trim().toLowerCase();
    const baseRows = runs.filter((run) => {
      if (statusFilter !== '__all__' && statusTone(run?.status) !== statusFilter) {
        return false;
      }
      if (!normalizedSearch) return true;
      const haystack = [
        run?.run_id,
        run?.environment_id,
        run?.runtime_id,
        run?.status,
        run?.snapshot_hash,
      ]
        .filter(Boolean)
        .join(' ')
        .toLowerCase();
      return haystack.includes(normalizedSearch);
    });

    const sorted = [...baseRows];
    sorted.sort((left, right) => {
      let result = 0;
      switch (sortKey) {
        case 'run_id':
          result = compareMaybeStrings(left.run_id, right.run_id);
          break;
        case 'environment_id':
          result = compareMaybeStrings(left.environment_id, right.environment_id);
          break;
        case 'status':
          result = compareMaybeStrings(statusTone(left.status), statusTone(right.status));
          break;
        case 'agent_count':
          result = compareMaybeNumbers(left.agent_count, right.agent_count);
          break;
        case 'elapsed_seconds':
          result = compareMaybeNumbers(left.elapsed_seconds, right.elapsed_seconds);
          break;
        case 'started_at':
        default:
          result = compareMaybeDates(left.started_at, right.started_at);
          break;
      }
      return sortDir === 'asc' ? result : -result;
    });
    return sorted;
  }, [runs, search, sortKey, sortDir, statusFilter]);

  const activeCount = useMemo(
    () => filteredRuns.filter((run) => ACTIVE_TONES.has(statusTone(run?.status))).length,
    [filteredRuns]
  );
  const launchTarget = environmentFilter
    ? `/environments/${encodeURIComponent(environmentFilter)}`
    : '/environments';

  const toggleSort = useCallback((nextKey) => {
    setSortKey((currentKey) => {
      if (currentKey === nextKey) {
        setSortDir((currentDir) => (currentDir === 'asc' ? 'desc' : 'asc'));
        return currentKey;
      }
      setSortDir('asc');
      return nextKey;
    });
  }, []);

  const handleAction = useCallback(
    async ({ action, fn, run, requiresConfirm = false }) => {
      if (requiresConfirm) {
        const accepted = window.confirm(`Delete run ${run.run_id}? This cannot be undone.`);
        if (!accepted) return;
      }
      setBusyAction(`${action}:${run.run_id}`);
      try {
        await fn(run.run_id);
        toast.success(`Updated run ${run.run_id}`);
        await load();
      } catch (err) {
        toast.error(err.message || `Failed to update run ${run.run_id}`);
      } finally {
        setBusyAction('');
      }
    },
    [load]
  );

  return (
    <div className="space-y-6">
      <PageHeader
        title="Runs"
        description="Dense operator registry for live and historical executions. Open any row for stats or traces."
      >
        <div className="flex items-center gap-2">
          <Button asChild size="sm" className="gap-2">
            <Link to={launchTarget}>
              <Plus className="h-4 w-4" />
              {environmentFilter ? 'Launch run' : 'New run'}
            </Link>
          </Button>
          <Badge variant="outline">{formatNumber(filteredRuns.length)} shown</Badge>
          <Badge variant="secondary">{formatNumber(activeCount)} active</Badge>
          <Button type="button" variant="outline" size="sm" className="gap-2" onClick={load} disabled={loading}>
            <RefreshCw className={cn('h-4 w-4', loading ? 'animate-spin' : '')} />
            Refresh
          </Button>
        </div>
      </PageHeader>

      {error ? (
        <Alert variant="destructive">
          <AlertCircle className="h-4 w-4" />
          <AlertTitle>Run registry unavailable</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}

      <Card className="border-stone-200/80 bg-stone-50/70 dark:border-stone-800 dark:bg-stone-900/50">
        <CardHeader className="gap-3">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="space-y-1">
              <CardTitle className="text-xl">Run Registry</CardTitle>
              <CardDescription>
                Every row keeps inspection and control actions visible without leaving the table.
              </CardDescription>
            </div>
            <div className="text-xs uppercase tracking-wide text-muted-foreground">
              {environmentFilter ? `Environment scope: ${environmentFilter}` : 'All environments'}
            </div>
          </div>
          <div className="flex flex-wrap items-end gap-3">
            <Field className="min-w-[240px] flex-1">
              <FieldLabel htmlFor="runs-search">Search</FieldLabel>
              <div className="relative">
                <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  id="runs-search"
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                  placeholder="Run, environment, runtime, or snapshot"
                  className="pl-9 pr-9"
                />
                {search ? (
                  <button
                    type="button"
                    aria-label="Clear search"
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                    onClick={() => setSearch('')}
                  >
                    <X className="h-4 w-4" />
                  </button>
                ) : null}
              </div>
            </Field>
            <Field className="w-full md:w-[220px]">
              <FieldLabel>Environment</FieldLabel>
              <Select
                value={environmentFilter || '__all__'}
                onValueChange={(value) => {
                  const nextParams = new URLSearchParams(searchParams);
                  if (value === '__all__') {
                    nextParams.delete('environment_id');
                  } else {
                    nextParams.set('environment_id', value);
                  }
                  setSearchParams(nextParams, { replace: true });
                }}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Filter by environment" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__all__">All environments</SelectItem>
                  {environments.map((environment) => (
                    <SelectItem key={environment.id} value={environment.id}>
                      {environment.name || environment.id}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
            <Field className="w-full md:w-[190px]">
              <FieldLabel>Status</FieldLabel>
              <Select value={statusFilter} onValueChange={setStatusFilter}>
                <SelectTrigger>
                  <SelectValue placeholder="Filter by status" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__all__">All states</SelectItem>
                  <SelectItem value="running">Active</SelectItem>
                  <SelectItem value="paused">Paused</SelectItem>
                  <SelectItem value="completed">Completed</SelectItem>
                  <SelectItem value="failed">Failed / cancelled</SelectItem>
                </SelectContent>
              </Select>
            </Field>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="rounded-lg border bg-background">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-[240px]">
                    <SortableHeader
                      label="Run"
                      sortKey="run_id"
                      currentKey={sortKey}
                      currentDir={sortDir}
                      onToggle={toggleSort}
                    />
                  </TableHead>
                  <TableHead className="w-[220px]">
                    <SortableHeader
                      label="Environment"
                      sortKey="environment_id"
                      currentKey={sortKey}
                      currentDir={sortDir}
                      onToggle={toggleSort}
                    />
                  </TableHead>
                  <TableHead>
                    <SortableHeader
                      label="Status"
                      sortKey="status"
                      currentKey={sortKey}
                      currentDir={sortDir}
                      onToggle={toggleSort}
                    />
                  </TableHead>
                  <TableHead className="text-right">
                    <SortableHeader
                      label="Agents"
                      sortKey="agent_count"
                      currentKey={sortKey}
                      currentDir={sortDir}
                      onToggle={toggleSort}
                      className="ml-auto"
                    />
                  </TableHead>
                  <TableHead>
                    <SortableHeader
                      label="Started"
                      sortKey="started_at"
                      currentKey={sortKey}
                      currentDir={sortDir}
                      onToggle={toggleSort}
                    />
                  </TableHead>
                  <TableHead className="text-right">
                    <SortableHeader
                      label="Duration"
                      sortKey="elapsed_seconds"
                      currentKey={sortKey}
                      currentDir={sortDir}
                      onToggle={toggleSort}
                      className="ml-auto"
                    />
                  </TableHead>
                  <TableHead>Snapshot</TableHead>
                  <TableHead className="text-right">Inspect</TableHead>
                  <TableHead className="text-right">Control</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {loading ? (
                  <LoadingTableRows columns={9} rows={6} />
                ) : filteredRuns.length ? (
                  filteredRuns.map((run) => (
                    <TableRow key={run.run_id} className="align-top">
                      <TableCell className="py-3">
                        <RowMeta
                          value={run.run_id}
                          href={`/runs/${encodeURIComponent(run.run_id)}/stats`}
                          label="Run ID"
                          secondary={run.runtime_id || 'runtime not declared'}
                        />
                      </TableCell>
                      <TableCell className="py-3">
                        <RowMeta
                          value={run.environment_id}
                          href={`/environments/${encodeURIComponent(run.environment_id)}`}
                          label="Environment ID"
                          secondary="Environment package"
                        />
                      </TableCell>
                      <TableCell className="py-3">
                        <StatusBadge status={run.status} />
                      </TableCell>
                      <TableCell className="py-3 text-right font-medium">
                        {formatNumber(run.agent_count || 0)}
                      </TableCell>
                      <TableCell className="py-3 text-sm">
                        {formatDateTime(run.started_at)}
                      </TableCell>
                      <TableCell className="py-3 text-right text-sm">
                        {formatRunDuration(run.started_at, run.ended_at)}
                      </TableCell>
                      <TableCell className="py-3">
                        <SnapshotMeta snapshotHash={run.snapshot_hash} />
                      </TableCell>
                      <TableCell className="py-3">
                        <InspectActions runId={run.run_id} />
                      </TableCell>
                      <TableCell className="py-3">
                        <RunActions run={run} busyAction={busyAction} onAction={handleAction} />
                      </TableCell>
                    </TableRow>
                  ))
                ) : (
                  <TableRow>
                    <TableCell colSpan={9} className="py-10 text-center text-sm text-muted-foreground">
                      No runs match the current filters.
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
