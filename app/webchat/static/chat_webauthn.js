(function () {
  'use strict';

  var loginView = document.getElementById('login-view');
  var chatView = document.getElementById('chat-view');
  var loginBtn = document.getElementById('login-btn');
  var loginError = document.getElementById('login-error');
  var thread = document.getElementById('thread');
  var statusLine = document.getElementById('status-line');
  var composer = document.getElementById('composer');
  var msgInput = document.getElementById('msg-input');
  var connDot = document.getElementById('conn-dot');
  var headerTitle = document.getElementById('header-title');
  var signoutBtn = document.getElementById('signout-btn');

  var ws = null;
  var reconnectAttempts = 0;
  var reconnectTimer = null;
  var connected = false;

  function setConn(state) { connDot.className = 'conn-dot ' + state; }

  function appendBubble(role, text) {
    var el = document.createElement('div');
    el.className = 'bubble ' + role;
    el.textContent = text;
    thread.appendChild(el);
    thread.scrollTop = thread.scrollHeight;
    return el;
  }

  function appendConfirmCard(frame) {
    var card = document.createElement('div');
    card.className = 'confirm-card';
    card.dataset.token = frame.token;
    var text = document.createElement('div');
    text.textContent = frame.text;
    card.appendChild(text);
    var actions = document.createElement('div');
    actions.className = 'confirm-actions';
    var yes = document.createElement('button');
    yes.className = 'confirm-btn yes'; yes.type = 'button'; yes.textContent = 'Yes';
    var no = document.createElement('button');
    no.className = 'confirm-btn no'; no.type = 'button'; no.textContent = 'No';
    yes.onclick = function () { respondConfirm(frame.token, 'confirm', card); };
    no.onclick = function () { respondConfirm(frame.token, 'cancel', card); };
    actions.appendChild(yes); actions.appendChild(no);
    card.appendChild(actions);
    thread.appendChild(card);
    thread.scrollTop = thread.scrollHeight;
  }

  function respondConfirm(token, type, card) {
    Array.prototype.forEach.call(card.querySelectorAll('.confirm-btn'), function (b) { b.disabled = true; });
    send({ type: type, token: token });
  }

  function resolveConfirmCard(frame) {
    var card = thread.querySelector('.confirm-card[data-token="' + CSS.escape(frame.token) + '"]');
    if (!card) return;
    var actions = card.querySelector('.confirm-actions');
    if (actions) actions.remove();
    var result = document.createElement('div');
    result.style.marginTop = '6px';
    result.style.color = frame.ok ? 'var(--ok)' : 'var(--danger)';
    result.style.fontSize = '11.5px';
    result.textContent = frame.text;
    card.appendChild(result);
  }

  function setStatus(text) { statusLine.textContent = text || ''; }

  function handleFrame(frame) {
    if (frame.type === 'message') {
      setStatus('');
      appendBubble('agent', frame.text || '(no response)');
    } else if (frame.type === 'status') {
      setStatus(frame.text);
    } else if (frame.type === 'confirm_request') {
      setStatus('');
      appendConfirmCard(frame);
    } else if (frame.type === 'confirm_result') {
      resolveConfirmCard(frame);
    }
  }

  function send(obj) {
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
  }

  function connectWS() {
    var proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(proto + '//' + location.host + '/ws');
    ws.onopen = function () { reconnectAttempts = 0; setConn('online'); };
    ws.onmessage = function (ev) {
      try { handleFrame(JSON.parse(ev.data)); } catch (e) { /* ignore malformed frame */ }
    };
    ws.onclose = function (ev) {
      setConn('offline');
      if (ev.code === 4401 || ev.code === 4403) { showLogin(); return; }
      if (connected) scheduleReconnect();
    };
    ws.onerror = function () { try { ws.close(); } catch (e) { /* already closing */ } };
  }

  function scheduleReconnect() {
    if (reconnectTimer) return;
    reconnectAttempts++;
    var delay = Math.min(1000 * Math.pow(2, reconnectAttempts), 15000);
    reconnectTimer = setTimeout(function () { reconnectTimer = null; connectWS(); }, delay);
  }

  function enterChat(name) {
    connected = true;
    loginView.style.display = 'none';
    chatView.style.display = 'flex';
    headerTitle.textContent = 'HomeAgent — ' + name;
    setConn('offline');
    connectWS();
    msgInput.focus();
  }

  function showLogin() {
    connected = false;
    chatView.style.display = 'none';
    loginView.style.display = 'flex';
    if (ws) { try { ws.close(); } catch (e) { /* noop */ } ws = null; }
    loginBtn.disabled = false;
  }

  function doLogin() {
    loginBtn.disabled = true;
    loginError.textContent = '';
    fetch('/api/webauthn/login/options', { method: 'POST' })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var publicKey = HomeAgentWebAuthn.decodeLoginOptions(data.options);
        return navigator.credentials.get({ publicKey: publicKey }).then(function (cred) {
          var body = { challenge_id: data.challenge_id, credential: HomeAgentWebAuthn.encodeAssertion(cred) };
          return fetch('/api/webauthn/login/verify', {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
          });
        });
      })
      .then(function (r) { if (!r.ok) throw new Error('login failed'); return r.json(); })
      .then(function (data) {
        thread.innerHTML = '';
        enterChat(data.name);
      })
      .catch(function () {
        loginError.textContent = 'Sign-in failed or was cancelled. Try again.';
        loginBtn.disabled = false;
      });
  }

  loginBtn.addEventListener('click', doLogin);

  composer.addEventListener('submit', function (ev) {
    ev.preventDefault();
    var text = msgInput.value.trim();
    if (!text) return;
    appendBubble('user', text);
    setStatus('thinking…');
    send({ type: 'message', text: text });
    msgInput.value = '';
  });

  signoutBtn.addEventListener('click', function () {
    fetch('/api/session', {
      method: 'DELETE',
      headers: { 'X-CSRF-Token': HomeAgentWebAuthn.readCookie('hac_csrf') },
    }).catch(function () { /* best-effort */ }).finally(showLogin);
  });

  // ---------------- Boot ----------------
  fetch('/api/me').then(function (r) {
    if (!r.ok) throw new Error('not signed in');
    return r.json();
  }).then(function (me) {
    enterChat(me.name);
  }).catch(showLogin);
})();
