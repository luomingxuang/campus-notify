/**
 * 「关注来源」筛选逻辑回归测试（无需浏览器）。
 *
 * 用最小 DOM / localStorage / location mock 跑 src_filter.py 注入的脚本，验证：
 * 1. 首次打开（无本地存储）→ 默认只有校级 + 5 个部门，20 个学院全关
 * 2. 选「我的学院」→ 该学院进入关注，其余学院仍关闭
 * 3. 换学院 → 旧学院自动退出，不会越选越多
 * 4. 重新载入（模拟刷新）→ 选择从 localStorage 恢复
 * 5. 链接带 ?dept=xxx → 该学院可见，但**不写入本地偏好**（不污染对方设置）
 * 6. 全选 / 恢复默认
 *
 * 用法：node scripts/check_src_filter.js
 */
const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const html = fs.readFileSync(
  path.join(ROOT, '..', '..', '4-交付物', '在线站点', 'index.html'),
  'utf8'
);

const blocks = [...html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g)].map(
  (m) => m[1]
);
const srcCode = blocks.find((b) => b.indexOf('campusNotify.sources.v1') !== -1);
if (!srcCode) {
  console.error('FAIL: 页面里找不到来源筛选脚本');
  process.exit(1);
}

const KEY = 'campusNotify.sources.v1';

function makeEl() {
  const el = {
    innerHTML: '',
    textContent: '',
    className: '',
    hidden: false,
    value: '',
    checked: false,
    _h: {},
    addEventListener(t, fn) { (this._h[t] = this._h[t] || []).push(fn); },
    fire(t, ev) { (this._h[t] || []).forEach((fn) => fn(ev || { target: this })); },
    getAttribute() { return null; },
    closest() { return null; },
    setAttribute() {}
  };
  return el;
}

let els = {};
let store = {};
let reapplyCount = 0;
let locationMock = { search: '' };

function buildDom() {
  els = {};
  ['srcOverlay', 'srcGroups', 'srcAll', 'srcReset', 'srcDone', 'srcBtn',
    'srcBanner', 'srcBannerText', 'srcBannerBtn', 'myCollege'].forEach((id) => {
    els[id] = makeEl();
  });
  els.srcBanner.hidden = true;
  els.srcOverlay.hidden = true;
}

const documentMock = {
  readyState: 'complete',
  getElementById: (id) => els[id] || null,
  querySelectorAll: () => [],
  addEventListener() {}
};

const windowMock = {
  __panelReapply() { reapplyCount += 1; }
};

function runScript() {
  const fn = new Function(
    'document', 'window', 'localStorage', 'location', '__URLSearchParams', srcCode
  );
  fn(
    documentMock,
    windowMock,
    {
      getItem: (k) => (Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null),
      setItem: (k, v) => { store[k] = String(v); },
      removeItem: (k) => { delete store[k]; }
    },
    locationMock,
    URLSearchParams
  );
}

function saved() {
  const raw = store[KEY];
  return raw ? JSON.parse(raw) : null;
}

function selectedIds() {
  const s = saved();
  return s ? s.selected : [];
}

let failed = 0;
function check(name, cond, extra) {
  if (cond) {
    console.log(`  PASS  ${name}`);
  } else {
    failed += 1;
    console.log(`  FAIL  ${name}${extra !== undefined ? ' — ' + extra : ''}`);
  }
}

const DEFAULTS = ['school', 'jwc', 'xsc', 'tw', 'zsjyc', 'hqglc'];

console.log('[1] 首次打开：默认只看校级 + 部门');
store = {};
locationMock.search = '';
buildDom();
runScript();
const f1 = windowMock.__srcFilter;
check('来源过滤器已就绪', !!f1 && typeof f1.matches === 'function');
check('校级公告可见', f1.matches('school') === true);
check('教务处在内', f1.matches('jwc') === true);
check('后勤在内', f1.matches('hqglc') === true);
check('计科院默认不可见', f1.matches('jxxy') === false);
check('音乐学院默认不可见', f1.matches('yyxy') === false);
check('未写本地存储（首次不落盘）', store[KEY] === undefined, store[KEY]);
check('按钮文案含来源', els.srcBtn.textContent.indexOf('6 个') !== -1, els.srcBtn.textContent);

console.log('[2] 选我的学院 = 计科院');
els.myCollege.value = 'jxxy';
els.myCollege.fire('change');
check('计科院变为可见', windowMock.__srcFilter.matches('jxxy') === true);
check('已写入本地存储', (selectedIds() || []).indexOf('jxxy') !== -1, JSON.stringify(selectedIds()));
check('记录了 college', saved() && saved().college === 'jxxy', saved() && saved().college);
check('其他学院仍未开启', windowMock.__srcFilter.matches('kjxy') === false);
check('校级仍在', windowMock.__srcFilter.matches('school') === true);

console.log('[3] 换学院 = 金融学院');
els.myCollege.value = 'jrxy';
els.myCollege.fire('change');
const after = selectedIds() || [];
check('金融已加入', after.indexOf('jrxy') !== -1, JSON.stringify(after));
check('计科已移除（不会越选越多）', after.indexOf('jxxy') === -1, JSON.stringify(after));
check('金融可见', windowMock.__srcFilter.matches('jrxy') === true);

console.log('[4] 刷新页面：从本地存储恢复');
buildDom();
runScript();
check('恢复后金融仍可见', windowMock.__srcFilter.matches('jrxy') === true);
check('恢复后计科仍关闭', windowMock.__srcFilter.matches('jxxy') === false);
check('恢复后我的学院是金融', els.myCollege.value === 'jrxy', els.myCollege.value);

console.log('[5] 别人发来的链接 ?dept=fxy');
const before = JSON.stringify(saved());
locationMock.search = '?sharecode=RQJWJJPBX4&dept=fxy';
buildDom();
runScript();
check('法学院临时可见', windowMock.__srcFilter.matches('fxy') === true);
check('未污染本地偏好', JSON.stringify(saved()) === before, `${before} -> ${JSON.stringify(saved())}`);
check('提示条已显示', els.srcBanner.hidden === false);
check('提示文案含学院名', els.srcBannerText.textContent.indexOf('法学院') !== -1,
  els.srcBannerText.textContent);
els.srcBannerBtn.fire('click');
check('点「设为我的学院」后落盘', (selectedIds() || []).indexOf('fxy') !== -1,
  JSON.stringify(selectedIds()));

console.log('[6] 全选与恢复默认');
locationMock.search = '';
buildDom();
runScript();
els.srcAll.fire('click');
check('全选后 26 个来源都可见',
  ['school', 'jxxy', 'yyxy', 'mkszyxy', 'hqglc'].every((id) => windowMock.__srcFilter.matches(id)));
check('全选落盘 26 条', (selectedIds() || []).length === 26, (selectedIds() || []).length);
els.srcReset.fire('click');
check('恢复默认后只剩 6 个', (selectedIds() || []).length === DEFAULTS.length,
  JSON.stringify(selectedIds()));
check('恢复默认后学院关闭', windowMock.__srcFilter.matches('jxxy') === false);
check('筛选被重新应用过', reapplyCount > 0, reapplyCount);

// 弹层能关掉，靠的是「[hidden] 压过组件自己的 display」这条 CSS。
// 之前缺这条，.src-overlay 的 display:flex 直接盖掉浏览器默认的 [hidden]{display:none}，
// 导致面板一打开就贴在页面上，点「完成」也没反应。下面四项任一项挂掉都会复现。
console.log('[7] 弹层可关闭（CSS 兜底）');
const overlayCss = (html.match(/\.src-overlay\s*\{([^}]*)\}/) || [, ''])[1];
const bannerCss = (html.match(/\.src-banner\s*\{([^}]*)\}/) || [, ''])[1];
const hiddenRule = (html.match(/\[hidden\]\s*\{([^}]*)\}/g) || []).join(' ');
check('基础样式含 [hidden] 隐藏规则', /\[hidden\]\s*\{[^}]*display\s*:\s*none/.test(html));
check('[hidden] 规则带 !important（否则压不住 display:flex）',
  /\[hidden\]\s*\{[^}]*display\s*:\s*none\s*!important/.test(hiddenRule));
check('.src-overlay 确实声明了 display（证明该规则必需）',
  /display\s*:/.test(overlayCss), overlayCss.trim().slice(0, 60));
check('.src-banner 确实声明了 display（同理）',
  /display\s*:/.test(bannerCss), bannerCss.trim().slice(0, 60));
check('遮罩初始带 hidden 属性',
  /<div[^>]+id="srcOverlay"[^>]*\shidden[\s>]/.test(html));
check('「完成」按钮绑定了关闭动作', /srcDone'\)\.addEventListener\('click'/.test(html));
check('支持 ESC 关闭', /Escape/.test(html));

console.log(failed === 0 ? '\n全部通过' : `\n${failed} 项失败`);
process.exit(failed === 0 ? 0 : 1);
