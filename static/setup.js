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
      const summary = status.legacy_summary;
      if (summary) {
        const parts = [` ${summary.with_file} de ${summary.items} itens serão movidos.`];
        if (summary.missing) parts.push(` ${summary.missing} já não tinham arquivo.`);
        if (summary.outside) {
          parts.push(` ${summary.outside} estão fora das pastas data/ e media/ e não serão movidos.`);
        }
        document.querySelector('#setup-legacy-summary').textContent = parts.join('');
      }
    })
    .catch(() => {});

  // Advice only: 8 characters are accepted, 12 or more are recommended.
  const password = form?.querySelector('input[name="password"]');
  const hint = document.querySelector('#setup-password-hint');
  const advice = hint?.textContent;
  password?.addEventListener('input', () => {
    const length = password.value.length;
    if (length === 0) hint.textContent = advice;
    else if (length < 8) hint.textContent = `Faltam ${8 - length} caractere${8 - length === 1 ? '' : 's'} para o mínimo de 8.`;
    else if (length < 12) hint.textContent = 'Aceita. Com 12 ou mais caracteres ela fica bem mais difícil de adivinhar.';
    else hint.textContent = 'Boa senha.';
  });

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
