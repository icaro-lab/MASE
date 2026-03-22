import React, { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { toast } from 'sonner';
import { platformApi } from '@/api';
import { PageHeader } from '@/components/shared/PageHeader';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';

function coerceParamValue(value, schema) {
  if (schema?.type === 'integer') {
    const parsed = Number.parseInt(String(value || '').trim(), 10);
    return Number.isFinite(parsed) ? parsed : schema?.default ?? 0;
  }
  return value;
}

export default function EnvironmentDetailPage() {
  const { environment_id } = useParams();
  const navigate = useNavigate();
  const [environment, setEnvironment] = useState(null);
  const [runtime, setRuntime] = useState(null);
  const [validation, setValidation] = useState(null);
  const [paramsState, setParamsState] = useState({});
  const [populationsState, setPopulationsState] = useState({});
  const [recentRuns, setRecentRuns] = useState([]);
  const [loading, setLoading] = useState(true);
  const [launching, setLaunching] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    async function load() {
      if (!environment_id) return;
      setLoading(true);
      try {
        const environmentPayload = await platformApi.getEnvironment(environment_id);
        const [runtimePayload, validationPayload, recentRunsPayload] = await Promise.all([
          platformApi.getRuntime(environmentPayload.runtime),
          platformApi.validateEnvironment(environment_id),
          platformApi.listRuns({ environment_id }),
        ]);
        if (cancelled) return;
        setEnvironment(environmentPayload);
        setRuntime(runtimePayload);
        setValidation(validationPayload);
        setRecentRuns(Array.isArray(recentRunsPayload) ? recentRunsPayload : []);
        const nextParams = {};
        Object.entries(environmentPayload?.params_schema || {}).forEach(([key, schema]) => {
          if (schema && Object.prototype.hasOwnProperty.call(schema, 'default')) {
            nextParams[key] = schema.default;
          } else {
            nextParams[key] = '';
          }
        });
        const nextPopulations = {};
        Object.entries(environmentPayload?.populations || {}).forEach(([populationId, spec]) => {
          nextPopulations[populationId] = {
            count: spec?.default_count ?? 0,
            model: spec?.default_model ?? '',
          };
        });
        setParamsState(nextParams);
        setPopulationsState(nextPopulations);
        setError('');
      } catch (err) {
        if (!cancelled) {
          setError(err.message || 'Failed to load environment.');
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [environment_id]);

  const totalAgents = useMemo(
    () => Object.values(populationsState).reduce((sum, row) => sum + Number.parseInt(String(row?.count || 0), 10), 0),
    [populationsState]
  );

  async function handleLaunch() {
    if (!environment_id) return;
    setLaunching(true);
    try {
      const params = {};
      Object.entries(environment?.params_schema || {}).forEach(([key, schema]) => {
        params[key] = coerceParamValue(paramsState[key], schema);
      });
      const populationOverrides = {};
      Object.entries(populationsState).forEach(([populationId, spec]) => {
        populationOverrides[populationId] = {
          count: Number.parseInt(String(spec?.count || 0), 10),
          model: spec?.model || undefined,
        };
      });
      const run = await platformApi.createRun({
        environment_id,
        params,
        population_overrides: populationOverrides,
      });
      toast.success(`Run ${run.run_id} started`);
      navigate(`/runs/${encodeURIComponent(run.run_id)}/stats`);
    } catch (err) {
      toast.error(err.message || 'Failed to start run');
    } finally {
      setLaunching(false);
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title={environment?.name || environment_id || 'Environment'}
        description={environment?.description || 'Environment package'}
      >
        {environment?.runtime ? <Badge variant="outline">{environment.runtime}</Badge> : null}
      </PageHeader>

      {loading ? <div className="text-sm text-muted-foreground">Loading environment…</div> : null}
      {error ? <div className="text-sm text-destructive">{error}</div> : null}

      {!loading && environment ? (
        <div className="grid gap-6 xl:grid-cols-[1.3fr_0.9fr]">
          <Card>
            <CardHeader>
              <CardTitle>Launch Run</CardTitle>
              <CardDescription>Create a run directly from this environment package.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
              <div className="grid gap-4 md:grid-cols-2">
                {Object.entries(environment?.params_schema || {}).map(([key, schema]) => (
                  <div key={key} className="space-y-2">
                    <Label htmlFor={`param-${key}`}>{key}</Label>
                    {schema?.type === 'enum' ? (
                      <Select
                        value={String(paramsState[key] ?? '')}
                        onValueChange={(value) => setParamsState((current) => ({ ...current, [key]: value }))}
                      >
                        <SelectTrigger id={`param-${key}`}>
                          <SelectValue placeholder={`Select ${key}`} />
                        </SelectTrigger>
                        <SelectContent>
                          {(schema?.values || []).map((value) => (
                            <SelectItem key={value} value={String(value)}>
                              {String(value)}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    ) : (
                      <Input
                        id={`param-${key}`}
                        type={schema?.type === 'integer' ? 'number' : 'text'}
                        value={String(paramsState[key] ?? '')}
                        onChange={(event) =>
                          setParamsState((current) => ({ ...current, [key]: event.target.value }))
                        }
                      />
                    )}
                  </div>
                ))}
              </div>

              <div className="space-y-4">
                <div className="flex items-center justify-between">
                  <div className="font-medium text-foreground">Populations</div>
                  <Badge variant="secondary">{totalAgents} agents</Badge>
                </div>
                <div className="space-y-4">
                  {Object.entries(environment?.populations || {}).map(([populationId, spec]) => (
                    <div key={populationId} className="grid gap-3 rounded-lg border p-4 md:grid-cols-3">
                      <div className="space-y-1 md:col-span-3">
                        <div className="font-medium">{populationId}</div>
                        <div className="text-xs uppercase tracking-wide text-muted-foreground">Environment-owned role</div>
                        <div className="text-sm text-muted-foreground">
                          path: {spec?.path || `populations/${populationId}`}
                        </div>
                      </div>
                      <div className="space-y-2">
                        <Label htmlFor={`${populationId}-count`}>Count</Label>
                        <Input
                          id={`${populationId}-count`}
                          type="number"
                          min="0"
                          value={String(populationsState?.[populationId]?.count ?? 0)}
                          onChange={(event) =>
                            setPopulationsState((current) => ({
                              ...current,
                              [populationId]: {
                                ...current[populationId],
                                count: event.target.value,
                              },
                            }))
                          }
                        />
                      </div>
                      <div className="space-y-2">
                        <Label htmlFor={`${populationId}-model`}>Model</Label>
                        <Input
                          id={`${populationId}-model`}
                          value={String(populationsState?.[populationId]?.model ?? '')}
                          onChange={(event) =>
                            setPopulationsState((current) => ({
                              ...current,
                              [populationId]: {
                                ...current[populationId],
                                model: event.target.value,
                              },
                            }))
                          }
                        />
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              <div className="flex gap-3">
                <Button onClick={handleLaunch} disabled={launching}>
                  {launching ? 'Starting…' : 'Start run'}
                </Button>
                <Button variant="outline" onClick={() => navigate(`/runs?environment_id=${encodeURIComponent(environment_id || '')}`)}>
                  View runs
                </Button>
              </div>
            </CardContent>
          </Card>

          <div className="space-y-6">
            <Card>
              <CardHeader>
                <CardTitle>Environment Contract</CardTitle>
                <CardDescription>Validation, world contract, and launch metadata.</CardDescription>
              </CardHeader>
              <CardContent className="space-y-4 text-sm">
                <div className="flex items-center gap-2">
                  <Badge variant={validation?.valid ? 'secondary' : 'destructive'}>
                    {validation?.valid ? 'valid' : 'invalid'}
                  </Badge>
                  {environment?.world_base ? (
                    <span className="text-muted-foreground">world base: {environment.world_base}</span>
                  ) : null}
                </div>
                {validation?.errors?.length ? (
                  <div className="space-y-1">
                    <div className="font-medium text-destructive">Errors</div>
                    {validation.errors.map((item) => (
                      <div key={item} className="text-destructive">{item}</div>
                    ))}
                  </div>
                ) : null}
                {validation?.warnings?.length ? (
                  <div className="space-y-1">
                    <div className="font-medium text-foreground">Warnings</div>
                    {validation.warnings.map((item) => (
                      <div key={item} className="text-muted-foreground">{item}</div>
                    ))}
                  </div>
                ) : null}
                <div className="space-y-1">
                  <div className="font-medium text-foreground">Environment skills</div>
                  <div className="flex flex-wrap gap-2">
                    {(environment?.environment_skills || []).map((skillId) => (
                      <Badge key={skillId} variant="outline">{skillId}</Badge>
                    ))}
                  </div>
                </div>
                {environment?.backend_contract?.required_endpoints?.length ? (
                  <div className="space-y-1">
                    <div className="font-medium text-foreground">Required backend endpoints</div>
                    <div className="space-y-1 text-muted-foreground">
                      {environment.backend_contract.required_endpoints.map((item) => (
                        <div key={item}>{item}</div>
                      ))}
                    </div>
                  </div>
                ) : null}
                {environment?.launch ? (
                  <div className="space-y-2">
                    <div className="font-medium text-foreground">Launch contract</div>
                    <div className="space-y-1 text-muted-foreground">
                      {environment.launch?.environment_service ? (
                        <div>
                          backend: {environment.launch.environment_service.service_name} · {environment.launch.environment_service.compose_file}
                        </div>
                      ) : null}
                      {environment.launch?.frontend_service ? (
                        <div>
                          frontend: {environment.launch.frontend_service.service_name} · {environment.launch.frontend_service.compose_file || 'same compose file'}
                        </div>
                      ) : null}
                      {environment.launch?.agent_worker_service ? (
                        <div>
                          agents: {environment.launch.agent_worker_service.service_name} · {environment.launch.agent_worker_service.compose_file}
                        </div>
                      ) : null}
                    </div>
                  </div>
                ) : null}
                {environment?.run_hooks?.length ? (
                  <div className="space-y-1">
                    <div className="font-medium text-foreground">Run hooks</div>
                    <div className="space-y-1 text-muted-foreground">
                      {environment.run_hooks.map((hook) => (
                        <div key={hook.id}>
                          {hook.id} · {hook.trigger} · {hook.background ? 'background' : 'inline'}
                        </div>
                      ))}
                    </div>
                  </div>
                ) : null}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Runtime</CardTitle>
                <CardDescription>Execution engine used by this environment.</CardDescription>
              </CardHeader>
              <CardContent className="space-y-3 text-sm">
                <div className="font-medium">{runtime?.name || environment?.runtime}</div>
                <div className="text-muted-foreground">{runtime?.description}</div>
                <div className="flex flex-wrap gap-2">
                  {(runtime?.required_population_files || []).map((item) => (
                    <Badge key={item} variant="outline">{item}</Badge>
                  ))}
                </div>
                <div className="space-y-1">
                  <div className="font-medium text-foreground">Baseline tool families</div>
                  <div className="flex flex-wrap gap-2">
                    {(runtime?.baseline_tool_families || []).length ? (
                      runtime.baseline_tool_families.map((item) => (
                        <Badge key={item} variant="secondary">{item}</Badge>
                      ))
                    ) : (
                      <span className="text-muted-foreground">No baseline tools declared.</span>
                    )}
                  </div>
                </div>
              </CardContent>
            </Card>

            <Card className="border-stone-200/80 bg-stone-50/70 dark:border-stone-800 dark:bg-stone-900/50">
              <CardHeader>
                <CardTitle>Environment Authoring Model</CardTitle>
                <CardDescription>This environment is the experiment package users copy and extend.</CardDescription>
              </CardHeader>
              <CardContent className="space-y-3 text-sm text-muted-foreground">
                <div>
                  Runtime owns the execution engine and baseline tools. This environment owns the backend,
                  populations, prompts, hooks, and experiment-local skills.
                </div>
                <div>
                  Population folders are the agent roles for this study. They are not standalone global templates.
                </div>
                <div className="font-medium text-foreground">
                  New experiment = new environment package.
                </div>
                <div className="space-y-1">
                  <div className="font-medium text-foreground">Minimum package</div>
                  <div>`environment.yaml`, `compose.run.yml`, `skill.md`, `backend/`, `skills/`, `populations/`</div>
                </div>
                <div className="space-y-1">
                  <div className="font-medium text-foreground">Required backend API</div>
                  <div>`GET /health`, `GET /contract`, `GET /skill.md`, `POST /auth/register`</div>
                </div>
                <div className="space-y-1">
                  <div className="font-medium text-foreground">Docker shape</div>
                  <div>One backend service is required. Frontend is optional. Agent workers stay shared and are wired through the environment launch manifest.</div>
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Recent Runs</CardTitle>
                <CardDescription>Runs launched from this environment stay discoverable here.</CardDescription>
              </CardHeader>
              <CardContent className="space-y-3 text-sm">
                {recentRuns.length ? (
                  recentRuns.slice(0, 5).map((run) => (
                    <div key={run.run_id} className="rounded-md border p-3">
                      <div className="flex items-start justify-between gap-3">
                        <div className="space-y-1">
                          <div className="font-medium">{run.run_id}</div>
                          <div className="text-muted-foreground">
                            {run.status} · {run.agent_count || 0} agents
                          </div>
                        </div>
                        <div className="flex flex-col items-end gap-1">
                          <Link className="text-sm font-medium text-primary hover:underline" to={`/runs/${encodeURIComponent(run.run_id)}/stats`}>
                            Dashboard
                          </Link>
                          <Link className="text-sm font-medium text-primary hover:underline" to={`/runs/${encodeURIComponent(run.run_id)}/traces`}>
                            Traces
                          </Link>
                        </div>
                      </div>
                    </div>
                  ))
                ) : (
                  <div className="text-muted-foreground">No runs yet for this environment.</div>
                )}
                <Link className="inline-flex text-sm font-medium text-primary hover:underline" to={`/runs?environment_id=${encodeURIComponent(environment_id || '')}`}>
                  View all runs
                </Link>
              </CardContent>
            </Card>
          </div>
        </div>
      ) : null}
    </div>
  );
}
