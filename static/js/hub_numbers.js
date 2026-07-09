(function () {
  'use strict';

  const digitMap = {
    '٠': '0', '١': '1', '٢': '2', '٣': '3', '٤': '4', '٥': '5', '٦': '6', '٧': '7', '٨': '8', '٩': '9',
    '۰': '0', '۱': '1', '۲': '2', '۳': '3', '۴': '4', '۵': '5', '۶': '6', '۷': '7', '۸': '8', '۹': '9',
    '−': '-', '–': '-', '—': '-'
  };
  const digitPattern = /[٠-٩۰-۹−–—]/g;
  const digitSearchPattern = /[٠-٩۰-۹−–—]/;
  const numericTextPattern = /[٠-٩۰-۹0-9]|ل\.س|SYP/i;
  const numericContextSelector = [
    '.hub-money',
    '.hub-number',
    '.hub-price',
    '.hub-order-number',
    '.latin-numbers',
    '.hub-badge',
    '.hub-stat-card',
    '.hub-table td',
    '.hub-table th',
    '.print-table td',
    '.print-table th',
    '.print-totals dd',
    '.menu-cart-total',
    '.menu-product-price',
    '.menu-list-price',
    '.order-confirm__line-total',
    '[data-cart-total]',
    '[data-delivery-fee]',
    '[data-total-with-delivery]',
    '[data-sticky-total]'
  ].join(',');
  const inputSelector = 'input[type="number"], input[inputmode="numeric"], input[type="tel"]';

  function latinDigits(value) {
    return String(value || '').replace(digitPattern, function (char) {
      return digitMap[char] || char;
    });
  }

  function normalizeTextNode(node) {
    if (!node || !node.nodeValue || !digitSearchPattern.test(node.nodeValue)) return;
    node.nodeValue = latinDigits(node.nodeValue);
  }

  function normalizeElementText(element) {
    if (!element || !numericTextPattern.test(element.textContent || '')) return;
    const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT, {
      acceptNode: function (node) {
        return digitSearchPattern.test(node.nodeValue || '') ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
      }
    });
    const nodes = [];
    let node;
    while ((node = walker.nextNode())) nodes.push(node);
    nodes.forEach(normalizeTextNode);
  }

  function normalizeInput(input) {
    if (!input) return;
    const value = latinDigits(input.value);
    if (input.value !== value) input.value = value;
  }

  function normalizeRoot(root) {
    if (!root || !root.querySelectorAll) return;
    if (root.matches && root.matches(numericContextSelector)) normalizeElementText(root);
    root.querySelectorAll(numericContextSelector).forEach(normalizeElementText);
    if (root.matches && root.matches(inputSelector)) normalizeInput(root);
    root.querySelectorAll(inputSelector).forEach(normalizeInput);
  }

  function bindInputNormalization(root) {
    if (!root || !root.addEventListener) return;
    root.addEventListener('input', function (event) {
      if (event.target && event.target.matches && event.target.matches(inputSelector)) normalizeInput(event.target);
    }, true);
  }

  function start() {
    normalizeRoot(document);
    bindInputNormalization(document);
    if (!window.MutationObserver) return;
    const observer = new MutationObserver(function (mutations) {
      mutations.forEach(function (mutation) {
        mutation.addedNodes.forEach(function (node) {
          if (node.nodeType === Node.TEXT_NODE) {
            const parent = node.parentElement;
            if (parent && parent.closest(numericContextSelector)) normalizeTextNode(node);
          } else if (node.nodeType === Node.ELEMENT_NODE) {
            normalizeRoot(node);
          }
        });
        if (mutation.type === 'characterData') {
          const parent = mutation.target.parentElement;
          if (parent && parent.closest(numericContextSelector)) normalizeTextNode(mutation.target);
        }
      });
    });
    observer.observe(document.body, { childList: true, subtree: true, characterData: true });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }

  window.HubNumbers = { latinDigits: latinDigits, normalizeRoot: normalizeRoot, normalizeInput: normalizeInput };
}());
