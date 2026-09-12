/* 全站导航与键盘可达性的注入脚本。
 *
 * 配合 web/lib/a11y.css 使用。做三件事：
 *   ① 注入「← 返回导航页」——改动前 5 个主页面没有任何一个能回导航页，
 *      演示时只能靠浏览器后退键；
 *   ② 注入跳转到主内容的 skip-link（键盘用户不必逐个 Tab 过导航）；
 *   ③ 把"看着能点但键盘点不到"的元素补上键盘可达性：
 *      · 星云页的实体列表项（div.row）→ tabindex + Enter/Space 激活
 *      · 方法能力页的示例胶囊、维度胶囊（span）→ role=button + 键盘
 *
 * 【为什么用脚本而不是直接改 HTML】
 *   这些元素是 JS 动态渲染出来的（列表随社区变化、胶囊随筛选变化），
 *   静态加属性会被重渲染冲掉。所以用 MutationObserver 持续补齐——
 *   它不是"补一次"，而是保证**任何时刻**新出现的元素也是可达的。
 */
(function () {
  'use strict';

  // ── ① 返回导航页 ──
  function addHome() {
    if (document.getElementById('a11y-home')) return;
    // index.html 自己就是导航页，不加
    var path = location.pathname.replace(/\\/g, '/');
    if (/(^|\/)index\.html$/.test(path) || /\/web\/?$/.test(path)) return;

    var a = document.createElement('a');
    a.id = 'a11y-home';
    a.href = './index.html';
    a.setAttribute('aria-label', '返回导航页');
    a.innerHTML = '<span aria-hidden="true">←</span> 导航页';
    document.body.appendChild(a);
  }

  // ── ② 跳过导航 ──
  function addSkip() {
    if (document.getElementById('a11y-skip')) return;
    var main = document.querySelector('main, #main, .wrap, #detail, body');
    if (main && !main.id) main.id = 'a11y-main';
    var target = main && main.id ? main.id : '';
    if (!target) return;
    var a = document.createElement('a');
    a.id = 'a11y-skip';
    a.href = '#' + target;
    a.textContent = '跳到主内容';
    document.body.insertBefore(a, document.body.firstChild);
  }

  // ── ③ 让动态渲染出的可点元素键盘可达 ──
  var SELECTORS = [
    // 星云页左栏实体列表：原本是 div.item，鼠标能点、键盘完全够不着
    // （选择器以实际 DOM 为准：是 `#list .item`，不是 `.row`）
    { sel: '#list .item', role: 'option' },
    // 方法能力页的胶囊（示例问句、维度筛选）
    { sel: '.chip, .capsule, .pill', role: 'button' },
    // 分析面板的类型行（可悬浮查看）
    { sel: '.type-row, .typebar', role: null }
  ];

  function makeReachable(el, role) {
    if (el.dataset.a11yDone) return;
    el.dataset.a11yDone = '1';
    if (!el.hasAttribute('tabindex')) el.setAttribute('tabindex', '0');
    if (role && !el.getAttribute('role')) el.setAttribute('role', role);
    // 已有 click 处理器的元素，补上键盘等价操作
    if (!el.dataset.a11yKeys) {
      el.dataset.a11yKeys = '1';
      el.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' || e.key === ' ' || e.key === 'Spacebar') {
          // 只在元素自身没有原生激活行为时才合成点击，
          // 否则 button/a 会被触发两次
          var tag = (el.tagName || '').toLowerCase();
          if (tag === 'button' || tag === 'a' || tag === 'input') return;
          e.preventDefault();
          el.click();
        }
      });
    }
  }

  function sweep(root) {
    SELECTORS.forEach(function (s) {
      var list;
      try { list = (root || document).querySelectorAll(s.sel); }
      catch (e) { return; }
      for (var i = 0; i < list.length; i++) makeReachable(list[i], s.role);
    });
  }

  function boot() {
    addHome();
    addSkip();
    sweep(document);
    // 动态内容：列表随社区切换整体重建、胶囊随筛选重渲染，
    // 所以必须持续观察而不是补一次。
    if (window.MutationObserver) {
      var pending = false;
      new MutationObserver(function () {
        if (pending) return;
        pending = true;
        requestAnimationFrame(function () { pending = false; sweep(document); });
      }).observe(document.body, { childList: true, subtree: true });
    }
    // 社区内外切换会改 body 的 class（a11y.css 依赖 .inside 调底部间距）
    if (window.MutationObserver) {
      new MutationObserver(function () {}).observe(document.body, { attributes: true, attributeFilter: ['class'] });
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
