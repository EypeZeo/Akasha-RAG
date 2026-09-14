import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import ExportModal from './ExportModal';
import { I18nProvider, TRANSLATIONS } from '../i18n';

describe('ExportModal', () => {
  it('the icon-only close button has an accessible name', () => {
    render(
      <I18nProvider>
        <ExportModal
          onClose={vi.fn()}
          onExportStarted={vi.fn()}
          collectionId="all"
          collectionTitle="All"
          doneCount={0}
        />
      </I18nProvider>,
    );
    expect(screen.getByRole('button', { name: TRANSLATIONS.en.close })).toBeTruthy();
  });
});
