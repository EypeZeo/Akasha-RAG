import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../api';
import type { Message } from '../components/ChatMessageRow';
import { checkExportSize, EXPORT_LIMITS, ExportCapacityError, fetchExportHistory, messageFromItem } from './chatHistory';

vi.mock('../api');

const item = (id: number, client_key?: string): api.MessageItem => ({
  id, client_key, session_id: 7, role: id % 2 ? 'user' : 'assistant', content: `m${id}`,
  route_type: 'vector', created_at: '2026-01-01',
});

describe('fetchExportHistory', () => {
  beforeEach(() => vi.clearAllMocks());
  it('uses a server high-water mark, pages backwards, merges client identities, and keeps local-only messages', async () => {
    vi.mocked(api.getSessionSnapshot).mockResolvedValue({ success: true, session_id: 7, snapshot_id: 401, total: 401 });
    vi.mocked(api.getSessionMessages)
      .mockResolvedValueOnce({ success: true, items: Array.from({ length: 200 }, (_, i) => item(i + 202)), has_more: true })
      .mockResolvedValueOnce({ success: true, items: Array.from({ length: 200 }, (_, i) => item(i + 2)), has_more: true })
      .mockResolvedValueOnce({ success: true, items: [item(1, 'local-one')], has_more: false });
    const localOne: Message = { clientKey: 'local-one', role: 'user', content: 'richer local m1' };
    const localOnly: Message = { clientKey: 'pending', role: 'assistant', content: 'not persisted at snapshot' };
    const progress: number[] = [];
    const result = await fetchExportHistory(7, [localOne, localOnly], new AbortController().signal, n => progress.push(n));

    expect(result).toHaveLength(402);
    expect(result[0]).toMatchObject({ id: 1, clientKey: 'local-one', content: 'richer local m1' });
    expect(result[result.length - 1]).toEqual(localOnly);
    expect(progress).toEqual([200, 400, 401]);
    expect(api.getSessionMessages).toHaveBeenNthCalledWith(2, 7, expect.objectContaining({ before: 202, until: 401, limit: 200 }));
  });

  it('rejects an invalid cursor or failed middle page without returning a partial export', async () => {
    vi.mocked(api.getSessionSnapshot).mockResolvedValue({ success: true, session_id: 7, snapshot_id: 201, total: 201 });
    vi.mocked(api.getSessionMessages)
      .mockResolvedValueOnce({ success: true, items: Array.from({ length: 200 }, (_, i) => item(i + 2)), has_more: true })
      .mockRejectedValueOnce(new Error('network'));
    await expect(fetchExportHistory(7, [], new AbortController().signal, vi.fn())).rejects.toThrow('network');

    vi.mocked(api.getSessionMessages).mockReset();
    vi.mocked(api.getSessionMessages).mockResolvedValue({ success: true, items: [item(201)], has_more: true });
    await expect(fetchExportHistory(7, [], new AbortController().signal, vi.fn())).rejects.toThrow('Invalid history cursor');
  });

  it('stops before another request after cancellation', async () => {
    const controller = new AbortController();
    vi.mocked(api.getSessionSnapshot).mockImplementation(async () => {
      controller.abort();
      return { success: true, session_id: 7, snapshot_id: 1, total: 1 };
    });
    await expect(fetchExportHistory(7, [], controller.signal, vi.fn())).rejects.toHaveProperty('name', 'AbortError');
    expect(api.getSessionMessages).not.toHaveBeenCalled();
  });

  it('enforces both message and UTF-8 byte budgets', () => {
    expect(() => checkExportSize(Array.from({ length: 3 }, (_, i) => ({ clientKey: `${i}`, role: 'user', content: 'x' })), { messages: 2, bytes: 100 })).toThrow(ExportCapacityError);
    expect(() => checkExportSize([{ clientKey: 'x', role: 'user', content: '中文' }], { messages: 2, bytes: 5 })).toThrow(ExportCapacityError);
  });
});

describe('fetchExportHistory budgets', () => {
  beforeEach(() => vi.clearAllMocks());

  // About 20 KiB of source title per message: 200 of them per page, so two pages fit in 8 MiB and a third does not.
  const heavy = (id: number): api.MessageItem => ({
    ...item(id),
    sources: [{ platform: 'douyin', platform_item_id: `s${id}`, title: 'x'.repeat(20 * 1024), url: 'https://example.test/s' }],
  });
  const heavyPage = (firstId: number) => ({
    success: true, items: Array.from({ length: 200 }, (_, i) => heavy(firstId + i)), has_more: true,
  });

  it('gives up part-way through the pages, with no partial result, once large sources exceed the byte budget', async () => {
    vi.mocked(api.getSessionSnapshot).mockResolvedValue({ success: true, session_id: 7, snapshot_id: 1000, total: 1000 });
    vi.mocked(api.getSessionMessages)
      .mockResolvedValueOnce(heavyPage(801)).mockResolvedValueOnce(heavyPage(601)).mockResolvedValueOnce(heavyPage(401))
      .mockResolvedValue(heavyPage(201));
    const progress: number[] = [];

    await expect(fetchExportHistory(7, [], new AbortController().signal, n => progress.push(n))).rejects.toBeInstanceOf(ExportCapacityError);

    expect(api.getSessionMessages).toHaveBeenCalledTimes(3); // pages 4 and 5 are never requested
    expect(progress).toEqual([200, 400]);
  });

  it('counts the sources the same way as the server: UTF-8 bytes of the content plus the compact JSON of the sources', () => {
    const sources = [{ platform: 'bilibili', platform_item_id: 'BV1', title: '标题', url: 'https://example.test/1' }];
    const message = { clientKey: 'k', role: 'assistant' as const, content: '中文回答', sources };
    const compact = new TextEncoder().encode(JSON.stringify(sources)).byteLength;
    const exact = new TextEncoder().encode('中文回答').byteLength + compact;

    expect(() => checkExportSize([message], { messages: 1, bytes: exact })).not.toThrow();
    expect(() => checkExportSize([message], { messages: 1, bytes: exact - 1 })).toThrow(ExportCapacityError);
  });

  it('refuses a session over the message limit before it requests a single page', async () => {
    vi.mocked(api.getSessionSnapshot).mockResolvedValue({
      success: true, session_id: 7, snapshot_id: 99999, total: EXPORT_LIMITS.messages + 1,
    });

    await expect(fetchExportHistory(7, [], new AbortController().signal, vi.fn())).rejects.toBeInstanceOf(ExportCapacityError);

    expect(api.getSessionMessages).not.toHaveBeenCalled();
  });

  it('refuses an oversized loaded window before it asks the server for anything', async () => {
    const loaded: Message[] = Array.from({ length: EXPORT_LIMITS.messages + 1 }, (_, i) => ({ clientKey: `k${i}`, role: 'user', content: 'x' }));

    await expect(fetchExportHistory(7, loaded, new AbortController().signal, vi.fn())).rejects.toBeInstanceOf(ExportCapacityError);

    expect(api.getSessionSnapshot).not.toHaveBeenCalled();
  });
});

describe('fetchExportHistory snapshot validation', () => {
  beforeEach(() => vi.clearAllMocks());

  it.each([
    ['is unsuccessful', { success: false, session_id: 7, snapshot_id: 5, total: 5 }],
    ['belongs to another session', { success: true, session_id: 8, snapshot_id: 5, total: 5 }],
    ['has a negative total', { success: true, session_id: 7, snapshot_id: 5, total: -1 }],
    ['has a fractional id', { success: true, session_id: 7, snapshot_id: 1.5, total: 5 }],
    ['has no total', { success: true, session_id: 7, snapshot_id: 5 } as { success: boolean; session_id: number; snapshot_id: number; total: number }],
  ])('rejects a snapshot that %s, and requests no page', async (_case, snapshot) => {
    vi.mocked(api.getSessionSnapshot).mockResolvedValue(snapshot);

    await expect(fetchExportHistory(7, [], new AbortController().signal, vi.fn())).rejects.toThrow('Invalid history snapshot');

    expect(api.getSessionMessages).not.toHaveBeenCalled();
  });

  it('does not export when the snapshot request itself fails', async () => {
    vi.mocked(api.getSessionSnapshot).mockRejectedValue(new Error('500: boom'));

    await expect(fetchExportHistory(7, [], new AbortController().signal, vi.fn())).rejects.toThrow('500: boom');

    expect(api.getSessionMessages).not.toHaveBeenCalled();
  });

  it('exports just the loaded messages for an empty session', async () => {
    vi.mocked(api.getSessionSnapshot).mockResolvedValue({ success: true, session_id: 7, snapshot_id: 0, total: 0 });
    const local: Message = { clientKey: 'only-local', role: 'user', content: 'hello' };

    const result = await fetchExportHistory(7, [local], new AbortController().signal, vi.fn());

    expect(result).toEqual([local]);
    expect(api.getSessionMessages).not.toHaveBeenCalled();
  });
});

describe('client keys identify a message across the server and the screen', () => {
  beforeEach(() => vi.clearAllMocks());

  it('uses the stored client key, and falls back to the row id for legacy rows', () => {
    expect(messageFromItem({ ...item(5), client_key: 'k-5' }).clientKey).toBe('k-5');
    expect(messageFromItem({ ...item(5), client_key: null }).clientKey).toBe('db:5');
    expect(messageFromItem(item(5)).clientKey).toBe('db:5');
  });

  it('merges a just-sent exchange (user and assistant keys) into the persisted rows without duplicating it', async () => {
    vi.mocked(api.getSessionSnapshot).mockResolvedValue({ success: true, session_id: 7, snapshot_id: 4, total: 4 });
    vi.mocked(api.getSessionMessages).mockResolvedValue({
      success: true, has_more: false,
      items: [item(1), item(2), { ...item(3), client_key: 'u-new' }, { ...item(4), client_key: 'a-new' }],
    });
    // The screen still holds the exchange under its client keys, with no row ids yet, and the answer is richer than the stored one.
    const loaded: Message[] = [
      { clientKey: 'u-new', role: 'user', content: 'm3' },
      { clientKey: 'a-new', role: 'assistant', content: 'm4 plus trace', latency_ms: 1200 },
    ];

    const result = await fetchExportHistory(7, loaded, new AbortController().signal, vi.fn());

    expect(result.map(message => message.clientKey)).toEqual(['db:1', 'db:2', 'u-new', 'a-new']);
    expect(result[3]).toMatchObject({ id: 4, content: 'm4 plus trace', latency_ms: 1200 });
  });
});
