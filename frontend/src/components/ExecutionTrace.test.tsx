import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { I18nProvider } from '../i18n';
import ExecutionTrace from './ExecutionTrace';

describe('ExecutionTrace', () => {
  it('renders diagnostic identifiers without rendering source text', () => {
    render(
      <I18nProvider>
        <ExecutionTrace
          latency_ms={12}
          trace={{
            route: 'vector',
            steps: [{ name: 'retrieval', time_ms: 3 }],
            chunks: [{ chunk_id: 'chunk-1', title: 'Synthetic title', score: 0.91 }],
          }}
        />
      </I18nProvider>,
    );

    fireEvent.click(screen.getByRole('button'));
    expect(screen.getByText('Synthetic title')).toBeTruthy();
    expect(screen.getByText('91%')).toBeTruthy();
    expect(screen.queryByText(/private transcript sentence/i)).toBeNull();
  });
});
