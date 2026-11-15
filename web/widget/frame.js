/* The website-chat UI. Talks only to /api/widget/{key}/*. The visitor token is
 * kept in this browser and sent as a header; the host page never sees it. */
(function () {
  "use strict";
  var params = new URLSearchParams(location.search);
  var key = params.get("k") || "";
  var color = params.get("color");
  if (color && /^#[0-9a-fA-F]{3,8}$/.test(color)) {
    document.documentElement.style.setProperty("--brand", color);
  }

  var log = document.getElementById("log");
  var form = document.getElementById("form");
  var input = document.getElementById("text");
  var sendBtn = document.getElementById("send");
  var storeKey = "jobab_visitor_" + key;
  var token = "";
  try { token = localStorage.getItem(storeKey) || ""; } catch (e) { /* private mode */ }
  var lastSeq = 0;
  var typingEl = null;

  function api(path, opts) {
    opts = opts || {};
    opts.headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
    if (token) opts.headers["X-Visitor-Token"] = token;
    return fetch("/api/widget/" + encodeURIComponent(key) + path, opts).then(function (r) {
      return r.json().then(function (body) { return { ok: r.ok, status: r.status, body: body }; });
    });
  }

  function addMessage(m) {
    if (m.seq && m.seq <= lastSeq) return;
    if (m.seq) lastSeq = Math.max(lastSeq, m.seq);
    hideTyping();
    var el = document.createElement("div");
    // In the widget the visitor is "me": their own messages (inbound to the
    // shop) sit on the right; the shop/AI replies (outbound) sit on the left.
    el.className = "msg " + (m.direction === "in" ? "out" : "in");
    el.textContent = m.text || "";
    log.appendChild(el);
    log.scrollTop = log.scrollHeight;
  }

  function showTyping() {
    if (typingEl) return;
    typingEl = document.createElement("div");
    typingEl.className = "typing";
    typingEl.textContent = "…";
    log.appendChild(typingEl);
    log.scrollTop = log.scrollHeight;
  }
  function hideTyping() {
    if (typingEl) { typingEl.remove(); typingEl = null; }
  }

  function start() {
    api("/session", { method: "POST", body: JSON.stringify({ visitor_token: token }) })
      .then(function (res) {
        if (!res.ok) { addMessage({ direction: "out", text: "Chat is unavailable right now." }); return; }
        token = res.body.visitor_token;
        try { localStorage.setItem(storeKey, token); } catch (e) { /* ignore */ }
        var cfg = res.body.config || {};
        if (cfg.title) document.getElementById("title").textContent = cfg.title;
        (res.body.messages || []).forEach(addMessage);
        if (!res.body.messages || !res.body.messages.length) {
          // The greeting is from the shop, so it shows on the left.
          if (cfg.greeting) addMessage({ direction: "out", text: cfg.greeting });
        }
        listen();
      });
  }

  function listen() {
    // Prefer the live stream; fall back to polling if it will not hold.
    var usePoll = true;
    try {
      var es = new EventSource("/api/widget/" + encodeURIComponent(key) +
        "/stream?token=" + encodeURIComponent(token));
      es.addEventListener("message", function (ev) {
        try { var d = JSON.parse(ev.data); if (d.message) addMessage(d.message); } catch (e) {}
      });
      es.onerror = function () { es.close(); if (usePoll) poll(); };
      es.onopen = function () { usePoll = false; };
      setTimeout(function () { if (usePoll) { es.close(); poll(); } }, 2500);
    } catch (e) { poll(); }
  }

  function poll() {
    setInterval(function () {
      api("/messages?after_seq=" + lastSeq, { method: "GET" }).then(function (res) {
        if (res.ok) (res.body.messages || []).forEach(addMessage);
      });
    }, 3000);
  }

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    var text = input.value.trim();
    if (!text) return;
    input.value = "";
    showTyping();
    sendBtn.disabled = true;
    // Show the sent message from the server's stored copy (which carries a seq),
    // so the poll and stream never render it a second time.
    api("/message", { method: "POST", body: JSON.stringify({ text: text }) })
      .then(function (res) {
        sendBtn.disabled = false;
        if (res.ok && res.body.message) { addMessage(res.body.message); showTyping(); }
        else if (!res.ok) { hideTyping();
          addMessage({ direction: "out", text: "Message not sent. Try again." }); }
      })
      .catch(function () { sendBtn.disabled = false; hideTyping(); });
  });

  start();
})();
