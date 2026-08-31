(function () {
  'use strict';

  var loginView = document.getElementById('login-view');
  var chatView = document.getElementById('chat-view');
  var userList = document.getElementById('user-list');
  var thread = document.getElementById('thread');
  var statusLine = document.getElementById('status-line');
  var composer = document.getElementById('composer');
  var msgInput = document.getElementById('msg-input');
  var sendBtn = document.getElementById('send-btn');
  var connDot = document.getElementById('conn-dot');
  var headerTitle = document.getElementById('header-title');
  var switchUserBtn = document.getElementById('switch-user-btn');

  var ws = null;
  var reconnectAttempts = 0;
  var reconnectTimer = null;
  var currentToken = null;

  function initials(name) {
    return (name || '?').trim().split(/\s+/).slice(0, 2).map(function (w) { return w[0]; }).join('').toUpperCase();
  }

  function setConn(state) {
    connDot.className = 'conn-dot ' + state;
  }

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
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(obj));
    }
  }

  function connectWS(token) {
    var proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(proto + '//' + location.host + '/ws?token=' + encodeURIComponent(token));
    ws.onopen = function () { reconnectAttempts = 0; setConn('online'); };
    ws.onmessage = function (ev) {
      try { handleFrame(JSON.parse(ev.data)); } catch (e) { /* ignore malformed frame */ }
    };
    ws.onclose = function (ev) {
      setConn('offline');
      if (ev.code === 4401) { logout(); return; }
      scheduleReconnect(token);
    };
    ws.onerror = function () { try { ws.close(); } catch (e) { /* already closing */ } };
  }

  function scheduleReconnect(token) {
    if (reconnectTimer) return;
    reconnectAttempts++;
    var delay = Math.min(1000 * Math.pow(2, reconnectAttempts), 15000);
    reconnectTimer = setTimeout(function () {
      reconnectTimer = null;
      if (currentToken === token) connectWS(token);
    }, delay);
  }

  function enterChat(token, name) {
    currentToken = token;
    loginView.style.display = 'none';
    chatView.style.display = 'flex';
    headerTitle.textContent = 'HomeAgent — ' + name;
    setConn('offline');
    connectWS(token);
    msgInput.focus();
  }

  function showLogin() {
    chatView.style.display = 'none';
    loginView.style.display = 'flex';
    if (ws) { try { ws.close(); } catch (e) { /* noop */ } ws = null; }
    currentToken = null;
    loadUsers();
  }

  function logout() {
    localStorage.removeItem('webchat_token');
    showLogin();
  }

  function loadUsers() {
    userList.innerHTML = '';
    var remembered = localStorage.getItem('webchat_last_user_id');
    fetch('/api/users').then(function (r) { return r.json(); }).then(function (data) {
      (data.users || []).forEach(function (u) {
        var tile = document.createElement('button');
        tile.type = 'button';
        tile.className = 'user-tile' + (u.id === remembered ? ' remembered' : '');
        var avatar = document.createElement('div');
        avatar.className = 'user-avatar';
        avatar.textContent = initials(u.name);
        var label = document.createElement('div');
        label.textContent = u.name;
        tile.appendChild(avatar);
        tile.appendChild(label);
        tile.onclick = function () { pickUser(u.id); };
        userList.appendChild(tile);
      });
    });
  }

  function pickUser(userId) {
    fetch('/api/session', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_id: userId }),
    }).then(function (r) {
      if (!r.ok) throw new Error('session create failed');
      return r.json();
    }).then(function (data) {
      localStorage.setItem('webchat_token', data.token);
      localStorage.setItem('webchat_last_user_id', data.user_id);
      thread.innerHTML = '';
      enterChat(data.token, data.name);
    }).catch(function () {
      appendBubble('system', 'Could not start a session — please try again.');
    });
  }

  composer.addEventListener('submit', function (ev) {
    ev.preventDefault();
    var text = msgInput.value.trim();
    if (!text) return;
    appendBubble('user', text);
    setStatus('thinking…');
    send({ type: 'message', text: text });
    msgInput.value = '';
  });

  switchUserBtn.addEventListener('click', function () {
    var token = currentToken;
    if (token) {
      fetch('/api/session', { method: 'DELETE', headers: { Authorization: 'Bearer ' + token } })
        .catch(function () { /* best-effort */ })
        .finally(function () { logout(); });
    } else {
      logout();
    }
  });

  // ---------------- Boot ----------------
  var savedToken = localStorage.getItem('webchat_token');
  if (savedToken) {
    fetch('/api/me', { headers: { Authorization: 'Bearer ' + savedToken } })
      .then(function (r) { if (!r.ok) throw new Error('expired'); return r.json(); })
      .then(function (me) { enterChat(savedToken, me.name); })
      .catch(function () { logout(); });
  } else {
    showLogin();
  }
})();
