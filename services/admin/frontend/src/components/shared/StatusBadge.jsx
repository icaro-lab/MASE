import React from 'react';
import { Badge } from '../ui/badge';
import { cn } from 'lib/utils';

function toneToVariant(tone) {
  const value = String(tone || '').toLowerCase();
  if (['running', 'healthy', 'active', 'success', 'completed', 'published', 'configured'].includes(value)) {
    return 'default';
  }
  if (['pending', 'polling', 'warning', 'paused', 'blocked', 'degraded'].includes(value)) {
    return 'secondary';
  }
  if (['failed', 'error', 'stopped', 'cancelled', 'timed_out', 'offline'].includes(value)) {
    return 'destructive';
  }
  return 'outline';
}

export function statusTone(status) {
  const value = String(status || '').toLowerCase();
  if (value.includes('run') || value.includes('start') || value.includes('warmup') || value.includes('setup')) {
    return 'running';
  }
  if (value.includes('pend')) return 'pending';
  if (value.includes('pause')) return 'paused';
  if (value.includes('complete') || value.includes('success')) return 'completed';
  if (value.includes('block') || value.includes('ban') || value.includes('deny')) return 'blocked';
  if (
    value.includes('fail') ||
    value.includes('error') ||
    value.includes('stop') ||
    value.includes('cancel') ||
    value.includes('timeout') ||
    value.includes('timed_out')
  ) {
    return 'failed';
  }
  return 'other';
}

export function StatusBadge({ status, tone, label, className }) {
  const resolvedTone = tone || statusTone(status);
  const resolvedLabel = label || status || 'unknown';

  return (
    <Badge
      variant={toneToVariant(resolvedTone)}
      className={cn('font-medium capitalize', className)}
    >
      {resolvedLabel}
    </Badge>
  );
}
