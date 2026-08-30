/*
 * Shared WebAuthn base64url <-> ArrayBuffer helpers for chat_webauthn.html
 * and invite.html. Kept in its own file (rather than inline) so it can be
 * an allowed same-origin script under a strict CSP with no 'unsafe-inline'
 * (see docs/household-identity-and-access-design.md Option H).
 *
 * Credential encode/decode is done manually rather than relying on
 * PublicKeyCredential.prototype.toJSON()/parseCreationOptionsFromJSON(),
 * which not every browser in household use implements yet.
 */
(function (global) {
  'use strict';

  function b64urlToBuf(b64url) {
    var b64 = b64url.replace(/-/g, '+').replace(/_/g, '/');
    while (b64.length % 4) b64 += '=';
    var str = atob(b64);
    var bytes = new Uint8Array(str.length);
    for (var i = 0; i < str.length; i++) bytes[i] = str.charCodeAt(i);
    return bytes.buffer;
  }

  function bufToB64url(buf) {
    var bytes = new Uint8Array(buf);
    var str = '';
    for (var i = 0; i < bytes.byteLength; i++) str += String.fromCharCode(bytes[i]);
    return btoa(str).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  }

  function decodeCredentialDescriptors(list) {
    return (list || []).map(function (c) {
      return { id: b64urlToBuf(c.id), type: c.type, transports: c.transports };
    });
  }

  function decodeRegistrationOptions(options) {
    return Object.assign({}, options, {
      challenge: b64urlToBuf(options.challenge),
      user: Object.assign({}, options.user, { id: b64urlToBuf(options.user.id) }),
      excludeCredentials: decodeCredentialDescriptors(options.excludeCredentials),
    });
  }

  function decodeLoginOptions(options) {
    return Object.assign({}, options, {
      challenge: b64urlToBuf(options.challenge),
      allowCredentials: decodeCredentialDescriptors(options.allowCredentials),
    });
  }

  function encodeAttestation(credential) {
    return {
      id: credential.id,
      rawId: bufToB64url(credential.rawId),
      type: credential.type,
      response: {
        clientDataJSON: bufToB64url(credential.response.clientDataJSON),
        attestationObject: bufToB64url(credential.response.attestationObject),
      },
      clientExtensionResults: credential.getClientExtensionResults ? credential.getClientExtensionResults() : {},
    };
  }

  function encodeAssertion(credential) {
    var userHandle = credential.response.userHandle;
    return {
      id: credential.id,
      rawId: bufToB64url(credential.rawId),
      type: credential.type,
      response: {
        clientDataJSON: bufToB64url(credential.response.clientDataJSON),
        authenticatorData: bufToB64url(credential.response.authenticatorData),
        signature: bufToB64url(credential.response.signature),
        userHandle: userHandle ? bufToB64url(userHandle) : null,
      },
      clientExtensionResults: credential.getClientExtensionResults ? credential.getClientExtensionResults() : {},
    };
  }

  function readCookie(name) {
    var match = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'));
    return match ? decodeURIComponent(match[1]) : '';
  }

  global.HomeAgentWebAuthn = {
    decodeRegistrationOptions: decodeRegistrationOptions,
    decodeLoginOptions: decodeLoginOptions,
    encodeAttestation: encodeAttestation,
    encodeAssertion: encodeAssertion,
    readCookie: readCookie,
  };
})(window);
