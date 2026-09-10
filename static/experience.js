/* Per-device presentation preference. It affects only the web interface. */
(function () {
  const storageKey = 'iris-experience';
  const validExperiences = new Set(['photos', 'workspace']);

  function getExperience() {
    const saved = localStorage.getItem(storageKey);
    return validExperiences.has(saved) ? saved : 'photos';
  }

  function applyExperience(experience, persist) {
    const next = validExperiences.has(experience) ? experience : 'photos';
    document.documentElement.dataset.experience = next;
    if (persist) localStorage.setItem(storageKey, next);
    window.dispatchEvent(new CustomEvent('iris:experience-changed', { detail: next }));
    return next;
  }

  window.getIrisExperience = getExperience;
  window.setIrisExperience = function (experience) {
    return applyExperience(experience, true);
  };

  applyExperience(getExperience(), false);
}());
