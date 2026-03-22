import { buildTelemetrySummary } from '../useRunDashboardController';

describe('buildTelemetrySummary', () => {
  it('includes heartbeat, round, model and action count for llm_io telemetry', () => {
    const summary = buildTelemetrySummary({
      event_type: 'llm_io',
      payload: {
        tick: 5,
        heartbeat_index: 2,
        round_index: 1,
        model: 'gpt-4o-mini',
        parsed_action_count: 2,
      },
    });

    expect(summary).toContain('hb=2');
    expect(summary).toContain('tick=5');
    expect(summary).toContain('r=1');
    expect(summary).toContain('model=gpt-4o-mini');
    expect(summary).toContain('actions=2');
  });

  it('includes stop reason, rounds and elapsed ms for heartbeat_result telemetry', () => {
    const summary = buildTelemetrySummary({
      event_type: 'heartbeat_result',
      payload: {
        tick: 9,
        heartbeat_index: 4,
        actions_executed: 3,
        heartbeat_status: 'completed',
        stop_reason: 'no_actionable_actions',
        rounds_executed: 2,
        elapsed_ms: 120,
      },
    });

    expect(summary).toContain('hb=4');
    expect(summary).toContain('tick=9');
    expect(summary).toContain('actions=3');
    expect(summary).toContain('status=completed');
    expect(summary).toContain('stop=no_actionable_actions');
    expect(summary).toContain('rounds=2');
    expect(summary).toContain('120ms');
  });

  it('reads nested payload.payload fields from controller-enveloped telemetry', () => {
    const summary = buildTelemetrySummary({
      event_type: 'heartbeat_result',
      payload: {
        payload: {
          tick: 12,
          heartbeat_index: 7,
          actions_executed: 1,
          heartbeat_status: 'completed',
          stop_reason: 'heartbeat_ok',
          rounds_executed: 0,
          elapsed_ms: 15,
        },
      },
    });

    expect(summary).toContain('hb=7');
    expect(summary).toContain('tick=12');
    expect(summary).toContain('actions=1');
    expect(summary).toContain('status=completed');
    expect(summary).toContain('stop=heartbeat_ok');
    expect(summary).toContain('rounds=0');
    expect(summary).toContain('15ms');
  });
});
