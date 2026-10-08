import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { initI18n } from './i18n'

// Await i18n before first render so no component ever sees raw translation
// keys during mount. initI18n loads exactly one language pack (current
// locale) instead of both, which is the whole point of the split.
// After a release, old tabs can't fetch route chunks with the new hash (lazy import 404) -- the page goes blank,
// and users see "there's clearly a result, but opening it is blank". Vite throws vite:preloadError on this failure;
// we catch it and do one full page reload, which fetches the new index.html. sessionStorage records the last reload time,
// and it won't reload again within 30s -- so when something is truly broken (e.g. a file missing on the CDN) it won't reload forever; the next release can auto-recover.
window.addEventListener('vite:preloadError', (event) => {
  const key = 'vite-preload-reloaded-at';
  const now = Date.now();
  let last = 0;
  try { last = Number(sessionStorage.getItem(key) || 0); } catch { /* private mode etc. */ }
  if (now - last < 30_000) return;   // just reloaded and still failing; let the error bubble to the console normally
  event.preventDefault();
  try { sessionStorage.setItem(key, String(now)); } catch { /* ignore */ }
  window.location.reload();
});

initI18n().then(() => {
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <App />
    </StrictMode>,
  )
})
