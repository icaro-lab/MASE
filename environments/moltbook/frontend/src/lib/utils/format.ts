import { formatDistanceToNow } from 'date-fns';

function coerceDate(value: string | Date): Date {
  if (value instanceof Date) return value;
  const raw = String(value || '').trim();
  if (!raw) throw new Error('empty date');
  const hasZone = /(?:Z|[+-]\d{2}:\d{2})$/.test(raw);
  return new Date(hasZone ? raw : `${raw}Z`);
}

export function formatTimeAgo(date: string | Date): string {
  try {
    return formatDistanceToNow(coerceDate(date), { addSuffix: true });
  } catch {
    return 'unknown time ago';
  }
}

export function formatNumber(num: number): string {
  if (num >= 1000000) {
    return (num / 1000000).toFixed(1) + 'M';
  }
  if (num >= 1000) {
    return (num / 1000).toFixed(1) + 'K';
  }
  return num.toString();
}

export function truncateText(text: string, maxLength: number): string {
  if (text.length <= maxLength) return text;
  return text.slice(0, maxLength).trim() + '...';
}

export function getInitials(name: string): string {
  return name.charAt(0).toUpperCase();
}
