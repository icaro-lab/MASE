import {
  hasBanWindowSignal,
  hasEgressSignal,
  normalizeEventPayload,
  normalizeOutcome,
} from '../governanceSignals';

describe('normalizeEventPayload', () => {
  it('unwraps nested payload.payload objects', () => {
    const payload = normalizeEventPayload({
      payload: {
        payload: {
          payload: {
            error_code: 'ia_blocked',
            detail: 'final',
          },
        },
      },
    });

    expect(payload).toEqual({ error_code: 'ia_blocked', detail: 'final' });
  });

  it('returns an empty object when payload is not an object', () => {
    expect(normalizeEventPayload({ payload: 'bad-payload' })).toEqual({});
    expect(normalizeEventPayload({})).toEqual({});
  });
});

describe('normalizeOutcome', () => {
  it('classifies confirmation outcomes', () => {
    const outcome = normalizeOutcome({ outcome: 'pending_confirmation' }, {});
    expect(outcome).toBe('confirmation');
  });

  it('classifies blocked outcomes', () => {
    const outcome = normalizeOutcome({ outcome: 'deny' }, {});
    expect(outcome).toBe('blocked');
  });

  it('classifies success outcomes', () => {
    const outcome = normalizeOutcome({ status: 'ok' }, {});
    expect(outcome).toBe('success');
  });

  it('classifies failed outcomes', () => {
    const outcome = normalizeOutcome({ result: 'failed' }, {});
    expect(outcome).toBe('failed');
  });

  it('falls back to other for unknown outcomes', () => {
    const outcome = normalizeOutcome({ outcome: 'noop' }, {});
    expect(outcome).toBe('other');
  });
});

describe('governance signal detection', () => {
  it('detects ban window signals from structured payload fields', () => {
    const event = {
      payload: {
        ia_metadata: {
          ban_minutes: '5',
        },
      },
    };

    expect(hasBanWindowSignal(event)).toBe(true);
  });

  it('detects ban window signals from free-text message hints', () => {
    const event = {
      payload: {
        error_message: 'Agent banned until next window',
      },
    };

    expect(hasBanWindowSignal(event)).toBe(true);
  });

  it('does not report ban window signal when hints are missing', () => {
    const event = {
      payload: {
        message: 'request processed normally',
      },
    };

    expect(hasBanWindowSignal(event)).toBe(false);
  });

  it('detects egress signals from error code', () => {
    const event = {
      payload: {
        error_code: 'egress_restricted',
      },
    };

    expect(hasEgressSignal(event)).toBe(true);
  });

  it('detects egress signals from free-text hints', () => {
    const event = {
      payload: {
        reason: 'blocked because target is non-allowlisted',
      },
    };

    expect(hasEgressSignal(event)).toBe(true);
  });

  it('does not report egress signal when hints are missing', () => {
    const event = {
      payload: {
        error_code: 'none',
        message: 'no egress policy issue',
      },
    };

    expect(hasEgressSignal(event)).toBe(false);
  });
});
