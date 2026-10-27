(function () {
  const GUMROAD_URL = 'https://xeyonai.gumroad.com/l/bsmupk';

  function buildProModal() {
    const modal = document.createElement('div');
    modal.id = 'hwui-pro-modal';
    modal.className = 'modal';
    modal.innerHTML =
      '<div class="modal-content" style="max-width:420px;">' +
        '<div class="modal-header">' +
          '<h3 id="hwui-pro-modal-title">Upgrade to HWUI Pro</h3>' +
          '<span class="close" onclick="closeHwuiProModal()">&times;</span>' +
        '</div>' +
        '<div class="modal-body" style="padding:22px; text-align:center;">' +
          '<div style="font-size:46px; margin-bottom:12px;">⭐</div>' +
          '<p id="hwui-pro-modal-message" style="color:#ccc; font-size:14px; line-height:1.6; margin:0 0 20px;"></p>' +
          '<p style="color:#888; font-size:12px; margin:0 0 18px;">One-time payment. No subscription. Yours forever.</p>' +
          '<button onclick="window.open(\'' + GUMROAD_URL + '\', \'_blank\')" ' +
            'style="background:linear-gradient(135deg,#667eea,#764ba2); border:none; color:#fff; ' +
            'font-weight:600; font-size:14px; padding:10px 26px; border-radius:8px; cursor:pointer;">' +
            'Get HWUI Pro' +
          '</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(modal);
    modal.addEventListener('click', function (event) {
      if (event.target === modal) closeHwuiProModal();
    });
    return modal;
  }

  window.showHwuiProModal = function (title, message) {
    const modal = document.getElementById('hwui-pro-modal') || buildProModal();
    document.getElementById('hwui-pro-modal-title').textContent = title || 'Upgrade to HWUI Pro';
    document.getElementById('hwui-pro-modal-message').textContent =
      message || 'This feature is available in HWUI Pro.';
    modal.style.display = 'block';
  };

  window.closeHwuiProModal = function () {
    const modal = document.getElementById('hwui-pro-modal');
    if (modal) modal.style.display = 'none';
  };
})();
