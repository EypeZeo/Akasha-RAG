/**
 * 后端 API 调用封装
 */

const BASE = '/api';

/**
 * Sent on every request so the backend's local-CSRF guard (app/core/security.py)
 * can tell this apart from a cross-origin page's "simple request" — browsers
 * cannot attach a custom header without first passing a CORS preflight, which
 * only this app's own origin can pass.
 */
const CLIENT_HEADERS = { 'X-Akasha-Client': '1' };

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${url}`, {
    headers: { 'Content-Type': 'application/json', ...CLIENT_HEADERS },
    ...options,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => 'Unknown error');
    throw new Error(`${res.status}: ${text}`);
  }
  return res.json();
}

// ---- Auth ----

export type PlatformKind = 'douyin' | 'bilibili';

export interface PlatformInfo {
  platform: PlatformKind;
  name: string;
  is_logged_in: boolean;
  status: string;
  account_id?: string;
  nickname?: string;
  avatar_url?: string;
  message?: string;
}

export async function listPlatforms(): Promise<{ success: boolean; platforms: PlatformInfo[] }> {
  return request('/auth/platforms');
}

export async function loginStart(): Promise<{ success: boolean; message: string; status: string }> {
  return request('/auth/douyin/login/start', { method: 'POST' });
}

export async function loginStatus(): Promise<{
  status: string;
  message: string;
  qrcode_image_base64?: string;
}> {
  return request('/auth/douyin/login/status');
}

export async function douyinGenerateQr(): Promise<{
  success: boolean;
  data?: {
    qrcode_image_base64?: string;
    status: string;
    expires_in: number;
  };
  message?: string;
}> {
  return request('/auth/douyin/qrcode/generate', { method: 'POST' });
}

export async function douyinRefreshQr(): Promise<{
  success: boolean;
  data?: {
    qrcode_image_base64?: string;
    status?: string;
  };
  message?: string;
}> {
  return request('/auth/douyin/qrcode/refresh', { method: 'POST' });
}

export async function douyinShowWindow(): Promise<{
  success: boolean;
  message: string;
}> {
  return request('/auth/douyin/window/show', { method: 'POST' });
}

export async function loginCancel(): Promise<{ success: boolean; message: string; status: string }> {
  return request('/auth/douyin/login/cancel', { method: 'POST' });
}

export async function logout(): Promise<{ success: boolean; message: string }> {
  return request('/auth/douyin/logout', { method: 'POST' });
}

export async function bilibiliGenerateQr(): Promise<{
  success: boolean;
  data?: {
    qrcode_key: string;
    qrcode_url: string;
    qrcode_image_base64: string;
    expires_in: number;
  };
  message?: string;
}> {
  return request('/auth/bilibili/qrcode/generate', { method: 'POST' });
}

export async function bilibiliPollQr(qrcodeKey: string): Promise<{
  success: boolean;
  status: string;
  message: string;
  account_id?: string;
  nickname?: string;
  avatar_url?: string;
}> {
  return request(`/auth/bilibili/qrcode/poll?qrcode_key=${encodeURIComponent(qrcodeKey)}`);
}

export async function bilibiliStatus(): Promise<{
  success: boolean;
  is_logged_in: boolean;
  account_id?: string;
  nickname?: string;
  avatar_url?: string;
  message?: string;
}> {
  return request('/auth/bilibili/status');
}

export async function bilibiliLogout(): Promise<{ success: boolean; message: string }> {
  return request('/auth/bilibili/logout', { method: 'POST' });
}

export async function logoutAll(): Promise<{ success: boolean; platforms: Record<string, { success: boolean; message: string }> }> {
  return request('/auth/logout-all', { method: 'POST' });
}

// ---- Favorites ----

export async function syncFavorites(platform: string = 'douyin'): Promise<{
  success: boolean;
  collections_total?: number;
  videos_total?: number;
  added_videos?: number;
  removed_videos?: number;
  added_notes?: number;
  removed_notes?: number;
  added_total?: number;
  removed_total?: number;
  summary_message?: string;
  videos_count?: number;
  notes_count?: number;
  invalid_count?: number;
  synced_total?: number;
  message?: string;
  results?: any[];
  partial?: boolean;
  platform_results?: Record<string, { success: boolean; message?: string }>;
}> {
  return request(`/favorites/sync?platform=${platform}`, { method: 'POST' });
}

export async function listCollections(platform?: string, signal?: AbortSignal): Promise<{ success: boolean; items: CollectionItem[]; total: number }> {
  const query = platform && platform !== 'all' ? `?platform=${platform}` : '';
  return request(`/favorites/collections${query}`, { signal });
}

export async function listCollectionVideos(
  collectionId: string, page = 1, size = 20, platform?: string, cursor?: string
): Promise<{
  success: boolean;
  items: VideoItem[];
  total: number;
  video_count?: number;
  note_count?: number;
  next_cursor?: string | null;
  has_more?: boolean;
}> {
  const pQuery = platform && platform !== 'all' ? `&platform=${platform}` : '';
  const cQuery = cursor ? `&cursor=${encodeURIComponent(cursor)}` : '';
  return request(`/favorites/collections/${collectionId}/videos?page=${page}&size=${size}${pQuery}${cQuery}`);
}

// ---- Knowledge ----

export interface SyncParams {
  scope?: 'all' | 'selected';
  collectionId?: string | null;
  contentType?: 'all' | 'video' | 'note';
  selectedIds?: string[];
  platform?: string;
}

export async function syncKnowledge(
  paramsOrIds?: SyncParams | string[],
  legacyContentType?: 'all' | 'video' | 'note',
): Promise<{ success: boolean; task_id?: string; pending_count?: number; message?: string }> {
  let body: {
    scope: string;
    collection_id: string | null;
    content_type: string;
    selected_ids: string[];
    platform?: string;
  };

  if (Array.isArray(paramsOrIds)) {
    const hasIds = paramsOrIds.length > 0;
    body = {
      scope: hasIds ? 'selected' : 'all',
      collection_id: null,
      content_type: legacyContentType || 'all',
      selected_ids: paramsOrIds,
      platform: 'all',
    };
  } else if (paramsOrIds && typeof paramsOrIds === 'object') {
    body = {
      scope: paramsOrIds.scope || (paramsOrIds.selectedIds && paramsOrIds.selectedIds.length > 0 ? 'selected' : 'all'),
      collection_id: paramsOrIds.collectionId && paramsOrIds.collectionId !== 'all' ? paramsOrIds.collectionId : null,
      content_type: paramsOrIds.contentType || 'all',
      selected_ids: paramsOrIds.selectedIds || [],
      platform: paramsOrIds.platform || 'all',
    };
  } else {
    body = {
      scope: 'all',
      collection_id: null,
      content_type: legacyContentType || 'all',
      selected_ids: [],
      platform: 'all',
    };
  }

  return request('/knowledge/sync', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export async function listPendingKnowledge(
  collectionId?: string | null,
  contentType?: 'all' | 'video' | 'note',
  page: number = 1,
  pageSize: number = 50,
  platform?: string,
  signal?: AbortSignal,
): Promise<{
  success: boolean;
  items: VideoItem[];
  total: number;
  video_count: number;
  note_count: number;
  page: number;
  page_size: number;
  has_more: boolean;
}> {
  const params = new URLSearchParams();
  if (collectionId && collectionId !== 'all') params.set('collection_id', collectionId);
  if (contentType && contentType !== 'all') params.set('content_type', contentType);
  if (platform && platform !== 'all') params.set('platform', platform);
  params.set('page', String(page));
  params.set('page_size', String(pageSize));
  return request(`/knowledge/pending?${params.toString()}`, { signal });
}

export async function resetFailedVideos(): Promise<{ success: boolean; reset_count: number; message?: string }> {
  return request('/knowledge/reset-failed', { method: 'POST' });
}

export async function deleteVideo(platformItemId: string, platform: string): Promise<{ success: boolean; message?: string }> {
  return request(`/knowledge/videos/${encodeURIComponent(platformItemId)}?platform=${encodeURIComponent(platform)}`, { method: 'DELETE' });
}

export async function getSyncProgress(taskId: string): Promise<any> {
  return request(`/knowledge/sync/${taskId}`);
}

export async function cancelSync(taskId: string): Promise<{ success: boolean; message?: string }> {
  return request(`/knowledge/sync/${taskId}/cancel`, { method: 'POST' });
}

export async function getKnowledgeStats(): Promise<any> {
  return request('/knowledge/stats');
}

export async function clearAllKnowledge(collectionId?: string, platform?: string): Promise<{ success: boolean; reset_count: number }> {
  return request('/knowledge/clear-all', {
    method: 'POST',
    body: JSON.stringify({ collection_id: collectionId || null, platform: platform || null }),
  });
}

export interface BatchExportParams {
  collection_id?: string | null;
  /** 收藏夹所属平台。远端收藏夹 ID 只在平台内唯一，同一个 ID 可能同时出现在两个平台。 */
  platform?: string;
  selected_ids?: string[];
  content_type: 'original' | 'ai' | 'both';
  format: 'markdown' | 'word' | 'excel' | 'ppt' | 'pdf';
  pack_mode: 'single' | 'zip';
  target_dir?: string | null;
  auto_open?: boolean;
}

export interface ExportProgress {
  success: boolean;
  status?: 'queued' | 'running' | 'done' | 'failed';
  progress?: number;
  total?: number;
  message?: string;
  mode?: 'local' | 'browser';
  result?: any;
}

/** 提交批量导出后台任务，立即返回 task_id。 */
export async function exportBatchStart(
  params: BatchExportParams,
): Promise<{ success: boolean; task_id: string; mode: 'local' | 'browser'; message?: string }> {
  const data = await request<any>('/knowledge/export/batch', {
    method: 'POST',
    body: JSON.stringify(params),
  });
  // The thrown message is only ever console.error()'d by the caller (see
  // ExportModal.tsx) — the UI always shows a stable, localized string
  // regardless of what this says. Keep the backend's specific reason here
  // so that diagnostic path is actually useful instead of a fixed string.
  if (!data.success) throw new Error(data.message || 'Export submission failed (no server message)');
  return data;
}

export async function getExportProgress(taskId: string): Promise<ExportProgress> {
  return request(`/knowledge/export/batch/${taskId}`);
}

/** 浏览器模式产物下载地址（产物保留一段时间，可反复下载）。 */
export function exportDownloadUrl(taskId: string): string {
  return `${BASE}/knowledge/export/batch/${taskId}/download`;
}

/** 弹出系统原生「选择文件夹」对话框。 */
export async function pickDirectory(
  initialDir?: string,
): Promise<{ success: boolean; path?: string; cancelled?: boolean; busy?: boolean; message?: string }> {
  return request('/system/pick-directory', {
    method: 'POST',
    body: JSON.stringify({ initial_dir: initialDir || null }),
  });
}

// ---- System Management ----

export async function getLogLevel(): Promise<{ success: boolean; current_level: string; available_levels: string[] }> {
  return request('/system/log-level');
}

export async function setLogLevel(level: string): Promise<{ success: boolean; current_level: string; message: string }> {
  return request('/system/log-level', {
    method: 'POST',
    body: JSON.stringify({ level }),
  });
}

export async function openLocalFolder(folderType: 'logs' | 'export' | 'custom', customPath?: string): Promise<{ success: boolean; path?: string; message?: string }> {
  return request('/system/open-folder', {
    method: 'POST',
    body: JSON.stringify({ folder_type: folderType, custom_path: customPath }),
  });
}

export async function getRecentLogs(lines: number = 100, logType: string = 'all'): Promise<{ success: boolean; lines: string[]; file?: string }> {
  return request(`/system/logs?lines=${lines}&log_type=${logType}`);
}

export interface AudioCacheStats {
  total_files: number;
  total_bytes: number;
  total_mb: number;
  audio_files: number;
  temp_files: number;
}

export async function getAudioCacheStats(): Promise<{ success: boolean; stats: AudioCacheStats }> {
  return request('/system/audio-cache-stats');
}

export async function cleanAudioCache(maxAgeHours?: number, maxSizeMb?: number): Promise<{
  success: boolean;
  result: {
    deleted_files: number;
    freed_bytes: number;
    freed_mb: number;
  };
}> {
  return request('/system/clean-audio-cache', {
    method: 'POST',
    body: JSON.stringify({ max_age_hours: maxAgeHours, max_size_mb: maxSizeMb }),
  });
}

// ---- Chat ----

export async function chatAsk(
  query: string,
  sessionId?: number | null,
  collectionId?: string | null,
  platform?: string,
): Promise<{
  success: boolean;
  answer?: string;
  sources?: SourceItem[];
  session_id?: number;
  route_type?: string;
  latency_ms?: number;
  message?: string;
}> {
  return request('/chat/ask', {
    method: 'POST',
    body: JSON.stringify({
      query,
      session_id: sessionId ?? null,
      collection_id: collectionId ?? null,
      platform: platform && platform !== 'all' ? platform : null,
    }),
  });
}

export async function* chatAskStream(
  query: string,
  sessionId?: number | null,
  collectionId?: string | null,
  platformOrSignal?: string | AbortSignal,
  maybeSignal?: AbortSignal,
  clientKeys?: { user: string; assistant: string },
): AsyncGenerator<any> {
  const platform = typeof platformOrSignal === 'string' ? platformOrSignal : undefined;
  const signal = platformOrSignal instanceof AbortSignal ? platformOrSignal : maybeSignal;

  const res = await fetch(`${BASE}/chat/ask/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...CLIENT_HEADERS },
    body: JSON.stringify({
      query,
      session_id: sessionId ?? null,
      collection_id: collectionId ?? null,
      platform: platform && platform !== 'all' ? platform : null,
      client_keys: clientKeys,
    }),
    signal,
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  if (!res.body) throw new Error('No stream body');

  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();

  let buffer = '';
  let eventType = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      if (buffer.trim()) {
        const line = buffer.replace(/\r$/, '');
        if (line.startsWith('data: ')) {
          try {
            const data = JSON.parse(line.slice(6));
            yield { ...data, _event: eventType };
          } catch {
            yield { raw: line.slice(6), _event: eventType };
          }
        }
      }
      break;
    }

    buffer += value;
    const lines = buffer.split('\n');
    buffer = lines.pop() ?? '';

    for (let line of lines) {
      line = line.replace(/\r$/, '');
      if (line.startsWith('event: ')) {
        eventType = line.slice(7).trim();
      } else if (line.startsWith('data: ')) {
        const raw = line.slice(6);
        try {
          const data = JSON.parse(raw);
          yield { ...data, _event: eventType };
        } catch {
          yield { raw, _event: eventType };
        }
        eventType = '';
      }
    }
  }
}

export async function listSessions(q?: string): Promise<{ success: boolean; items: SessionItem[] }> {
  const query = q && q.trim() ? `?q=${encodeURIComponent(q.trim())}` : '';
  return request(`/chat/sessions${query}`);
}

export async function getSessionMessages(
  sessionId: number,
  opts?: { before?: number; limit?: number; until?: number; signal?: AbortSignal },
): Promise<{ success: boolean; items: MessageItem[]; has_more?: boolean }> {
  const params = new URLSearchParams();
  if (opts?.before != null) params.set('before', String(opts.before));
  if (opts?.limit != null) params.set('limit', String(opts.limit));
  if (opts?.until != null) params.set('until', String(opts.until));
  const qs = params.toString();
  return request(`/chat/sessions/${sessionId}/messages${qs ? `?${qs}` : ''}`, { signal: opts?.signal });
}

export async function getSessionSnapshot(sessionId: number, signal?: AbortSignal): Promise<{
  success: boolean; session_id: number; snapshot_id: number; total: number;
}> {
  return request(`/chat/sessions/${sessionId}/snapshot`, { signal });
}

export async function renameSession(sessionId: number, title: string): Promise<{ success: boolean }> {
  return request(`/chat/sessions/${sessionId}`, {
    method: 'PATCH',
    body: JSON.stringify({ title }),
  });
}

export async function deleteSession(sessionId: number): Promise<{ success: boolean }> {
  return request(`/chat/sessions/${sessionId}`, { method: 'DELETE' });
}

// ---- Settings ----

export interface ChatProvider {
  id: string;
  display_name: string;
  protocol: 'openai' | 'anthropic';
  base_url: string;
  model_id: string;
  api_key_masked: string;
  is_active: boolean;
}

export async function getSettingsStatus(): Promise<{ success: boolean; chat_ready: boolean; ingest_ready: boolean }> {
  return request('/settings/status');
}

export async function getDashscopeKey(): Promise<{ success: boolean; configured: boolean; api_key_masked: string }> {
  return request('/settings/dashscope-key');
}

export async function setDashscopeKey(apiKey: string): Promise<{ success: boolean }> {
  return request('/settings/dashscope-key', { method: 'PUT', body: JSON.stringify({ api_key: apiKey }) });
}

export async function listChatProviders(): Promise<{ success: boolean; providers: ChatProvider[]; active_id: string | null }> {
  return request('/settings/chat-providers');
}

export async function detectChatProvider(): Promise<{
  success: boolean;
  status: 'valid' | 'invalid' | 'unreachable' | 'not_configured';
  provider: ChatProvider | null;
}> {
  return request('/settings/chat-providers/detect', { method: 'POST' });
}

export async function upsertChatProvider(provider: {
  id?: string;
  display_name: string;
  protocol: 'openai' | 'anthropic';
  base_url: string;
  /** leave empty when editing to keep the previously saved key unchanged */
  api_key?: string;
  model_id: string;
}): Promise<{ success: boolean; provider: ChatProvider }> {
  return request('/settings/chat-providers', { method: 'POST', body: JSON.stringify(provider) });
}

export async function deleteChatProvider(providerId: string): Promise<{ success: boolean }> {
  return request(`/settings/chat-providers/${providerId}`, { method: 'DELETE' });
}

export async function activateChatProvider(providerId: string): Promise<{ success: boolean; provider: ChatProvider }> {
  return request('/settings/chat-providers/activate', {
    method: 'POST',
    body: JSON.stringify({ provider_id: providerId }),
  });
}

// ---- Types ----

export interface CollectionItem {
  id: number;
  collection_id: string;
  title: string;
  video_count: number;
  is_active: boolean;
  platform?: string;
}

export interface VideoItem {
  id: number;
  collection_id: string;
  platform_item_id: string;
  url: string;
  title: string;
  author: string;
  duration: number;
  item_type?: 'video' | 'note';
  status: string;
  error_message?: string;
  platform?: string;
}

export interface SourceItem {
  platform_item_id: string;
  title: string;
  url: string;
  score?: number;
  platform?: string;
}

export interface SessionItem {
  id: number;
  title: string;
  message_count: number;
  last_message_at: string | null;
  created_at: string;
}

export interface MessageItem {
  id: number;
  client_key?: string | null;
  session_id: number;
  role: 'user' | 'assistant';
  content: string;
  route_type: string;
  latency_ms?: number;
  created_at: string;
  sources?: SourceItem[];
}
