/* LStudio demo: подставляет данные клиента в готовый сайт-образец прямо в браузере.
   Данные лежат в window.__DEMO__ = {v:{...}, items:[...], cats:[...], photos:{...}, gallery:[...], ops:[...], watch:bool}
   Каждый шаг (op) описан в файле шаблона templates/<имя>.json. */
(function () {
  "use strict";
  var D = window.__DEMO__;
  if (!D) return;
  var V = D.v || {};
  var busy = false;
  var obs = null;

  function qsa(sel, root) {
    try { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }
    catch (e) { console.warn("[demo] плохой селектор:", sel); return []; }
  }
  function fmt(s, ctx) {
    if (s == null) return "";
    return String(s).replace(/\{([a-zA-Z0-9_]+)\}/g, function (m, k) {
      if (ctx && ctx[k] != null) return ctx[k];
      if (V[k] != null) return V[k];
      return "";
    });
  }
  function emptyVars(s, ctx) {          // есть ли в шаблоне строки поля, которое не заполнено
    var empty = false;
    String(s || "").replace(/\{([a-zA-Z0-9_]+)\}/g, function (m, k) {
      var x = ctx && ctx[k] != null ? ctx[k] : V[k];
      if (x == null || String(x).trim() === "") empty = true;
    });
    return empty;
  }
  function esc(s) {
    return String(s).replace(/[&<>"]/g, function (c) { return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]; });
  }
  function setImg(el, url) {
    if (!url || !el) return;
    if (el.tagName === "IMG") {
      el.removeAttribute("loading");
      if (el.getAttribute("src") !== url) { el.setAttribute("src", url); }
      el.removeAttribute("srcset"); el.removeAttribute("sizes");
      var pic = el.parentNode;
      if (pic && pic.tagName === "PICTURE") qsa("source", pic).forEach(function (s) { s.remove(); });
    } else {
      el.style.backgroundImage = 'url("' + url + '")';
      el.style.backgroundSize = el.style.backgroundSize || "cover";
      el.style.backgroundPosition = el.style.backgroundPosition || "center";
    }
  }
  function photoFor(slot, ctx, fallback) {
    if (slot === "item") {
      if (ctx && ctx.photo) return ctx.photo;
      if (fallback === "gallery") {
        var g = D.gallery || [];
        return g.length ? g[((+(ctx && ctx.n) || 1) - 1) % g.length] : "";
      }
      return fallback ? (D.photos || {})[fmt(fallback, ctx)] : "";
    }
    var p = D.photos || {};
    return p[slot];
  }
  // убрать элемент, а если указано — его ближайшего «родителя» (например, всю строку контактов)
  function drop(el, up) {
    var t = up ? (el.closest(up) || el) : el;
    t.remove();
  }

  // ---------- замена текста по всей странице ----------
  var ATTRS = ["alt", "title", "placeholder", "aria-label", "content", "value", "data-title"];
  function replaceAll(map) {
    var keys = Object.keys(map).filter(Boolean).sort(function (a, b) { return b.length - a.length; });
    if (!keys.length) return;
    var pairs = keys.map(function (k) { return [k, fmt(map[k])]; });
    function rep(s) {
      var out = s;
      pairs.forEach(function (p) { if (out.indexOf(p[0]) !== -1) out = out.split(p[0]).join(p[1]); });
      return out;
    }
    var w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null);
    var n, list = [];
    while ((n = w.nextNode())) list.push(n);
    list.forEach(function (t) {
      var p = t.parentNode;
      if (!p || p.tagName === "SCRIPT" || p.tagName === "STYLE") return;
      var r = rep(t.nodeValue);
      if (r !== t.nodeValue) t.nodeValue = r;
    });
    qsa("*").forEach(function (el) {
      ATTRS.forEach(function (a) {
        var v = el.getAttribute && el.getAttribute(a);
        if (v) { var r = rep(v); if (r !== v) el.setAttribute(a, r); }
      });
    });
    if (document.title) document.title = rep(document.title);
  }

  // ---------- одно поле внутри элемента ----------
  function applyField(root, f, ctx) {
    var els = f.sel ? qsa(f.sel, root) : [root];
    if (!f.all && els.length > 1) els = [els[0]];
    els.forEach(function (el) {
      if (f.remove) { el.remove(); return; }
      var src = f.text != null ? f.text : f.html;
      if (src != null && emptyVars(src, ctx)) {
        if (f.empty === "keep") return;
        if (f.empty === "remove" || f.empty === undefined) { drop(el, f.up); return; }
      }
      if (f.text != null) { var tv = fmt(f.text, ctx); if (el.textContent !== tv) el.textContent = tv; }
      else if (f.html != null) {
        var keep = f.keep ? qsa(":scope > " + f.keep, el) : [];
        el.innerHTML = fmt(f.html, escCtx(ctx));
        keep.forEach(function (k) { el.insertBefore(k, el.firstChild); });
      }
      if (f.li && ctx && ctx.items) {
        el.innerHTML = "";
        ctx.items.slice(0, f.max || 6).forEach(function (it) {
          var li = document.createElement("li");
          li.textContent = fmt(f.li, it).replace(/\s+[—–-]\s*$/, "");
          el.appendChild(li);
        });
      }
      if (f.attr) {
        var av = fmt(f.value, ctx);
        if (av) el.setAttribute(f.attr, av); else if (f.empty !== "keep") el.removeAttribute(f.attr);
      }
      if (f.photo) {
        var url = photoFor(f.photo, ctx, f.fallback);
        if (url) setImg(f.bg ? (el.closest(f.bg) || el) : el, url);
        else if (f.nophoto === "remove") drop(el, f.up);
      }
    });
  }
  function escCtx(ctx) {               // в html-полях данные клиента экранируем
    var o = {}, k;
    for (k in V) o[k] = esc(V[k]);
    if (ctx) for (k in ctx) o[k] = esc(ctx[k]);
    return o;
  }

  // ---------- список: карточки услуг, позиции меню и т. п. ----------
  function applyList(op, scope, ctxOverride) {
    var data = ctxOverride || D[op.from || "items"] || [];
    if (op.only_photos) data = data.filter(function (x) { return x.photo; });
    if (op.offset) data = data.slice(op.offset);
    if (op.max) data = data.slice(0, op.max);
    if (!data.length && !op.clear) return;
    var roots = op.scope ? qsa(op.scope, scope) : [scope || document];
    roots.forEach(function (root) {
      if (op.inplace) {                // сайты на React: меняем карточки на месте, лишние прячем
        var els = qsa(op.item, root);
        if (!els.length) return;
        data.forEach(function (it, i) {
          var el = els[i];
          if (!el) {
            el = els[0].cloneNode(true);
            el.setAttribute("data-demo", "1");
            els[els.length - 1].parentNode.appendChild(el);
            els.push(el);
          }
          if (el.style.display === "none") el.style.display = "";
          var ctx = Object.assign({}, it, {n: String(i + 1), nn: (i < 9 ? "0" : "") + (i + 1)});
          (op.fields || []).forEach(function (f) { applyField(el, f, ctx); });
        });
        els.slice(data.length).forEach(function (e) { e.style.display = "none"; });
        return;
      }
      var originals = qsa(op.item, root).filter(function (e) { return !e.hasAttribute("data-demo"); });
      if (!originals.length) return;          // уже заменено (или такого блока нет)
      var proto = originals[(op.proto || 1) - 1] || originals[0];
      var parent = originals[0].parentNode;
      var anchor = originals[0];
      var marker = document.createComment("demo-list");
      parent.insertBefore(marker, anchor);
      qsa(op.item, root).forEach(function (e) { if (e.hasAttribute("data-demo")) e.remove(); });
      originals.forEach(function (e) { e.remove(); });
      data.forEach(function (it, i) {
        var c = proto.cloneNode(true);
        c.setAttribute("data-demo", "1");
        var ctx = Object.assign({}, it, {n: String(i + 1), nn: (i < 9 ? "0" : "") + (i + 1)});
        (op.fields || []).forEach(function (f) { applyField(c, f, ctx); });
        marker.parentNode.insertBefore(c, marker);
      });
      marker.remove();
    });
  }

  // ---------- группы: разделы меню по категориям ----------
  function applyGroups(op) {
    var cats = (D.cats || []).filter(function (c) { return c.items.length; });
    if (!cats.length) return;
    var groups = qsa(op.group).filter(function (e) { return !e.hasAttribute("data-demo"); });
    if (!groups.length) return;
    var proto = groups[0], parent = proto.parentNode, marker = document.createComment("demo-groups");
    parent.insertBefore(marker, proto);
    groups.forEach(function (g) { g.remove(); });
    cats.forEach(function (cat, i) {
      var g = proto.cloneNode(true);
      g.setAttribute("data-demo", "1");
      var ctx = {cat: cat.name, items: cat.items, photo: cat.photo, cat_id: "demo-cat-" + (i + 1), n: String(i + 1), nn: (i < 9 ? "0" : "") + (i + 1)};
      (op.fields || []).forEach(function (f) { applyField(g, f, ctx); });
      (op.lists || []).forEach(function (l) { applyList(l, g, cat.items); });
      marker.parentNode.insertBefore(g, marker);
    });
    marker.remove();
  }

  // ---------- галерея из фото клиента ----------
  function applyGallery(op) {
    var pics = D.gallery || [];
    if (pics.length < (op.min || 3) || document.getElementById("demo-gallery")) return;
    var at = qsa(op.after)[0];
    if (!at) return;
    var s = document.createElement("section");
    s.id = "demo-gallery";
    s.className = op["class"] || "";
    s.innerHTML = '<div class="dg-in"><h2 class="dg-title"></h2><div class="dg-grid"></div></div>';
    s.querySelector(".dg-title").textContent = fmt(op.title || "Наши работы");
    var grid = s.querySelector(".dg-grid");
    pics.slice(0, op.max || 12).forEach(function (u) {
      var f = document.createElement("figure");
      var im = document.createElement("img");
      im.src = u; im.alt = ""; im.loading = "lazy";
      f.appendChild(im); grid.appendChild(f);
    });
    at.parentNode.insertBefore(s, at.nextSibling);
    if (!document.getElementById("demo-gallery-css")) {
      var st = document.createElement("style");
      st.id = "demo-gallery-css";
      st.textContent = "#demo-gallery{padding:90px 40px}#demo-gallery .dg-in{max-width:1240px;margin:0 auto}" +
        "#demo-gallery .dg-title{font-size:clamp(34px,4.4vw,64px);line-height:1.05;margin:0 0 36px}" +
        "#demo-gallery .dg-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px}" +
        "#demo-gallery figure{margin:0;aspect-ratio:1/1;overflow:hidden;border-radius:var(--dg-radius,6px)}" +
        "#demo-gallery img{display:block;width:100%;height:100%;object-fit:cover}" +
        "@media(max-width:700px){#demo-gallery{padding:56px 16px}#demo-gallery .dg-grid{grid-template-columns:1fr 1fr;gap:8px}}";
      document.head.appendChild(st);
    }
  }

  // ---------- подгон огромных надписей по ширине ----------
  function fitAll() {
    (D.ops || []).forEach(function (op) {
      if (!op.fit) return;
      qsa(op.fit).forEach(function (el) {
        el.style.fontSize = "";
        el.style.whiteSpace = op.nowrap ? "nowrap" : "";
        var box = op.box ? (el.closest(op.box) || el.parentNode) : el.parentNode;
        var max = box.clientWidth * (op.ratio || 0.94);
        if (op.maxw) max = Math.min(max, op.maxw);
        var size = parseFloat(getComputedStyle(el).fontSize);
        var guard = 60;
        while (guard-- > 0 && el.scrollWidth > max && size > (op.min || 14)) {
          size *= 0.92;
          el.style.fontSize = size + "px";
        }
      });
    });
  }

  function allowed(op) {                 // «when»: шаг выполняется, только если есть данные
    var w = op.when;
    if (!w) return true;
    var neg = w.charAt(0) === "!";
    var k = neg ? w.slice(1) : w;
    var has;
    if (k === "items") has = (D.items || []).length > 0;
    else if (k === "photos") has = (D.gallery || []).length > 0;
    else if (k === "item_photos") has = (D.items || []).some(function (x) { return x.photo; });
    else if (k === "cats") has = (D.cats || []).length > 1;
    else has = !!(V[k] && String(V[k]).trim());
    return neg ? !has : has;
  }

  function run() {
    if (busy) return;
    busy = true;
    if (obs) obs.disconnect();
    try {
      (D.ops || []).forEach(function (op) {
        try {
          if (!allowed(op)) return;
          if (op.css && !document.getElementById("demo-css-" + (op.id || "x"))) {
            var st = document.createElement("style");
            st.id = "demo-css-" + (op.id || "x");
            st.textContent = fmt(op.css);
            document.head.appendChild(st);
          }
          if (op.replace) replaceAll(op.replace);
          if (op.text || op.html || op.attr) {
            qsa(op.text || op.html || op.attr).forEach(function (el, i) {
              if (!op.all && i > 0) return;
              var f = Object.assign({}, op, {sel: "", text: op.text ? op.value : undefined,
                                               html: op.html ? op.value : undefined,
                                               attr: op.attr ? op.name : undefined});
              applyField(el, f, null);
            });
          }
          if (op.remove) qsa(op.remove).forEach(function (el) { el.remove(); });
          if (op.img) {
            var url = photoFor(op.slot);
            if (url) qsa(op.img).forEach(function (el, i) { if (op.all || i === 0) setImg(el, url); });
          }
          if (op.tel) {
            qsa('a[href^="tel:"]').forEach(function (a) {
              if (V.phone_href) a.setAttribute("href", "tel:" + V.phone_href);
            });
            qsa('a[href^="mailto:"]').forEach(function (a) {
              if (V.email) { a.setAttribute("href", "mailto:" + V.email); a.textContent = V.email; }
            });
          }
          if (op.list) applyList(Object.assign({}, op, {item: op.list}), null, null);
          if (op.group) applyGroups(op);
          if (op.gallery) applyGallery(op.gallery);
          if (op.dispatch) qsa(op.dispatch).forEach(function (el) { el.dispatchEvent(new Event(op.event || "scroll")); });
        } catch (e) { console.warn("[demo] шаг не выполнен", op, e); }
      });
      fitAll();
    } finally {
      busy = false;
      if (obs) obs.observe(document.body, {childList: true, subtree: true, characterData: true});
    }
  }

  function start() {
    run();
    setTimeout(run, 400);
    setTimeout(run, 1500);
    if (D.watch) {                      // сайты на React перерисовывают страницу — подставляем снова
      var t = null;
      obs = new MutationObserver(function () {
        if (busy) return;
        clearTimeout(t); t = setTimeout(run, 150);
      });
      obs.observe(document.body, {childList: true, subtree: true, characterData: true});
    }
    window.addEventListener("resize", fitAll);
    window.__demoRun = run;
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
