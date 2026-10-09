import { zhCN } from './messages';
export type Locale = 'en' | 'zh-CN';
export const localeKey = 'asteria.ui.locale';
let current: Locale = 'en';
const listeners = new Set<() => void>();
export function resolveLocale(saved: string | null, languages: readonly string[]): Locale {
  if (saved === 'en' || saved === 'zh-CN') return saved;
  return languages[0]?.toLowerCase().startsWith('zh') ? 'zh-CN' : 'en';
}
export const getLocale = () => current;
export const subscribeLocale = (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; };
export function setLocale(locale: Locale, persist = true) {
  current = locale;
  if (typeof window !== 'undefined') {
    if (persist) try { localStorage.setItem(localeKey, locale); } catch { /* Session-only preference when storage is unavailable. */ }
    document.documentElement.lang = locale;
  }
  listeners.forEach(listener => listener());
}
export function initializeLocale() {
  let saved = null;
  try { saved = localStorage.getItem(localeKey); } catch { /* Browser preference remains available. */ }
  setLocale(resolveLocale(saved, navigator.languages), false);
}
export function translate(locale: Locale, key: string, values: Record<string, string | number> = {}) {
  const text = locale === 'zh-CN' ? (zhCN[key] ?? key) : key;
  return text.replace(/\{(\w+)\}/g, (match, name) => String(values[name] ?? match));
}
export function uiNotice(message: string) {
  const corrected = /^Corrected file ready\. “([\s\S]*)” has been kept so its history stays clear\. You can remove it from its file menu when you are ready\.$/.exec(message);
  return corrected ? t('Corrected file ready. “{name}” has been kept so its history stays clear. You can remove it from its file menu when you are ready.', {name: corrected[1]}) : t(message);
}
// Only UI messages use t(). Never pass file names, chat, notes or model output.
export const t = (key: string, values?: Record<string, string | number>) => translate(current, key, values);
export function uiError(message: string) {
  if (current === 'en' || zhCN[message]) return t(message);
  if (/Failed to fetch|NetworkError|Network request failed/i.test(message)) return t('Connection failed. Please try again.');
  if (/timeout|timed out/i.test(message)) return t('The request timed out. Your saved content is safe. Please try again.');
  if (/conflict|revision|changed/i.test(message)) return t('The saved version changed. Your draft is kept. Refresh and try again.');
  if (/not found|unavailable/i.test(message)) return t('This item is unavailable. Refresh and try again.');
  if (/busy|already.*progress|still.*progress/i.test(message)) return t('Another operation is in progress. Please try again shortly.');
  return t('The operation failed. Your saved content is safe. Please try again.');
}
