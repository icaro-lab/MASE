import { describe, expect, it } from 'vitest';
import { formatDateTime, formatRunDuration, formatTime } from '../formatters';

describe('formatters UTC parsing', () => {
  it('treats naive ISO timestamps as UTC for date/time formatting', () => {
    const naive = '2026-02-26T00:11:30';
    const utc = '2026-02-26T00:11:30Z';
    expect(formatDateTime(naive)).toBe(formatDateTime(utc));
    expect(formatTime(naive)).toBe(formatTime(utc));
  });

  it('treats naive run timestamps as UTC when computing duration', () => {
    const naiveStart = '2026-02-26T00:11:30';
    const naiveEnd = '2026-02-26T00:13:00';
    const utcStart = '2026-02-26T00:11:30Z';
    const utcEnd = '2026-02-26T00:13:00Z';
    expect(formatRunDuration(naiveStart, naiveEnd)).toBe(formatRunDuration(utcStart, utcEnd));
  });
});
