import React from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { AlertCircle, CheckCircle2, ExternalLink, Info, Loader2, MoreHorizontal, Pause, Play, RefreshCw, Square } from 'lucide-react';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from 'components/ui/alert-dialog';
import { Alert as ShadcnAlert, AlertDescription, AlertTitle } from 'components/ui/alert';
import { Button } from 'components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from 'components/ui/dropdown-menu';
import { Tabs, TabsList, TabsTrigger } from 'components/ui/tabs';
import { RunLivePanel } from 'components/run/RunLivePanel';
import { RunTracingPanel } from 'components/run/RunTracingPanel';
import { useRunDashboardController } from 'components/run/useRunDashboardController';
import { LoadingState } from 'components/shared/LoadingState';
import { StatusBadge } from 'components/shared/StatusBadge';
import {
  formatDateTime,
  formatNumber,
  formatRunDuration,
  formatTime,
} from '@/utils/formatters';

function toneForAlert(type) {
  if (type === 'error') {
    return { variant: 'destructive', icon: AlertCircle, title: 'Error' };
  }
  if (type === 'warning') {
    return { variant: 'default', icon: AlertCircle, title: 'Warning' };
  }
  if (type === 'success') {
    return { variant: 'default', icon: CheckCircle2, title: 'Success' };
  }
  return { variant: 'default', icon: Info, title: 'Info' };
}

function RunAlert({ type = 'info', message }) {
  if (!message) return null;
  const tone = toneForAlert(type);
  const Icon = tone.icon;

  return (
    <ShadcnAlert variant={tone.variant}>
      <Icon className="h-4 w-4" />
      <AlertTitle>{tone.title}</AlertTitle>
      <AlertDescription>{message}</AlertDescription>
    </ShadcnAlert>
  );
}

function HeaderStat({ label, value }) {
  return (
    <div className="space-y-0.5">
      <p className="text-[10px] uppercase tracking-wider text-muted-foreground">{label}</p>
      <p className="text-sm font-semibold tracking-tight">{value}</p>
    </div>
  );
}

const RunDashboard = ({ mode = 'stats' }) => {
  const navigate = useNavigate();
  const { trace_id } = useParams();
  const controller = useRunDashboardController({ mode });

  const {
    runBasePath,
    runPathQuery,
    loading,
    run,
    error,
    runCostError,
    schedulerStatusError,
    streamIndicator,
    schedulerIndicator,
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
    confirmDialog,
    closeConfirmDialog,
    livePanelProps,
    tracingPanelProps,
  } = controller;
  const normalizedMode = String(mode || '').toLowerCase();
  const selectedMode = normalizedMode === 'stats' || normalizedMode === 'live'
      ? 'stats'
      : 'traces';
  const normalizedTraceId = String(trace_id || '').trim();
  const isTraceDetailMode = selectedMode === 'traces' && Boolean(normalizedTraceId);

  if (loading && !run) {
    return (
      <div className="space-y-4 px-3 pt-3">
        <LoadingState rows={8} />
      </div>
    );
  }

  if (isTraceDetailMode) {
    return (
      <div className="flex h-[calc(100dvh-3.5rem)] min-h-[620px] flex-col">
        <div className="min-h-0 flex-1">
          <RunTracingPanel
            mode="events"
            {...tracingPanelProps}
            fullPageTraceId={normalizedTraceId}
            runBasePath={runBasePath}
            runPathQuery={runPathQuery}
            formatDateTime={formatDateTime}
            formatNumber={formatNumber}
            onRefresh={refreshAll}
          />
        </div>

        <AlertDialog
          open={confirmDialog.open}
          onOpenChange={(nextOpen) => {
            if (!nextOpen) closeConfirmDialog(false);
          }}
        >
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>{confirmDialog.title}</AlertDialogTitle>
              <AlertDialogDescription>{confirmDialog.description}</AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel asChild>
                <Button type="button" variant="outline">Cancel</Button>
              </AlertDialogCancel>
              <AlertDialogAction asChild>
                <Button
                  type="button"
                  variant={confirmDialog.destructive ? 'destructive' : 'default'}
                  onClick={(event) => {
                    event.preventDefault();
                    closeConfirmDialog(true);
                  }}
                >
                  {confirmDialog.confirmLabel}
                </Button>
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </div>
    );
  }

  const duration = run ? formatRunDuration(run.started_at, run.ended_at) : '-';

  return (
    <div className="flex h-[calc(100dvh-3.5rem)] min-h-[620px] flex-col">
      <div className="space-y-2 border-b bg-background px-3 pt-2.5 pb-2">
        {/* Row 1: Tabs + stats */}
        <div className="flex flex-wrap items-center gap-4">
          <Tabs
            value={selectedMode}
            onValueChange={(nextMode) => navigate(`${runBasePath}/${nextMode}${runPathQuery}`)}
            className="w-full md:w-auto"
          >
            <TabsList>
              <TabsTrigger value="stats">Stats</TabsTrigger>
              <TabsTrigger value="traces">Traces</TabsTrigger>
            </TabsList>
          </Tabs>

          <div className="hidden items-center gap-5 md:flex">
            <HeaderStat label="Duration" value={duration} />
            <HeaderStat label="Events" value={formatNumber(livePanelProps.eventCount)} />
            <HeaderStat label="Cost" value={pricingUnavailable ? 'n/a' : `$${formatNumber(livePanelProps.costTotals?.total ?? 0)}`} />
            <HeaderStat label="Agents" value={formatNumber(livePanelProps.agentCount)} />
          </div>
        </div>

        {/* Row 2: Status indicators + actions */}
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge tone={streamIndicator.tone} label={streamIndicator.label} className="h-6 text-xs" />
            <StatusBadge
              tone={schedulerIndicator.tone}
              label={schedulerIndicator.label}
              className="h-6 max-w-[320px] truncate text-xs"
              title={schedulerIndicator.detail}
            />
          </div>

          <div className="flex flex-wrap items-center gap-1.5">
            {selectedMode === 'stats' ? (
              <Button type="button" variant="outline" size="sm" onClick={refreshAll} disabled={loading}>
                {loading ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
                Refresh
              </Button>
            ) : null}

            {canOpenEnvironment ? (
              <Button
                type="button"
                size="sm"
                variant={environmentActionMode === 'open' ? 'default' : 'outline'}
                onClick={openEnvironmentPreview}
                disabled={actionBusy === 'run:restart-stack'}
              >
                {actionBusy === 'run:restart-stack' ? <Loader2 size={14} className="animate-spin" /> : <ExternalLink size={14} />}
                {environmentActionMode === 'open' ? 'Open environment' : 'Relaunch & open environment'}
              </Button>
            ) : null}

            {canPauseResume ? (
              <Button
                type="button"
                size="sm"
                variant={runToggleAction === 'pause' ? 'outline' : 'default'}
                disabled={Boolean(actionBusy)}
                onClick={handlePauseResume}
              >
                {actionBusy === `run:${runToggleAction}` ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : runToggleAction === 'pause' ? (
                  <Pause size={14} />
                ) : (
                  <Play size={14} />
                )}
                {runToggleAction === 'pause' ? 'Pause' : 'Resume'}
              </Button>
            ) : null}

            {canStopRun ? (
              <Button
                type="button"
                size="sm"
                variant="destructive"
                disabled={stopping || Boolean(actionBusy)}
                onClick={handleStopRun}
              >
                {stopping ? <Loader2 size={14} className="animate-spin" /> : <Square size={14} />}
                Stop
              </Button>
            ) : null}

            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button type="button" size="icon" variant="outline" className="h-8 w-8">
                  <MoreHorizontal size={14} />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem
                  className="text-destructive focus:text-destructive"
                  disabled={Boolean(actionBusy) || !canDeleteRun}
                  onClick={handleDeleteRun}
                >
                  {actionBusy === 'run:delete' ? <Loader2 size={14} className="animate-spin" /> : null}
                  Delete run
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </div>

        <RunAlert type="error" message={error} />
        <RunAlert type="warning" message={runCostError} />
        <RunAlert
          type="warning"
          message={schedulerStatusError ? `Scheduler status unavailable: ${schedulerStatusError}` : ''}
        />
      </div>

      {selectedMode === 'stats' ? (
        <div className="px-3 pb-3 pt-2">
          <RunLivePanel
            {...livePanelProps}
            formatDateTime={formatDateTime}
            formatNumber={formatNumber}
            formatRunDuration={formatRunDuration}
            formatTime={formatTime}
          />
        </div>
      ) : selectedMode === 'traces' ? (
        <div className="min-h-0 flex-1">
          <RunTracingPanel
            mode="events"
            {...tracingPanelProps}
            fullPageTraceId={String(trace_id || '').trim()}
            runBasePath={runBasePath}
            runPathQuery={runPathQuery}
            formatDateTime={formatDateTime}
            formatNumber={formatNumber}
            onRefresh={refreshAll}
          />
        </div>
      ) : null}

      <AlertDialog
        open={confirmDialog.open}
        onOpenChange={(nextOpen) => {
          if (!nextOpen) closeConfirmDialog(false);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{confirmDialog.title}</AlertDialogTitle>
            <AlertDialogDescription>{confirmDialog.description}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel asChild>
              <Button type="button" variant="outline">Cancel</Button>
            </AlertDialogCancel>
            <AlertDialogAction asChild>
              <Button
                type="button"
                variant={confirmDialog.destructive ? 'destructive' : 'default'}
                onClick={(event) => {
                  event.preventDefault();
                  closeConfirmDialog(true);
                }}
              >
                {confirmDialog.confirmLabel}
              </Button>
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
};

export default RunDashboard;
