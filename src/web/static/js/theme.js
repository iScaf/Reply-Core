/* 主题切换：观测台（暗）⇄ 档案馆（暖），localStorage 持久化 */
(function () {
  var root = document.documentElement;
  var btn = document.getElementById('themeToggle');
  var label = document.getElementById('themeLabel');
  function paint(t) {
    label.textContent = t === 'warm' ? '观测台模式' : '档案馆模式';
    btn.setAttribute('aria-pressed', t === 'warm' ? 'true' : 'false');
    btn.title = t === 'warm' ? '切回暗色观测台主题' : '切换暖色档案馆主题';
  }
  btn.addEventListener('click', function () {
    var t = root.dataset.theme === 'warm' ? 'dark' : 'warm';
    root.dataset.theme = t;
    try { localStorage.setItem('rc-theme', t); } catch (e) {}
    paint(t);
  });
  paint(root.dataset.theme === 'warm' ? 'warm' : 'dark');
})();
