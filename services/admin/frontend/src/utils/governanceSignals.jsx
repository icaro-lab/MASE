export function asOptionalNumber(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function eventTimestamp(event) {
  return event?.timestamp || event?.created_at || event?.occurred_at || '';
}

export function normalizeEventPayload(event) {
  let payload = event?.payload;
  let depth = 0;

  while (payload && typeof payload === 'object' && payload.payload && typeof payload.payload === 'object' && depth < 3) {
    payload = payload.payload;
    depth += 1;
  }

  return payload && typeof payload === 'object' ? payload : {};
}

export function normalizeOutcome(event, payload = normalizeEventPayload(event)) {
  const values = [
    event?.outcome,
    event?.result,
    event?.status,
    payload?.outcome,
    payload?.result,
    payload?.status,
    payload?.error_code,
    payload?.error,
    payload?.error_message,
  ]
    .filter((value) => value != null)
    .map((value) => String(value).toLowerCase())
    .join(' ');

  if (
    values.includes('confirmation_required') ||
    values.includes('pending_confirmation') ||
    values.includes('requires_confirmation') ||
    values.includes('confirm')
  ) {
    return 'confirmation';
  }

  if (values.includes('block') || values.includes('deny') || values.includes('ban')) {
    return 'blocked';
  }

  if (
    values.includes('success') ||
    values.includes('allow') ||
    values.includes('complete') ||
    values.includes('executed') ||
    values.includes('ok')
  ) {
    return 'success';
  }

  if (
    values.includes('fail') ||
    values.includes('error') ||
    values.includes('reject') ||
    values.includes('abort') ||
    values.includes('invalid')
  ) {
    return 'failed';
  }

  return 'other';
}

export function hasBanWindowSignal(event, payload = normalizeEventPayload(event)) {
  const iaMetadata = payload?.ia_metadata && typeof payload.ia_metadata === 'object' ? payload.ia_metadata : {};
  const receiptAck =
    payload?.manifest_receipt?.ack && typeof payload.manifest_receipt.ack === 'object'
      ? payload.manifest_receipt.ack
      : {};

  const datetimeFields = [
    payload?.ban_until,
    payload?.active_ban_until,
    payload?.blocked_until,
    payload?.penalty_until,
    iaMetadata?.ban_until,
    iaMetadata?.active_ban_until,
  ];
  if (datetimeFields.some((value) => typeof value === 'string' && value.trim())) return true;

  const countFields = [
    payload?.ban_minutes,
    payload?.ban_window_minutes,
    payload?.ban_window_seconds,
    iaMetadata?.ban_minutes,
    receiptAck?.max_similar_ban_minutes,
  ];
  if (countFields.some((value) => (asOptionalNumber(value) ?? 0) > 0)) return true;

  const message = [
    event?.outcome,
    payload?.error,
    payload?.error_message,
    payload?.response_preview,
    payload?.reason,
    payload?.message,
  ]
    .filter((value) => value != null)
    .map((value) => String(value).toLowerCase())
    .join(' ');

  return (
    (message.includes('ban') && (message.includes('until') || message.includes('minute') || message.includes('window'))) ||
    message.includes('banned')
  );
}

export function hasEgressSignal(event, payload = normalizeEventPayload(event)) {
  const errorCode = String(payload?.error_code || payload?.code || '').toLowerCase();
  if (errorCode === 'egress_restricted') return true;

  const message = [
    event?.outcome,
    payload?.error,
    payload?.error_message,
    payload?.response_preview,
    payload?.reason,
    payload?.message,
  ]
    .filter((value) => value != null)
    .map((value) => String(value).toLowerCase())
    .join(' ');

  return message.includes('egress restricted') || message.includes('non-allowlisted');
}

export function hasConfiguredCostLimit(costResponse) {
  const limits = costResponse?.cost_limits;
  if (!limits || typeof limits !== 'object') return false;

  const maxTotal = asOptionalNumber(limits.max_total_cost_usd ?? limits.max_cost_usd);
  const maxPerAgent = asOptionalNumber(limits.max_cost_usd_per_agent ?? limits.max_agent_cost_usd);
  return maxTotal != null || maxPerAgent != null;
}

export function costTotalUsd(costResponse) {
  const total = asOptionalNumber(costResponse?.totals?.total_cost_usd ?? costResponse?.totals?.total);
  return total ?? 0;
}
