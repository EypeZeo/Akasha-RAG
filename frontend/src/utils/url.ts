/** Only http(s) links (e.g. from scraped source data) are safe to render as anchors. */
export function safeHref(url: string | undefined | null): string {
  if (!url) return '#';
  try {
    const u = new URL(url, typeof window !== 'undefined' ? window.location.origin : 'http://localhost');
    return u.protocol === 'http:' || u.protocol === 'https:' ? url : '#';
  } catch {
    return '#';
  }
}
