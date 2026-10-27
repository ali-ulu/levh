/**
 * The single storage key for the theme (#567).
 *
 * Lives in a plain module, not in `theme-provider.tsx`, because that file is
 * `"use client"`. A server component that imports a value from a client module
 * gets a client-reference stub, and interpolating that stub into the pre-paint
 * script in `layout.tsx` emits a throwing function body instead of the string —
 * which is invalid JS and crashes the page on load. Both the provider and the
 * script import the constant from here so the two cannot drift.
 */
export const THEME_STORAGE_KEY = "levh-theme";

/**
 * The blocking script `layout.tsx` inlines before paint (#567).
 *
 * Built here rather than inline in the layout so a test can evaluate the exact
 * string the browser will run: the failure mode this guards against is a
 * constant that interpolates to a client-reference stub, which is syntactically
 * invalid and crashes every page (the e2e suite caught it once; this catches it
 * in a unit test first).
 */
export function themeBootstrapScript(): string {
  return `(function(){try{var m=document.cookie.match(/(?:^|; )${THEME_STORAGE_KEY}=([^;]+)/);var t=m?decodeURIComponent(m[1]):null;if(!t){t=localStorage.getItem('${THEME_STORAGE_KEY}')}if(t==='dark'||(!t&&window.matchMedia('(prefers-color-scheme: dark)').matches)){document.documentElement.classList.add('dark')}else{document.documentElement.classList.remove('dark')}}catch(e){}})();`;
}
