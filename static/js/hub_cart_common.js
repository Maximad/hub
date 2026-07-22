(function (window) {
  const MAX_QTY = 999;
  const arabicIndic = '٠١٢٣٤٥٦٧٨٩';
  const easternArabicIndic = '۰۱۲۳۴۵۶۷۸۹';

  function normalizeDigits(value) {
    return String(value ?? '').replace(/[٠-٩۰-۹]/g, (char) => {
      const arabicIndex = arabicIndic.indexOf(char);
      if (arabicIndex >= 0) return String(arabicIndex);
      const easternIndex = easternArabicIndic.indexOf(char);
      return easternIndex >= 0 ? String(easternIndex) : char;
    });
  }

  function parseQuantity(value, options) {
    const opts = Object.assign({ blankAsZero: true, max: MAX_QTY }, options || {});
    const raw = normalizeDigits(value).trim();
    if (!raw) return opts.blankAsZero ? 0 : null;
    if (!/^\d+$/.test(raw)) return 0;
    return clampQuantity(Number(raw), opts.max);
  }

  function clampQuantity(value, max) {
    const upper = Number.isFinite(Number(max)) ? Math.max(0, Number(max)) : MAX_QTY;
    const numeric = Number.isFinite(Number(value)) ? Math.floor(Number(value)) : 0;
    return Math.min(Math.max(numeric, 0), upper);
  }

  function stepQuantity(current, delta, max) {
    return clampQuantity(parseQuantity(current, { max }) + Number(delta || 0), max);
  }

  function formatMoney(value) {
    return `${(Number(value) || 0).toLocaleString('en-US')} ل.س`;
  }

  function dispatchCartUpdated(target, detail) {
    (target || document).dispatchEvent(new CustomEvent('hub:cart-updated', { bubbles: true, detail: detail || {} }));
  }

  function setLoading(button, loading, label) {
    if (!button) return;
    if (loading) {
      button.dataset.originalText = button.textContent;
      button.disabled = true;
      if (label) button.textContent = label;
    } else {
      button.disabled = false;
      if (button.dataset.originalText) button.textContent = button.dataset.originalText;
      delete button.dataset.originalText;
    }
  }

  window.HubCartCommon = { MAX_QTY, normalizeDigits, parseQuantity, clampQuantity, stepQuantity, formatMoney, dispatchCartUpdated, setLoading };
})(window);
