/**
 * 「整卡可点」回归测试（无需浏览器）。
 *
 * 页面脚本把卡片做成点哪都能打开原文，但必须让开几个场景，否则会互相打架。
 * 用最小 DOM mock 跑 state.py 注入的面板脚本，逐条验证：
 * 1. 点卡片空白处 / 摘要 / 要点 → 打开原文
 * 2. 点标题链接 → 不重复打开（交给浏览器默认行为）
 * 3. 点「☆ 收藏」→ 只收藏，不跳走
 * 4. 正在选文字时点卡片 → 不跳走（否则复制摘要会被打断）
 * 5. Ctrl / Cmd / Shift / 中键点击 → 交给浏览器（后台开标签）
 * 6. 卡片没有 data-href → 什么都不做，不报错
 * 7. 提示条：默认显示，点「知道了」后记住不再显示
 *
 * 用法：node scripts/check_card_click.js
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
// 面板主脚本的特征：包含 data-href 读取
const panelCode = blocks.find((b) => b.indexOf('data-href') !== -1);
if (!panelCode) {
  console.error('FAIL: 页面里找不到面板主脚本（没有 data-href 逻辑）');
  process.exit(1);
}

let failed = 0;
function check(name, ok, detail) {
  if (ok) {
    console.log(`  PASS  ${name}`);
  } else {
    failed++;
    console.log(`  FAIL  ${name}${detail !== undefined ? `  → ${JSON.stringify(detail)}` : ''}`);
  }
}

// ---------- 最小 DOM mock ----------
function makeEl(extra) {
  return Object.assign(
    {
      value: '',
      style: {},
      dataset: {},
      hidden: false,
      textContent: '',
      _handlers: {},
      classList: { add() {}, remove() {} },
      addEventListener(t, fn) {
        (this._handlers[t] = this._handlers[t] || []).push(fn);
      },
      fire(t, ev) {
        (this._handlers[t] || []).forEach((fn) => fn(ev));
      },
      insertBefore() {},
      getAttribute() {
        return null;
      },
    },
    extra
  );
}

const store = {};
const opened = [];
let selection = '';

const nodes = {
  q: makeEl(),
  site: makeEl(),
  sort: makeEl(),
  list: makeEl(),
  empty: makeEl(),
  tips: makeEl({ hidden: true }),
  tipsClose: makeEl(),
};

const card = makeEl({
  dataset: {
    search: '',
    site: '教务处',
    siteId: 'jwc',
    cat: '教务',
    isfav: '0',
    dl: '1',
    pub: '1',
  },
  getAttribute(k) {
    return k === 'data-href' ? 'https://jwc.hafu.edu.cn/info/1/2.htm' : null;
  },
});

const cardNoHref = makeEl({ getAttribute() { return null; } });

const documentMock = {
  getElementById(id) {
    return nodes[id] || null;
  },
  querySelectorAll(sel) {
    return sel === '.card' ? [card, cardNoHref] : [];
  },
  addEventListener(t, fn) {
    (documentMock._handlers[t] = documentMock._handlers[t] || []).push(fn);
  },
  _handlers: {},
};

const windowMock = {
  open(url) {
    opened.push(url);
    return null;
  },
  getSelection() {
    return { toString: () => selection };
  },
};

function runPanel() {
  // 用 Function 注入，避免脚本里的 IIFE 拿到真实全局
  const fn = new Function(
    'document',
    'window',
    'localStorage',
    'setTimeout',
    panelCode
  );
  const localStorageMock = {
    getItem: (k) => (Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null),
    setItem: (k, v) => {
      store[k] = String(v);
    },
  };
  fn(documentMock, windowMock, localStorageMock, () => {});
}

// 构造点击目标：tag 模拟元素类型，closest 支持逗号选择器列表与 .card
function mkNode(tag, ownerCard) {
  const node = {
    tag,
    closest(sel) {
      const parts = sel.split(',').map((s) => s.trim());
      if (parts.indexOf(tag) !== -1) return node;
      if (parts.indexOf('.card') !== -1) return ownerCard || null;
      return null;
    },
  };
  return node;
}

function click(node, opts) {
  const ev = Object.assign(
    { target: node, button: 0, metaKey: false, ctrlKey: false, shiftKey: false, altKey: false },
    opts || {}
  );
  nodes.list.fire('click', ev);
}

// ---------- 开跑 ----------
runPanel();

console.log('[1] 点卡片各区域都能打开原文');
opened.length = 0;
selection = '';
click(mkNode('div', card)); // 卡片空白/正文区域
check('点摘要区域 → 打开原文', opened.length === 1 && opened[0] === card.getAttribute('data-href'), opened);
opened.length = 0;
click(mkNode('span', card)); // 「↗」箭头
check('点箭头 → 打开原文', opened.length === 1, opened);

console.log('[2] 该让开的地方必须让开');
opened.length = 0;
click(mkNode('a', card));
check('点标题链接 → 不重复打开', opened.length === 0, opened);

opened.length = 0;
click(mkNode('button', card));
check('点「☆ 收藏」→ 不跳走', opened.length === 0, opened);

opened.length = 0;
selection = '教学计划 11 月 13 日前须完成录入';
click(mkNode('div', card));
check('选中文字时点卡片 → 不跳走', opened.length === 0, opened);
selection = '';

const mods = [
  ['ctrlKey', 'Ctrl/⌘ 点击'],
  ['metaKey', 'Meta 点击'],
  ['shiftKey', 'Shift 点击'],
  ['altKey', 'Alt 点击'],
];
mods.forEach(([key, label]) => {
  opened.length = 0;
  click(mkNode('div', card), { [key]: true });
  check(`${label} → 交给浏览器`, opened.length === 0, opened);
});

opened.length = 0;
click(mkNode('div', card), { button: 1 });
check('中键点击 → 交给浏览器', opened.length === 0, opened);

console.log('[3] 边界');
opened.length = 0;
click(mkNode('div', cardNoHref));
check('无 data-href 的卡片 → 不报错也不跳', opened.length === 0, opened);

opened.length = 0;
click(mkNode('div', null));
check('点在列表空白处（非卡片）→ 不跳', opened.length === 0, opened);

console.log('[4] 用法提示条');
check('首次打开显示提示', nodes.tips.hidden === false);
check('提示文案说明点卡片与标题', html.indexOf('点卡片任意位置') !== -1);
nodes.tipsClose.fire('click', { target: nodes.tipsClose });
check('点「知道了」后隐藏', nodes.tips.hidden === true);
check('关闭状态已落盘', store['campusNotify.tips.v1'] === 'off', store['campusNotify.tips.v1']);

// 重新载入模拟刷新：应保持关闭
nodes.tips.hidden = true;
runPanel();
check('刷新后不再打扰', nodes.tips.hidden === true);

console.log(failed === 0 ? '\n全部通过' : `\n${failed} 项失败`);
process.exit(failed === 0 ? 0 : 1);
