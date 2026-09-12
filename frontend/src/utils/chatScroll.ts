/**
 * 对话滚动相关的纯函数（便于单测，且与 ChatPanel 的虚拟列表逻辑解耦）。
 */

/** 视口底部到内容底部的距离在阈值内即视为「贴底」，用于流式期间是否自动跟随。 */
export const STICK_TO_BOTTOM_PX = 120;

export interface ScrollMetrics {
  scrollTop: number;
  scrollHeight: number;
  clientHeight: number;
}

/** 距底距离（像素）。 */
export function distanceFromBottom({ scrollTop, scrollHeight, clientHeight }: ScrollMetrics): number {
  return scrollHeight - scrollTop - clientHeight;
}

/** 是否足够贴近底部（用户没有主动上滚阅读历史）。 */
export function isNearBottom(m: ScrollMetrics, threshold = STICK_TO_BOTTOM_PX): boolean {
  return distanceFromBottom(m) < threshold;
}
