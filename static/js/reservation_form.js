(() => {
  const init = () => document.querySelectorAll('form[data-reservation-form]:not([data-reservation-ready])').forEach((form) => {
    form.dataset.reservationReady = 'true';
    const type = form.querySelector('[name="reservation_type"]');
    const event = form.querySelector('[name="event"]');
    const performance = form.querySelector('[name="performance"]');
    const room = form.querySelector('[name="room"]');
    const table = form.querySelector('[name="table_area"]');
    const schedule = form.querySelector('#event-schedule');
    const toggle = () => {
      const eventMode = type.value === 'event';
      form.querySelectorAll('[data-reservation-group]').forEach((group) => {
        const visible = group.dataset.reservationGroup === (eventMode ? 'event' : 'regular');
        group.hidden = !visible; group.setAttribute('aria-hidden', String(!visible));
        group.querySelectorAll('input,select,textarea').forEach((input) => {
          input.disabled = !visible;
          if (input.dataset.requiredWhenVisible !== undefined) { input.required = visible; input.setAttribute('aria-required', String(visible)); }
        });
      });
      updateSchedule();
    };
    const updateSchedule = () => {
      const option = performance && performance.value ? performance.selectedOptions[0] : (event && event.selectedOptions[0]);
      schedule.textContent = option && option.value ? `التاريخ: ${option.dataset.date} | الوقت: ${option.dataset.time} | المساحة: ${option.dataset.room}` : '';
    };
    const updatePerformances = () => {
      if (!performance) return;
      const selected = performance.value;
      let available = 0;
      for (const option of [...performance.options]) {
        if (!option.value) continue;
        const visible = option.dataset.eventId === event.value;
        option.hidden = !visible;
        option.disabled = !visible;
        if (visible) available++;
      }
      if (!performance.selectedOptions[0] || performance.selectedOptions[0].disabled) performance.value = '';
      if (!performance.value && available === 1) {
        const only = [...performance.options].find(o => o.value && !o.disabled);
        if (only) performance.value = only.value;
      }
      updateSchedule();
    };
    const loadTables = async () => {
      const selected = table.value;
      table.innerHTML = '<option value="">بدون طاولة</option>';
      if (!room.value) return;
      const response = await fetch(`${form.dataset.tablesUrl || '/staff/reservations/tables/'}?room=${encodeURIComponent(room.value)}`, {headers: {'X-Requested-With': 'XMLHttpRequest'}});
      if (!response.ok) return;
      const payload = await response.json();
      payload.results.forEach((row) => table.add(new Option(row.text, row.id, false, String(row.id) === selected)));
    };
    type.addEventListener('change', toggle); event.addEventListener('change', updatePerformances);
    if (performance) performance.addEventListener('change', updateSchedule);
    room.addEventListener('change', loadTables);
    updatePerformances();
    toggle();
  });
  document.addEventListener('DOMContentLoaded', init);
  document.addEventListener('htmx:afterSwap', init);
})();
