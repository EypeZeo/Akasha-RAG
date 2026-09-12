import { describe, it, expect } from 'vitest';
import { aggregateSyncCounts, getFailedPlatforms } from './syncSummary';

describe('aggregateSyncCounts', () => {
  it('UI-01: does not double-count when both a top-level aggregate and results[] are present', () => {
    const r = {
      added_videos: 5,
      removed_videos: 1,
      added_notes: 2,
      removed_notes: 0,
      invalid_count: 3,
      results: [
        { added_videos: 3, removed_videos: 1, added_notes: 2, removed_notes: 0, invalid_count: 1 },
        { added_videos: 2, removed_videos: 0, added_notes: 0, removed_notes: 0, invalid_count: 2 },
      ],
    };

    expect(aggregateSyncCounts(r)).toEqual({
      addedVideos: 5,
      removedVideos: 1,
      addedNotes: 2,
      removedNotes: 0,
      invalidCount: 3,
    });
  });

  it('sums per-platform results when there is no top-level aggregate to fall back on', () => {
    const r = {
      results: [
        { added_videos: 3, invalid_count: 1 },
        { added_videos: 2, invalid_count: 2 },
      ],
    };

    expect(aggregateSyncCounts(r)).toEqual({
      addedVideos: 5,
      removedVideos: 0,
      addedNotes: 0,
      removedNotes: 0,
      invalidCount: 3,
    });
  });

  it('falls back to the top-level fields for a single-platform sync (no results[])', () => {
    const r = { added_videos: 4, removed_videos: 2, invalid_count: 1 };

    expect(aggregateSyncCounts(r)).toEqual({
      addedVideos: 4,
      removedVideos: 2,
      addedNotes: 0,
      removedNotes: 0,
      invalidCount: 1,
    });
  });

  it('handles a response with neither field present', () => {
    expect(aggregateSyncCounts({})).toEqual({
      addedVideos: 0,
      removedVideos: 0,
      addedNotes: 0,
      removedNotes: 0,
      invalidCount: 0,
    });
  });

  it('tolerates null/undefined entries inside results[]', () => {
    const r = { results: [null, { added_videos: 1 }, undefined] };
    expect(aggregateSyncCounts(r).addedVideos).toBe(1);
  });
});

describe('getFailedPlatforms', () => {
  it('returns an empty list when there is no platform_results field', () => {
    expect(getFailedPlatforms({})).toEqual([]);
  });

  it('returns an empty list when every platform succeeded', () => {
    const r = { platform_results: { douyin: { success: true }, bilibili: { success: true } } };
    expect(getFailedPlatforms(r)).toEqual([]);
  });

  it('names the platform that failed without hiding a partial success', () => {
    const r = { platform_results: { douyin: { success: false, message: 'boom' }, bilibili: { success: true } } };
    expect(getFailedPlatforms(r)).toEqual(['douyin']);
  });

  it('names every platform when all of them failed', () => {
    const r = { platform_results: { douyin: { success: false }, bilibili: { success: false } } };
    expect(getFailedPlatforms(r).sort()).toEqual(['bilibili', 'douyin']);
  });
});
