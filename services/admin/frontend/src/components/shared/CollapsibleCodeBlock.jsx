import React, { useMemo, useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { Button } from '../ui/button';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '../ui/collapsible';
import { cn } from 'lib/utils';
import { CopyButton } from './CopyButton';

function normalizeText(value) {
  if (value == null) return '';
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

export function CollapsibleCodeBlock({
  title = 'Details',
  value,
  defaultOpen = false,
  emptyMessage = '-',
  preClassName,
}) {
  const [open, setOpen] = useState(defaultOpen);
  const content = useMemo(() => normalizeText(value), [value]);
  const display = content || emptyMessage;

  return (
    <Collapsible open={open} onOpenChange={setOpen} className="space-y-2">
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs uppercase tracking-wide text-muted-foreground">{title}</p>
        <div className="flex items-center gap-1">
          <CopyButton value={content} />
          <CollapsibleTrigger asChild>
            <Button type="button" variant="ghost" size="sm" className="h-7 gap-1 px-2 text-xs">
              {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
              {open ? 'Hide' : 'Show'}
            </Button>
          </CollapsibleTrigger>
        </div>
      </div>
      <CollapsibleContent>
        <pre
          className={cn(
            'max-h-[420px] overflow-auto rounded-lg border bg-muted/20 p-3 font-mono text-xs whitespace-pre-wrap break-words [overflow-wrap:anywhere]',
            preClassName
          )}
        >
          {display}
        </pre>
      </CollapsibleContent>
    </Collapsible>
  );
}
