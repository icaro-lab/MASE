import React from 'react';
import { Card, CardContent } from '../ui/card';
import { Progress } from '../ui/progress';

export function KpiCard({ label, value, sub, progress }) {
  return (
    <Card>
      <CardContent className="p-4">
        <p className="text-xs uppercase tracking-wide text-muted-foreground">{label}</p>
        <p className="mt-1 text-2xl font-semibold tracking-tight">{value}</p>
        {typeof progress === 'number' ? (
          <Progress value={progress} className="mt-2 h-1" />
        ) : null}
        {sub ? (
          <p className="mt-1 text-xs text-muted-foreground">{sub}</p>
        ) : null}
      </CardContent>
    </Card>
  );
}
