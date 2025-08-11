// static/js/theme.js
(function () {
  const root = document.documentElement;
  const btn = document.getElementById('themeToggle');
  const saved = localStorage.getItem('rg-theme') || 'dark';
  root.setAttribute('data-theme', saved);

  if (btn) {
    btn.textContent = saved === 'dark' ? '🌙' : '☀️';
    btn.addEventListener('click', () => {
      const curr = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
      root.setAttribute('data-theme', curr);
      localStorage.setItem('rg-theme', curr);
      btn.textContent = curr === 'dark' ? '🌙' : '☀️';
    });
  }
})();
