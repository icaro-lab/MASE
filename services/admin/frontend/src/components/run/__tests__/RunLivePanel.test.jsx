import React from 'react';
import { render, screen } from '@testing-library/react';
import { RunLivePanel } from '../RunLivePanel';

function makeTelemetryRow(index) {
  return {
    event_id: `evt-${index}`,
    event_type: 'llm_io',
    timestamp: `2026-02-25T10:${String(index).padStart(2, '0')}:00Z`,
    agent_id: 'agent-1',
    payload: {
      event_type: 'llm_io',
      tick: index,
      round_index: index % 2,
      model: 'gpt-4o-mini',
      response_text: `out-${index}`,
      user_message: `in-${index}`,
      llm_tokens_input: 10,
      llm_tokens_output: 5,
    },
  };
}

function renderRunLivePanel(telemetryTail, { run = { status: 'running', started_at: '2026-02-25T10:00:00Z' }, historyEvents } = {}) {
  return render(
    <RunLivePanel
      run={run}
      eventCount={telemetryTail.length}
      eventTypeCounts={{}}
      heartbeatSummaryRows={[]}
      agentCount={1}
      costTotals={{ total: 0, tokensIn: 0, tokensOut: 0 }}
      actionSeries={[]}
      costSeries={[]}
      tokenSeries={[]}
      terminalFilters={{ type: '', agent: '' }}
      onTerminalFiltersChange={vi.fn()}
      telemetryTypeOptions={[{ value: '', label: 'All types' }]}
      telemetryAgentOptions={[
        { value: '', label: 'All agents' },
        { value: 'agent-1', label: 'agent-1' },
      ]}
      filteredTelemetryTail={telemetryTail}
      telemetryTail={telemetryTail}
      historyEvents={historyEvents || telemetryTail}
      selectedTelemetryEvent={null}
      onSelectTelemetryEvent={vi.fn()}
      formatDateTime={(value) => String(value)}
      formatNumber={(value) => String(value ?? 0)}
      formatRunDuration={() => '-'}
      formatTime={(value) => String(value)}
      telemetryEventType={(event) => event?.event_type || event?.payload?.event_type || 'telemetry'}
      telemetryTick={(event) => Number(event?.payload?.tick ?? 0)}
      telemetryAgentId={(event) => event?.agent_id || '<system>'}
      buildTelemetrySummary={(event) => `tick=${event?.payload?.tick ?? '-'}`}
      extractCostUsd={() => ({ llm: 0, ia: 0, total: 0 })}
      extractTokens={(event) => ({
        input: Number(event?.payload?.llm_tokens_input || 0),
        output: Number(event?.payload?.llm_tokens_output || 0),
      })}
    />
  );
}

describe('RunLivePanel', () => {
  it('renders Agents card with agent data', () => {
    const events = Array.from({ length: 5 }, (_, index) => makeTelemetryRow(index));
    renderRunLivePanel(events);

    expect(screen.getAllByText('Agents').length).toBeGreaterThan(0);
    expect(screen.getAllByText('agent-1').length).toBeGreaterThan(0);
  });

  it('renders Terminal section', () => {
    const events = Array.from({ length: 5 }, (_, index) => makeTelemetryRow(index));
    renderRunLivePanel(events);

    expect(screen.getByText('Terminal')).toBeInTheDocument();
  });

  it('renders Models & tokens card', () => {
    const events = Array.from({ length: 5 }, (_, index) => makeTelemetryRow(index));
    renderRunLivePanel(events);

    expect(screen.getByText('Models & tokens')).toBeInTheDocument();
  });

  it('falls back to historyEvents when telemetryTail is empty', () => {
    const history = Array.from({ length: 3 }, (_, index) => makeTelemetryRow(index));
    renderRunLivePanel([], { historyEvents: history });

    // Agent stats should still populate from historyEvents fallback
    expect(screen.getAllByText('agent-1').length).toBeGreaterThan(0);
  });
});
