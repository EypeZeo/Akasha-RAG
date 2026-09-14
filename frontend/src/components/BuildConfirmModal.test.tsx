import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import BuildConfirmModal from './BuildConfirmModal';
import { I18nProvider, TRANSLATIONS } from '../i18n';
import * as api from '../api';

vi.mock('../api');

describe('BuildConfirmModal', () => {
  beforeEach(() => {
    vi.mocked(api.listPendingKnowledge).mockResolvedValue({
      success: true, items: [], total: 0, video_count: 0, note_count: 0, page: 1, has_more: false,
    } as any);
  });

  it('the icon-only close button has an accessible name', async () => {
    render(
      <I18nProvider>
        <BuildConfirmModal onClose={vi.fn()} onConfirm={vi.fn()} pendingCount={0} />
      </I18nProvider>,
    );
    await waitFor(() => {
      expect(screen.getByRole('button', { name: TRANSLATIONS.en.close })).toBeTruthy();
    });
  });
});
