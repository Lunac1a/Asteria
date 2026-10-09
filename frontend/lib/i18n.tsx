"use client";
import { useEffect, useSyncExternalStore } from 'react';
import { getLocale, initializeLocale, localeKey, subscribeLocale } from './locale';
export { t, uiError, uiNotice, getLocale, setLocale } from './locale';
export function useI18n() { return useSyncExternalStore(subscribeLocale, getLocale, () => 'en' as const); }
export function LocaleProvider({ children }: { children: React.ReactNode }) {
  useEffect(() => {
    initializeLocale();
    const changed = (event: StorageEvent) => { if (event.key === localeKey || event.key === null) initializeLocale(); };
    window.addEventListener('storage', changed);
    return () => window.removeEventListener('storage', changed);
  }, []);
  return children;
}
