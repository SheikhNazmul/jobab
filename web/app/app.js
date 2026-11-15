/* Jobab dashboard — a small single-page app over the JSON API.
 * No build step: plain modules-free JavaScript, served as one file. */
"use strict";

// ---- tiny helpers ----------------------------------------------------------
function h(tag, attrs) {
  var el = document.createElement(tag);
  attrs = attrs || {};
  for (var k in attrs) {
    if (k === "class") el.className = attrs[k];
    else if (k === "html") el.innerHTML = attrs[k];
    else if (k === "text") el.textContent = attrs[k];
    else if (k.slice(0, 2) === "on") el.addEventListener(k.slice(2).toLowerCase(), attrs[k]);
    else if (attrs[k] != null && attrs[k] !== false) el.setAttribute(k, attrs[k]);
  }
  for (var i = 2; i < arguments.length; i++) {
    var c = arguments[i];
    if (c == null || c === false) continue;
    if (Array.isArray(c)) c.forEach(function (x) { if (x != null) el.appendChild(txt(x)); });
    else el.appendChild(txt(c));
  }
  return el;
}
function txt(c) { return typeof c === "string" || typeof c === "number"
  ? document.createTextNode(String(c)) : c; }
function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); return el; }
function money(n) { return "৳" + Number(n || 0).toLocaleString("en-IN"); }
function when(s) { var d = new Date(s); return d.toLocaleString("en-GB",
  { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }); }

function api(method, path, body) {
  var opts = { method: method, headers: { "X-CSRF": "1" }, credentials: "same-origin" };
  if (body !== undefined) { opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body); }
  return fetch("/api" + path, opts).then(function (r) {
    if (r.status === 204) return {};
    return r.json().then(function (j) {
      if (!r.ok) throw (j && j.error) || { code: "error", message: "Request failed" };
      return j;
    });
  });
}
function toast(msg, bad) {
  var t = h("div", { class: "toast" + (bad ? " bad" : ""), text: msg });
  document.body.appendChild(t);
  setTimeout(function () { t.remove(); }, 2600);
}
function fail(e) { toast((e && e.message) || "Something went wrong", true); }

var app = document.getElementById("app");
var state = { me: null, view: "overview", stream: null, counts: {} };

// ---- boot ------------------------------------------------------------------
api("GET", "/auth/me").then(function (me) { state.me = me; renderShell(); })
  .catch(function () { renderLogin(); });

// ---- login -----------------------------------------------------------------
function renderLogin(signup) {
  var errEl = h("p", { class: "err" });
  function submit(e) {
    e.preventDefault();
    var f = e.target;
    var payload = signup
      ? { name: f.name.value, shop_name: f.shop.value, email: f.email.value, password: f.pw.value }
      : { email: f.email.value, password: f.pw.value };
    errEl.textContent = "";
    api("POST", signup ? "/auth/signup" : "/auth/login", payload)
      .then(function () { return api("GET", "/auth/me"); })
      .then(function (me) { state.me = me; renderShell(); })
      .catch(function (err) { errEl.textContent = err.message || "Could not sign in."; });
  }
  var form = h("form", { onsubmit: submit },
    h("div", { class: "brandmark" }, h("span", { class: "logo", text: "J" }), "Jobab"),
    h("h1", {}, signup ? "Create your shop" : "Welcome back"),
    h("p", { class: "sub" }, signup ? "Start your free trial." : "Sign in to your dashboard."),
    signup && field("Your name", h("input", { name: "name", required: true })),
    signup && field("Shop name", h("input", { name: "shop", required: true })),
    field("Email", h("input", { name: "email", type: "email", required: true })),
    field("Password", h("input", { name: "pw", type: "password", required: true,
      minlength: signup ? 10 : 1 })),
    h("button", { class: "btn block", type: "submit" }, signup ? "Create shop" : "Sign in"),
    errEl,
    h("p", { style: "text-align:center;margin:14px 0 0" },
      h("button", { class: "link", type: "button",
        onclick: function () { renderLogin(!signup); } },
        signup ? "Already have an account? Sign in" : "New here? Create a shop")));
  clear(app).appendChild(h("div", { class: "auth" }, h("div", { class: "auth-card" }, form)));
}
function field(label, input) {
  return h("div", { class: "field" }, h("label", {}, label), input);
}

// ---- shell -----------------------------------------------------------------
var NAV = [
  ["overview", "Overview"], ["inbox", "Inbox"], ["orders", "Orders"], ["products", "Products"],
  ["leads", "Leads"], ["complaints", "Complaints"], ["channels", "Channels"],
  ["settings", "Settings"], ["team", "Team"],
];
function renderShell() {
  var nav = h("nav");
  var items = NAV.slice();
  if (state.me.is_admin) items.push(["admin", "Admin"]);
  items.forEach(function (it) {
    var btn = h("button", { "data-view": it[0],
      onclick: function () { go(it[0]); } }, it[1]);
    if (it[0] === "inbox") btn.appendChild(h("span", { class: "count hidden", id: "nav-human" }));
    nav.appendChild(btn);
  });
  var shop = state.me.shop;
  var shell = h("div", { class: "shell" },
    h("aside", { class: "side" },
      h("div", { class: "brandmark" }, h("span", { class: "logo", text: "J" }), "Jobab"),
      nav,
      h("div", { class: "who" },
        h("b", {}, shop.name),
        h("span", {}, state.me.user.email), h("br"),
        h("button", { class: "link", style: "margin-top:6px",
          onclick: logout }, "Sign out"))),
    h("div", { class: "main" },
      h("div", { class: "topbar" },
        h("h2", { id: "view-title" }, "Overview"),
        h("span", { class: "credits", id: "credits" })),
      h("div", { class: "view", id: "view" })));
  clear(app).appendChild(shell);
  updateCredits();
  connectStream();
  go("overview");
}
function updateCredits() {
  var s = state.me.shop;
  var el = document.getElementById("credits");
  if (el) el.innerHTML = "Credits left <b>" + (s.credits_left) + "</b> / " + s.credits_total +
    " &middot; " + s.plan;
}
function logout() { api("POST", "/auth/logout").then(function () { location.reload(); }); }

function go(view) {
  state.view = view;
  document.querySelectorAll(".side nav button").forEach(function (b) {
    b.classList.toggle("active", b.getAttribute("data-view") === view);
  });
  var title = (NAV.concat([["admin", "Admin"]]).find(function (n) { return n[0] === view; }) || [])[1];
  document.getElementById("view-title").textContent = title || "";
  var host = document.getElementById("view");
  clear(host);
  (VIEWS[view] || VIEWS.overview)(host);
}

// ---- live stream -----------------------------------------------------------
function connectStream() {
  if (state.stream) state.stream.close();
  try {
    var es = new EventSource("/api/stream");
    state.stream = es;
    ["message", "order", "handoff", "lead", "complaint", "comment", "conversation_update",
      "message_status"].forEach(function (name) {
      es.addEventListener(name, function (ev) { onEvent(name, safeJSON(ev.data)); });
    });
  } catch (e) { /* no live updates */ }
}
function safeJSON(s) { try { return JSON.parse(s); } catch (e) { return {}; } }
function onEvent(name, data) {
  if (name === "handoff" || name === "message") refreshHumanBadge();
  if (state.view === "inbox" && window.__inbox) window.__inbox.onEvent(name, data);
  if (name === "order" && state.view === "orders" && window.__reload) window.__reload();
  if (name === "order") toast("New order " + (data.number || ""));
  if (name === "handoff") toast("A chat needs a person");
}
function refreshHumanBadge() {
  api("GET", "/conversations?filter=needs_human&limit=1").then(function () {})
    .catch(function () {});
  api("GET", "/analytics/overview").then(function (o) {
    var el = document.getElementById("nav-human");
    if (!el) return;
    var n = o.totals.needs_human;
    el.textContent = n; el.classList.toggle("hidden", !n);
  }).catch(function () {});
}

// ---- views -----------------------------------------------------------------
var VIEWS = {};

VIEWS.overview = function (host) {
  var tiles = h("div", { class: "tiles" });
  host.appendChild(tiles);
  var chart = h("div", { class: "card" }, h("h3", {}, "Last 14 days"),
    h("div", { class: "body", id: "chart" }, h("p", { class: "muted" }, "Loading…")));
  host.appendChild(chart);
  api("GET", "/analytics/overview").then(function (o) {
    var t = o.totals;
    var defs = [
      ["Orders today", t.orders_today], ["Revenue today", money(t.revenue_today)],
      ["Messages today", t.messages_today], ["AI replies today", t.ai_replies_today],
      ["Needs a person", t.needs_human], ["New leads today", t.new_leads_today],
      ["To review", t.orders_to_review], ["Credits left", t.credits_left],
    ];
    defs.forEach(function (d) {
      tiles.appendChild(h("div", { class: "tile" },
        h("div", { class: "k" }, d[0]), h("div", { class: "v" }, String(d[1]))));
    });
    refreshHumanBadge();
    drawChart(document.getElementById("chart"), o.days);
  }).catch(fail);
};

function drawChart(host, days) {
  clear(host);
  var max = Math.max(1, Math.max.apply(null, days.map(function (d) {
    return Math.max(d.customer_messages, d.orders); })));
  var row = h("div", { style: "display:flex;gap:6px;align-items:flex-end;height:150px" });
  days.forEach(function (d) {
    var col = h("div", { style: "flex:1;display:flex;flex-direction:column;justify-content:flex-end;" +
      "gap:2px;align-items:center", title: d.date + ": " + d.customer_messages + " msgs, " +
      d.orders + " orders" },
      h("div", { style: "width:60%;background:#cdd7e4;border-radius:3px 3px 0 0;height:" +
        (d.customer_messages / max * 120) + "px" }),
      h("div", { style: "width:60%;background:var(--brand);border-radius:3px 3px 0 0;height:" +
        (d.orders / max * 120) + "px" }));
    row.appendChild(col);
  });
  host.appendChild(row);
  host.appendChild(h("p", { class: "muted", style: "margin:10px 0 0;font-size:12px" },
    "▇ customer messages  ▇ orders"));
}

// ---- inbox -----------------------------------------------------------------
VIEWS.inbox = function (host) {
  var inbox = { active: null, filter: "all", convos: [] };
  window.__inbox = inbox;
  var listEl = h("div", { class: "convos" });
  var threadEl = h("div", { class: "thread" },
    h("div", { class: "empty" }, "Pick a conversation to read it."));
  host.parentNode.style.padding = "0";
  host.style.padding = "0";
  var filterBar = h("div", { style: "padding:10px;border-bottom:1px solid var(--line);" +
    "display:flex;gap:6px;background:var(--panel)" });
  [["all", "All"], ["needs_human", "Needs me"], ["unread", "Unread"], ["open", "Open"]]
    .forEach(function (f) {
      filterBar.appendChild(h("button", { class: "btn sm ghost", "data-f": f[0],
        onclick: function () { inbox.filter = f[0]; loadConvos(); } }, f[1]));
    });
  var leftCol = h("div", { style: "display:flex;flex-direction:column;overflow:hidden" },
    filterBar, listEl);
  host.appendChild(h("div", { class: "inbox" }, leftCol, threadEl));

  function loadConvos() {
    filterBar.querySelectorAll("button").forEach(function (b) {
      b.classList.toggle("active", b.getAttribute("data-f") === inbox.filter); });
    api("GET", "/conversations?filter=" + inbox.filter + "&limit=60").then(function (r) {
      inbox.convos = r.items; renderList();
    }).catch(fail);
  }
  function renderList() {
    clear(listEl);
    if (!inbox.convos.length) { listEl.appendChild(h("div", { class: "empty" }, "No conversations.")); return; }
    inbox.convos.forEach(function (c) {
      var tags = [];
      if (c.needs_human) tags.push(h("span", { class: "tag bad" }, "needs me"));
      else if (!c.ai_enabled) tags.push(h("span", { class: "tag" }, "manual"));
      else tags.push(h("span", { class: "tag ai" }, "AI"));
      var el = h("div", { class: "convo" + (inbox.active === c.id ? " active" : ""),
        onclick: function () { openThread(c.id); } },
        h("div", { class: "row1" },
          h("span", { class: "name" }, c.contact.name),
          h("span", { class: "tag" }, c.channel.type),
          c.unread ? h("span", { class: "unread" }, String(c.unread)) : null),
        h("div", { class: "prev" }, c.last_preview || ""),
        h("div", { style: "margin-top:4px;display:flex;gap:5px" }, tags));
      listEl.appendChild(el);
    });
  }
  function openThread(id) {
    inbox.active = id; renderList();
    clear(threadEl).appendChild(h("div", { class: "empty" }, "Loading…"));
    Promise.all([api("GET", "/conversations/" + id),
      api("GET", "/conversations/" + id + "/messages")]).then(function (res) {
      renderThread(res[0], res[1].messages);
    }).catch(fail);
  }
  function renderThread(convo, messages) {
    var log = h("div", { class: "thread-log" });
    messages.forEach(function (m) { log.appendChild(bubble(m)); });
    var aiBtn = h("button", { class: "btn sm ghost", onclick: function () {
      api("POST", "/conversations/" + convo.id + "/ai", { enabled: !convo.ai_enabled })
        .then(function () { openThread(convo.id); }).catch(fail);
    } }, convo.ai_enabled ? "Take over" : "Give back to AI");
    var resolveBtn = convo.needs_human ? h("button", { class: "btn sm",
      onclick: function () { api("POST", "/conversations/" + convo.id + "/resolve")
        .then(function () { loadConvos(); openThread(convo.id); }).catch(fail); } }, "Resolve") : null;
    var input = h("input", { placeholder: "Write a reply…", maxlength: 4000 });
    function send(e) {
      e.preventDefault();
      var text = input.value.trim(); if (!text) return;
      input.value = "";
      api("POST", "/conversations/" + convo.id + "/reply", { text: text }).then(function (r) {
        log.appendChild(bubble(r.message)); log.scrollTop = log.scrollHeight;
      }).catch(fail);
    }
    clear(threadEl);
    threadEl.appendChild(h("div", { class: "thread-head" },
      h("span", { class: "name" }, convo.contact.name +
        (convo.contact.phone ? " · " + convo.contact.phone : "")),
      convo.ai_enabled ? h("span", { class: "tag ai" }, "AI on") : h("span", { class: "tag" }, "manual"),
      resolveBtn, aiBtn));
    threadEl.appendChild(log);
    threadEl.appendChild(h("form", { class: "thread-form", onsubmit: send },
      input, h("button", { class: "btn", type: "submit" }, "Send")));
    log.scrollTop = log.scrollHeight;
    inbox.activeLog = log; inbox.activeId = convo.id;
  }
  function bubble(m) {
    var cls = m.direction === "in" ? "in" : (m.sender === "ai" ? "ai" : "out");
    var who = m.sender === "ai" ? "AI" : (m.sender === "agent" ? "You" : null);
    return h("div", { class: "bubble " + cls },
      who ? h("div", { class: "who" }, who) : null, m.text || "");
  }
  inbox.onEvent = function (name, data) {
    if (name === "message") {
      if (data.conversation_id === inbox.activeId && inbox.activeLog) {
        inbox.activeLog.appendChild(bubble(data.message));
        inbox.activeLog.scrollTop = inbox.activeLog.scrollHeight;
      }
      loadConvos();
    } else if (name === "handoff" || name === "conversation_update") { loadConvos(); }
  };
  loadConvos();
};

// ---- products --------------------------------------------------------------
VIEWS.products = function (host) {
  var q = h("input", { type: "search", placeholder: "Search products…",
    oninput: debounce(load, 300) });
  var toolbar = h("div", { class: "toolbar" }, q, h("span", { class: "spacer" }),
    h("button", { class: "btn ghost sm", onclick: importCsv }, "Import CSV"),
    h("button", { class: "btn ghost sm", onclick: wooSync }, "Sync WooCommerce"),
    h("button", { class: "btn sm", onclick: function () { editProduct(null); } }, "+ Add product"));
  var card = h("div", { class: "card" }, h("div", { class: "body", id: "ptable" }));
  host.appendChild(toolbar); host.appendChild(card);
  window.__reload = load;
  function load() {
    api("GET", "/products?q=" + encodeURIComponent(q.value) + "&per_page=100").then(function (r) {
      var host2 = clear(document.getElementById("ptable"));
      if (!r.items.length) { host2.appendChild(h("div", { class: "empty" }, "No products yet.")); return; }
      var t = h("table", {}, h("thead", {}, h("tr", {},
        h("th", {}, "Product"), h("th", {}, "Price"), h("th", {}, "Stock"),
        h("th", {}, "Status"), h("th", {}, ""))));
      var tb = h("tbody");
      r.items.forEach(function (p) {
        tb.appendChild(h("tr", {},
          h("td", {}, h("b", {}, p.name), p.category ? h("div", { class: "muted" }, p.category) : null),
          h("td", {}, money(p.price)),
          h("td", {}, p.track_stock ? String(p.stock) : "—"),
          h("td", {}, p.is_active ? h("span", { class: "tag ok" }, "active")
            : h("span", { class: "tag" }, "hidden")),
          h("td", {}, h("button", { class: "btn ghost sm",
            onclick: function () { editProduct(p); } }, "Edit"))));
      });
      t.appendChild(tb); host2.appendChild(t);
    }).catch(fail);
  }
  function editProduct(p) {
    var f = {};
    var body = h("div", { class: "body" },
      field("Name", f.name = h("input", { value: p ? p.name : "", required: true })),
      h("div", { class: "grid2" },
        field("Price (৳)", f.price = h("input", { type: "number", min: 0, step: "0.01",
          value: p ? p.price : "" })),
        field("Stock", f.stock = h("input", { type: "number", min: 0, value: p ? p.stock : 0 }))),
      h("div", { class: "grid2" },
        field("Category", f.category = h("input", { value: p ? p.category : "" })),
        field("Brand", f.brand = h("input", { value: p ? p.brand : "" }))),
      field("Tags (for search, comma separated)", f.tags = h("input", { value: p ? p.tags : "" })),
      field("Description", f.description = h("textarea", { rows: 3 }, p ? p.description : "")),
      field("", f.active = checkbox("Active (shown to customers and the AI)",
        p ? p.is_active : true)));
    modal(p ? "Edit product" : "Add product", body, function (close) {
      var payload = { name: f.name.value, price: f.price.value || 0,
        stock: parseInt(f.stock.value || 0, 10), category: f.category.value,
        brand: f.brand.value, tags: f.tags.value, description: f.description.value,
        track_stock: true, is_active: f.active.checked };
      var req = p ? api("PATCH", "/products/" + p.id, payload)
        : api("POST", "/products", payload);
      req.then(function () { close(); toast("Saved"); load(); }).catch(fail);
    }, p ? [h("button", { class: "btn danger", onclick: function (e) {
      if (!confirm("Delete this product?")) return;
      api("DELETE", "/products/" + p.id).then(function () {
        e.target.closest(".modal-bg").remove(); toast("Deleted"); load(); }).catch(fail);
    } }, "Delete")] : []);
  }
  function importCsv() {
    var ta = h("textarea", { rows: 8, placeholder: "name,price,stock,category\nCotton Shirt,850,10,Men",
      style: "width:100%;font-family:monospace" });
    modal("Import products from CSV", h("div", { class: "body" },
      h("p", { class: "muted" }, "First row must be the column names. Needs name and price."),
      ta), function (close) {
      api("POST", "/products/import", { csv: ta.value }).then(function (r) {
        close(); toast("Added " + r.created + ", updated " + r.updated); load();
      }).catch(fail);
    });
  }
  function wooSync() {
    var f = {};
    modal("Sync from WooCommerce", h("div", { class: "body" },
      h("p", { class: "muted" }, "Create a read-only REST API key in WooCommerce, then paste it here."),
      field("Store URL", f.url = h("input", { placeholder: "https://yourstore.com" })),
      field("Consumer key", f.key = h("input", { placeholder: "ck_…" })),
      field("Consumer secret", f.secret = h("input", { placeholder: "cs_…" }))),
      function (close) {
        api("POST", "/products/sync/woocommerce", { url: f.url.value,
          consumer_key: f.key.value, consumer_secret: f.secret.value }).then(function (r) {
          close(); toast("Synced: " + r.created + " new, " + r.updated + " updated"); load();
        }).catch(fail);
      });
  }
  load();
};

// ---- orders ----------------------------------------------------------------
VIEWS.orders = function (host) {
  var sel = h("select", { onchange: load },
    h("option", { value: "" }, "All statuses"),
    ["pending", "confirmed", "packed", "shipped", "delivered", "cancelled", "returned"]
      .map(function (s) { return h("option", { value: s }, s); }));
  var q = h("input", { type: "search", placeholder: "Number, name or phone…",
    oninput: debounce(load, 300) });
  host.appendChild(h("div", { class: "toolbar" }, q, sel));
  var card = h("div", { class: "card" }, h("div", { class: "body", id: "otable" }));
  host.appendChild(card);
  window.__reload = load;
  function load() {
    api("GET", "/orders?status=" + sel.value + "&q=" + encodeURIComponent(q.value) + "&limit=100")
      .then(function (r) {
        var host2 = clear(document.getElementById("otable"));
        if (!r.items.length) { host2.appendChild(h("div", { class: "empty" }, "No orders yet.")); return; }
        var t = h("table", {}, h("thead", {}, h("tr", {},
          h("th", {}, "Order"), h("th", {}, "Customer"), h("th", {}, "Items"),
          h("th", {}, "Total"), h("th", {}, "Status"), h("th", {}, "By"))));
        var tb = h("tbody");
        r.items.forEach(function (o) {
          var statusSel = h("select", { onchange: function () {
            api("PATCH", "/orders/" + o.id, { status: statusSel.value })
              .then(function () { toast("Updated"); }).catch(fail);
          } }, ["pending", "confirmed", "packed", "shipped", "delivered", "cancelled", "returned"]
            .map(function (s) { return h("option", { value: s, selected: s === o.status }, s); }));
          tb.appendChild(h("tr", { style: o.needs_review ? "background:#fff8ec" : "",
            onclick: function (e) { if (e.target.tagName !== "SELECT") orderDetail(o); } },
            h("td", {}, h("b", {}, o.number),
              o.needs_review ? h("div", {}, h("span", { class: "tag warn" }, "review")) : null),
            h("td", {}, o.customer_name || "—", h("div", { class: "muted" }, o.phone)),
            h("td", {}, String((o.items || []).reduce(function (a, i) { return a + i.quantity; }, 0))),
            h("td", {}, money(o.total)),
            h("td", {}, statusSel),
            h("td", {}, o.created_by === "ai" ? h("span", { class: "tag ai" }, "AI")
              : h("span", { class: "tag" }, "manual"))));
        });
        t.appendChild(tb); host2.appendChild(t);
      }).catch(fail);
  }
  function orderDetail(o) {
    var lines = (o.items || []).map(function (i) {
      return h("tr", {}, h("td", {}, i.quantity + "× " + i.name + (i.variant ? " (" + i.variant + ")" : "")),
        h("td", { style: "text-align:right" }, money(i.total))); });
    var f = {};
    modal("Order " + o.number, h("div", { class: "body" },
      h("table", {}, h("tbody", {}, lines,
        h("tr", {}, h("td", {}, "Delivery"), h("td", { style: "text-align:right" }, money(o.delivery_charge))),
        h("tr", {}, h("td", {}, h("b", {}, "Total")), h("td", { style: "text-align:right" }, h("b", {}, money(o.total)))))),
      h("p", { class: "muted", style: "margin-top:12px" }, o.address || ""),
      h("div", { class: "grid2" },
        field("Courier", f.courier = h("input", { value: o.courier || "" })),
        field("Tracking code", f.tracking = h("input", { value: o.tracking_code || "" })))),
      function (close) {
        api("PATCH", "/orders/" + o.id, { courier: f.courier.value, tracking_code: f.tracking.value })
          .then(function () { close(); toast("Saved"); load(); }).catch(fail);
      });
  }
  load();
};

// ---- leads & complaints ----------------------------------------------------
VIEWS.leads = function (host) {
  simpleList(host, "/leads", ["Name", "Phone", "Interest", "Status", ""], function (l) {
    var sel = statusSelect(["new", "contacted", "won", "lost"], l.status, function (v) {
      api("PATCH", "/leads/" + l.id + "?status=" + v).then(function () { toast("Updated"); }).catch(fail);
    });
    return [l.name || "—", l.phone || "—", l.interest || "", sel, ""];
  });
};
VIEWS.complaints = function (host) {
  simpleList(host, "/complaints", ["Complaint", "Status", "When", ""], function (c) {
    var btn = c.status === "open" ? h("button", { class: "btn sm ghost", onclick: function () {
      api("POST", "/complaints/" + c.id + "/resolve").then(function () {
        toast("Resolved"); go("complaints"); }).catch(fail);
    } }, "Resolve") : h("span", { class: "tag ok" }, "resolved");
    return [c.summary, h("span", { class: "tag " + (c.status === "open" ? "bad" : "ok") }, c.status),
      when(c.created_at), btn];
  });
};
function simpleList(host, path, cols, rowFn) {
  var card = h("div", { class: "card" }, h("div", { class: "body", id: "list" }));
  host.appendChild(card);
  api("GET", path).then(function (r) {
    var host2 = clear(document.getElementById("list"));
    if (!r.items.length) { host2.appendChild(h("div", { class: "empty" }, "Nothing here yet.")); return; }
    var t = h("table", {}, h("thead", {}, h("tr", {}, cols.map(function (c) { return h("th", {}, c); }))));
    var tb = h("tbody");
    r.items.forEach(function (item) {
      tb.appendChild(h("tr", {}, rowFn(item).map(function (c) { return h("td", {}, c); })));
    });
    t.appendChild(tb); host2.appendChild(t);
  }).catch(fail);
}
function statusSelect(opts, current, onchange) {
  return h("select", { onchange: function (e) { onchange(e.target.value); } },
    opts.map(function (o) { return h("option", { value: o, selected: o === current }, o); }));
}

// ---- channels --------------------------------------------------------------
VIEWS.channels = function (host) {
  var card = h("div", { class: "card" }, h("h3", {}, "Your channels"),
    h("div", { class: "body", id: "chlist" }));
  host.appendChild(card);
  var embed = h("div", { class: "card" }, h("h3", {}, "Website chat"),
    h("div", { class: "body", id: "embed" }));
  host.appendChild(embed);
  var connect = h("div", { class: "card" }, h("h3", {}, "Connect Messenger, Instagram or WhatsApp"),
    h("div", { class: "body" },
      h("p", { class: "muted" }, "Paste the Page or WhatsApp access token from your Meta app. " +
        "See the setup guide for how to get it."),
      metaForm()));
  host.appendChild(connect);
  function load() {
    api("GET", "/channels").then(function (r) {
      var host2 = clear(document.getElementById("chlist"));
      var t = h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Channel"),
        h("th", {}, "Type"), h("th", {}, "Status"), h("th", {}, ""))));
      var tb = h("tbody");
      r.channels.forEach(function (c) {
        tb.appendChild(h("tr", {},
          h("td", {}, c.name || c.type), h("td", {}, c.type),
          h("td", {}, c.is_active ? h("span", { class: "tag ok" }, "on")
            : h("span", { class: "tag" }, "off")),
          h("td", {}, c.type === "webchat" ? h("span", { class: "muted" }, "always on")
            : h("button", { class: "btn ghost sm", onclick: function () {
              if (!confirm("Disconnect " + c.name + "?")) return;
              api("DELETE", "/channels/" + c.id).then(function () { toast("Removed"); load(); }).catch(fail);
            } }, "Remove"))));
      });
      t.appendChild(tb); host2.appendChild(t);
    }).catch(fail);
    api("GET", "/channels/widget-code").then(function (r) {
      clear(document.getElementById("embed")).appendChild(
        h("div", {}, h("p", { class: "muted" }, "Paste this before </body> on your website:"),
          h("textarea", { rows: 2, readonly: true, style: "width:100%;font-family:monospace" },
            r.snippet)));
    }).catch(function () {});
  }
  function metaForm() {
    var f = {};
    return h("form", { onsubmit: function (e) {
      e.preventDefault();
      api("POST", "/channels/meta", { type: f.type.value, external_id: f.ext.value,
        name: f.name.value, token: f.token.value }).then(function () {
        toast("Connected"); f.token.value = ""; load();
      }).catch(fail);
    } },
      h("div", { class: "grid2" },
        field("Channel", f.type = h("select", {},
          h("option", { value: "messenger" }, "Facebook Messenger"),
          h("option", { value: "instagram" }, "Instagram"),
          h("option", { value: "whatsapp" }, "WhatsApp"))),
        field("Name", f.name = h("input", { placeholder: "My Page" }))),
      field("Page ID / IG account ID / WhatsApp phone-number ID",
        f.ext = h("input", { required: true })),
      field("Access token", f.token = h("input", { required: true })),
      h("button", { class: "btn", type: "submit" }, "Connect"));
  }
  load();
};

// ---- settings --------------------------------------------------------------
VIEWS.settings = function (host) {
  api("GET", "/shop/settings").then(function (r) {
    var s = r.settings; var f = {};
    function section(title, inner) {
      return h("div", { class: "card" }, h("h3", {}, title), h("div", { class: "body" }, inner));
    }
    var zonesBox = h("div", {});
    (s.delivery.zones || []).forEach(function (z) { zonesBox.appendChild(zoneRow(z)); });
    function zoneRow(z) {
      var row = h("div", { class: "grid2", style: "margin-bottom:8px;grid-template-columns:2fr 1fr auto" },
        h("input", { class: "z-name", value: z ? z.name : "", placeholder: "Inside Dhaka" }),
        h("input", { class: "z-charge", type: "number", value: z ? z.charge : "", placeholder: "70" }),
        h("button", { class: "btn ghost sm", type: "button",
          onclick: function () { row.remove(); } }, "✕"));
      row.dataset.id = z ? z.id : "";
      return row;
    }
    var form = h("form", { onsubmit: save },
      section("Business", h("div", {},
        field("Shop name", f.name = h("input", { value: r.name })),
        field("About your shop (the AI uses this)",
          f.desc = h("textarea", { rows: 2 }, s.business.description)),
        h("div", { class: "grid2" },
          field("Phone", f.phone = h("input", { value: s.business.phone })),
          field("Opening hours", f.hours = h("input", { value: s.business.hours }))))),
      section("AI assistant", h("div", {},
        field("", f.aiOn = checkbox("Let the AI answer customers", s.ai.enabled)),
        field("Tone", f.tone = h("input", { value: s.ai.tone })),
        field("Extra instructions for the AI",
          f.inst = h("textarea", { rows: 3 }, s.ai.instructions)),
        h("div", { class: "grid2" },
          field("Hold AI orders above (৳, blank = never)",
            f.review = h("input", { type: "number", value: s.ai.review_orders_over || "" })),
          field("Pause AI after a human replies (minutes)",
            f.pause = h("input", { type: "number", value: s.ai.pause_minutes }))),
        field("", f.disclose = checkbox("Tell customers it is an AI if they ask", s.ai.disclose_ai)))),
      section("Delivery", h("div", {},
        h("p", { class: "muted" }, "Name each area and its charge. The AI uses these for totals."),
        zonesBox,
        h("button", { class: "btn ghost sm", type: "button",
          onclick: function () { zonesBox.appendChild(zoneRow(null)); } }, "+ Add zone"),
        field("Free delivery over (৳, blank = never)",
          f.freeOver = h("input", { type: "number", value: s.delivery.free_over || "",
            style: "margin-top:12px" })))),
      section("Payment & policies", h("div", {},
        field("Payment methods", f.pay = h("input", { value: s.payment.methods,
          placeholder: "Cash on delivery, bKash" })),
        field("Return policy", f.returns = h("textarea", { rows: 2 }, s.policies.returns)))),
      section("Comment auto-reply (Facebook & Instagram)", h("div", {},
        field("", f.cAuto = checkbox("Publicly reply to comments on posts", s.comments.auto_reply)),
        field("", f.cPriv = checkbox("Also send a private message to the commenter",
          s.comments.private_reply)),
        field("Public reply text", f.cPub = h("input", { value: s.comments.public_text })),
        field("Private message text", f.cMsg = h("input", { value: s.comments.private_text })))),
      h("button", { class: "btn", type: "submit", style: "margin-top:4px" }, "Save settings"));
    host.appendChild(form);

    function save(e) {
      e.preventDefault();
      var zones = Array.prototype.map.call(zonesBox.children, function (row) {
        var name = row.querySelector(".z-name").value.trim();
        if (!name) return null;
        return { id: row.dataset.id || slug(name), name: name,
          charge: parseFloat(row.querySelector(".z-charge").value || 0) };
      }).filter(Boolean);
      var payload = {
        business: Object.assign({}, s.business, { description: f.desc.value,
          phone: f.phone.value, hours: f.hours.value }),
        ai: Object.assign({}, s.ai, { enabled: f.aiOn.checked, tone: f.tone.value,
          instructions: f.inst.value, disclose_ai: f.disclose.checked,
          pause_minutes: parseInt(f.pause.value || 0, 10),
          review_orders_over: f.review.value ? parseFloat(f.review.value) : null }),
        delivery: Object.assign({}, s.delivery, { zones: zones,
          free_over: f.freeOver.value ? parseFloat(f.freeOver.value) : null }),
        payment: Object.assign({}, s.payment, { methods: f.pay.value }),
        policies: Object.assign({}, s.policies, { returns: f.returns.value }),
        comments: { auto_reply: f.cAuto.checked, private_reply: f.cPriv.checked,
          public_text: f.cPub.value, private_text: f.cMsg.value },
        orders: s.orders,
      };
      Promise.all([
        api("PUT", "/shop/settings", payload),
        r.name !== f.name.value ? api("PATCH", "/shop/profile", { name: f.name.value }) : null,
      ]).then(function () { toast("Settings saved"); }).catch(fail);
    }
  }).catch(fail);
};

// ---- team ------------------------------------------------------------------
VIEWS.team = function (host) {
  var card = h("div", { class: "card" }, h("h3", {}, "Team"), h("div", { class: "body", id: "team" }));
  host.appendChild(card);
  host.appendChild(h("button", { class: "btn sm", style: "margin-top:14px",
    onclick: addMember }, "+ Add member"));
  function load() {
    api("GET", "/shop/team").then(function (rows) {
      var host2 = clear(document.getElementById("team"));
      var t = h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Name"), h("th", {}, "Email"),
        h("th", {}, "Role"), h("th", {}, ""))));
      var tb = h("tbody");
      rows.forEach(function (m) {
        var roleSel = h("select", { onchange: function () {
          api("PATCH", "/shop/team/" + m.id + "?role=" + roleSel.value)
            .then(function () { toast("Updated"); }).catch(fail);
        } }, ["owner", "manager", "agent"].map(function (ro) {
          return h("option", { value: ro, selected: ro === m.role }, ro); }));
        tb.appendChild(h("tr", {}, h("td", {}, m.name), h("td", {}, m.email),
          h("td", {}, roleSel),
          h("td", {}, m.user_id === state.me.user.id ? h("span", { class: "muted" }, "you")
            : h("button", { class: "btn ghost sm", onclick: function () {
              if (!confirm("Remove " + m.email + "?")) return;
              api("DELETE", "/shop/team/" + m.id).then(function () { toast("Removed"); load(); }).catch(fail);
            } }, "Remove"))));
      });
      t.appendChild(tb); host2.appendChild(t);
    }).catch(fail);
  }
  function addMember() {
    var f = {};
    modal("Add team member", h("div", { class: "body" },
      field("Name", f.name = h("input", { required: true })),
      field("Email", f.email = h("input", { type: "email", required: true })),
      field("Temporary password", f.pw = h("input", { value: randomPw() })),
      field("Role", f.role = h("select", {}, ["agent", "manager", "owner"].map(function (r) {
        return h("option", { value: r }, r); })))), function (close) {
      api("POST", "/shop/team", { name: f.name.value, email: f.email.value,
        password: f.pw.value, role: f.role.value }).then(function () {
        close(); toast("Added"); load();
      }).catch(fail);
    });
  }
  load();
};

// ---- admin -----------------------------------------------------------------
VIEWS.admin = function (host) {
  api("GET", "/admin/overview").then(function (o) {
    var tiles = h("div", { class: "tiles" });
    [["Shops", o.shops], ["Active", o.active_shops], ["Orders 30d", o.orders_30d],
      ["AI replies 30d", o.ai_replies_30d]].forEach(function (d) {
      tiles.appendChild(h("div", { class: "tile" }, h("div", { class: "k" }, d[0]),
        h("div", { class: "v" }, String(d[1]))));
    });
    host.appendChild(tiles);
  }).catch(fail);
  var card = h("div", { class: "card" }, h("h3", {}, "All shops"), h("div", { class: "body", id: "shops" }));
  host.appendChild(card);
  api("GET", "/admin/shops").then(function (r) {
    var host2 = clear(document.getElementById("shops"));
    var t = h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Shop"), h("th", {}, "Owner"),
      h("th", {}, "Plan"), h("th", {}, "Credits"), h("th", {}, ""))));
    var tb = h("tbody");
    r.shops.forEach(function (s) {
      tb.appendChild(h("tr", {}, h("td", {}, s.name), h("td", {}, s.owner_email),
        h("td", {}, s.plan),
        h("td", {}, s.credits_left + " / " + s.credits_total),
        h("td", {}, h("button", { class: "btn ghost sm", onclick: function () {
          var amt = prompt("Add how many credits?", "1000");
          if (!amt) return;
          api("POST", "/admin/shops/" + s.id + "/credits", { amount: parseInt(amt, 10) })
            .then(function () { toast("Added"); go("admin"); }).catch(fail);
        } }, "Add credits"))));
    });
    t.appendChild(tb); host2.appendChild(t);
  }).catch(fail);
};

// ---- shared UI -------------------------------------------------------------
function modal(title, bodyNode, onSave, extraFoot) {
  var bg = h("div", { class: "modal-bg", onclick: function (e) {
    if (e.target === bg) bg.remove(); } });
  function close() { bg.remove(); }
  var foot = h("div", { class: "foot" },
    (extraFoot || []), h("span", { class: "spacer" }),
    h("button", { class: "btn ghost", onclick: close }, "Cancel"),
    onSave ? h("button", { class: "btn", onclick: function () { onSave(close); } }, "Save") : null);
  bg.appendChild(h("div", { class: "modal" }, h("h3", {}, title), bodyNode, foot));
  document.body.appendChild(bg);
}
function checkbox(label, checked) {
  var input = h("input", { type: "checkbox" });
  input.checked = !!checked;
  var wrap = h("label", { style: "display:flex;gap:8px;align-items:center;cursor:pointer" },
    input, h("span", {}, label));
  Object.defineProperty(wrap, "checked", { get: function () { return input.checked; } });
  return wrap;
}
function debounce(fn, ms) {
  var t; return function () { clearTimeout(t); t = setTimeout(fn, ms); };
}
function slug(s) { return s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "zone"; }
function randomPw() { return "Jb-" + Math.random().toString(36).slice(2, 10); }
