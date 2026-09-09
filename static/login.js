(() => {
  const form = document.querySelector('#login-form');
  const error = document.querySelector('#login-error');
  form?.addEventListener('submit', async (event) => {
    event.preventDefault();
    error.hidden = true;
    const response = await fetch('/api/auth/login', { method: 'POST', body: new FormData(form) });
    if (!response.ok) {
      error.textContent = 'Usuário ou senha inválidos.';
      error.hidden = false;
      return;
    }
    window.location.assign('/');
  });
})();
