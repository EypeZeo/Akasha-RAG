import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { useVirtualizer } from '@tanstack/react-virtual';

vi.mock('@tanstack/react-virtual', async () => {
  const { createVirtualizerModuleMock } = await import('./virtualizerFake');
  return createVirtualizerModuleMock();
});

function ListProbe({ count }: { count: number }) {
  const items = ['a', 'b', 'c'].slice(0, count);
  const virtualizer = useVirtualizer({
    count: items.length,
    getScrollElement: () => null,
    estimateSize: () => 100,
    getItemKey: (i: number) => items[i],
  });
  return (
    <div style={{ height: virtualizer.getTotalSize() }}>
      {virtualizer.getVirtualItems().map(vi => (
        <div key={vi.key} data-testid="row">{items[vi.index]}</div>
      ))}
    </div>
  );
}

describe('virtualizerFake smoke test', () => {
  it('renders every item, not a windowed subset', () => {
    render(<ListProbe count={3} />);
    const rows = screen.getAllByTestId('row');
    expect(rows.map(r => r.textContent)).toEqual(['a', 'b', 'c']);
  });

  it('getTotalSize reflects the configured row height and count', () => {
    const { container } = render(<ListProbe count={2} />);
    expect(container.firstElementChild).toHaveStyle({ height: '200px' });
  });
});
