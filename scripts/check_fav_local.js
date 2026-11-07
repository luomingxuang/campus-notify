/**
 * 收藏逻辑回归测试（无需浏览器）。
 *
 * 用最小 DOM / localStorage mock 跑 fav_local.py 注入的那段脚本，验证：
 * 1. 初始无收藏
 * 2. 点一次收藏 → 计数 +1、按钮变「★ 已收藏」、写进 localStorage
 * 3. 再点一次 → 取消，计数归零
 * 4. 重新载入脚本（模拟刷新页面）→ 从 localStorage 恢复为已收藏
 * 5. 「我的收藏」筛选开关会调用面板的 __panelSetFavOnly
 *
 * 用法：node scripts/check_fav_local.js
 */
const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const html = fs.readFileSync(
  path.join(ROOT, '..', '..', '4-交付物', '在线站点', 'index.html'),
  'utf8'
);

// 收集页面里的内联脚本（收藏逻辑按特征定位，见下）
const blocks = [...html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g)].map(
  (m) => m[1]
);
if (blocks.length < 2) {
  console.error('FAIL: 页面里找不到内联脚本');
  process.exit(1);
}
// 按特征定位收藏脚本（页面里还有其他内联脚本，不能只取最后一段）
const favCode = blocks.find((b) => b.indexOf('campusNotify.favs.v1') !== -1);
if (!favCode) {
  console.error('FAIL: 页面里找不到收藏脚本');
  process.exit(1);
}

function makeEl(attrs) {
  const el = {
    attrs: Object.assign({}, attrs),
    textContent: '☆ 收藏',
    className: 'fav',
    hidden: false,
    _handlers: {},
    getAttribute(k) {
      return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null;
    },
    setAttribute(k, v) {
      this.attrs[k] = String(v);
    },
    addEventListener(t, fn) {
      (this._handlers[t] = this._handlers[t] || []).push(fn);
    },
    click() {
      (this._handlers.click || []).forEach((fn) => fn({ target: this, preventDefault() {} }));
      (docHandlers.click || []).forEach((fn) => fn({ target: this, preventDefault() {} }));
    },
    closest(sel) {
      if (sel === '.fav' && (this.className || '').split(/\s+/).indexOf('fav') !== -1) return this;
      if (sel === '.card') return this.card || null;
      return null;
    }
  };
  return el;
}

let docHandlers = {};
let favBtns = [];
let filterBtn = null;
let toastEl = null;
let store = {};
let panelFavOnlyCalls = [];

function buildDom() {
  docHandlers = {};
  panelFavOnlyCalls = [];
  favBtns = [];

  function buildCard(url, title, site, deadline) {
    const card = { dataset: {}, attrs: {}, setAttribute(k, v) { this.attrs[k] = v; } };
    const btn = makeEl({
      'data-url': url,
      'data-title': title,
      'data-site': site,
      'data-deadline': deadline,
      'aria-pressed': 'false'
    });
    btn.card = card;
    favBtns.push(btn);
    return card;
  }

  buildCard('https://jwc.example.edu/a.htm', '关于开展期中教学检查的通知', '教务处', '2026-11-10');
  buildCard('https://xsc.example.edu/b.htm', '国家助学金申请通知', '学生处', '2026-11-20');

  filterBtn = makeEl({ id: 'favFilter' });
  filterBtn.textContent = '☆ 我的收藏（0）';
  filterBtn.className = 'chip';
  toastEl = makeEl({ id: 'toast' });
  toastEl.hidden = true;
}

const documentMock = {
  readyState: 'complete',
  getElementById(id) {
    if (id === 'favFilter') return filterBtn;
    if (id === 'toast') return toastEl;
    return null;
  },
  querySelectorAll(sel) {
    return sel === '.fav' ? favBtns : [];
  },
  addEventListener(t, fn) {
    (docHandlers[t] = docHandlers[t] || []).push(fn);
  }
};

const windowMock = {
  __panelSetFavOnly(on) {
    panelFavOnlyCalls.push(!!on);
  }
};

function runScript() {
  const fn = new Function(
    'document',
    'window',
    'localStorage',
    'setTimeout',
    'clearTimeout',
    favCode
  );
  fn(
    documentMock,
    windowMock,
    {
      getItem: (k) => (Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null),
      setItem: (k, v) => { store[k] = String(v); },
      removeItem: (k) => { delete store[k]; }
    },
    (fn2) => fn2(), // 立即执行，避免 toast 定时器挂住进程
    () => {}
  );
}

function favCount() {
  const raw = store['campusNotify.favs.v1'];
  return raw ? Object.keys(JSON.parse(raw)).length : 0;
}

let failed = 0;
function check(name, cond, extra) {
  if (cond) {
    console.log(`  PASS  ${name}`);
  } else {
    failed += 1;
    console.log(`  FAIL  ${name}${extra ? ' — ' + extra : ''}`);
  }
}

console.log('[1] 首次载入，无收藏');
buildDom();
runScript();
check('计数为 0', filterBtn.textContent.includes('（0）'), filterBtn.textContent);
check('按钮文案为未收藏', favBtns[0].textContent === '☆ 收藏', favBtns[0].textContent);
check('卡片 data-isfav=0', favBtns[0].card.attrs['data-isfav'] === '0');

console.log('[2] 点第一条收藏');
favBtns[0].click();
check('本地存储写入 1 条', favCount() === 1, '实际 ' + favCount());
check('按钮变已收藏', favBtns[0].textContent === '★ 已收藏', favBtns[0].textContent);
check('卡片 data-isfav=1', favBtns[0].card.attrs['data-isfav'] === '1');
check('计数变 1', filterBtn.textContent.includes('（1）'), filterBtn.textContent);
check('存的标题正确', (() => {
  const raw = store['campusNotify.favs.v1'];
  if (!raw) return false;
  const row = JSON.parse(raw)['https://jwc.example.edu/a.htm'];
  return !!row && row.title.indexOf('期中') >= 0;
})());

console.log('[3] 再点一次取消');
favBtns[0].click();
check('存储清空', favCount() === 0, '实际 ' + favCount());
check('按钮回未收藏', favBtns[0].textContent === '☆ 收藏', favBtns[0].textContent);

console.log('[4] 收藏后刷新页面（重新载入脚本）');
favBtns[0].click();
buildDom(); // DOM 重来，但 localStorage 保留
runScript();
check('刷新后仍是已收藏', favBtns[0].textContent === '★ 已收藏', favBtns[0].textContent);
check('刷新后计数为 1', filterBtn.textContent.includes('（1）'), filterBtn.textContent);

console.log('[5] 「我的收藏」筛选开关');
filterBtn.addEventListener('click', () => {});
filterBtn._handlers.click = filterBtn._handlers.click || [];
filterBtn.click();
check('面板收到 favOnly=true', panelFavOnlyCalls[panelFavOnlyCalls.length - 1] === true,
  JSON.stringify(panelFavOnlyCalls));
check('筛选按钮变为 active', filterBtn.className.indexOf('active') >= 0, filterBtn.className);
filterBtn.click();
check('再次点击关闭筛选', panelFavOnlyCalls[panelFavOnlyCalls.length - 1] === false,
  JSON.stringify(panelFavOnlyCalls));

console.log(failed === 0 ? '\n全部通过' : `\n${failed} 项失败`);
process.exit(failed === 0 ? 0 : 1);
