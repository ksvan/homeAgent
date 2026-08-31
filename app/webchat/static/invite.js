(function () {
  'use strict';

  var claimText = document.getElementById('claim-text');
  var setupBtn = document.getElementById('setup-btn');
  var status = document.getElementById('status');

  var pathParts = location.pathname.split('/');
  var token = decodeURIComponent(pathParts[pathParts.length - 1] || '');

  function setStatus(text, cls) {
    status.textContent = text || '';
    status.className = cls || '';
  }

  fetch('/api/invite/' + encodeURIComponent(token)).then(function (r) {
    if (!r.ok) throw new Error('invalid invite');
    return r.json();
  }).then(function (data) {
    claimText.textContent = 'Set up a passkey for ' + data.name + ' on this device.';
    setupBtn.style.display = '';
  }).catch(function () {
    claimText.textContent = 'This invite link is invalid or has expired. Ask a household admin for a new one.';
  });

  setupBtn.addEventListener('click', function () {
    setupBtn.disabled = true;
    setStatus('');
    fetch('/api/webauthn/register/options', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ invite_token: token }),
    })
      .then(function (r) { if (!r.ok) throw new Error('options failed'); return r.json(); })
      .then(function (data) {
        var publicKey = HomeAgentWebAuthn.decodeRegistrationOptions(data.options);
        return navigator.credentials.create({ publicKey: publicKey }).then(function (cred) {
          var body = {
            invite_token: token,
            challenge_id: data.challenge_id,
            credential: HomeAgentWebAuthn.encodeAttestation(cred),
          };
          return fetch('/api/webauthn/register/verify', {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
          });
        });
      })
      .then(function (r) { if (!r.ok) throw new Error('verify failed'); return r.json(); })
      .then(function () {
        setStatus('Passkey created. Redirecting…', 'ok');
        setTimeout(function () { location.href = '/'; }, 1200);
      })
      .catch(function () {
        setStatus('Could not set up the passkey. The invite may already be used — ask for a new link.', 'error');
        setupBtn.disabled = false;
      });
  });
})();
