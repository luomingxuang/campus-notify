"""在线站点的「收藏」增强层（本地存储版，零账号）。

为什么不用云端 + 登录：
1. 通知页的核心动作是「看」，登录会把第一次访问的人挡在门外，转化代价远大于收益。
2. 收藏是「我这台设备上的待办清单」，本地保存就够用，不必为它引入账号体系。
3. 少一个后端依赖，页面静态托管即可，也不会有登录回跳丢参数、会话过期这类问题。

边界（必须在页面上如实说明，不做跨设备承诺）：
- 收藏存在浏览器 localStorage，换设备/换浏览器/清缓存会丢。
- 隐私模式或无痕窗口可能在关闭后清空。
"""

STYLE = """
  .meta { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
  .fav {
    font: inherit; font-size: 12px; cursor: pointer; flex: none; padding: 3px 10px;
    border-radius: 7px; border: 1px solid #cbd5e1; background: #fff; color: #64748b;
    transition: .15s;
  }
  .fav:hover { border-color: #f59e0b; color: #b45309; background: #fffbeb; }
  .fav.on { border-color: #f59e0b; background: #fffbeb; color: #b45309; font-weight: 600; }
  .fav:focus-visible { outline: 2px solid #4f46e5; outline-offset: 2px; }
  .toast {
    position: fixed; left: 50%; bottom: 34px; transform: translateX(-50%);
    z-index: 60; padding: 9px 16px; border-radius: 9px; font-size: 13px;
    background: #1e293b; color: #fff; box-shadow: 0 8px 24px rgba(15,23,42,.24);
  }
  .toast.err { background: #b91c1c; }
"""

BODY = """<div class="toast" id="toast" hidden></div>
<script>
(function () {
  var KEY = 'campusNotify.favs.v1';
  var favs = {};
  var favOnly = false;
  var toastTimer = null;

  function el(id) { return document.getElementById(id); }

  function readStore() {
    try {
      var raw = localStorage.getItem(KEY);
      var obj = raw ? JSON.parse(raw) : {};
      return obj && typeof obj === 'object' ? obj : {};
    } catch (e) { return {}; }
  }

  function writeStore() {
    try {
      localStorage.setItem(KEY, JSON.stringify(favs));
      return true;
    } catch (e) {
      return false;
    }
  }

  function count() {
    var n = 0;
    for (var k in favs) { if (Object.prototype.hasOwnProperty.call(favs, k)) { n++; } }
    return n;
  }

  function toast(msg, isErr) {
    var t = el('toast');
    if (!t) { return; }
    t.textContent = msg;
    t.className = 'toast' + (isErr ? ' err' : '');
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { t.hidden = true; }, 2600);
  }

  function paint() {
    var btns = document.querySelectorAll('.fav');
    for (var i = 0; i < btns.length; i++) {
      var btn = btns[i];
      var url = btn.getAttribute('data-url');
      var on = !!favs[url];
      btn.textContent = on ? '★ 已收藏' : '☆ 收藏';
      btn.setAttribute('aria-pressed', on ? 'true' : 'false');
      btn.className = 'fav' + (on ? ' on' : '');
      var card = btn.closest ? btn.closest('.card') : null;
      if (card) { card.setAttribute('data-isfav', on ? '1' : '0'); }
    }
    var f = el('favFilter');
    if (f) {
      f.textContent = (favOnly ? '★' : '☆') + ' 我的收藏（' + count() + '）';
      f.className = 'chip' + (favOnly ? ' active' : '');
    }
  }

  function applyFilter() {
    if (typeof window.__panelSetFavOnly === 'function') {
      window.__panelSetFavOnly(favOnly);
    }
  }

  function toggle(btn) {
    var url = btn.getAttribute('data-url');
    if (!url) { return; }
    if (favs[url]) {
      delete favs[url];
    } else {
      favs[url] = {
        title: btn.getAttribute('data-title') || '',
        site: btn.getAttribute('data-site') || '',
        deadline: btn.getAttribute('data-deadline') || '',
        saved_at: new Date().toISOString().slice(0, 16).replace('T', ' ')
      };
    }
    if (!writeStore()) {
      toast('这台浏览器不允许保存本地数据（可能是无痕窗口），收藏没存住', true);
    } else {
      toast(favs[url] ? '已收藏，只存在这台设备上' : '已取消收藏');
    }
    paint();
    applyFilter();
  }

  function boot() {
    favs = readStore();
    paint();

    var ff = el('favFilter');
    if (ff) {
      ff.addEventListener('click', function () {
        favOnly = !favOnly;
        paint();
        applyFilter();
        if (favOnly && count() === 0) {
          toast('还没有收藏：点卡片右下角「☆ 收藏」把要办的事攒在这里');
        }
      });
    }

    document.addEventListener('click', function (e) {
      var t = e.target;
      var btn = t && t.closest ? t.closest('.fav') : null;
      if (btn) { e.preventDefault(); toggle(btn); }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
</script>"""


def fav_css() -> str:
    return STYLE


def fav_body() -> str:
    return BODY
