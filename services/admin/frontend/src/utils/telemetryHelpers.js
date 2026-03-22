import { normalizeEventPayload } from './governanceSignals';

export function asNumber(value) {
  if (value == null) return null;
  if (typeof value === 'string' && !value.trim()) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function parseTimestampMs(value) {
  if (!value) return Number.NaN;
  if (value instanceof Date) return value.getTime();
  const text = String(value).trim();
  if (!text) return Number.NaN;
  const hasTimezone = /(?:Z|[+\-]\d{2}:\d{2})$/i.test(text);
  const normalized = hasTimezone ? text : `${text}Z`;
  return new Date(normalized).getTime();
}

export function eventTypeOf(event, telemetryEventType) {
  return String(telemetryEventType(event) || event?.event_type || event?.type || 'telemetry');
}

export function modelOf(event) {
  const payload = normalizeEventPayload(event);
  return String(payload.model || event?.model || '-').trim() || '-';
}

export function truncate(value, max = 140) {
  const text = String(value || '');
  if (!text) return '-';
  return text.length > max ? `${text.slice(0, max)}...` : text;
}
