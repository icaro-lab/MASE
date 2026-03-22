import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { platformApi } from '@/api';
import { PageHeader } from '@/components/shared/PageHeader';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';

function RuntimeCard({ runtime }) {
  const toolFamilies = Array.isArray(runtime?.baseline_tool_families)
    ? runtime.baseline_tool_families
    : [];

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div className="space-y-1">
            <CardTitle>{runtime?.name || runtime?.id || 'Runtime'}</CardTitle>
            <CardDescription>{runtime?.description || 'Agent execution engine.'}</CardDescription>
          </div>
          <Badge variant="outline">{runtime?.id || 'runtime'}</Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        <div className="space-y-1">
          <div className="font-medium text-foreground">Scope</div>
          <div className="text-muted-foreground">
            Execution engine only. Environment packages own populations, prompts, and experiment-local roles.
          </div>
        </div>
        <div className="space-y-1">
          <div className="font-medium text-foreground">Population files</div>
          <div className="flex flex-wrap gap-2">
            {(runtime?.required_population_files || []).map((item) => (
              <Badge key={item} variant="secondary">{item}</Badge>
            ))}
          </div>
        </div>
        <div className="space-y-1">
          <div className="font-medium text-foreground">Baseline tool families</div>
          <div className="flex flex-wrap gap-2">
            {toolFamilies.length
              ? toolFamilies.map((item) => <Badge key={item} variant="outline">{item}</Badge>)
              : <span className="text-muted-foreground">No baseline tool families declared.</span>}
          </div>
        </div>
        <Link className="text-sm font-medium text-primary hover:underline" to={`/environments?runtime=${encodeURIComponent(runtime?.id || '')}`}>
          View environments using this runtime
        </Link>
      </CardContent>
    </Card>
  );
}

export default function RuntimesPage() {
  const [runtimes, setRuntimes] = useState([]);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    platformApi
      .listRuntimes()
      .then((payload) => {
        if (!cancelled) {
          setRuntimes(Array.isArray(payload) ? payload : []);
          setError('');
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err.message || 'Failed to load runtimes.');
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Runtimes"
        description="Reusable execution engines that define baseline tools, heartbeat behavior, and workspace expectations."
      />
      {error ? <div className="text-sm text-destructive">{error}</div> : null}
      <div className="grid gap-4 lg:grid-cols-2">
        {runtimes.map((runtime) => (
          <RuntimeCard key={runtime.id || runtime.name} runtime={runtime} />
        ))}
      </div>
    </div>
  );
}
