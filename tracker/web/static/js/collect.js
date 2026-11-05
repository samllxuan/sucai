(function () {
  'use strict';
  var form = document.getElementById('collect-form');
  function scope() {
    var select = document.getElementById('collect-country');
    var count = Number(select.options[select.selectedIndex].dataset.count || 1);
    var pages = form.querySelector('input[name="mode"]:checked').value === 'pages';
    var targets = pages ? form.querySelectorAll('input[name="page_ids"]:checked').length : 1;
    if (pages && document.getElementById('new-page').value.trim()) targets += 1;
    var limit = Number(document.getElementById('max-ads').value || 30);
    var days = document.getElementById('launch-days');
    var order = document.getElementById('collection-sort');
    document.getElementById('scope-summary').textContent =
      '范围：' + (count > 1 ? '欧洲 ' + count + ' 国' : select.options[select.selectedIndex].textContent) +
      ' · 预计 ' + count * targets + ' 次搜索 · 每次最多读取 ' + limit + ' 条，跨国重复广告合并后入库。' +
      '上线范围：' + days.options[days.selectedIndex].textContent + '；' + order.options[order.selectedIndex].textContent + '。' +
      (count > 1 ? '开始后及后续自动采集都会使用此区域，逐国进度见下方。' : '');
    document.getElementById('europe-country-list').hidden = select.value !== 'EUROPE';
  }
  function mode() {
    var pages = form.querySelector('input[name="mode"]:checked').value === 'pages';
    ['keyword-target', 'page-target'].forEach(function (id) {
      var el = document.getElementById(id), enabled = (id === 'page-target') === pages;
      el.hidden = !enabled;
      el.disabled = !enabled;
    });
    scope();
  }
  form.querySelectorAll('input[name="mode"]').forEach(function (el) { el.addEventListener('change', mode); });
  form.addEventListener('input', scope);
  form.addEventListener('change', scope);
  mode();
})();
