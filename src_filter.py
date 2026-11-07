"""在线站点的「关注来源」筛选层（本地存储版，零账号）。

要解决的问题：站点从 4 个扩到 26 个后，全塞进一个列表就是信息过载 ——
现有样本里面向教师的占 29%，还有大量招标采购、科研申报，对普通学生是噪音。

设计取舍：
1. **默认窄**：一进来只显示校级公告 + 5 个学生最相关的部门（教务/学生处/团委/招生就业/后勤）。
2. **学院要自己选**：20 个学院默认全关，用户选了自己的学院才加进来。这一条最关键 ——
   计科学生不需要看音乐学院的演出通知。
3. **按学院分享**：链接带 ?dept=jxxy 打开就是「校级 + 计科」视图，各院群里发对应链接，
   谁都不会看到无关内容。URL 参数不改写本地偏好，避免点别人链接把自己的设置改掉。
4. 选择存 localStorage，不登录、不联网。
"""

import json

TEMPLATE = """<style>
  .src-btn { white-space: nowrap; }
  .src-overlay {
    position: fixed; inset: 0; z-index: 55; display: flex;
    align-items: center; justify-content: center; padding: 20px;
    background: rgba(15,23,42,.55);
  }
  .src-card {
    background: #fff; border-radius: 15px; width: 100%; max-width: 460px;
    max-height: 82vh; display: flex; flex-direction: column;
    box-shadow: 0 18px 50px rgba(15,23,42,.3);
  }
  .src-head { padding: 18px 20px 12px; border-bottom: 1px solid #e2e8f0; }
  .src-head h3 { margin: 0 0 4px; font-size: 15px; color: #1e293b; }
  .src-head p { margin: 0; font-size: 12px; color: #64748b; line-height: 1.6; }
  .src-body { padding: 14px 20px; overflow-y: auto; flex: 1; }
  .src-group { margin-bottom: 16px; }
  .src-group:last-child { margin-bottom: 4px; }
  .src-group h4 {
    margin: 0 0 8px; font-size: 12px; font-weight: 600; color: #475569;
    display: flex; align-items: center; gap: 6px;
  }
  .src-group h4 span { font-weight: 400; color: #94a3b8; }
  .src-grid { display: flex; flex-wrap: wrap; gap: 6px; }
  .src-item {
    display: inline-flex; align-items: center; gap: 5px; cursor: pointer;
    font-size: 12px; padding: 5px 10px; border-radius: 7px;
    border: 1px solid #cbd5e1; background: #fff; color: #475569;
    user-select: none; transition: .12s;
  }
  .src-item:hover { border-color: #818cf8; }
  .src-item.on { border-color: #4f46e5; background: #eef2ff; color: #3730a3; font-weight: 600; }
  .src-item input { margin: 0; width: 12px; height: 12px; accent-color: #4f46e5; }
  .src-college { margin-bottom: 16px; }
  .src-college label { display: block; font-size: 12px; font-weight: 600; color: #475569; margin-bottom: 6px; }
  .src-college select {
    width: 100%; font: inherit; font-size: 13px; padding: 8px 10px;
    border-radius: 8px; border: 1px solid #cbd5e1; background: #fff; color: #1e293b;
  }
  .src-hint { font-size: 11.5px; color: #94a3b8; margin: 6px 0 0; }
  .src-foot {
    padding: 12px 20px; border-top: 1px solid #e2e8f0;
    display: flex; gap: 8px; align-items: center;
  }
  .src-foot .spacer { flex: 1; }
  .src-foot button {
    font: inherit; font-size: 12.5px; cursor: pointer; padding: 7px 14px;
    border-radius: 8px; border: 1px solid #cbd5e1; background: #fff; color: #475569;
  }
  .src-foot button:hover { border-color: #818cf8; color: #4338ca; }
  .src-foot button.primary { background: #4f46e5; border-color: #4f46e5; color: #fff; }
  .src-foot button.primary:hover { background: #4338ca; }
  .src-banner {
    margin: 10px auto 0; max-width: 860px; padding: 9px 14px; border-radius: 9px;
    background: #eef2ff; border: 1px solid #c7d2fe; font-size: 12.5px; color: #3730a3;
    display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  }
  .src-banner button {
    font: inherit; font-size: 12px; cursor: pointer; padding: 4px 10px;
    border-radius: 7px; border: 1px solid #818cf8; background: #fff; color: #4338ca;
  }
</style>

<div class="src-overlay" id="srcOverlay" hidden>
  <div class="src-card" role="dialog" aria-modal="true" aria-labelledby="srcTitle">
    <div class="src-head">
      <h3 id="srcTitle">关注哪些来源</h3>
      <p>先选你的学院，本院通知才会出现。其余单位可按需勾选。</p>
    </div>
    <div class="src-body">
      <div class="src-college">
        <label for="myCollege">我的学院</label>
        <select id="myCollege"></select>
        <p class="src-hint">选了就自动把该学院加入关注；辅修或双学位可以在下面多选。</p>
      </div>
      <div id="srcGroups"></div>
    </div>
    <div class="src-foot">
      <button id="srcAll">全选</button>
      <button id="srcReset">恢复默认</button>
      <span class="spacer"></span>
      <button class="primary" id="srcDone">完成</button>
    </div>
  </div>
</div>

<div class="src-banner" id="srcBanner" hidden>
  <span id="srcBannerText"></span>
  <button id="srcBannerBtn">设为我的学院</button>
</div>

<script>
(function () {
  var KEY = 'campusNotify.sources.v1';
  var SITES = __SITES_JSON__;
  var DEFAULTS = __DEFAULTS__;
  var GROUP_ORDER = ['校级', '部门', '学院'];
  var GROUP_TITLE = { '校级': '校级公共', '部门': '职能部门', '学院': '教学院部' };

  var byId = {};
  SITES.forEach(function (s) { byId[s.id] = s; });

  var state = loadState();
  var urlDept = '';

  function el(id) { return document.getElementById(id); }

  function loadState() {
    try {
      var raw = localStorage.getItem(KEY);
      if (raw) {
        var o = JSON.parse(raw);
        if (o && Object.prototype.toString.call(o.selected) === '[object Array]') {
          return { selected: o.selected.slice(), college: o.college || '' };
        }
      }
    } catch (e) { /* 忽略，用默认值 */ }
    return { selected: DEFAULTS.slice(), college: '' };
  }

  function saveState() {
    try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (e) { /* 无痕模式忽略 */ }
  }

  function has(id) { return state.selected.indexOf(id) !== -1; }

  function toggle(id, on) {
    var i = state.selected.indexOf(id);
    if (on && i === -1) { state.selected.push(id); }
    if (!on && i !== -1) { state.selected.splice(i, 1); }
  }

  function reapply() {
    if (typeof window.__panelReapply === 'function') { window.__panelReapply(); }
    updateBtn();
    syncInputs();
  }

  function updateBtn() {
    var btn = el('srcBtn');
    if (!btn) { return; }
    var n = state.selected.length;
    var label = '来源：';
    if (urlDept) {
      label += '校级 + ' + (byId[urlDept] ? byId[urlDept].short : urlDept);
    } else if (n === 0) {
      label += '未选';
    } else if (n >= SITES.length) {
      label += '全部 ' + n + ' 个';
    } else {
      label += n + ' 个';
    }
    btn.textContent = label;
    btn.className = 'chip' + (n > 0 && n < SITES.length ? ' active' : '');
  }

  function syncInputs() {
    var boxes = document.querySelectorAll('.src-item input');
    for (var i = 0; i < boxes.length; i++) {
      var id = boxes[i].getAttribute('data-id');
      var on = has(id);
      boxes[i].checked = on;
      var wrap = boxes[i].closest('.src-item');
      if (wrap) { wrap.className = 'src-item' + (on ? ' on' : ''); }
    }
    var sel = el('myCollege');
    if (sel) { sel.value = state.college || ''; }
  }

  function buildPanel() {
    var groups = {};
    SITES.forEach(function (s) {
      (groups[s.group] = groups[s.group] || []).push(s);
    });

    var sel = el('myCollege');
    sel.innerHTML = '<option value="">— 还没选 —</option>' + SITES.filter(function (s) {
      return s.group === '学院';
    }).map(function (s) {
      return '<option value="' + s.id + '">' + s.name + '</option>';
    }).join('');
    sel.addEventListener('change', function () {
      var id = sel.value;
      if (state.college && state.college !== id) {
        // 换学院时把旧学院移出关注，避免越选越多
        var old = state.college;
        var others = SITES.filter(function (s) {
          return s.group === '学院' && s.id !== old && has(s.id);
        });
        if (others.length === 0) { toggle(old, false); }
      }
      state.college = id;
      if (id) { toggle(id, true); }
      saveState();
      reapply();
    });

    var html = '';
    GROUP_ORDER.forEach(function (g) {
      if (!groups[g]) { return; }
      html += '<div class="src-group"><h4>' + GROUP_TITLE[g] +
        ' <span>' + groups[g].length + ' 个</span></h4><div class="src-grid">';
      groups[g].forEach(function (s) {
        html += '<label class="src-item" data-for="' + s.id + '">' +
          '<input type="checkbox" data-id="' + s.id + '">' + s.short + '</label>';
      });
      html += '</div></div>';
    });
    el('srcGroups').innerHTML = html;

    el('srcGroups').addEventListener('change', function (e) {
      var t = e.target;
      if (t && t.getAttribute('data-id')) {
        toggle(t.getAttribute('data-id'), t.checked);
        saveState();
        reapply();
      }
    });

    el('srcAll').addEventListener('click', function () {
      state.selected = SITES.map(function (s) { return s.id; });
      saveState();
      reapply();
    });

    el('srcReset').addEventListener('click', function () {
      state.selected = DEFAULTS.slice();
      state.college = '';
      saveState();
      reapply();
    });

    el('srcDone').addEventListener('click', function () { closePanel(); });
    el('srcOverlay').addEventListener('click', function (e) {
      if (e.target === el('srcOverlay')) { closePanel(); }
    });
    // 手机上误触遮罩、电脑上按 ESC 都该能退出去
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && !el('srcOverlay').hidden) { closePanel(); }
    });
  }

  function openPanel() {
    el('srcOverlay').hidden = false;
    document.body.style.overflow = 'hidden';
    syncInputs();
  }

  function closePanel() {
    el('srcOverlay').hidden = true;
    document.body.style.overflow = '';
  }

  function handleUrlDept() {
    var q = new URLSearchParams(location.search);
    var dept = q.get('dept');
    if (!dept || !byId[dept] || byId[dept].group !== '学院') { return false; }
    urlDept = dept;
    // 只影响本次浏览，不写本地偏好 —— 点别人的分享链接不该改掉自己的设置
    if (!has(dept)) { state.selected = state.selected.concat([dept]); }
    var banner = el('srcBanner');
    var text = el('srcBannerText');
    if (banner && text) {
      text.textContent = '正在看「' + byId[dept].name + '」视图。想以后都默认这样？';
      banner.hidden = false;
      el('srcBannerBtn').textContent = '设为我的学院';
      el('srcBannerBtn').addEventListener('click', function () {
        state.college = dept;
        toggle(dept, true);
        saveState();
        banner.hidden = true;
        if (typeof window.__toast === 'function') { window.__toast('已设为我的学院'); }
      });
    }
    return true;
  }

  // 还没选学院时给一次引导：否则用户会以为「本院通知一条都没有」
  function setupFirstVisitHint() {
    if (urlDept || state.college) { return; }
    var banner = el('srcBanner');
    var text = el('srcBannerText');
    var btn = el('srcBannerBtn');
    if (!banner || !text || !btn) { return; }
    text.textContent = '默认只显示校级通知。选上你的学院，本院通知才会一起出现。';
    btn.textContent = '选我的学院';
    banner.hidden = false;
    btn.addEventListener('click', function () {
      banner.hidden = true;
      openPanel();
    });
  }

  function matches(siteId) {
    return has(siteId);
  }

  function boot() {
    buildPanel();
    var fromUrl = handleUrlDept();
    window.__srcFilter = { matches: matches };
    reapply();
    if (!fromUrl) { setupFirstVisitHint(); }

    var btn = el('srcBtn');
    if (btn) { btn.addEventListener('click', openPanel); }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
</script>"""


def bundle(sites: list[dict], default_ids: list[str]) -> str:
    """生成关注来源面板（样式 + DOM + 脚本）。"""
    slim = [
        {
            "id": s["id"],
            "name": s["name"],
            "short": s.get("short") or s["name"],
            "group": s.get("group", "部门"),
        }
        for s in sites
    ]
    return (
        TEMPLATE.replace("__SITES_JSON__", json.dumps(slim, ensure_ascii=False))
        .replace("__DEFAULTS__", json.dumps(default_ids, ensure_ascii=False))
    )
