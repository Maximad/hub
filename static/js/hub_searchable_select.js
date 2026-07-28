(() => {
  const init = (root = document) => root.querySelectorAll('select[data-hub-searchable-select="true"]:not([data-search-ready])').forEach((select) => {
    select.dataset.searchReady = 'true';
    const search = document.createElement('input');
    search.type = 'search';
    search.className = 'hub-select-search';
    search.placeholder = select.dataset.searchPlaceholder || 'اكتب للبحث…';
    search.setAttribute('aria-label', search.placeholder);
    search.dir = 'auto';
    const options = Array.from(select.options);
    search.addEventListener('input', () => {
      const query = search.value.trim().toLocaleLowerCase();
      options.forEach((option) => { option.hidden = Boolean(query) && !option.text.toLocaleLowerCase().includes(query) && !option.selected; });
    });
    select.before(search);
  });
  document.addEventListener('DOMContentLoaded', () => init());
  document.addEventListener('htmx:afterSwap', (event) => init(event.target));
  document.addEventListener('formset:added', (event) => init(event.target));
  window.HubSearchableSelect = { init };
})();
