(() => {
  const path = window.location.pathname || '';
  const body = document.body;
  if (!body) return;

  if (path === '/staff/internet/' || path.startsWith('/staff/internet/session/')) {
    body.classList.add('staff-internet-v2');
  }

  if (path === '/staff/internet/') {
    const headings = Array.from(document.querySelectorAll('h2, h3'));
    const title = headings.find((node) => node.textContent.includes('الإنترنت والعمل'));
    if (title) title.textContent = 'الإنترنت';

    const staleNotice = Array.from(document.querySelectorAll('.hub-badge-info')).find((node) =>
      node.textContent.includes('فوترة يدوية فقط')
    );
    if (staleNotice) {
      staleNotice.textContent = 'إدارة الباقات والجلسات وحالة التجهيز الشبكي من مكان واحد.';
      staleNotice.classList.add('internet-v2__intro');
    }

    const sectionHeadings = Array.from(document.querySelectorAll('h3.hub-section-title'));
    const labels = [
      ['أ. بيع صلاحية إنترنت', 'إضافة إنترنت'],
      ['ب. ملخص الإنترنت', 'الحالة الآن'],
      ['ج. صلاحيات الوصول التجارية الفعالة والحديثة', 'الصلاحيات الفعالة'],
      ['د. جلسات الاستخدام الفعالة / الحالية', 'الجلسات الفعالة'],
      ['هـ. الجلسات الحديثة / المنتهية', 'السجل الحديث'],
    ];
    for (const heading of sectionHeadings) {
      for (const [from, to] of labels) {
        if (heading.textContent.trim() === from) heading.textContent = to;
      }
    }
  }
})();
