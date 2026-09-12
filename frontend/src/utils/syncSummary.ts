/**
 * 收藏夹同步结果的计数聚合（纯函数，便于单测，与 SourcesPanel 的弹窗逻辑解耦）。
 *
 * UI-01: `platform="all"` 时后端会同时返回顶层聚合字段（`added_videos` 等）
 * 和逐平台的 `results[]` 明细——这两者是同一份数据的两种表示，只能选一个
 * source 求和，绝不能两个都加，否则数字会翻倍。
 */

export interface SyncResultLike {
  added_videos?: number;
  removed_videos?: number;
  added_notes?: number;
  removed_notes?: number;
  invalid_count?: number;
  results?: Array<{
    added_videos?: number;
    removed_videos?: number;
    added_notes?: number;
    removed_notes?: number;
    invalid_count?: number;
  } | null | undefined> | null;
}

export interface SyncCounts {
  addedVideos: number;
  removedVideos: number;
  addedNotes: number;
  removedNotes: number;
  invalidCount: number;
}

export function aggregateSyncCounts(r: SyncResultLike): SyncCounts {
  if (Array.isArray(r.results)) {
    const counts: SyncCounts = { addedVideos: 0, removedVideos: 0, addedNotes: 0, removedNotes: 0, invalidCount: 0 };
    for (const item of r.results) {
      counts.addedVideos += item?.added_videos ?? 0;
      counts.removedVideos += item?.removed_videos ?? 0;
      counts.addedNotes += item?.added_notes ?? 0;
      counts.removedNotes += item?.removed_notes ?? 0;
      counts.invalidCount += item?.invalid_count ?? 0;
    }
    return counts;
  }
  return {
    addedVideos: r.added_videos ?? 0,
    removedVideos: r.removed_videos ?? 0,
    addedNotes: r.added_notes ?? 0,
    removedNotes: r.removed_notes ?? 0,
    invalidCount: r.invalid_count ?? 0,
  };
}
