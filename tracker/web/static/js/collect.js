(function () {
  'use strict';
  var form = document.getElementById('collect-form');
  var country = document.getElementById('collect-country');
  var launchDays = document.getElementById('launch-days');
  var metaCountry = '';

  function platform() {
    var checked = form.querySelector('input[name="collect_platform"]:checked');
    return checked ? checked.value : 'meta';
  }

  function setField(id, disabled, hidden) {
    var el = document.getElementById(id);
    if (!el) return;
    el.disabled = disabled;
    el.hidden = hidden;
  }

  function setNotes(isTikTok) {
    ['meta-window-note', 'meta-scope-note'].forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.hidden = isTikTok;
    });
    ['tiktok-target-note', 'tiktok-scope-note'].forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.hidden = !isTikTok;
    });
  }

  function setLaunchLabel() {
    Array.prototype.forEach.call(launchDays.options, function (option) {
      var label = option.dataset[platform() === 'tiktok' ? 'tiktokLabel' : 'metaLabel'];
      if (label) option.textContent = label;
    });
  }

  function setCountries(isTikTok) {
    var region = document.getElementById('region-countries');
    if (isTikTok) {
      if (country.value) metaCountry = country.value;
      region.disabled = true;
      region.hidden = true;
      Array.prototype.forEach.call(country.options, function (option) {
        if (option.parentElement === region) return;
        var supported = option.dataset.tiktok === '1';
        option.disabled = !supported;
        option.hidden = !supported;
      });
      if (!country.selectedOptions[0] || country.selectedOptions[0].dataset.tiktok !== '1') {
        country.value = 'GB';
      }
    } else {
      region.disabled = false;
      region.hidden = false;
      Array.prototype.forEach.call(country.options, function (option) {
        option.disabled = false;
        option.hidden = false;
      });
      if (metaCountry) country.value = metaCountry;
    }
    document.getElementById('europe-country-list').hidden =
      isTikTok || country.value !== 'EUROPE';
  }

  function scope() {
    var isTikTok = platform() === 'tiktok';
    var count = isTikTok ? 1 : Number(country.options[country.selectedIndex].dataset.count || 1);
    var pages = form.querySelector('input[name="mode"]:checked').value === 'pages';
    var targets = pages ? form.querySelectorAll('input[name="page_ids"]:checked').length : 1;
    if (pages && document.getElementById('new-page').value.trim()) targets += 1;
    var limit = Number(document.getElementById('max-ads').value || 30);
    var order = document.getElementById('collection-sort');
    if (isTikTok) {
      document.getElementById('scope-summary').textContent =
        '范围：TikTok 公开广告库 · ' + country.options[country.selectedIndex].textContent +
        ' · 完整短语匹配 · 每次最多读取 ' + limit + ' 条。上线范围：' +
        launchDays.options[launchDays.selectedIndex].textContent + '。';
    } else {
      document.getElementById('scope-summary').textContent =
        '范围：' + (count > 1 ? '欧洲 ' + count + ' 国' : country.options[country.selectedIndex].textContent) +
        ' · 预计 ' + count * targets + ' 次搜索 · 每次最多读取 ' + limit + ' 条，跨国重复广告合并后入库。' +
        '上线范围：' + launchDays.options[launchDays.selectedIndex].textContent + '；' +
        order.options[order.selectedIndex].textContent + '。' +
        (count > 1 ? '开始后及后续自动采集都会使用此区域，逐国进度见下方。' : '');
    }
    document.getElementById('europe-country-list').hidden =
      isTikTok || country.value !== 'EUROPE';
  }

  function mode() {
    var isTikTok = platform() === 'tiktok';
    if (isTikTok) {
      var keywordMode = form.querySelector('input[name="mode"][value="keyword"]');
      keywordMode.checked = true;
    }
    var pages = form.querySelector('input[name="mode"]:checked').value === 'pages';
    ['keyword-target', 'page-target'].forEach(function (id) {
      var el = document.getElementById(id), enabled = (id === 'page-target') === pages;
      el.hidden = !enabled;
      el.disabled = !enabled;
    });
    var pagesMode = form.querySelector('input[name="mode"][value="pages"]');
    pagesMode.disabled = isTikTok;
    var match = document.getElementById('match-mode');
    match.querySelector('option[value="broad"]').disabled = isTikTok;
    if (isTikTok) match.value = 'exact';
    setField('sort-field', isTikTok, isTikTok);
    setField('status-field', isTikTok, isTikTok);
    scope();
  }

  function selectPlatform() {
    var isTikTok = platform() === 'tiktok';
    setCountries(isTikTok);
    setNotes(isTikTok);
    setLaunchLabel();
    mode();
  }

  form.querySelectorAll('input[name="mode"]').forEach(function (el) { el.addEventListener('change', mode); });
  form.querySelectorAll('input[name="collect_platform"]').forEach(function (el) {
    el.addEventListener('change', selectPlatform);
  });
  form.addEventListener('input', scope);
  form.addEventListener('change', scope);
  selectPlatform();
})();
