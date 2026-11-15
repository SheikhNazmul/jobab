/*
 * Jobab chat widget loader.
 * Embed with: <script src="https://YOURDOMAIN/widget.js" data-key="wk_..."></script>
 * It adds a launcher button and, when opened, an iframe with the chat. The
 * iframe is sandboxed and talks only to the Jobab API, so the host page's data
 * is never exposed to it.
 */
(function () {
  "use strict";
  var script = document.currentScript;
  if (!script) return;
  var key = script.getAttribute("data-key");
  if (!key) {
    console.warn("[Jobab] widget script is missing data-key");
    return;
  }
  var origin = new URL(script.src).origin;
  var color = script.getAttribute("data-color") || "#0C6B4D";
  var side = script.getAttribute("data-side") === "left" ? "left" : "right";

  if (window.__jobabWidgetLoaded) return;
  window.__jobabWidgetLoaded = true;

  var open = false;
  var panel, button;

  function css(el, styles) {
    for (var k in styles) el.style[k] = styles[k];
  }

  function build() {
    button = document.createElement("button");
    button.setAttribute("aria-label", "Open chat");
    button.innerHTML = bubbleIcon();
    css(button, {
      position: "fixed", bottom: "20px", zIndex: 2147483000,
      width: "60px", height: "60px", borderRadius: "50%", border: "none",
      background: color, color: "#fff", cursor: "pointer",
      boxShadow: "0 8px 30px rgba(0,0,0,.28)", transition: "transform .15s ease",
    });
    button.style[side] = "20px";
    button.onmouseenter = function () { button.style.transform = "scale(1.06)"; };
    button.onmouseleave = function () { button.style.transform = "scale(1)"; };
    button.onclick = toggle;

    panel = document.createElement("iframe");
    panel.title = "Chat";
    panel.src = origin + "/widget/frame?k=" + encodeURIComponent(key) +
      "&color=" + encodeURIComponent(color);
    panel.setAttribute("allow", "clipboard-write");
    css(panel, {
      position: "fixed", bottom: "92px", zIndex: 2147483000,
      width: "380px", height: "560px", maxWidth: "calc(100vw - 32px)",
      maxHeight: "calc(100vh - 120px)", border: "none", borderRadius: "16px",
      boxShadow: "0 16px 50px rgba(0,0,0,.3)", display: "none", background: "#fff",
    });
    panel.style[side] = "20px";

    document.body.appendChild(panel);
    document.body.appendChild(button);
  }

  function toggle() {
    open = !open;
    panel.style.display = open ? "block" : "none";
    button.innerHTML = open ? closeIcon() : bubbleIcon();
  }

  function bubbleIcon() {
    return '<svg width="28" height="28" viewBox="0 0 24 24" fill="none" ' +
      'style="margin:auto" xmlns="http://www.w3.org/2000/svg"><path d="M12 3C7 3 ' +
      '3 6.6 3 11c0 2.1.9 4 2.5 5.4L4 21l5-1.6c1 .3 2 .5 3 .5 5 0 9-3.6 9-8s-4-8-9-8Z" ' +
      'fill="currentColor"/></svg>';
  }
  function closeIcon() {
    return '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" ' +
      'style="margin:auto" xmlns="http://www.w3.org/2000/svg"><path d="M6 6l12 12M18 6 ' +
      '6 18" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/></svg>';
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", build);
  } else {
    build();
  }
})();
