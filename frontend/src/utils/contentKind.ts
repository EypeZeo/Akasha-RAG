/** Explicit platform metadata takes precedence over an unknown media duration. */
export function isNoteItem(item: { item_type?: string; duration?: number | null }): boolean {
  if (item.item_type === 'note') return true;
  if (item.item_type === 'video') return false;
  return (item.duration ?? 0) === 0;
}
