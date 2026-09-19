export interface FakeVirtualItem {
  key: string | number;
  index: number;
  start: number;
  size: number;
}

interface UseVirtualizerOptions {
  count: number;
  getItemKey?: (index: number) => string | number;
}

/**
 * Returns the module shape a `vi.mock('@tanstack/react-virtual', factory)`
 * callback should return. Renders every item — no real overscan windowing,
 * since jsdom has no real layout and windowing would only produce
 * misleading results here (e.g. "not all sent messages are visible").
 */
export function createVirtualizerModuleMock(rowHeight = 100) {
  return {
    useVirtualizer: (opts: UseVirtualizerOptions) => {
      const getItemKey = opts.getItemKey ?? ((i: number) => i);
      const items: FakeVirtualItem[] = Array.from({ length: opts.count }, (_, i) => ({
        key: getItemKey(i),
        index: i,
        start: i * rowHeight,
        size: rowHeight,
      }));
      return {
        getVirtualItems: () => items,
        getTotalSize: () => opts.count * rowHeight,
        measureElement: () => {},
        measure: () => {},
        getOffsetForIndex: (index: number) => [index * rowHeight, 'start'] as [number, 'start'],
      };
    },
  };
}
