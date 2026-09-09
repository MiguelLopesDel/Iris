(function () {
  const storageKey = 'iris-theme';
  const themeColor = document.querySelector('meta[name="theme-color"]');

  function preferredTheme() {
    const saved = localStorage.getItem(storageKey);
    if (saved === 'light' || saved === 'dark') return saved;
    return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
  }

  function applyTheme(theme, persist) {
    document.documentElement.dataset.theme = theme;
    document.documentElement.style.colorScheme = theme;
    if (themeColor) themeColor.content = theme === 'light' ? '#f5f3ed' : '#0b0d12';
    if (persist) localStorage.setItem(storageKey, theme);
    document.querySelectorAll('[data-theme-toggle]').forEach(function (button) {
      const light = theme === 'light';
      button.innerHTML = '<span aria-hidden="true">' + (light ? '☾' : '☀') + '</span>'
        + '<span class="theme-label">' + (light ? 'Tema escuro' : 'Tema claro') + '</span>';
      button.setAttribute('aria-label', light ? 'Ativar tema escuro' : 'Ativar tema claro');
      button.title = light ? 'Ativar tema escuro' : 'Ativar tema claro';
    });
  }

  applyTheme(preferredTheme(), false);
  document.addEventListener('DOMContentLoaded', function () {
    applyTheme(document.documentElement.dataset.theme || preferredTheme(), false);
    document.addEventListener('click', function (event) {
      const button = event.target.closest('[data-theme-toggle]');
      if (!button) return;
      applyTheme(document.documentElement.dataset.theme === 'light' ? 'dark' : 'light', true);
    });
  });
})();
