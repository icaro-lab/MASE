export const ROME_LOCALE = 'it-IT';
export const ROME_TIMEZONE = 'Europe/Rome';

export function asNumber(value, fallback = 0) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

export function formatNumber(value) {
  return asNumber(value).toLocaleString(ROME_LOCALE);
}

function parseDateValue(value) {
  if (!value) return null;
  if (value instanceof Date) {
    return Number.isNaN(value.getTime()) ? null : value;
  }
  const text = String(value).trim();
  if (!text) return null;
  const hasTimezone = /(?:Z|[+\-]\d{2}:\d{2})$/i.test(text);
  const normalized = hasTimezone ? text : `${text}Z`;
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function formatDateTime(value) {
  if (!value) return 'N/A';
  const date = parseDateValue(value);
  if (!date) return 'N/A';
  return new Intl.DateTimeFormat(ROME_LOCALE, {
    timeZone: ROME_TIMEZONE,
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date);
}

export function formatTime(value) {
  if (!value) return 'N/A';
  const date = parseDateValue(value);
  if (!date) return 'N/A';
  return new Intl.DateTimeFormat(ROME_LOCALE, {
    timeZone: ROME_TIMEZONE,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).format(date);
}

export function formatDurationSeconds(value) {
  const total = Math.max(0, Math.floor(asNumber(value)));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;

  if (hours > 0) {
    return `${hours}h ${minutes}m ${seconds}s`;
  }
  if (minutes > 0) {
    return `${minutes}m ${seconds}s`;
  }
  return `${seconds}s`;
}

export function formatRunDuration(startedAt, endedAt) {
  if (!startedAt) return 'N/A';
  const startDate = parseDateValue(startedAt);
  if (!startDate) return 'N/A';
  const endDate = endedAt ? parseDateValue(endedAt) : null;
  const start = startDate.getTime();
  const end = endDate ? endDate.getTime() : Date.now();
  if (!Number.isFinite(end)) return 'N/A';
  return formatDurationSeconds((end - start) / 1000);
}

export function formatPenaltyMinutes(value) {
  const minutes = asNumber(value);
  if (minutes <= 0) return '0 min';
  if (minutes >= 60) {
    const hours = (minutes / 60).toFixed(minutes % 60 === 0 ? 0 : 1);
    return `${hours} h`;
  }
  return `${minutes.toFixed(minutes % 1 === 0 ? 0 : 1)} min`;
}
