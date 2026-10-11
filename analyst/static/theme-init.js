/* Runs before first paint (external file so the Content-Security-Policy can forbid inline scripts). */
try { var t = localStorage.getItem('ui.theme'); t = t && JSON.parse(t); if (t === 'light' || t === 'dark') document.documentElement.dataset.theme = t; } catch (e) { }
