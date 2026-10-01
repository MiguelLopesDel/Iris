(() => {
  const form = document.querySelector('#setup-form');
  const error = document.querySelector('#setup-error');
  const showError = (message) => {
    error.textContent = message;
    error.hidden = false;
  };

  fetch('/api/setup', { headers: { Accept: 'application/json' } })
    .then((response) => response.json())
    .then((status) => {
      if (!status.required) window.location.replace('/');
      document.querySelector('#setup-legacy').hidden = !status.legacy_library;
    })
    .catch(() => {});

  form?.addEventListener('submit', async (event) => {
    event.preventDefault();
    error.hidden = true;
    const data = Object.fromEntries(new FormData(form));
    if (data.password !== data.confirm) {
      showError('As senhas não conferem.');
      return;
    }
    const button = form.querySelector('button');
    button.disabled = true;
    try {
      const response = await fetch('/api/setup', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          code: data.code,
          username: data.username,
          display_name: data.display_name,
          password: data.password,
        }),
      });
      if (response.ok) {
        window.location.assign('/');
        return;
      }
      const body = await response.json().catch(() => ({}));
      showError(typeof body.detail === 'string' ? body.detail : 'Não foi possível concluir a configuração.');
    } catch {
      showError('O servidor não respondeu. Tente de novo.');
    } finally {
      button.disabled = false;
    }
  });
})();
