/* Service worker register. Loaded after the page renders so it never blocks
 * the first paint. Registers only on secure origins (https) or localhost —
 * PWA install needs a secure context. */
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker
      .register("/sw.js")
      .then((reg) => {
        // Pick up a newer sw.js as soon as it exists; never stalls startup.
        try {
          reg.update().catch(() => {});
        } catch (_) {
          /* best effort */
        }
        console.log("[levh:pwa] service worker registered");
      })
      .catch(() => {
        console.log("[levh:pwa] service worker registration skipped");
      });
  });
}