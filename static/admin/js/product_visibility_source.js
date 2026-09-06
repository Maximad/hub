document.addEventListener('DOMContentLoaded', () => {
  const body = document.body;
  if (!body.classList.contains('app-core') || !body.classList.contains('model-product')) return;

  const canonicalFields = [
    {
      id: 'id_visible_on_qr',
      label: 'Visible (Menu + POS)',
      help: 'Single source of truth for product visibility in both the public menu and Staff POS.',
    },
    {
      id: 'id_orderable_on_qr',
      label: 'Orderable (Menu + POS)',
      help: 'Single source of truth for whether this product can be ordered from both the public menu and Staff POS.',
    },
  ];

  canonicalFields.forEach(({ id, label, help }) => {
    const input = document.getElementById(id);
    if (!input) return;

    const labelNode = document.querySelector(`label[for="${id}"]`);
    if (labelNode) labelNode.textContent = label;

    const row = input.closest('.form-row');
    if (!row || row.querySelector('.hub-canonical-product-help')) return;
    const helpNode = document.createElement('span');
    helpNode.className = 'hub-canonical-product-help';
    helpNode.textContent = help;
    row.appendChild(helpNode);
  });
});
