import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { AlertCircle, Loader2, PauseCircle, PlayCircle, RefreshCw } from 'lucide-react';
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
import { Alert, AlertDescription, AlertTitle } from 'components/ui/alert';
import { Button } from 'components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from 'components/ui/card';
import { Field, FieldLabel } from 'components/ui/field';
import { Form, FormActions, FormSection } from 'components/ui/form';
import { Input } from 'components/ui/input';
import { ScrollArea } from 'components/ui/scroll-area';
import { Skeleton } from 'components/ui/skeleton';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from 'components/ui/table';
import { environmentApi, healthApi, platformSettingsApi } from '@/api';
import { formatDateTime } from '@/utils/formatters';
import { PageHeader } from 'components/shared/PageHeader';
import { StatusBadge } from 'components/shared/StatusBadge';
import { KpiCard } from 'components/shared/KpiCard';
import { useConfirmAction } from '@/hooks/useConfirmAction';

function probeState(probe) {
  if (!probe) return { label: 'unavailable', tone: 'other' };
  if (probe.error) return { label: 'error', tone: 'failed' };

  const value = String(
    probe.status ?? probe.state ?? probe.health ?? probe.readiness ?? probe.live ?? ''
  ).toLowerCase();

  const healthy =
    probe.ok === true ||
    probe.healthy === true ||
    ['ok', 'healthy', 'ready', 'live', 'up', 'pass'].includes(value);
  if (healthy) return { label: 'healthy', tone: 'active' };

  const failed =
    probe.ok === false ||
    probe.healthy === false ||
    value.includes('fail') || value.includes('down') || value.includes('error') || value.includes('unready');
  if (failed) return { label: value || 'failed', tone: 'failed' };

  return { label: value || 'unknown', tone: 'other' };
}

function probeDetails(probe) {
  if (!probe) return 'No response';
  if (probe.error) return probe.error;

  const entries = Object.entries(probe)
    .filter(([key]) => !['status', 'state', 'health', 'readiness', 'live', 'healthy', 'ok'].includes(key))
    .slice(0, 3);

  if (entries.length === 0) return 'Status metadata only';
  return entries
    .map(([key, value]) => `${key}: ${typeof value === 'object' ? JSON.stringify(value) : String(value)}`)
    .join(' | ');
}

function environmentName(env) {
  return env?.name || env?.id || env?.key || '';
}

function environmentStatus(env) {
  const raw = String(
    env?.status ?? env?.state ??
      (typeof env?.running === 'boolean' ? (env.running ? 'running' : 'stopped') : 'unknown')
  ).toLowerCase();

  if (raw.includes('run') || raw.includes('up') || raw.includes('ready')) return { label: raw, tone: 'running' };
  if (raw.includes('stop') || raw.includes('down')) return { label: raw, tone: 'stopped' };
  if (raw.includes('error') || raw.includes('fail')) return { label: raw, tone: 'failed' };
  return { label: raw || 'unknown', tone: 'other' };
}

const ConfigPage = () => {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [actionError, setActionError] = useState('');
  const [busyAction, setBusyAction] = useState('');
  const [openRouterSettings, setOpenRouterSettings] = useState({
    openrouter_api_key_configured: false,
    openrouter_api_key_masked: null,
    source: 'missing',
  });
  const [openRouterInput, setOpenRouterInput] = useState('');
  const [openRouterSaving, setOpenRouterSaving] = useState(false);
  const [openRouterMessage, setOpenRouterMessage] = useState('');
  const [openRouterMessageType, setOpenRouterMessageType] = useState('success');
  const [snapshot, setSnapshot] = useState({
    health: null,
    liveness: null,
    readiness: null,
    environments: [],
    loadedAt: '',
  });
  const [openRouterSettingsWarning, setOpenRouterSettingsWarning] = useState('');
  const { dialog, confirm, close } = useConfirmAction();

  const loadData = useCallback(async () => {
    setLoading(true);
    setError('');
    setOpenRouterMessage('');
    setOpenRouterSettingsWarning('');

    const [healthResult, livenessResult, readinessResult, environmentsResult, openRouterSettingsResult] = await Promise.allSettled([
      healthApi.get(),
      healthApi.liveness(),
      healthApi.readiness(),
      environmentApi.list(),
      platformSettingsApi.getOpenRouterSettings(),
    ]);

    setSnapshot({
      health: healthResult.status === 'fulfilled' ? healthResult.value : { error: healthResult.reason?.message || 'Unavailable' },
      liveness: livenessResult.status === 'fulfilled' ? livenessResult.value : { error: livenessResult.reason?.message || 'Unavailable' },
      readiness: readinessResult.status === 'fulfilled' ? readinessResult.value : { error: readinessResult.reason?.message || 'Unavailable' },
      environments: environmentsResult.status === 'fulfilled' && Array.isArray(environmentsResult.value) ? environmentsResult.value : [],
      loadedAt: new Date().toISOString(),
    });

    if (environmentsResult.status !== 'fulfilled') {
      setError(environmentsResult.reason?.message || 'Environment registry unavailable');
    }
    if (openRouterSettingsResult.status === 'fulfilled') {
      setOpenRouterSettings(openRouterSettingsResult.value);
    } else {
      setOpenRouterSettings({ openrouter_api_key_configured: false, openrouter_api_key_masked: null, source: 'missing' });
      setOpenRouterSettingsWarning(openRouterSettingsResult.reason?.message || 'OpenRouter settings could not be loaded.');
    }

    setLoading(false);
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const probes = useMemo(
    () => [
      { key: 'health', endpoint: '/api/health', label: 'Health', data: snapshot.health },
      { key: 'liveness', endpoint: '/api/health/live', label: 'Liveness', data: snapshot.liveness },
      { key: 'readiness', endpoint: '/api/health/ready', label: 'Readiness', data: snapshot.readiness },
    ],
    [snapshot.health, snapshot.liveness, snapshot.readiness]
  );

  const healthyProbeCount = useMemo(() => {
    return probes.filter((probe) => probeState(probe.data).tone === 'active').length;
  }, [probes]);

  const environmentRows = useMemo(() => {
    return snapshot.environments.map((env) => {
      const name = environmentName(env);
      const status = environmentStatus(env);
      return { key: name || JSON.stringify(env), name, status, version: env.version || '-', description: env.description || '-' };
    });
  }, [snapshot.environments]);

  const handleEnvironmentAction = async (record, action) => {
    if (!record.name) return;
    if (action === 'stop') {
      const confirmed = await confirm({
        title: `Stop environment ${record.name}?`,
        description: 'This stops the selected environment runtime.',
        confirmLabel: 'Stop',
        destructive: true,
      });
      if (!confirmed) return;
    }

    setBusyAction(`${action}:${record.name}`);
    setActionError('');
    try {
      if (action === 'start') await environmentApi.start(record.name);
      else await environmentApi.stop(record.name);
      await loadData();
    } catch (actionFailure) {
      setActionError(actionFailure.message || `Failed to ${action} environment`);
    } finally {
      setBusyAction('');
    }
  };

  const handleOpenRouterSave = async (value) => {
    setOpenRouterSaving(true);
    setOpenRouterMessage('');
    setActionError('');

    try {
      const payload = await platformSettingsApi.updateOpenRouterSettings(value === '' ? '' : value.trim());
      setOpenRouterSettings(payload);
      setOpenRouterInput('');
      if (payload.openrouter_api_key_configured) {
        const sourceLabel = payload.source === 'custom' ? 'admin Config' : 'environment configuration';
        setOpenRouterMessage(`General OpenRouter key saved. Runs will use this ${sourceLabel} key when no per-run key is provided.`);
      } else if (payload.source === 'environment') {
        setOpenRouterMessage('General key cleared. Runs will use the environment key if it is configured.');
      } else {
        setOpenRouterMessage('No general OpenRouter key is configured. Runs require a per-run key until this is set.');
      }
      setOpenRouterMessageType('success');
    } catch (err) {
      setOpenRouterMessage(err.message || 'Failed to save OpenRouter key');
      setOpenRouterMessageType('error');
    } finally {
      setOpenRouterSaving(false);
    }
  };

  const sourceLabel = useMemo(() => {
    if (openRouterSettings.source === 'custom') return 'Configured in Admin UI';
    if (openRouterSettings.source === 'environment') return 'Configured by environment';
    return 'Not configured';
  }, [openRouterSettings.source]);

  return (
    <div className="space-y-6">
      <PageHeader title="Config" description="Health probes and environment lifecycle controls.">
        <Button variant="outline" size="sm" onClick={loadData} disabled={loading}>
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
          Refresh
        </Button>
      </PageHeader>

      {error ? (
        <Alert variant="destructive">
          <AlertCircle className="h-4 w-4" />
          <AlertTitle>Error</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}
      {openRouterSettingsWarning ? (
        <Alert>
          <AlertCircle className="h-4 w-4" />
          <AlertTitle>Warning</AlertTitle>
          <AlertDescription>{openRouterSettingsWarning}</AlertDescription>
        </Alert>
      ) : null}
      {actionError ? (
        <Alert variant="destructive">
          <AlertCircle className="h-4 w-4" />
          <AlertTitle>Error</AlertTitle>
          <AlertDescription>{actionError}</AlertDescription>
        </Alert>
      ) : null}

      {loading ? (
        <div className="grid gap-4 sm:grid-cols-3">
          <Skeleton className="h-24" />
          <Skeleton className="h-24" />
          <Skeleton className="h-24" />
        </div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-3">
          <KpiCard
            label="Health probes"
            value={`${healthyProbeCount}/${probes.length}`}
            sub={healthyProbeCount === probes.length ? 'All healthy' : 'Some probes degraded'}
          />
          <KpiCard label="Environments" value={String(environmentRows.length)} sub="Registered templates" />
          <KpiCard label="Last refresh" value={snapshot.loadedAt ? formatDateTime(snapshot.loadedAt) : '-'} />
        </div>
      )}

      <Card>
        <CardHeader className="border-b py-4">
          <CardTitle className="text-base">Health probes</CardTitle>
        </CardHeader>
        <CardContent className="pt-4">
          <ScrollArea className="rounded-md border">
            <Table>
              <caption className="sr-only">Health probes table</caption>
              <TableHeader>
                <TableRow>
                  <TableHead>Probe</TableHead>
                  <TableHead>Endpoint</TableHead>
                  <TableHead className="w-36">Status</TableHead>
                  <TableHead>Details</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {probes.map((probe) => {
                  const status = probeState(probe.data);
                  return (
                    <TableRow key={probe.key}>
                      <TableCell>{probe.label}</TableCell>
                      <TableCell>
                        <span className="font-mono text-sm">{probe.endpoint}</span>
                      </TableCell>
                      <TableCell>
                        <StatusBadge status={status.label} tone={status.tone} />
                      </TableCell>
                      <TableCell>{probeDetails(probe.data)}</TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </ScrollArea>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="border-b py-4">
          <CardTitle className="text-base">General OpenRouter key</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4 pt-4">
          <Form
            className="space-y-4"
            onSubmit={(event) => {
              event.preventDefault();
              handleOpenRouterSave(openRouterInput);
            }}
          >
            <FormSection>
              <Field className="gap-1.5">
                <FieldLabel htmlFor="openrouter-general-key">General OpenRouter key</FieldLabel>
                <Input
                  id="openrouter-general-key"
                  className="w-full"
                  value={openRouterInput}
                  onChange={(event) => setOpenRouterInput(event.target.value)}
                  type="password"
                  placeholder="General key used for all runs when no per-run key is provided"
                />
                <p className="text-sm text-muted-foreground">
                  Current: {openRouterSettings.openrouter_api_key_configured ? sourceLabel : 'not configured'}.
                  {openRouterSettings.openrouter_api_key_masked ? (
                    <> Masked value: <span className="font-mono">{openRouterSettings.openrouter_api_key_masked}</span></>
                  ) : null}
                </p>
              </Field>
            </FormSection>

            <FormActions className="justify-start">
              <Button type="submit" size="sm" disabled={openRouterSaving}>
                {openRouterSaving ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                Save key
              </Button>
              <Button
                type="button"
                variant="destructive"
                size="sm"
                onClick={async () => {
                  const confirmed = await confirm({
                    title: 'Clear general OpenRouter key?',
                    description: 'Runs will stop using the general key until another key is configured.',
                    confirmLabel: 'Clear key',
                    destructive: true,
                  });
                  if (!confirmed) return;
                  handleOpenRouterSave('');
                }}
                disabled={openRouterSaving}
              >
                Clear general key
              </Button>
            </FormActions>
          </Form>

          {openRouterMessage ? (
            <Alert variant={openRouterMessageType === 'error' ? 'destructive' : 'default'}>
              <AlertCircle className="h-4 w-4" />
              <AlertTitle>{openRouterMessageType === 'error' ? 'Error' : 'Info'}</AlertTitle>
              <AlertDescription>{openRouterMessage}</AlertDescription>
            </Alert>
          ) : null}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="border-b py-4">
          <CardTitle className="text-base">Environment lifecycle controls</CardTitle>
        </CardHeader>
        <CardContent className="pt-4">
          <ScrollArea className="rounded-md border">
            <Table>
              <caption className="sr-only">Environment lifecycle controls table</caption>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead className="w-28">Status</TableHead>
                  <TableHead className="w-28">Version</TableHead>
                  <TableHead>Description</TableHead>
                  <TableHead className="w-48">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {environmentRows.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={5} className="py-8 text-center text-sm text-muted-foreground">
                      {loading ? 'Loading environments...' : 'No environments available'}
                    </TableCell>
                  </TableRow>
                ) : (
                  environmentRows.map((record) => {
                    const starting = busyAction === `start:${record.name}`;
                    const stopping = busyAction === `stop:${record.name}`;
                    const disabled = Boolean(busyAction) || !record.name;

                    return (
                      <TableRow key={record.key}>
                        <TableCell>
                          <span className="font-mono text-sm">{record.name || 'unknown'}</span>
                        </TableCell>
                        <TableCell>
                          <StatusBadge status={record.status.label} tone={record.status.tone} />
                        </TableCell>
                        <TableCell>
                          <span className="font-mono text-sm">{record.version}</span>
                        </TableCell>
                        <TableCell>{record.description}</TableCell>
                        <TableCell>
                          <div className="inline-flex items-center gap-1.5">
                            <Button size="sm" disabled={disabled} onClick={() => handleEnvironmentAction(record, 'start')}>
                              {starting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <PlayCircle className="h-3.5 w-3.5" />}
                              Start
                            </Button>
                            <Button size="sm" variant="destructive" disabled={disabled} onClick={() => handleEnvironmentAction(record, 'stop')}>
                              {stopping ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <PauseCircle className="h-3.5 w-3.5" />}
                              Stop
                            </Button>
                          </div>
                        </TableCell>
                      </TableRow>
                    );
                  })
                )}
              </TableBody>
            </Table>
          </ScrollArea>
        </CardContent>
      </Card>

      <AlertDialog open={dialog.open} onOpenChange={(nextOpen) => { if (!nextOpen) close(false); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{dialog.title}</AlertDialogTitle>
            <AlertDialogDescription>{dialog.description}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel asChild>
              <Button type="button" variant="outline">Cancel</Button>
            </AlertDialogCancel>
            <AlertDialogAction asChild>
              <Button
                type="button"
                variant={dialog.destructive ? 'destructive' : 'default'}
                onClick={(event) => { event.preventDefault(); close(true); }}
              >
                {dialog.confirmLabel}
              </Button>
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
};

export default ConfigPage;
