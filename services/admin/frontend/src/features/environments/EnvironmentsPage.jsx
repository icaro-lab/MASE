import React, { useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { platformApi } from '@/api';
import { PageHeader } from '@/components/shared/PageHeader';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';

function EnvironmentCard({ environment }) {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div className="space-y-1">
            <CardTitle>{environment?.name || environment?.id || 'Environment'}</CardTitle>
            <CardDescription>{environment?.description || 'Experiment package.'}</CardDescription>
          </div>
          <Badge variant="outline">{environment?.runtime || 'runtime'}</Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        <div className="space-y-1">
          <div className="font-medium text-foreground">World base</div>
          <div className="text-muted-foreground">{environment?.world_base || 'Custom world'}</div>
        </div>
        <div className="space-y-1">
          <div className="font-medium text-foreground">Population roles</div>
          <div className="flex flex-wrap gap-2">
            {Object.keys(environment?.populations || {}).map((populationId) => (
              <Badge key={populationId} variant="secondary">{populationId}</Badge>
            ))}
          </div>
        </div>
        <div className="space-y-1">
          <div className="font-medium text-foreground">Environment skills</div>
          <div className="flex flex-wrap gap-2">
            {(environment?.environment_skills || []).map((skillId) => (
              <Badge key={skillId} variant="outline">{skillId}</Badge>
            ))}
          </div>
        </div>
        <div className="flex gap-4">
          <Link className="font-medium text-primary hover:underline" to={`/environments/${encodeURIComponent(environment?.id || '')}`}>
            Start run
          </Link>
          <Link className="font-medium text-primary hover:underline" to={`/environments/${encodeURIComponent(environment?.id || '')}`}>
            Open environment
          </Link>
          <Link className="font-medium text-primary hover:underline" to={`/runs?environment_id=${encodeURIComponent(environment?.id || '')}`}>
            View runs
          </Link>
        </div>
      </CardContent>
    </Card>
  );
}

export default function EnvironmentsPage() {
  const [searchParams] = useSearchParams();
  const [environments, setEnvironments] = useState([]);
  const [query, setQuery] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    platformApi
      .listEnvironments()
      .then((payload) => {
        if (!cancelled) {
          setEnvironments(Array.isArray(payload) ? payload : []);
          setError('');
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err.message || 'Failed to load environments.');
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const runtimeFilter = String(searchParams.get('runtime') || '').trim();
  const filtered = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    return environments.filter((environment) => {
      if (runtimeFilter && String(environment?.runtime || '') !== runtimeFilter) {
        return false;
      }
      if (!normalizedQuery) return true;
      const haystack = [
        environment?.id,
        environment?.name,
        environment?.description,
        environment?.runtime,
      ]
        .filter(Boolean)
        .join(' ')
        .toLowerCase();
      return haystack.includes(normalizedQuery);
    });
  }, [environments, query, runtimeFilter]);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Environments"
        description="Experiment packages that bundle a world, population roles, skills, and authored launch defaults."
      />
      <Card className="border-stone-200/80 bg-stone-50/70 dark:border-stone-800 dark:bg-stone-900/50">
        <CardHeader>
          <CardTitle className="text-xl">Create An Experiment By Creating An Environment</CardTitle>
          <CardDescription>
            In v1, a new study is a new environment package. Pick a runtime, define the world backend and skills,
            add population folders with prompts and tool access, then launch runs from that package.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 text-sm text-muted-foreground md:grid-cols-3">
          <div className="space-y-1">
            <div className="font-medium text-foreground">1. Choose a runtime</div>
            <div>Use a runtime only for the agent execution engine and its baseline tools.</div>
          </div>
          <div className="space-y-1">
            <div className="font-medium text-foreground">2. Author the environment</div>
            <div>Put backend APIs, environment skills, populations, hooks, and defaults in one package.</div>
          </div>
          <div className="space-y-1">
            <div className="font-medium text-foreground">3. Launch runs</div>
            <div>The run registry is the operator surface for traces, stats, and control actions.</div>
          </div>
        </CardContent>
      </Card>
      <div className="flex flex-wrap gap-3">
        <Input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search environments"
          className="max-w-sm"
        />
        {runtimeFilter ? <Badge variant="outline">runtime: {runtimeFilter}</Badge> : null}
      </div>
      {error ? <div className="text-sm text-destructive">{error}</div> : null}
      <div className="grid gap-4 lg:grid-cols-2">
        {filtered.map((environment) => (
          <EnvironmentCard key={environment.id || environment.name} environment={environment} />
        ))}
      </div>
    </div>
  );
}
