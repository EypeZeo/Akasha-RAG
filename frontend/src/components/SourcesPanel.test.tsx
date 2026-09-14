import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, waitFor } from '@testing-library/react';
import SourcesPanel from './SourcesPanel';
import { I18nProvider, TRANSLATIONS } from '../i18n';
import * as api from '../api';

vi.mock('../api');

const ACTIVE_EXPORT_KEY = 'akasha:active_export';

function setup() {
  render(
    <I18nProvider>
      <SourcesPanel
        onBuildDone={vi.fn()}
        selectedId="all"
        onSelectCollection={vi.fn()}
        statsRefreshKey={0}
        collectionsPerPage={20}
        videosPerPage={20}
        onOpenSettings={vi.fn()}
      />
    </I18nProvider>,
  );
}

describe('SourcesPanel export status card', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
    // 组件挂载时会调用 listCollections/getKnowledgeStats；提供最小成功响应，
    // 避免它们的失败掩盖了本测试真正要验证的导出卡片渲染。
    vi.mocked(api.listCollections).mockResolvedValue({ success: true, items: [] } as any);
    vi.mocked(api.getKnowledgeStats).mockResolvedValue({ success: true } as any);
  });

  it('does not render the raw backend exception text on export failure', async () => {
    // 模拟"刷新页面后恢复未完成的导出任务"这条路径：localStorage 里有一个
    // 活跃导出任务，组件挂载时会轮询一次 getExportProgress。
    localStorage.setItem(ACTIVE_EXPORT_KEY, JSON.stringify({ id: 'task-1', mode: 'local' }));
    const rawBackendMessage = 'PermissionError: [WinError 5] 拒绝访问: 导出目标目录';
    vi.mocked(api.getExportProgress).mockResolvedValue({
      success: true,
      status: 'failed',
      progress: 1,
      total: 1,
      message: rawBackendMessage,
    } as any);
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});

    setup();

    await waitFor(() => {
      expect(api.getExportProgress).toHaveBeenCalled();
    });
    // Test environment defaults to English (no stored language preference,
    // navigator.language doesn't match a supported locale in jsdom).
    await waitFor(() => {
      expect(document.body.textContent).toContain(TRANSLATIONS.en.exportFailedRetry);
    });

    expect(document.body.textContent).not.toContain(rawBackendMessage);
    expect(errorSpy).toHaveBeenCalledWith('Export failed:', rawBackendMessage);
  });
});
