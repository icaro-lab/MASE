import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { RunTracingPanel } from '../RunTracingPanel';

function makeLlmEvent({
  id,
  traceId,
  timestamp,
  agentId,
  tick,
  round,
  model,
  input,
  output,
  cost = 0,
}) {
  return {
    event_id: id,
    trace_id: traceId,
    event_type: 'llm_io',
    timestamp,
    agent_id: agentId,
    payload: {
      event_type: 'llm_io',
      trace_id: traceId,
      tick,
      round_index: round,
      model,
      user_message: input,
      response_text: output,
      llm_cost_usd: cost,
      llm_tokens_input: 10,
      llm_tokens_output: 6,
    },
  };
}

function makeActionEvent({
  id,
  traceId,
  timestamp,
  agentId,
  tick,
  method = 'GET',
  url = '/api/items',
  status = 200,
}) {
  return {
    event_id: id,
    trace_id: traceId,
    event_type: 'action_attempt',
    timestamp,
    agent_id: agentId,
    payload: {
      event_type: 'action_attempt',
      trace_id: traceId,
      tick,
      method,
      url,
      status_code: status,
    },
  };
}

function renderTracingPanel(telemetryTail, options = {}) {
  const mode = options.mode || 'tracing';
  const route = options.route || '/runs/run-1/traces';
  const searchFilters = options.searchFilters || { drill: '', type: '', agent: '' };
  return render(
    <MemoryRouter initialEntries={[route]}>
      <RunTracingPanel
        mode={mode}
        telemetryTail={telemetryTail}
        historyEvents={telemetryTail}
        searchFilters={searchFilters}
        telemetryAgentOptions={[
          { value: '', label: 'All agents' },
          { value: 'agent-1', label: 'agent-1' },
          { value: 'agent-2', label: 'agent-2' },
        ]}
        telemetryEventType={(event) => event?.event_type || event?.payload?.event_type || 'telemetry'}
        telemetryTick={(event) => Number(event?.payload?.tick ?? 0)}
        telemetryAgentId={(event) => event?.agent_id || '<system>'}
        extractCostUsd={(event) => {
          const llm = Number(event?.payload?.llm_cost_usd || 0);
          return { llm, total: llm };
        }}
        extractTokens={(event) => ({
          input: Number(event?.payload?.llm_tokens_input || 0),
          output: Number(event?.payload?.llm_tokens_output || 0),
        })}
        promptPartBySha={new Map()}
        formatDateTime={(value) => String(value)}
        formatNumber={(value) => String(value ?? 0)}
      />
    </MemoryRouter>
  );
}

describe('RunTracingPanel', () => {
  it('renders empty state when no llm telemetry exists', () => {
    renderTracingPanel([]);
    expect(screen.getByText('No run events recorded yet.')).toBeInTheDocument();
  });

  it('applies filters and opens inspector sheet from selected row', async () => {
    const user = userEvent.setup();
    const telemetryTail = [
      makeLlmEvent({
        id: 'evt-1',
        timestamp: '2026-02-25T10:00:00Z',
        agentId: 'agent-1',
        tick: 0,
        round: 0,
        model: 'dummy-model',
        input: 'first input',
        output: 'first output',
        cost: 0,
      }),
      makeLlmEvent({
        id: 'evt-2',
        timestamp: '2026-02-25T10:01:00Z',
        agentId: 'agent-2',
        tick: 0,
        round: 1,
        model: 'gpt-4o-mini',
        input: 'second input',
        output: 'second output',
        cost: 0,
      }),
      {
        event_id: 'evt-heartbeat',
        event_type: 'heartbeat_result',
        timestamp: '2026-02-25T10:02:00Z',
        agent_id: 'agent-1',
        payload: { event_type: 'heartbeat_result', tick: 2 },
      },
    ];

    const { rerender, container } = renderTracingPanel(telemetryTail);
    const getTableCell = (text) => {
      const cells = container.querySelectorAll('td');
      return Array.from(cells).find((cell) => cell.textContent?.includes(text));
    };
    expect(getTableCell('dummy-model')).toBeTruthy();
    expect(getTableCell('gpt-4o-mini')).toBeTruthy();

    await user.click(screen.getByRole('checkbox', { name: 'agent-2' }));

    expect(getTableCell('dummy-model')).toBeFalsy();
    expect(getTableCell('gpt-4o-mini')).toBeTruthy();

    await user.click(getTableCell('gpt-4o-mini'));
    expect(screen.getByText('Trace details')).toBeInTheDocument();
    expect(screen.getByText('Select any table row to inspect details without leaving the list.')).toBeInTheDocument();
    expect(screen.getAllByText('second output').length).toBeGreaterThan(0);

    // SSE-like update: rerender with an extra llm_io event and verify it appears.
    rerender(
      <MemoryRouter initialEntries={['/runs/run-1/traces']}>
        <RunTracingPanel
          telemetryTail={[
            ...telemetryTail,
            makeLlmEvent({
              id: 'evt-3',
              timestamp: '2026-02-25T10:03:00Z',
              agentId: 'agent-2',
              tick: 0,
              round: 0,
              model: 'openrouter/anthropic/claude',
              input: 'third input',
              output: 'third output',
              cost: 0,
            }),
          ]}
          historyEvents={[
            ...telemetryTail,
            makeLlmEvent({
              id: 'evt-3',
              timestamp: '2026-02-25T10:03:00Z',
              agentId: 'agent-2',
              tick: 0,
              round: 0,
              model: 'openrouter/anthropic/claude',
              input: 'third input',
              output: 'third output',
              cost: 0,
            }),
          ]}
          searchFilters={{ drill: '', type: '', agent: '' }}
          telemetryAgentOptions={[
            { value: '', label: 'All agents' },
            { value: 'agent-1', label: 'agent-1' },
            { value: 'agent-2', label: 'agent-2' },
          ]}
          telemetryEventType={(event) => event?.event_type || event?.payload?.event_type || 'telemetry'}
          telemetryTick={(event) => Number(event?.payload?.tick ?? 0)}
          telemetryAgentId={(event) => event?.agent_id || '<system>'}
          extractCostUsd={(event) => {
            const llm = Number(event?.payload?.llm_cost_usd || 0);
            return { llm, total: llm };
          }}
          extractTokens={(event) => ({
            input: Number(event?.payload?.llm_tokens_input || 0),
            output: Number(event?.payload?.llm_tokens_output || 0),
          })}
          promptPartBySha={new Map()}
          formatDateTime={(value) => String(value)}
          formatNumber={(value) => String(value ?? 0)}
        />
      </MemoryRouter>
    );

    expect(screen.getAllByText('openrouter/anthropic/claude').length).toBeGreaterThan(0);
  });

  it('supports legacy type filter and keeps events mode event-centric', () => {
    const telemetryTail = [
      makeLlmEvent({
        id: 'evt-llm-1',
        traceId: 'trace-1',
        timestamp: '2026-02-25T10:00:00Z',
        agentId: 'agent-1',
        tick: 0,
        round: 0,
        model: 'gpt-4o-mini',
        input: 'llm input',
        output: 'llm output',
        cost: 0.01,
      }),
      makeActionEvent({
        id: 'evt-action-1',
        traceId: 'trace-2',
        timestamp: '2026-02-25T10:00:30Z',
        agentId: 'agent-1',
        tick: 0,
      }),
    ];

    renderTracingPanel(telemetryTail, {
      mode: 'events',
      route: '/runs/run-1/traces?type=action_attempt',
      searchFilters: { drill: '', type: 'action_attempt', agent: '' },
    });

    expect(screen.getByText('Action')).toBeInTheDocument();
    // LLM Call type should not appear in the filtered table
    expect(screen.queryByText('LLM Call')).not.toBeInTheDocument();
  });

  it('supports legacy drill=cost filter in events mode', () => {
    const telemetryTail = [
      makeLlmEvent({
        id: 'evt-llm-cost',
        traceId: 'trace-1',
        timestamp: '2026-02-25T10:00:00Z',
        agentId: 'agent-1',
        tick: 0,
        round: 0,
        model: 'gpt-4o-mini',
        input: 'llm input',
        output: 'llm output',
        cost: 0.2,
      }),
      makeActionEvent({
        id: 'evt-action-zero',
        traceId: 'trace-2',
        timestamp: '2026-02-25T10:00:30Z',
        agentId: 'agent-1',
        tick: 0,
      }),
    ];

    renderTracingPanel(telemetryTail, {
      mode: 'events',
      route: '/runs/run-1/traces?drill=cost',
      searchFilters: { drill: 'cost', type: '', agent: '' },
    });

    expect(screen.getAllByText('LLM Call').length).toBeGreaterThan(0);
    expect(screen.queryByText('Action')).not.toBeInTheDocument();
  });

  it('supports keyboard row activation and opens inspector sheet', async () => {
    const telemetryTail = [
      makeLlmEvent({
        id: 'evt-1',
        traceId: 'trace-1',
        timestamp: '2026-02-25T10:00:00Z',
        agentId: 'agent-1',
        tick: 0,
        round: 0,
        model: 'gpt-4o-mini',
        input: 'first input',
        output: 'first output',
      }),
      makeLlmEvent({
        id: 'evt-2',
        traceId: 'trace-2',
        timestamp: '2026-02-25T10:01:00Z',
        agentId: 'agent-1',
        tick: 1,
        round: 0,
        model: 'openrouter/anthropic/claude',
        input: 'second input',
        output: 'second output',
      }),
    ];

    renderTracingPanel(telemetryTail);

    const cells = document.querySelectorAll('td');
    const gptCell = Array.from(cells).find((cell) => cell.textContent?.includes('gpt-4o-mini'));
    const row = gptCell?.closest('tr');
    expect(row).not.toBeNull();
    row.focus();
    fireEvent.keyDown(row, { key: 'Enter', code: 'Enter' });

    expect(screen.getByText('Trace details')).toBeInTheDocument();
    expect(screen.getByText('Select any table row to inspect details without leaving the list.')).toBeInTheDocument();
  });

  it('opens side inspector on inspect route', async () => {
    const user = userEvent.setup();
    const telemetryTail = [
      makeLlmEvent({
        id: 'evt-11',
        traceId: 'trace-11',
        timestamp: '2026-02-25T10:00:00Z',
        agentId: 'agent-1',
        tick: 3,
        round: 0,
        model: 'gpt-4o-mini',
        input: 'inspect input',
        output: 'inspect output',
        cost: 0.01,
      }),
      {
        ...makeActionEvent({
          id: 'evt-action',
          traceId: 'trace-11',
          timestamp: '2026-02-25T10:00:01Z',
          agentId: 'agent-1',
          tick: 3,
        }),
      },
    ];

    render(
      <MemoryRouter initialEntries={['/runs/run-1/traces?trace=evt-11&panel=1']}>
        <RunTracingPanel
          mode="inspect"
          telemetryTail={telemetryTail}
          historyEvents={telemetryTail}
          searchFilters={{ drill: '', type: '', agent: '' }}
          telemetryAgentOptions={[
            { value: '', label: 'All agents' },
            { value: 'agent-1', label: 'agent-1' },
          ]}
          telemetryEventType={(event) => event?.event_type || event?.payload?.event_type || 'telemetry'}
          telemetryTick={(event) => Number(event?.payload?.tick ?? 0)}
          telemetryAgentId={(event) => event?.agent_id || '<system>'}
          extractCostUsd={(event) => {
            const llm = Number(event?.payload?.llm_cost_usd || 0);
            return { llm, total: llm };
          }}
          extractTokens={(event) => ({
            input: Number(event?.payload?.llm_tokens_input || 0),
            output: Number(event?.payload?.llm_tokens_output || 0),
          })}
          promptPartBySha={new Map()}
          formatDateTime={(value) => String(value)}
          formatNumber={(value) => String(value ?? 0)}
        />
      </MemoryRouter>
    );

    expect(screen.getByText('Trace details')).toBeInTheDocument();
    await user.click(screen.getByRole('tab', { name: 'Log View' }));
    expect(screen.getByText('Agent Trace Tree (tick-level)')).toBeInTheDocument();
    expect(screen.getByText('Timeline Events')).toBeInTheDocument();
  });

  it('resolves inspect selection by trace_id query key', async () => {
    const telemetryTail = [
      makeLlmEvent({
        id: 'evt-aaa',
        traceId: 'trace-aaa',
        timestamp: '2026-02-25T10:01:00Z',
        agentId: 'agent-1',
        tick: 1,
        round: 0,
        model: 'gpt-4o-mini',
        input: 'first input',
        output: 'first output',
      }),
      makeLlmEvent({
        id: 'evt-bbb',
        traceId: 'trace-bbb',
        timestamp: '2026-02-25T10:00:00Z',
        agentId: 'agent-1',
        tick: 2,
        round: 0,
        model: 'openrouter/anthropic/claude',
        input: 'second input',
        output: 'second output',
      }),
    ];

    renderTracingPanel(telemetryTail, {
      mode: 'inspect',
      route: '/runs/run-1/traces?trace=trace-bbb&panel=1',
    });

    expect(screen.getByText('Trace details')).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getAllByText('openrouter/anthropic/claude').length).toBeGreaterThan(0);
    });
  });

  it('shows n/a cost when llm pricing metadata is missing', () => {
    const telemetryTail = [
      {
        event_id: 'evt-missing-pricing',
        event_type: 'llm_io',
        timestamp: '2026-02-25T10:00:00Z',
        agent_id: 'agent-1',
        payload: {
          event_type: 'llm_io',
          tick: 0,
          round_index: 0,
          model: 'dummy-model',
          user_message: 'input',
          response_text: 'output',
          llm_cost_usd: 0,
        },
      },
    ];

    renderTracingPanel(telemetryTail);

    expect(screen.getByText('n/a')).toBeInTheDocument();
  });
});
