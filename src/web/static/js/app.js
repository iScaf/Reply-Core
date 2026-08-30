/* 主逻辑：登录 / 统计 / 检索台 / 文档表 / 审核队列 / 问答抽屉 */
(function () {
  'use strict';
  var $ = function (id) { return document.getElementById(id); };

  /* ---------- 登录 / 登出 ---------- */
  $('loginBtn').addEventListener('click', doLogin);
  $('tokenInput').addEventListener('keydown', function (e) { if (e.key === 'Enter') doLogin(); });
  async function doLogin() {
    $('loginErr').textContent = '';
    try {
      await api('/api/login', { method: 'POST', body: { token: $('tokenInput').value } });
      $('tokenInput').value = '';
      enterConsole();
    } catch (e) {
      $('loginErr').textContent = e.message;
    }
  }
  $('logoutBtn').addEventListener('click', async function () {
    try { await api('/api/logout', { method: 'POST' }); } catch (e) {}
    document.body.classList.remove('authed');
  });

  /* ---------- 初始化 ---------- */
  enterConsole();
  async function enterConsole() {
    try {
      const stats = await api('/api/stats');
      document.body.classList.add('authed');
      renderStats(stats);
      loadDocuments();
      loadReview();
    } catch (e) { /* 401 已由 api() 处理 */ }
  }

  /* ---------- 健康统计 ---------- */
  function renderStats(s) {
    $('stDb').textContent = 'paradedb';
    $('stDbDot').className = 'dot ok';
    const vecOn = s.vector_mode !== 'none';
    $('stVec').textContent = s.vector_mode + (s.embedding_column ? ' / ' + s.embedding_column.replace('_embedding', '') : '');
    $('stVecDot').className = vecOn ? 'dot ok' : 'dot warn';
    $('stAi').textContent = s.ai_ready ? 'online' : 'offline';
    $('stAiDot').className = s.ai_ready ? 'dot ok' : 'dot warn';
    const pending = Math.max(0, s.community.pending);
    $('stQueue').textContent = pending + ' 待审';
    $('stQueueDot').className = pending ? 'dot warn' : 'dot ok';
    $('navPending').textContent = pending;
    $('searchSub').textContent =
      'pgvector HNSW ⇄ ParadeDB BM25 → RRF(k=60) · 向量模式 ' + s.vector_mode;

    $('vitals').innerHTML = ''
      + vital(s.tutorials.documents, '教程文档')
      + vital(s.community.documents, '社区设定条目')
      + vital(s.forum_threads, '论坛帖索引')
      + vital(s.tutorials.chunks + s.community.chunks, '向量块 chunks')
      + vital(pending, '待审条目');
  }
  function vital(v, label) {
    return '<div class="vital"><div class="v">' + v + '</div><div class="l">' + label + '</div></div>';
  }

  /* ---------- 检索测试台 ---------- */
  $('searchBtn').addEventListener('click', doSearch);
  $('searchInput').addEventListener('keydown', function (e) { if (e.key === 'Enter') doSearch(); });
  async function doSearch() {
    const q = $('searchInput').value.trim();
    if (!q) return;
    const btn = $('searchBtn');
    btn.disabled = true; btn.textContent = '检索中…';
    try {
      const data = await api('/api/search', {
        method: 'POST',
        body: { query: q, scope: $('searchScope').value, top_k: 10 },
      });
      renderSearch(data);
    } catch (e) {
      $('vecHits').innerHTML = bm25Err();
      $('searchSub').textContent = '检索失败: ' + e.message;
    } finally {
      btn.disabled = false; btn.textContent = '执行检索';
    }
  }
  function bm25Err() { return '<div class="channel-off">通道不可用</div>'; }

  function renderSearch(data) {
    $('searchSub').textContent = 'pgvector HNSW ⇄ ParadeDB BM25 → RRF · 耗时 ' + data.elapsed_ms + 'ms · 向量模式 ' + data.vector_mode;
    const sem = data.results.filter(r => r.semantic_rank).slice(0, 3);
    const kw = data.results.filter(r => r.keyword_rank).slice(0, 3);

    $('vecHits').innerHTML = data.channels.semantic
      ? (sem.length ? sem.map(rowBar('semantic')).join('') : emptyHits())
      : '<div class="channel-off">向量通道未启用</div>';
    $('bm25Hits').innerHTML = kw.length ? kw.map(rowBar('keyword')).join('') : emptyHits();

    const top = data.results.slice(0, 3);
    $('fusionBody').innerHTML = top.length
      ? top.map((r, i) =>
          '<div class="fusion-item' + (i === 0 ? ' top' : '') + '">'
          + '<span class="medal">' + (i + 1) + '</span>'
          + '<span class="fname" title="' + esc(r.title) + '">' + esc(r.title) + '</span>'
          + '<span class="fscore">' + r.rrf_score.toFixed(4) + '</span></div>').join('')
      : '<div class="fusion-empty">无结果</div>';

    $('searchResults').innerHTML = data.results.slice(0, 6).map(function (r) {
      const chips = ['<span class="sr-chip rrf">RRF ' + r.rrf_score.toFixed(4) + '</span>'];
      if (r.semantic_rank) chips.push('<span class="sr-chip vec">语义#' + r.semantic_rank + (r.vec_distance != null ? ' · d=' + r.vec_distance.toFixed(3) : '') + '</span>');
      if (r.keyword_rank) chips.push('<span class="sr-chip bm25">词#' + r.keyword_rank + (r.bm25_score != null ? ' · ' + r.bm25_score.toFixed(2) : '') + '</span>');
      return '<div class="sr-item"><div class="sr-head"><span class="sr-title">' + esc(r.title) + '</span>'
        + '<span>' + r.source + '#' + r.chunk_id + '</span>' + chips.join('') + '</div>'
        + '<div class="sr-text">' + esc(r.chunk_text) + '</div></div>';
    }).join('');
  }
  function rowBar(channel) {
    return function (r, i) {
      const width = [100, 78, 58][i] || 50;
      const isSem = channel === 'semantic';
      const score = isSem
        ? (r.vec_distance != null ? r.vec_distance.toFixed(3) : '--')
        : (r.bm25_score != null ? r.bm25_score.toFixed(2) : '--');
      return '<div class="hit"><span class="rank">#' + (isSem ? r.semantic_rank : r.keyword_rank) + '</span>'
        + '<div class="bar-track"><div class="bar" style="width:' + width + '%"></div></div>'
        + '<span class="score">' + score + '</span></div>';
    };
  }
  function emptyHits() { return '<div class="channel-off">本通道无命中</div>'; }

  /* ---------- 文档列表 ---------- */
  $('docSource').addEventListener('change', loadDocuments);
  async function loadDocuments() {
    const source = $('docSource').value;
    $('docSecTitle').textContent = '知识库 · ' + (source === 'tutorials' ? '教程库' : '社区设定');
    try {
      const data = await api('/api/documents?source=' + source + '&page=1&page_size=8');
      const tbody = $('docTbody');
      if (!data.items.length) {
        tbody.innerHTML = '<tr><td colspan="5" class="table-empty">知识库暂无文档 — 在 Discord 端提交教程/设定后此处展示</td></tr>';
        return;
      }
      tbody.innerHTML = data.items.map(function (d) {
        return '<tr><td class="doc-id">' + (source === 'tutorials' ? 'T-' : 'C-') + d.id + '</td>'
          + '<td class="doc-title">' + esc(d.title || '（无标题）') + '</td>'
          + '<td>' + (d.category ? '<span class="tag chip-db">' + esc(d.category) + '</span>' : '—') + '</td>'
          + '<td>' + d.chunk_count + '</td>'
          + '<td class="state-ok">' + fmtTime(d.updated_at) + '</td></tr>';
      }).join('');
    } catch (e) {
      $('docTbody').innerHTML = '<tr><td colspan="5" class="table-empty">加载失败: ' + esc(e.message) + '</td></tr>';
    }
  }

  /* ---------- 审核队列 ---------- */
  async function loadReview() {
    try {
      const data = await api('/api/review/pending?page=1&page_size=6');
      const grid = $('reviewGrid');
      if (!data.items.length) {
        grid.innerHTML = '<div class="review-empty">队列为空 — 社区设定提交经 Discord 投票后在此处理</div>';
        return;
      }
      grid.innerHTML = data.items.map(function (it) {
        return '<div class="review-card"><h5>' + esc(it.title) + '</h5>'
          + '<p>' + esc(it.content_text || '（无内容摘要）') + '</p>'
          + '<div class="review-meta"><span>提案人 ' + it.proposer_id + '</span><span>距截止 ' + fmtDeadline(it.expires_at) + '</span></div>'
          + '<div class="review-acts">'
          + '<button class="btn ok" data-act="approve" data-id="' + it.id + '">批准收录</button>'
          + '<button class="btn no" data-act="reject" data-id="' + it.id + '">驳回</button>'
          + '</div></div>';
      }).join('');
      grid.querySelectorAll('button[data-act]').forEach(function (btn) {
        btn.addEventListener('click', function () { actReview(btn.dataset.act, Number(btn.dataset.id)); });
      });
    } catch (e) {
      $('reviewGrid').innerHTML = '<div class="review-empty">加载失败: ' + esc(e.message) + '</div>';
    }
  }
  async function actReview(act, id) {
    try {
      await api('/api/review/' + id + '/' + act, {
        method: 'POST',
        body: act === 'reject' ? { reason: 'Web 控制台驳回' } : {},
      });
      loadReview();
      const s = await api('/api/stats');
      renderStats(s);
    } catch (e) {
      alert('操作失败: ' + e.message);
      loadReview();
    }
  }

  /* ---------- 问答演示 ---------- */
  const chatHistory = [];
  $('chatSend').addEventListener('click', sendChat);
  $('chatInput').addEventListener('keydown', function (e) { if (e.key === 'Enter') sendChat(); });
  async function sendChat() {
    const text = $('chatInput').value.trim();
    if (!text) return;
    $('chatInput').value = '';
    pushMsg('user', esc(text));
    const send = $('chatSend'); send.disabled = true;
    const waiting = pushMsg('bot', '<span style="color:var(--dim)">检索知识库并生成回复…</span>');
    try {
      const data = await api('/api/chat', {
        method: 'POST',
        body: { message: text, scope: $('chatScope').value, history: chatHistory.slice(-6) },
      });
      waiting.remove();
      if (data.model) $('chatModelTag').textContent = 'model: ' + data.model;

      let html = linkCitations(data.reply || '', data.citations);
      if (data.tool_trace && data.tool_trace.length) {
        html += '<div class="tool-trace">'
          + data.tool_trace.map(function (t) {
              return '⚙ ' + esc(t.name) + '(' + esc(JSON.stringify(t.arguments)) + ') · ' + t.elapsed_ms + 'ms';
            }).join('<br>')
          + '</div>';
      }
      if (data.degraded) {
        html += '<div class="degrade-note">⚠ 已降级为仅检索结果：' + esc(data.degrade_reason || '') + '</div>';
      }
      pushMsg('bot', html);
      chatHistory.push({ role: 'user', content: text });
      if (data.reply) chatHistory.push({ role: 'assistant', content: data.reply });
    } catch (e) {
      waiting.remove();
      pushMsg('bot', '<span style="color:var(--warn)">请求失败: ' + esc(e.message) + '</span>');
    } finally {
      send.disabled = false;
    }
  }
  function pushMsg(role, html) {
    const div = document.createElement('div');
    div.className = 'msg ' + role;
    div.innerHTML = '<div class="who"' + (role === 'user' ? ' style="text-align:right"' : '') + '>'
      + (role === 'user' ? '管理员' : '秒回喵 · Reply-Core') + '</div>'
      + '<div class="bubble">' + html + '</div>';
    const box = $('chatMsgs');
    box.appendChild(div);
    box.scrollTop = box.scrollHeight;
    return div;
  }
  /* 把回复中的 [资料N] 替换为引用徽章（悬停显示来源标题） */
  function linkCitations(reply, citations) {
    return esc(reply).replace(/\[资料(\d+)\]/g, function (m, n) {
      const c = citations[Number(n) - 1];
      const tip = c ? ' title="' + esc(c.title) + '"' : '';
      return '<span class="cite"' + tip + '>资料' + n + '</span>';
    });
  }

  /* ---------- 工具 ---------- */
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function fmtTime(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    return isNaN(d) ? '—' : d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
  }
  function fmtDeadline(iso) {
    if (!iso) return '—';
    const ms = new Date(iso) - Date.now();
    if (isNaN(ms)) return '—';
    if (ms <= 0) return '已截止';
    const h = Math.floor(ms / 3600000);
    return h >= 1 ? h + 'h' : Math.max(1, Math.floor(ms / 60000)) + 'm';
  }
})();
