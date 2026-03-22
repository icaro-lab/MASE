import React, { useEffect, useMemo, useState } from 'react';
import { RefreshCw, X } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '../ui/card';
import { Button } from '../ui/button';
import { Field, FieldLabel } from '../ui/field';
import { Input } from '../ui/input';
import { Alert, AlertDescription, AlertTitle } from '../ui/alert';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../ui/table';
import { CollapsibleCodeBlock } from '../shared/CollapsibleCodeBlock';
import { EmptyState } from '../shared/EmptyState';
import { SelectField } from '../shared/SelectField';
import { StatusBadge } from '../shared/StatusBadge';
import { TablePagination } from '../shared/TablePagination';

const PAGE_SIZE = 25;

function handleEnterOrSpace(event, onActivate) {
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault();
    onActivate();
  }
}

export function RunEventsPanel({
  searchFilters,
  filters,
  onFiltersChange,
  eventTypeOptions,
  onRefresh,
  historyLoading,
  historyError,
  filteredHistoryEvents,
  historyEvents,
  selectedHistoryEvent,
  onSelectHistoryEvent,
  formatDateTime,
}) {
  const [page, setPage] = useState(1);
  const historyRows = useMemo(
    () =>
      filteredHistoryEvents.map((event, index) => ({
        key: event.event_id || `${event.timestamp || 'ts'}-${index}`,
        event,
        timestamp: event.timestamp,
        agentId: event.agent_id || '<system>',
        eventType: event.event_type || 'unknown',
        actionName: event.action_name || '-',
        outcome: event.outcome || 'unknown',
      })),
    [filteredHistoryEvents]
  );
  const totalPages = Math.max(1, Math.ceil(historyRows.length / PAGE_SIZE));
  const pagedRows = useMemo(() => {
    const start = (page - 1) * PAGE_SIZE;
    return historyRows.slice(start, start + PAGE_SIZE);
  }, [historyRows, page]);

  useEffect(() => {
    if (page > totalPages) setPage(totalPages);
  }, [page, totalPages]);

  useEffect(() => {
    setPage(1);
  }, [filters.agent, filters.type, searchFilters.drill]);

  return (
    <Card>
      <CardHeader className="flex flex-col gap-3 border-b pb-4">
        <div className="flex flex-wrap items-center gap-2">
          <CardTitle className="text-base">Events (history)</CardTitle>
          {searchFilters.drill ? <StatusBadge tone="warning" label={`Drill: ${searchFilters.drill}`} /> : null}
        </div>

        <div className="flex flex-wrap gap-2">
          <Field className="w-full sm:w-[220px]">
            <FieldLabel className="sr-only" htmlFor="run-events-type-filter">Run events type filter</FieldLabel>
            <SelectField
              id="run-events-type-filter"
              ariaLabel="Run events type filter"
              value={filters.type}
              onChange={(value) => onFiltersChange({ ...filters, type: value })}
              options={eventTypeOptions}
            />
          </Field>

          <Field className="relative w-full sm:w-[220px]">
            <FieldLabel className="sr-only" htmlFor="run-events-agent-filter">Run events agent filter</FieldLabel>
            <Input
              id="run-events-agent-filter"
              aria-label="Run events agent filter"
              value={filters.agent}
              placeholder="Filter by agent id"
              onChange={(event) => onFiltersChange({ ...filters, agent: event.target.value })}
              className="pr-9"
            />
            {filters.agent ? (
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="absolute right-1 top-1/2 h-7 w-7 -translate-y-1/2"
                onClick={() => onFiltersChange({ ...filters, agent: '' })}
                aria-label="Clear run events agent filter"
              >
                <X size={14} />
              </Button>
            ) : null}
          </Field>

          <Button type="button" variant="outline" size="sm" onClick={onRefresh} disabled={historyLoading}>
            <RefreshCw size={14} className={historyLoading ? 'animate-spin' : ''} />
            Refresh
          </Button>
        </div>
      </CardHeader>

      <CardContent className="space-y-4 pt-6">
        {historyError ? (
          <Alert variant="destructive">
            <AlertTitle>Error</AlertTitle>
            <AlertDescription>{historyError}</AlertDescription>
          </Alert>
        ) : null}

        <div className="overflow-hidden rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-[165px]">Time</TableHead>
                <TableHead className="w-[170px]">Agent</TableHead>
                <TableHead className="w-[165px]">Type</TableHead>
                <TableHead>Action</TableHead>
                <TableHead className="w-[140px]">Outcome</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {pagedRows.length ? (
                pagedRows.map((row) => {
                  const selected = String(selectedHistoryEvent?.event_id || '') === String(row?.event?.event_id || '');
                  return (
                    <TableRow
                      key={row.key}
                      className="cursor-pointer"
                      data-state={selected ? 'selected' : undefined}
                      onClick={() => onSelectHistoryEvent(row.event)}
                      onKeyDown={(event) => handleEnterOrSpace(event, () => onSelectHistoryEvent(row.event))}
                      tabIndex={0}
                      role="button"
                      aria-label={`Inspect run event ${row.eventType || 'unknown'} for ${row.agentId || 'system'}`}
                    >
                      <TableCell>
                        <span className="font-mono text-xs">{formatDateTime(row.timestamp)}</span>
                      </TableCell>
                      <TableCell>
                        <span className="font-mono text-xs">{row.agentId}</span>
                      </TableCell>
                      <TableCell>
                        <StatusBadge tone="other" label={row.eventType} className="normal-case" />
                      </TableCell>
                      <TableCell>{row.actionName}</TableCell>
                      <TableCell>
                        <StatusBadge status={row.outcome} />
                      </TableCell>
                    </TableRow>
                  );
                })
              ) : (
                <TableRow>
                  <TableCell className="py-6 text-center text-sm text-muted-foreground" colSpan={5}>
                    {historyLoading
                      ? 'Loading events...'
                      : historyEvents.length
                        ? 'No events match active drill/type/agent filters.'
                        : 'No events yet'}
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>

          <TablePagination page={page} totalPages={totalPages} onPageChange={setPage} />
        </div>

        {selectedHistoryEvent ? (
          <CollapsibleCodeBlock
            title="Selected event"
            value={selectedHistoryEvent}
            defaultOpen
            preClassName="max-h-[540px]"
          />
        ) : (
          <EmptyState description="Select an event row to inspect payload details." />
        )}
      </CardContent>
    </Card>
  );
}
