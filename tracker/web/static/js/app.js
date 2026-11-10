/* Ads screen. Renders the library page the server prepared: window.__LIBRARY__
   carries the first page (see tracker/web/routes/ads.py), and every filter, sort
   or page change asks /api/ads for the next one. Filtering, Version grouping,
   sorting and pagination happen in the database (tracker/db/library.py), so the
   browser only ever holds one page of cards. The view mode (gallery, grid, copy)
   is client-side. Cards link to /ads/<ad_archive_id>. Strings go through _() /
   ngettext() from static/js/i18n.js. */
(function () {
  "use strict";

  var DATA = window.__LIBRARY__ || { cards: [], champions: [], total: 0, page: 0, pages: 1 };
  var $ = function (id) { return document.getElementById(id); };
  var SEARCH_DELAY_MS = 250;

  // ---- helpers ------------------------------------------------------------
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function initials(name) {
    return String(name || "?").trim().split(/\s+/).filter(Boolean)
      .slice(0, 2).map(function (w) { return w[0]; }).join("").toUpperCase() || "?";
  }
  function tier(d) {
    if (d >= 90) return ["ever", _("Evergreen")];
    if (d >= 60) return ["gold", _("Gold")];
    if (d >= 30) return ["win", _("Winner")];
    if (d >= 8) return ["test", _("Testing")];
    return ["new", _("New")];
  }
  function daysLabel(d) { return _("%(days)sd", { days: d }); }
  function pill(d, dark, labelOnly) {
    var t = tier(d), body = labelOnly ? esc(t[1]) : '<span class="d">' + daysLabel(d) + "</span> · " + esc(t[1]);
    return '<span class="pill t-' + t[0] + (dark ? " ondark" : "") + '">' + body + "</span>";
  }
  var mediaIcon = { image: "i-image", video: "i-play", carousel: "i-layers" };
  var mediaLabel = { image: _("Image"), video: _("Video"), carousel: _("Carousel"),
                     catalog: _("Catalog"), dynamic: _("Dynamic creative") };
  // "Carousel · 6 cards", "Catalog · 6 cards", "Image · 3 formats", "Video"
  function kindLabel(a) {
    return mediaLabel[a.kind || a.media] +
      (a.cards > 1 ? " · " + ngettext("%(num)s card", "%(num)s cards", a.cards) : formatsLabel(a));
  }
  // The Page's profile picture (initials when it has none or it can't load).
  function avatar(a) {
    var ph = esc(initials(a.page || a.co));
    return '<span class="tav" title="' + esc(a.page || a.co) + '">' + (a.avatar
      ? '<img src="' + esc(a.avatar) + '" alt="" loading="lazy" data-ph="' + ph + '">'
      : '<span class="ph">' + ph + "</span>") + "</span>";
  }

  function crInner(ad) {
    // data-ph drives the base.html error handler: unreachable media -> monogram.
    if (ad.hero) return '<img src="' + esc(ad.hero) + '" alt="" loading="lazy" data-ph="封面暂不可用">';
    return '<div class="ph">封面暂不可用</div>';
  }
  function countryText(names) {
    names = names || [];
    return names.length > 4 ? names.slice(0, 4).join('、') + ' 等' + names.length + '国' : names.join('、');
  }
  function adFacts(a) {
    var covered = a.coverage_country_names || [], observed = a.observed_country_names || [];
    var reaches = (a.reach_summary || []).filter(function (r) { return r.reach != null; }).map(function (r) {
      var reach = typeof r.reach === 'number' ? r.reach.toLocaleString('zh-CN') : String(r.reach);
      return (r.region_name || r.region) + ' ' + reach;
    });
    var platformName = { meta: 'Meta', tiktok: 'TikTok' }[a.platform] || a.platform || 'Meta';
    return '<div class="adfacts">' +
      '<div><span>平台</span><b>' + esc(platformName) + '</b></div>' +
      '<div><span>上线日期</span><b>' + esc(a.delivery_start || '未公开') + '</b></div>' +
      '<div><span>收录日期</span><b>' + esc(a.first_seen || '未获取') + '</b></div>' +
      '<div title="' + esc(covered.join('、') || '尚未获取公开地区披露') + '"><span>公开覆盖</span><b>' + esc(countryText(covered) || '未获取') + '</b></div>' +
      '<div title="搜索命中国家，不代表完整投放范围"><span>搜索命中</span><b>' + esc(countryText(observed) || '未记录') + '</b></div>' +
      (reaches.length ? '<div title="' + esc(platformName + ' 公开估算值') + '"><span>覆盖人数</span><b>' + esc(reaches.join(' · ')) + '</b></div>' : '') +
      (a.coverage_status === 'failed' ? '<small>覆盖更新失败；已有数据保留</small>' : '') +
      '</div>';
  }

  // ---- state + filter controls -------------------------------------------
  // Default to compact cards so dates/countries are visible before the long preview.
  var initial = new URLSearchParams(location.search);
  var initialStatus = initial.get('status');
  var savedView = 'grid';
  try { var storedView = localStorage.getItem('l2-library-view'); if (['grid','gallery','copy'].indexOf(storedView)>=0) savedView=storedView; } catch (e) {}
  var state = { view: savedView, media: initial.get('media') || "", status: ['active','inactive','all'].indexOf(initialStatus) >= 0 ? initialStatus : "all", dur: initial.get('min_days') || "", q: initial.get('q') || "", collectedApp: initial.get('collected_app') || "", page: DATA.page || 0 };

  function segwire(id, key) {
    var host = $(id);
    if (!host) return;
    host.querySelectorAll("button").forEach(function (b) {
      b.addEventListener("click", function () {
        host.querySelectorAll("button").forEach(function (x) { x.classList.remove("on"); });
        b.classList.add("on");
        state[key] = b.dataset.v;
        refresh();
      });
    });
  }
  segwire("f-media", "media");
  segwire("f-status", "status");
  segwire("f-dur", "dur");
  ['status','media','dur'].forEach(function (key) {
    var host = $(key === 'dur' ? 'f-dur' : 'f-' + key);
    host.querySelectorAll('button').forEach(function (b) { b.classList.toggle('on', b.dataset.v === state[key]); });
  });
  if (initial.get('sort') && ['dur','new','launch','rank'].indexOf(initial.get('sort')) >= 0) $('sort').value = initial.get('sort');

  // Duration: presets clear the custom box; the custom box overrides the presets.
  var durCustom = $("f-dur-custom");
  if (durCustom) {
    $("f-dur").querySelectorAll("button").forEach(function (b) {
      b.addEventListener("click", function () { durCustom.value = ""; });
    });
    durCustom.addEventListener("input", function () {
      var v = durCustom.value.trim();
      $("f-dur").querySelectorAll("button").forEach(function (x) { x.classList.remove("on"); });
      if (v) {
        state.dur = v;
      } else {
        state.dur = "";
        var any = $("f-dur").querySelector('button[data-v=""]');
        if (any) any.classList.add("on");
      }
      refresh();
    });
  }

  $("view").querySelectorAll("button").forEach(function (b) {
    b.classList.toggle('on',b.dataset.v===state.view);
    b.addEventListener("click", function () {
      $("view").querySelectorAll("button").forEach(function (x) { x.classList.remove("on"); });
      b.classList.add("on");
      state.view = b.dataset.v;
      try { localStorage.setItem('l2-library-view',state.view); } catch (err) {}
      renderGrid();
    });
  });

  // Company filter: the companies ticked (templates/_company_filter.html, ticked
  // from ?company= on load); none ticked means every company.
  function companies() {
    return Array.prototype.map.call($("f-company").querySelectorAll("input:checked"),
                                    function (b) { return b.value; });
  }
  $("f-company").addEventListener("change", function () { refresh(); });
  $("sort").addEventListener("change", function () { refresh(); });

  var qbox = $("q"), searchTimer = null;
  if (qbox) {
    qbox.value = state.q;
    qbox.addEventListener("input", function () {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(function () { state.q = qbox.value.trim(); refresh(); }, SEARCH_DELAY_MS);
    });
  }

  // pager
  $("pg-prev").addEventListener("click", function () { goToPage(state.page - 1); });
  $("pg-next").addEventListener("click", function () { goToPage(state.page + 1); });
  function goToPage(page) {
    state.page = page;
    load().then(function () { window.scrollTo({ top: 0, behavior: "smooth" }); });
  }

  $("champ-prev").addEventListener("click", function () { scrollChamps(-1); });
  $("champ-next").addEventListener("click", function () { scrollChamps(1); });
  function scrollChamps(dir) {
    var el = $("champs");
    el.scrollBy({ left: dir * Math.round(el.clientWidth * 0.8), behavior: "smooth" });
  }

  // ---- loading -------------------------------------------------------------
  function query() {
    var p = new URLSearchParams();
    companies().forEach(function (name) { p.append("company", name); });
    if (state.media) p.set("media", state.media);
    p.set("status", state.status);
    if (parseInt(state.dur, 10)) p.set("min_days", parseInt(state.dur, 10));
    if (state.q) p.set("q", state.q);
    if (state.collectedApp) p.set('collected_app',state.collectedApp);
    return p;
  }

  // Any filter or sort change starts from the first page; the pager keeps its page.
  function refresh() { state.page = 0; return load(); }

  var latest = 0;  // only the newest request's answer is painted
  function load() {
    var p = query(), mine = ++latest;
    p.set("sort", $("sort").value);
    p.set("page", Math.max(0, state.page));
    $("grid").setAttribute("aria-busy", "true");
    return fetch("/api/ads?" + p.toString(), { headers: { Accept: "application/json" }, cache: "no-store" })
      .then(function (r) {
        if (r.status === 401) { location.href = "/login"; throw new Error("unauthorized"); }
        return r.json();
      })
      .then(function (data) {
        if (mine !== latest) return;
        data.has_ads = DATA.has_ads;
        DATA = data;
        state.page = data.page;
        render();
      })
      .catch(function () {})
      .then(function () { if (mine === latest) $("grid").removeAttribute("aria-busy"); });
  }

  function rankHint() {
    var hint = $("rank-hint");
    if (!hint) return;
    if ($("sort").value !== "rank") { hint.hidden = true; return; }
    hint.hidden = false;
    hint.textContent = state.status === "inactive"
      ? _("Inactive ads have no impression rank: Meta only ranks ads that are running now.")
      : _("Impression rank is Meta's order of one company's active ads, so #1 means top within that company. It can't be compared across companies, so ads are grouped by company.");
  }

  // ---- render -------------------------------------------------------------
  function href(a) { return "/ads/" + encodeURIComponent(a.id); }
  function topFlag(a) {
    return a.rank === 0
      ? '<span class="rankflag" title="' + esc(_("Most impressions among %(company)s's active ads", { company: a.co })) + '"><svg class="icon fill" style="width:11px;height:11px;color:var(--accent)"><use href="#i-bolt"/></svg> ' + esc(_("Top ad")) + "</span>"
      : "";
  }
  function versionsLabel(a) { return a.versions > 1 ? ngettext("%(num)s version", "%(num)s versions", a.versions) : ""; }
  function formatsLabel(a) { return a.formats > 1 ? " · " + ngettext("%(num)s format", "%(num)s formats", a.formats) : ""; }

  function render() {
    var cos = companies();
    rankHint();
    $("champ-note").textContent = cos.length ? _("Top ads from %(company)s", { company: cos.join(", ") })
                                             : _("Each competitor's longest-running ads");
    // Export CSV downloads what the filters show (see tracker/web/routes/export.py).
    var exp = $("export-csv");
    if (exp) exp.href = "/ads.csv?" + query().toString();
    var summary=$('library-summary');
    if(summary) summary.textContent='当前：' + ({all:'全部状态',active:'仅投放中',inactive:'仅已停投'}[state.status]||state.status) + ' · ' + (DATA.ad_count == null ? DATA.total : DATA.ad_count) + ' 条广告 · ' + DATA.total + ' 个素材卡片（同创意版本合并）' + (state.status==='active' ? '；已停投素材已被隐藏，可切换“全部”查看' : '');
    var address=query(); address.set('sort',$('sort').value); if(state.page) address.set('page',state.page);
    var newAddress=location.pathname+'?'+address.toString()+location.hash;
    if(newAddress!==location.pathname+location.search+location.hash) history.replaceState(null,'',newAddress);

    $("champs").innerHTML = (DATA.champions || []).map(function (a) {
      return '<a class="champ" href="' + href(a) + '">' +
        '<div class="cr">' + crInner(a) + "</div>" +
        '<div class="meta">' +
          '<div style="display:flex;justify-content:space-between;align-items:flex-start">' +
            '<span class="co">' + esc(a.co) + "</span>" + topFlag(a) +
          "</div>" +
          '<div class="big"><div class="d">' + a.days + "<span>" + esc(_("d")) + "</span></div>" +
            '<div style="margin-top:9px">' + pill(a.days, true, true) + '</div><div class="champ-facts">上线 ' + esc(a.delivery_start || '未公开') + '<br>公开覆盖 ' + esc(countryText(a.coverage_country_names) || '未获取') + '</div></div>' +
        "</div></a>";
    }).join("");

    $("count").textContent = DATA.total;
    renderGrid();

    var pager = $("pager");
    if (DATA.pages > 1) {
      pager.hidden = false;
      $("pg-info").textContent = _("Page %(page)s of %(pages)s", { page: DATA.page + 1, pages: DATA.pages }) + " · " +
        ngettext("%(num)s ad", "%(num)s ads", DATA.total);
      $("pg-prev").disabled = DATA.page === 0;
      $("pg-next").disabled = DATA.page >= DATA.pages - 1;
    } else {
      pager.hidden = true;
    }
  }

  function renderGrid() {
    var list = DATA.cards || [], grid = $("grid");
    grid.className = "grid mode-" + state.view;

    if (!list.length) {
      grid.className = "grid";
      grid.innerHTML = '<div class="empty"><h3>' + esc(_("No ads here yet")) + "</h3><p>" +
        esc(DATA.has_ads ? _("Try clearing a filter or switching status.") :
          _("Once a collection run finishes, ads will appear here. Add competitor pages in Settings.")) +
        "</p></div>";
      return;
    }

    if (state.view === "copy") {
      grid.innerHTML = list.map(function (a, i) {
        var rankBit = a.rank != null
          ? '<span>·</span> <span title="' + esc(_("Rank among this company's active ads only")) + '">' + esc(_("#%(rank)s by impressions at %(company)s", { rank: a.rank + 1, company: a.co })) + "</span>"
          : "";
        if (a.versions > 1) rankBit += " <span>·</span> " + versionsLabel(a);
        return '<a class="copyrow" style="animation-delay:' + (i * 22) + 'ms" href="' + href(a) + '">' +
          '<div class="thumb"><div class="cr">' + crInner(a) + "</div></div>" +
          '<div class="body">' +
            '<div class="cometa">' + avatar(a) + esc(a.co) + " " + pill(a.days) + " " + rankBit + "</div>" +
            adFacts(a) +
            "<h3>" + esc(a.title || _("Untitled ad")) + "</h3>" +
            "<p>" + esc(a.body) + "</p>" +
          "</div>" +
          '<div class="side">' +
            (a.cta ? '<span class="tag">' + esc(a.cta) + "</span>" : "") +
            '<button class="copybtn" type="button" data-copy="' + esc(a.body) + '">' +
              '<svg class="icon" style="width:14px;height:14px"><use href="#i-copy"/></svg> ' + esc(_("Copy")) + "</button>" +
          "</div></a>";
      }).join("");
      return;
    }

    // Gallery and grid both show each ad's first image only (a carousel's other
    // cards are on the ad's page), so a page never loads more than its covers.
    grid.innerHTML = list.map(function (a, i) {
      return '<a class="tile" style="animation-delay:' + (i * 20) + 'ms" href="' + href(a) + '">' +
        adFacts(a) +
        '<div class="cr">' + crInner(a) +
          '<span class="mediatag"><svg class="icon ' + (a.media === "video" ? "fill" : "") +
            '" style="width:12px;height:12px"><use href="#' + mediaIcon[a.media] + '"/></svg> ' + esc(kindLabel(a)) + "</span>" +
          (a.versions > 1 ? '<span class="vertag">' + versionsLabel(a) + "</span>" : "") +
          '<div class="crmeta"><span class="co">' + avatar(a) + esc(a.co) + "</span>" + pill(a.days, true) + "</div>" +
        "</div></a>";
    }).join("");
  }

  // delegated clicks inside the grid's <a> rows: the copy button.
  // preventDefault stops the parent <a> from navigating to the detail page.
  $("grid").addEventListener("click", function (e) {
    var btn = e.target.closest("[data-copy]");
    if (btn) {
      e.preventDefault(); e.stopPropagation();
      var text = btn.getAttribute("data-copy") || "";
      if (navigator.clipboard) navigator.clipboard.writeText(text).catch(function () {});
      var label = btn.innerHTML;
      btn.textContent = _("Copied");
      setTimeout(function () { btn.innerHTML = label; }, 1200);
    }
  });

  render();
})();
