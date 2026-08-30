/* fetch 封装：自动 JSON、401 时切回登录视图 */
(function () {
  window.api = async function api(path, options) {
    options = options || {};
    var opts = {
      method: options.method || 'GET',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
    };
    if (options.body !== undefined) opts.body = JSON.stringify(options.body);

    var resp = await fetch(path, opts);
    if (resp.status === 401) {
      document.body.classList.remove('authed');
      throw new Error('未认证');
    }
    var data = null;
    try { data = await resp.json(); } catch (e) { /* 空响应 */ }
    if (!resp.ok) {
      var msg = (data && data.detail) || ('请求失败 ' + resp.status);
      throw new Error(msg);
    }
    return data;
  };
})();
