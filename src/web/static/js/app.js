/* 主逻辑：登录 / 视图切换 / 统计 / 检索台 / 文档库（教程·设定）/ 审核队列 / 问答演示 */
(function () {
  'use strict';
  var $ = function (id) { return document.getElementById(id); };

  /* ================= 登录 / 登出 ================= */
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

  /* ================= 视图切换 ================= */
  var entered = false;
  var loaders = {
    'search': async function () { try { renderStats(await api('/api/stats')); } catch (e) {} },
    'docs-tutorials': function () { docViews.tutorials.refresh(); },
    'docs-settings': function () { docViews.settings.refresh(); },
    'review': loadReview,
    'users': function () { usersView.refresh(); },
    'skills': function () { skillsView.enter(); },
  };
  function showView(name) {
    document.querySelectorAll('.nav-item').forEach(function (n) {
      n.classList.toggle('active', n.dataset.view === name);
    });
    document.querySelectorAll('.view').forEach(function (v) {
      v.classList.toggle('hidden', v.id !== 'view-' + name);
    });
    if (loaders[name]) loaders[name]();
    // 动画 A：进入视图的区块波浪入场（GSAP）
    if (window.gsap) {
      const secs = document.querySelectorAll('#view-' + name + ' .sec');
      secs.forEach(function (sec) {
        gsap.fromTo(sec, { y: 22, opacity: 0 },
          { y: 0, opacity: 1, duration: .45, ease: 'power2.out' });
      });
      const rows = document.querySelectorAll('#view-' + name + ' tbody tr');
      gsap.fromTo(rows, { opacity: 0 }, { opacity: 1, duration: .3, stagger: .03, delay: .1 });
    }
  }
  document.querySelectorAll('.nav-item').forEach(function (n) {
    n.addEventListener('click', function () { showView(n.dataset.view); });
  });

  async function enterConsole() {
    try {
      const stats = await api('/api/stats');
      document.body.classList.add('authed');
      renderStats(stats);
      if (!entered) {
        entered = true;
        showView('search');
        loadOlderHistory(true);  // 进入控制台时加载最近 5 轮历史
        loadChatModels();        // 加载问答可选模型列表（后端持久化缓存）
        loadChatPersonas();      // 加载人设选择（后台「技能与人设」维护）
      } else {
        showView(document.querySelector('.nav-item.active').dataset.view);
      }
    } catch (e) { /* 401 已由 api() 处理 */ }
  }

  /* ================= 健康统计 ================= */
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
    // 动画 B：统计数字从 0 滚动到真实值
    if (window.gsap) {
      document.querySelectorAll('#vitals .v').forEach(function (el) {
        const end = Number(el.textContent) || 0;
        const o = { n: 0 };
        gsap.to(o, { n: end, duration: .9, ease: 'power2.out',
          onUpdate: function () { el.textContent = Math.round(o.n); } });
        gsap.fromTo(el, { scale: .5, transformOrigin: 'left center' },
          { scale: 1, duration: .45, ease: 'back.out(2.4)' });
      });
    }
  }
  function vital(v, label) {
    return '<div class="vital"><div class="v">' + v + '</div><div class="l">' + label + '</div></div>';
  }

  /* ================= 检索测试台（签名元素：双通道 → RRF） ================= */
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
      $('vecHits').innerHTML = '<div class="channel-off">通道不可用</div>';
      $('searchSub').textContent = '检索失败: ' + e.message;
    } finally {
      btn.disabled = false; btn.textContent = '执行检索';
    }
  }

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

  /* ================= 文档库视图（教程库 / 社区设定 共用组件） ================= */
  function createDocView(source, prefix) {
    const state = { page: 1, pageSize: 10, q: '', total: 0 };
    const tbody = $('tbody-' + prefix), pager = $('pager-' + prefix), sub = $('sub-' + prefix);

    async function refresh(page) {
      if (page) state.page = page;
      tbody.innerHTML = '<tr><td colspan="5" class="table-empty">加载中…</td></tr>';
      try {
        const data = await api('/api/documents?source=' + source
          + '&page=' + state.page + '&page_size=' + state.pageSize
          + (state.q ? '&q=' + encodeURIComponent(state.q) : ''));
        state.total = data.total;
        const totalPages = Math.max(1, Math.ceil(data.total / state.pageSize));
        sub.textContent = '共 ' + data.total + ' 篇 · 第 ' + data.page + ' / ' + totalPages + ' 页';
        if (!data.items.length) {
          tbody.innerHTML = '<tr><td colspan="5" class="table-empty">没有匹配的文档</td></tr>';
          pager.innerHTML = '';
          return;
        }
        tbody.innerHTML = data.items.map(function (d) {
          return '<tr class="row-click" data-id="' + d.id + '">'
            + '<td class="doc-id">' + (source === 'tutorials' ? 'T-' : 'C-') + d.id + '</td>'
            + '<td class="doc-title">' + esc(d.title || '（无标题）') + '</td>'
            + '<td>' + (d.category ? '<span class="tag chip-db">' + esc(d.category) + '</span>' : '—') + '</td>'
            + '<td>' + d.chunk_count + '</td>'
            + '<td class="state-ok">' + fmtTime(d.updated_at) + '</td></tr>';
        }).join('');
        tbody.querySelectorAll('tr').forEach(function (tr) {
          tr.addEventListener('click', function () { openDoc(source, Number(tr.dataset.id)); });
        });
        renderPager(data.page, totalPages);
      } catch (e) {
        tbody.innerHTML = '<tr><td colspan="5" class="table-empty">加载失败: ' + esc(e.message) + '</td></tr>';
      }
    }

    function renderPager(page, totalPages) {
      if (totalPages <= 1) { pager.innerHTML = ''; return; }
      pager.innerHTML = ''
        + '<button id="' + prefix + '-prev"' + (page <= 1 ? ' disabled' : '') + '>上一页</button>'
        + '<span>第 ' + page + ' / ' + totalPages + ' 页</span>'
        + '<button id="' + prefix + '-next"' + (page >= totalPages ? ' disabled' : '') + '>下一页</button>';
      $('' + prefix + '-prev').addEventListener('click', function () { refresh(page - 1); });
      $('' + prefix + '-next').addEventListener('click', function () { refresh(page + 1); });
    }

    $('q-' + prefix).addEventListener('keydown', function (e) {
      if (e.key === 'Enter') { state.q = $('q-' + prefix).value.trim(); refresh(1); }
    });
    $('reload-' + prefix).addEventListener('click', function () {
      state.q = $('q-' + prefix).value.trim(); refresh(1);
    });

    // 教程库视图：上传文档（md/pdf/docx/xlsx → 解析 → 切块 → 向量化）
    if (source === 'tutorials') {
      const statusBox = $('upload-status-tutorials');
      const fileInput = $('file-tutorials');
      function showStatus(text, isError) {
        statusBox.style.display = '';
        statusBox.className = isError ? 'state-no' : 'table-empty';
        statusBox.textContent = text;
      }
      $('upload-tutorials').addEventListener('click', function () { fileInput.click(); });
      fileInput.addEventListener('change', async function () {
        const file = fileInput.files[0];
        fileInput.value = '';
        if (!file) return;
        if (file.size > 10 * 1024 * 1024) { showStatus('文件超过 10MB 限制', true); return; }
        showStatus('正在上传并解析「' + file.name + '」…（大文件的切块与向量化需要一些时间）');
        try {
          const form = new FormData();
          form.append('file', file);
          const resp = await fetch('/api/documents/tutorials/upload', {
            method: 'POST',
            credentials: 'same-origin',
            body: form,
          });
          const data = await resp.json().catch(function () { return null; });
          if (resp.status === 401) { showStatus('未认证，请重新登录', true); return; }
          if (!resp.ok) throw new Error((data && data.detail) || ('上传失败 ' + resp.status));
          showStatus('入库成功：' + data.title
            + '（可检索块 ' + data.chunk_count + ' · 节级父块 ' + data.parent_count + '）');
          refresh(1);
        } catch (e) {
          showStatus('上传失败: ' + e.message, true);
        }
      });
    }

    return { refresh: function () { refresh(state.page || 1); } };
  }
  const docViews = {
    tutorials: createDocView('tutorials', 'tutorials'),
    settings: createDocView('community_settings', 'settings'),
  };

  /* ================= 用户信息视图 ================= */
  const usersViewState = { page: 1, pageSize: 20, q: '' };

  async function refreshUsers(page) {
    const state = usersViewState;
    if (page) state.page = page;
    const tbody = $('tbody-users'), pager = $('pager-users'), sub = $('sub-users');
    tbody.innerHTML = '<tr><td colspan="4" class="table-empty">加载中…</td></tr>';
    try {
      const data = await api('/api/users?page=' + state.page
        + '&page_size=' + state.pageSize
        + (state.q ? '&q=' + encodeURIComponent(state.q) : ''));
      sub.textContent = '共 ' + data.total + ' 位用户 · 第 ' + data.page + ' / '
        + Math.max(1, Math.ceil(data.total / state.pageSize)) + ' 页';
      if (!data.items.length) {
        tbody.innerHTML = '<tr><td colspan="4" class="table-empty">没有匹配的用户</td></tr>';
        pager.innerHTML = '';
        return;
      }
      tbody.innerHTML = data.items.map(function (u) {
        return '<tr class="row-click" data-id="' + u.id + '">'
          + '<td class="doc-id">' + esc(u.discord_id || '（未绑定）') + '</td>'
          + '<td>' + esc(u.title || '（无昵称）') + '</td>'
          + '<td>' + esc((u.personal_summary || '—').slice(0, 60)) + '</td>'
          + '<td>' + (u.personal_message_count || 0) + '</td></tr>';
      }).join('');
      tbody.querySelectorAll('tr').forEach(function (tr) {
        tr.addEventListener('click', function () { openUser(Number(tr.dataset.id)); });
      });
      const totalPages = Math.max(1, Math.ceil(data.total / state.pageSize));
      pager.innerHTML = ''
        + '<button id="users-prev"' + (state.page <= 1 ? ' disabled' : '') + '>上一页</button>'
        + '<span>第 ' + state.page + ' / ' + totalPages + ' 页</span>'
        + '<button id="users-next"' + (state.page >= totalPages ? ' disabled' : '') + '>下一页</button>';
      $('users-prev').addEventListener('click', function () { refreshUsers(state.page - 1); });
      $('users-next').addEventListener('click', function () { refreshUsers(state.page + 1); });
    } catch (e) {
      tbody.innerHTML = '<tr><td colspan="4" class="table-empty">加载失败: ' + esc(e.message) + '</td></tr>';
    }
  }

  const usersView = { refresh: function () { refreshUsers(usersViewState.page || 1); } };
  $('q-users').addEventListener('keydown', function (e) {
    if (e.key === 'Enter') { usersViewState.q = $('q-users').value.trim(); refreshUsers(1); }
  });
  $('reload-users').addEventListener('click', function () {
    usersViewState.q = $('q-users').value.trim(); refreshUsers(1);
  });

  /* ---------- 用户详情弹层 ---------- */
  let currentUser = null;
  async function openUser(id) {
    currentUser = id;
    try {
      const u = await api('/api/users/' + id);
      $('umTitle').textContent = '用户 ' + (u.title || u.discord_id || u.id);
      $('umMeta').innerHTML = ''
        + (u.discord_id ? '<span>Discord ID ' + esc(u.discord_id) + '</span>' : '')
        + '<span>消息 ' + (u.personal_message_count || 0) + ' 条</span>'
        + '<span>' + (u.memory_notes.length) + ' 条记忆笔记</span>';
      $('umSummary').textContent = u.personal_summary || u.full_text || '（暂无档案）';
      $('umNotes').innerHTML = u.memory_notes.map(function (n) {
        return '<div class="chunk"><span class="idx">' + esc(n.category) + '</span>' + esc(n.content) + '</div>';
      }).join('') || '<div class="table-empty">暂无记忆笔记</div>';
      renderUserPending(u.pending_history || []);
      renderUserBlocks(u.recent_blocks || []);
      $('userModal').classList.remove('hidden');
    } catch (e) {
      alert('加载用户详情失败: ' + e.message);
    }
  }
  function renderUserBlocks(blocks) {
    $('umBlocks').innerHTML = blocks.map(function (b) {
      return '<div class="chunk"><span class="idx">#' + b.id + ' · '
        + (b.message_count || 0) + ' 条 · ' + (b.start_time ? b.start_time.slice(0, 10) : '')
        + '</span><div class="md-body">' + mdRender(b.conversation_text, []) + '</div></div>';
    }).join('') || '<div class="table-empty">暂无对话记录</div>';
  }
  /* 未打包对话（member_profiles.history）：尚未凑满 block_size 的近期轮次 */
  function renderUserPending(turns) {
    $('umPending').innerHTML = turns.map(function (t) {
      const time = t.timestamp ? ' · ' + fmtTime(t.timestamp) : '';
      return '<div class="chunk"><span class="idx">'
        + (t.role === 'user' ? '用户' : '秒回喵') + time
        + '</span><div class="md-body">' + mdRender(t.content, []) + '</div></div>';
    }).join('') || '<div class="table-empty">暂无未打包对话（近期聊天已全部归档为对话块）</div>';
  }
  $('umChatQuery').addEventListener('keydown', async function (e) {
    if (e.key !== 'Enter' || !currentUser) return;
    const q = this.value.trim();
    if (!q) return;
    try {
      const data = await api('/api/users/' + currentUser + '/chats?q=' + encodeURIComponent(q));
      $('umChats').innerHTML = data.items.map(function (b) {
        return '<div class="chunk"><span class="idx">#' + b.id + ' · '
          + (b.start_time ? b.start_time.slice(0, 10) : '') + '</span>'
          + esc(b.conversation_text) + '</div>';
      }).join('') || '<div class="table-empty">没有匹配的对话记录</div>';
    } catch (e) {
      $('umChats').innerHTML = '<div class="table-empty">搜索失败: ' + esc(e.message) + '</div>';
    }
  });
  $('umClose').addEventListener('click', function () { $('userModal').classList.add('hidden'); });
  $('userModal').addEventListener('click', function (e) {
    if (e.target === this) this.classList.add('hidden');
  });

  /* ================= 技能与人设视图 ================= */
  const skillsView = {
    entered: false,
    currentSkill: null,   // 当前编辑的技能名（null = 新建）
    currentPersona: null, // 当前编辑的人设名
    enter: function () {
      if (!this.entered) { this.entered = true; this.loadSkills(); this.loadPersonas(); }
    },
    loadSkills: async function () {
      const list = $('skillList');
      list.innerHTML = '<div class="dim2" style="padding:8px;">加载中…</div>';
      try {
        const data = await api('/api/skills');
        list.innerHTML = data.items.map(function (s) {
          return '<div class="sk-item" data-name="' + esc(s.name) + '">'
            + '<b>' + esc(s.display_name || s.name) + '</b>'
            + '<span class="sk-item-badges">'
            + (s.enabled ? '' : '<span class="dim2">[已禁用]</span>')
            + '<span class="dim2">' + esc(s.injection_mode) + '</span></span></div>';
        }).join('');
        list.querySelectorAll('.sk-item').forEach(function (el) {
          el.addEventListener('click', function () {
            list.querySelectorAll('.sk-item').forEach(function (x) { x.classList.remove('active'); });
            el.classList.add('active');
            skillsView.openSkill(el.dataset.name);
          });
        });
      } catch (e) {
        list.innerHTML = '<div class="dim2" style="padding:8px;">加载失败: ' + esc(e.message) + '</div>';
      }
    },
    openSkill: async function (name) {
      skillsView.currentSkill = name;
      try {
        const s = await api('/api/skills/' + name);
        $('skillNameInput').value = s.name;
        $('skillNameInput').readOnly = true;
        $('skillDisplayInput').value = s.display_name || '';
        $('skillDescInput').value = s.description || '';
        $('skillModeSelect').value = s.injection_mode || 'prompt';
        $('skillEnabledCheck').checked = !!s.enabled;
        $('skillContentInput').value = s.content || '';
        skillsView.setPreview(true);  // 默认预览展示（md 渲染）
      } catch (e) { alert('读取技能失败: ' + e.message); }
    },
    loadPersonas: async function () {
      const list = $('personaList');
      list.innerHTML = '<div class="dim2" style="padding:8px;">加载中…</div>';
      try {
        const data = await api('/api/persona');
        list.innerHTML = data.items.map(function (p) {
          return '<div class="sk-item" data-name="' + esc(p.name) + '">'
            + '<b>' + esc(p.display_name || p.name) + '</b>'
            + '<span class="sk-item-badges">'
            + (p.is_default ? '<span class="ok">[默认]</span>' : '')
            + (p.enabled ? '' : '<span class="dim2">[已禁用]</span>') + '</span></div>';
        }).join('');
        list.querySelectorAll('.sk-item').forEach(function (el) {
          el.addEventListener('click', function () {
            list.querySelectorAll('.sk-item').forEach(function (x) { x.classList.remove('active'); });
            el.classList.add('active');
            skillsView.openPersona(el.dataset.name);
          });
        });
        // 默认选中第一个
        const first = list.querySelector('.sk-item');
        if (first) first.click();
      } catch (e) {
        list.innerHTML = '<div class="dim2" style="padding:8px;">加载失败: ' + esc(e.message) + '</div>';
      }
    },
    openPersona: async function (name) {
      skillsView.currentPersona = name;
      try {
        const p = await api('/api/persona/' + name);
        $('personaNameInput').value = p.name;
        $('personaDisplayInput').value = p.display_name || '';
        $('personaEnabledCheck').checked = !!p.enabled;
        $('personaDefaultCheck').checked = !!p.is_default;
        $('personaContentInput').value = p.system_prompt || '';
      } catch (e) { alert('读取人设失败: ' + e.message); }
    },
  };

  // Tab 切换
  document.querySelectorAll('.sk-tab').forEach(function (tab) {
    tab.addEventListener('click', function () {
      document.querySelectorAll('.sk-tab').forEach(function (t) { t.classList.remove('active'); });
      tab.classList.add('active');
      $('sk-pane-skill').style.display = tab.dataset.tab === 'skill' ? '' : 'none';
      $('sk-pane-persona').style.display = tab.dataset.tab === 'persona' ? '' : 'none';
    });
  });

  // 新建技能（清空编辑器，名称可写，保持编辑态）
  $('skillNew').addEventListener('click', function () {
    skillsView.currentSkill = null;
    ['skillNameInput', 'skillDisplayInput', 'skillDescInput', 'skillContentInput'].forEach(function (id) { $(id).value = ''; });
    $('skillNameInput').readOnly = false;
    $('skillModeSelect').value = 'prompt';
    $('skillEnabledCheck').checked = true;
    skillsView.setPreview(false);
    $('skillContentInput').focus();
  });

  // 编辑/预览切换（Markdown 渲染，复用 marked + DOMPurify）
  skillsView.setPreview = function (on) {
    const input = $('skillContentInput');
    const preview = $('skillPreview');
    const btn = $('skillPreviewToggle');
    if (on) {
      preview.innerHTML = (window.marked && window.DOMPurify)
        ? DOMPurify.sanitize(marked.parse(input.value || ''))
        : esc(input.value).replace(/\n/g, '<br>');
      input.style.display = 'none';
      preview.style.display = 'block';
      btn.textContent = '✏ 编辑';
    } else {
      preview.style.display = 'none';
      input.style.display = '';
      btn.textContent = '👁 预览';
    }
  };
  $('skillPreviewToggle').addEventListener('click', function () {
    const previewOn = $('skillPreview').style.display === 'block';
    skillsView.setPreview(!previewOn);
  });

  // 保存技能
  $('skillSave').addEventListener('click', async function () {
    const name = $('skillNameInput').value.trim();
    const content = $('skillContentInput').value;
    if (!name) { alert('请填写技能名'); return; }
    if (!content.trim()) { alert('技能正文不能为空'); return; }
    try {
      $('skillSave').disabled = true;
      await api('/api/skills/' + name, {
        method: 'PUT',
        body: {
          content: content,
          display_name: $('skillDisplayInput').value.trim() || null,
          description: $('skillDescInput').value,
          injection_mode: $('skillModeSelect').value,
          enabled: $('skillEnabledCheck').checked,
        },
      });
      await skillsView.loadSkills();
      skillsView.openSkill(name);
    } catch (e) { alert('保存失败: ' + e.message); }
    finally { $('skillSave').disabled = false; }
  });

  // 保存人设
  $('personaSave').addEventListener('click', async function () {
    const name = $('personaNameInput').value.trim();
    const content = $('personaContentInput').value;
    if (!name) { alert('请先在左侧选择人设'); return; }
    if (!content.trim()) { alert('人设正文不能为空'); return; }
    try {
      $('personaSave').disabled = true;
      $('personaSave').textContent = '保存并生效…';
      await api('/api/persona/' + name, {
        method: 'PUT',
        body: {
          system_prompt: content,
          display_name: $('personaDisplayInput').value.trim() || null,
          is_default: $('personaDefaultCheck').checked,
          enabled: $('personaEnabledCheck').checked,
        },
      });
      await skillsView.loadPersonas();
    } catch (e) { alert('保存失败: ' + e.message); }
    finally { $('personaSave').disabled = false; $('personaSave').textContent = '保存'; }
  });

  /* ================= 文档详情弹层 ================= */
  const docModalState = { source: null, id: null };

  async function openDoc(source, id) {
    docModalState.source = source;
    docModalState.id = id;
    // 教程库提供编辑/重传/删除操作
    $('dmActions').style.display = source === 'tutorials' ? '' : 'none';
    setDocModalMode('view');
    try {
      const d = await api('/api/documents/' + source + '/' + id);
      $('dmTitle').textContent = (source === 'tutorials' ? 'T-' : 'C-') + d.id + ' ' + (d.title || '（无标题）');
      const cat = d.category ? '<span>分类 ' + esc(d.category) + '</span>' : '';
      const author = d.author ? '<span>作者 ' + esc(d.author) + '</span>' : '';
      const link = d.source_url ? '<a href="' + esc(d.source_url) + '" target="_blank" rel="noopener">原始链接</a>' : '';
      $('dmMeta').innerHTML = cat + author + link
        + '<span>' + (d.chunks ? d.chunks.length : 0) + ' 个分块</span>'
        + '<span>更新于 ' + fmtTime(d.updated_at) + '</span>';
      $('dmFulltext').textContent = d.full_text || '';
      $('dmChunks').innerHTML = (d.chunks || []).map(function (c) {
        return '<div class="chunk"><span class="idx">#' + (c.chunk_index != null ? c.chunk_index : '?') + ' · id ' + c.id + '</span>' + esc(c.chunk_text) + '</div>';
      }).join('') || '<div class="table-empty">无分块</div>';
      $('docModal').classList.remove('hidden');
    } catch (e) {
      alert('加载文档详情失败: ' + e.message);
    }
  }

  function setDocModalMode(mode) {
    const editing = mode === 'edit';
    $('dmViewMode').style.display = editing ? 'none' : '';
    $('dmEditMode').style.display = editing ? '' : 'none';
    $('dmChunksHeading').style.display = editing ? 'none' : '';
    $('dmChunks').style.display = editing ? 'none' : '';
    $('dmSave').style.display = editing ? '' : 'none';
    $('dmCancel').style.display = editing ? '' : 'none';
    $('dmActions').style.display = (!editing && docModalState.source === 'tutorials') ? '' : 'none';
  }

  $('dmEdit').addEventListener('click', function () {
    $('dmTitleInput').value = ($('dmTitle').textContent || '').replace(/^T-\d+\s*/, '');
    $('dmContentInput').value = $('dmFulltext').textContent || '';
    setDocModalMode('edit');
  });
  $('dmCancel').addEventListener('click', function () { setDocModalMode('view'); });
  $('dmSave').addEventListener('click', async function () {
    try {
      $('dmSave').disabled = true;
      $('dmSave').textContent = '保存并重新向量化…';
      const r = await api('/api/documents/tutorials/' + docModalState.id, {
        method: 'PUT',
        body: {
          title: $('dmTitleInput').value,
          content: $('dmContentInput').value,
        },
      });
      alert('已保存：可检索块 ' + r.chunk_count + ' · 节级父块 ' + r.parent_count);
      setDocModalMode('view');
      openDoc('tutorials', docModalState.id);
      docViews.tutorials.refresh();
    } catch (e) {
      alert('保存失败: ' + e.message);
    } finally {
      $('dmSave').disabled = false;
      $('dmSave').textContent = '保存';
    }
  });
  $('dmReupload').addEventListener('click', function () { $('dmReuploadFile').click(); });
  $('dmReuploadFile').addEventListener('change', async function () {
    const file = this.files[0];
    this.value = '';
    if (!file || docModalState.id == null) return;
    if (!confirm('重新上传将替换「' + file.name + '」的内容并重新向量化，标题保持不变。继续？')) return;
    try {
      const form = new FormData();
      form.append('file', file);
      const resp = await fetch('/api/documents/tutorials/' + docModalState.id + '/reupload', {
        method: 'POST',
        credentials: 'same-origin',
        body: form,
      });
      const data = await resp.json().catch(function () { return null; });
      if (!resp.ok) throw new Error((data && data.detail) || ('上传失败 ' + resp.status));
      alert('已重新上传：可检索块 ' + data.chunk_count + ' · 节级父块 ' + data.parent_count);
      openDoc('tutorials', docModalState.id);
      docViews.tutorials.refresh();
    } catch (e) {
      alert('重新上传失败: ' + e.message);
    }
  });
  $('dmDelete').addEventListener('click', async function () {
    if (!confirm('确定删除该教程及其全部分块？此操作不可恢复。')) return;
    try {
      await api('/api/documents/tutorials/' + docModalState.id, { method: 'DELETE' });
      closeDoc();
      docViews.tutorials.refresh();
    } catch (e) {
      alert('删除失败: ' + e.message);
    }
  });
  $('dmClose').addEventListener('click', closeDoc);
  $('docModal').addEventListener('click', function (e) { if (e.target === this) closeDoc(); });
  function closeDoc() { $('docModal').classList.add('hidden'); }

  /* ================= 审核队列视图 ================= */
  async function loadReview() {
    try {
      const data = await api('/api/review/pending?page=1&page_size=12');
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

  /* ================= 问答演示 ================= */
  const chatHistory = [];
  let historyPage = 0;          // 已加载的历史页码（0 = 尚未加载）
  let historyHasMore = false;
  let loadingHistory = false;

  // 渲染一条历史消息（静态，无流式效果）
  function renderHistoryMsg(role, text, opts) {
    opts = opts || {};
    let html;
    if (opts.usage) {
      html = mdRender(text, opts.citations || [])
        + '<div class="usage-line">⏱ ' + (opts.elapsed_ms != null ? opts.elapsed_ms + 'ms · ' : '')
        + '📈 ' + (opts.usage.prompt_tokens || 0) + ' in / '
        + (opts.usage.completion_tokens || 0) + ' out tokens</div>';
    } else {
      html = esc(text);
    }
    (opts.toolTrace || []).forEach(function (t) {
      html += '<div class="tool-line">'
        + '<div class="tool-head"><span class="tool-icon">⚙</span> <b>'
        + esc(t.name) + '</b><span class="tool-head-status done">'
        + (t.elapsed_ms != null ? t.elapsed_ms + 'ms' : '') + '</span></div>'
        + '</div>';
    });
    pushMsg(role, html, { markdown: role === 'bot' });
  }

  // 加载一页历史（page 递增，向上追加更早的消息）
  async function loadOlderHistory(initial) {
    if (loadingHistory || (historyHasMore === false && !initial)) return;
    loadingHistory = true;
    try {
      const data = await api('/api/chat/history?page=' + (historyPage + 1) + '&rounds=5');
      historyPage = data.page;
      historyHasMore = data.has_more;
      const box = $('chatMsgs');
      const older = document.createDocumentFragment();
      data.messages.forEach(function (m) {
        const role = m.role === 'user' ? 'user' : 'bot';
        if (role === 'user') {
          const div = document.createElement('div');
          div.className = 'msg user';
          div.innerHTML = '<div class="who" style="text-align:right">管理员</div>'
            + '<div class="bubble">' + esc(m.content || '') + '</div>';
          older.appendChild(div);
          return;
        }
        // bot 消息：与现场生成一致的结构（思维链折叠块 + 可展开工具行 + 正文 + usage）
        const div = document.createElement('div');
        div.className = 'msg bot';
        const bubble = document.createElement('div');
        bubble.className = 'bubble md';
        if (m.reasoning) {
          const secs = m.elapsed_ms != null ? (m.elapsed_ms / 1000).toFixed(1) : '';
          const tb = document.createElement('div');
          tb.className = 'think-block collapsed';
          tb.innerHTML = '<div class="think-head"><span class="think-icon">🤔</span>'
            + '<span class="think-title">已深度思考' + (secs ? '（用时 ' + secs + ' 秒）' : '')
            + '</span><span class="think-arrow">▾</span></div><div class="think-body"></div>';
          tb.querySelector('.think-body').textContent = m.reasoning;
          tb.querySelector('.think-head').addEventListener('click', function () {
            tb.classList.toggle('collapsed');
          });
          bubble.appendChild(tb);
        }
        (m.tool_trace || []).forEach(function (t) {
          const line = document.createElement('div');
          line.className = 'tool-line collapsed';
          const displayName = t.display || t.name;
          const desc = t.description ? ' — ' + esc(t.description) : '';
          const argsJson = JSON.stringify(t.arguments || {});
          line.innerHTML = '<div class="tool-head"><span class="tool-icon">⚙</span> <b>'
            + esc(displayName) + '</b><span class="tool-head-status done">'
            + (t.elapsed_ms != null ? t.elapsed_ms + 'ms' : '') + '</span><span class="think-arrow">▾</span></div>'
            + '<div class="tool-detail"><div class="tool-desc">' + desc + '</div>'
            + '<div class="tool-args">' + esc(argsJson) + '</div>'
            + '<div class="tool-status done"><span class="tool-summary" title="'
            + esc(t.summary || '') + '">' + esc((t.summary || '').slice(0, 60))
            + ((t.summary || '').length > 60 ? '…' : '') + '</span></div></div>';
          line.addEventListener('click', function () { line.classList.toggle('collapsed'); });
          bubble.appendChild(line);
        });
        const contentEl = document.createElement('div');
        contentEl.className = 'md-content';
        contentEl.innerHTML = mdRender(m.content || '', []);
        bubble.appendChild(contentEl);
        if (m.prompt_tokens || m.completion_tokens) {
          const usage = document.createElement('div');
          usage.className = 'usage-line';
          usage.textContent = '⏱ ' + (m.elapsed_ms != null ? m.elapsed_ms + 'ms' : '')
            + ' · 📈 ' + (m.prompt_tokens || 0) + ' in / ' + (m.completion_tokens || 0) + ' out tokens';
          bubble.appendChild(usage);
        }
        div.appendChild(document.createElement('div'));
        div.firstChild.className = 'who';
        div.firstChild.textContent = '秒回喵 · Reply-Core';
        div.appendChild(bubble);
        older.appendChild(div);
      });
      if (initial) {
        box.insertBefore(older, box.firstChild);
        box.scrollTop = box.scrollHeight;
      } else if (data.messages.length) {
        const prevHeight = box.scrollHeight;
        box.insertBefore(older, box.firstChild);
        box.scrollTop = box.scrollHeight - prevHeight; // 保持视口位置
      }
      // 顶部"加载更早"按钮
      let more = document.getElementById('loadOlderBtn');
      if (historyHasMore) {
        if (!more) {
          more = document.createElement('button');
          more.id = 'loadOlderBtn';
          more.className = 'load-older';
          more.textContent = '↑ 加载更早的对话';
          more.addEventListener('click', function () { loadOlderHistory(false); });
          box.insertBefore(more, box.firstChild);
        }
      } else if (more) {
        more.remove();
      }
    } catch (e) {
      /* 历史加载失败不打断使用 */
    } finally {
      loadingHistory = false;
    }
  }

  $('chatSend').addEventListener('click', sendChat);
  $('chatInput').addEventListener('keydown', function (e) { if (e.key === 'Enter') sendChat(); });

  /* ---------- 模型选择：列表来自后端持久化缓存，选择记住在 localStorage ---------- */
  const CHAT_MODEL_KEY = 'rc-chat-model';
  async function loadChatModels() {
    const sel = $('chatModel');
    try {
      const data = await api('/api/chat/models');
      const saved = (() => { try { return localStorage.getItem(CHAT_MODEL_KEY); } catch (e) { return null; } })();
      sel.innerHTML = data.models.map(function (m) {
        return '<option value="' + esc(m) + '">' + esc(m) + '</option>';
      }).join('');
      if (saved && data.models.indexOf(saved) >= 0) {
        sel.value = saved;                       // 恢复上次选择
      } else {
        sel.value = data.default;                // 无有效记忆则用默认模型
        if (saved) { try { localStorage.removeItem(CHAT_MODEL_KEY); } catch (e) {} }
      }
    } catch (e) {
      sel.innerHTML = '<option value="">默认</option>';
    }
  }
  $('chatModel').addEventListener('change', function () {
    try { localStorage.setItem(CHAT_MODEL_KEY, this.value); } catch (e) {}
  });

  /* ---------- 人设选择：列表来自 bot_persona（后台「技能与人设」维护） ---------- */
  async function loadChatPersonas() {
    const sel = $('chatPersona');
    if (!sel) return;
    try {
      const data = await api('/api/persona');
      sel.innerHTML = '<option value="">默认</option>'
        + data.items.filter(function (p) { return p.enabled !== false; }).map(function (p) {
          return '<option value="' + esc(p.name) + '">' + esc(p.display_name || p.name) + '</option>';
        }).join('');
      const saved = (() => { try { return localStorage.getItem('rc-chat-persona'); } catch (e) { return null; } })();
      const exists = [...sel.options].some(function (o) { return o.value === saved; });
      sel.value = exists ? saved : '';
    } catch (e) {
      sel.innerHTML = '<option value="">默认</option>';
    }
  }
  $('chatPersona').addEventListener('change', function () {
    try { localStorage.setItem('rc-chat-persona', this.value); } catch (e) {}
  });
  /* ================= DeepSeek 风格流式渲染 ================= */
  // 思维链折叠区块：流式展开滚动；正文/下一轮开始时折叠为"已深度思考（用时）"
  function createThinkBlock(bubble) {
    const block = document.createElement('div');
    block.className = 'think-block thinking';
    block.innerHTML = '<div class="think-head"><span class="think-icon">🤔</span>'
      + '<span class="think-title">正在深度思考…</span><span class="think-arrow">▾</span></div>'
      + '<div class="think-body"></div>';
    bubble.appendChild(block);
    const head = block.querySelector('.think-head');
    head.addEventListener('click', function () {
      block.classList.toggle('collapsed');
    });
    const box = document.getElementById('chatMsgs');
    return {
      block: block,
      body: block.querySelector('.think-body'),
      title: block.querySelector('.think-title'),
      text: '',
      startedAt: Date.now(),
      append: function (delta) {
        this.text += delta;
        this.body.textContent = this.text;
        box.scrollTop = box.scrollHeight; // 思考中保持滚动到底
      },
      collapse: function () {
        if (block.classList.contains('collapsed')) return;
        const secs = ((Date.now() - this.startedAt) / 1000).toFixed(1);
        this.title.textContent = '已深度思考（用时 ' + secs + ' 秒）';
        block.classList.remove('thinking');
        block.classList.add('collapsed');
      },
    };
  }

  // 工具调用行：默认缩回（仅名称+耗时），点击展开查看描述/参数/结果摘要
  function appendToolLine(bubble, data) {
    const line = document.createElement('div');
    line.className = 'tool-line running collapsed';
    const displayName = data.display || data.name;
    const desc = data.description ? ' — ' + esc(data.description) : '';
    const argsJson = JSON.stringify(data.arguments || {});
    line.innerHTML = '<div class="tool-head">'
      + '<span class="tool-icon">⚙</span> <b>' + esc(displayName) + '</b>'
      + '<span class="tool-head-status">执行中…</span>'
      + '<span class="think-arrow">▾</span></div>'
      + '<div class="tool-detail">'
      + '<div class="tool-desc">' + desc + '</div>'
      + '<div class="tool-args">' + esc(argsJson) + '</div>'
      + '<div class="tool-status">执行中…</div>'
      + '</div>';
    // 仅点击标题行切换折叠/展开，详情区域（参数/结果）可正常选中复制
    line.querySelector('.tool-head').addEventListener('click', function () {
      if (line.classList.contains('running')) return; // 执行中不可折叠
      line.classList.toggle('collapsed');
    });
    bubble.appendChild(line);
    line.__done = function (doneData) {
      line.classList.remove('running');
      const status = line.querySelector('.tool-status');
      const summary = doneData.summary || '';
      status.className = 'tool-status done';
      status.innerHTML = '✓ ' + (doneData.elapsed_ms != null ? doneData.elapsed_ms + 'ms · ' : '')
        + '<span class="tool-summary" title="' + esc(summary) + '">' + esc(summary.slice(0, 60))
        + (summary.length > 60 ? '…' : '') + '</span>';
      const headStatus = line.querySelector('.tool-head-status');
      if (headStatus) {
        headStatus.textContent = '✓ ' + (doneData.elapsed_ms != null ? doneData.elapsed_ms + 'ms' : '完成');
        headStatus.className = 'tool-head-status done';
      }
    };
    return line;
  }

  // 折叠当前气泡内所有处于展开状态的超时思考块（进入正文/新一轮时调用）
  function collapseThinking(bubble) {
    bubble.querySelectorAll('.think-block.thinking').forEach(function (b) {
      const secs = ((Date.now() - Number(b.dataset.started || Date.now())) / 1000).toFixed(1);
      const title = b.querySelector('.think-title');
      if (title && title.textContent.indexOf('已深度思考') !== 0) {
        title.textContent = '已深度思考（用时 ' + secs + ' 秒）';
      }
      b.classList.remove('thinking');
      b.classList.add('collapsed');
    });
  }

  async function sendChat() {
    const text = $('chatInput').value.trim();
    if (!text) return;
    $('chatInput').value = '';
    pushMsg('user', esc(text));
    const send = $('chatSend'); send.disabled = true;

    // 流式气泡骨架：状态行（预检索计时）/ 思维链区块 / 工具行 / 正文区
    const shell = document.createElement('div');
    shell.className = 'msg bot';
    shell.innerHTML = '<div class="who">秒回喵 · Reply-Core</div><div class="bubble md streaming"></div>';
    const bubble = shell.querySelector('.bubble');
    let chatBox = $('chatMsgs');
    chatBox.appendChild(shell);
    chatBox.scrollTop = chatBox.scrollHeight;

    // 预检索等待态：动态状态行 + 已等待时间
    const statusLine = document.createElement('div');
    statusLine.className = 'search-status';
    statusLine.innerHTML = '<span class="tool-icon">🔍</span> 正在检索知识库… <span class="search-timer">0.0s</span>';
    bubble.appendChild(statusLine);
    const searchStart = Date.now();
    const searchTimer = setInterval(function () {
      statusLine.querySelector('.search-timer').textContent =
        ((Date.now() - searchStart) / 1000).toFixed(1) + 's';
    }, 100);

    let citations = [];
    let toolTrace = [];
    let currentThink = null;      // 当前轮思维链区块
    let contentEl = null;         // 正文容器
    let contentText = '';         // 正文累积
    let renderTimer = null;       // 正文 markdown 重渲染节流
    let finalData = null;

    const flushRender = function () {
      if (!contentEl) return;
      contentEl.innerHTML = mdRender(contentText, citations);
      chatBox.scrollTop = chatBox.scrollHeight;
    };

    const handleEvent = function (event, data) {
      if (event === 'citations') {
        citations = data.citations || [];
        statusLine.innerHTML = '<span class="tool-icon">🔍</span> 已检索到 '
          + citations.length + ' 条相关资料';
        setTimeout(function () { statusLine.remove(); }, 1200);
        clearInterval(searchTimer);
      } else if (event === 'round_start') {
        if (currentThink) currentThink.collapse();
        collapseThinking(bubble);
        contentEl = null;         // 新一轮开始：重开正文区
        // 轮次标签（首轮之后才显示分隔）
        if (data.round > 1) {
          const roundLine = document.createElement('div');
          roundLine.className = 'round-line';
          roundLine.textContent = '—— 工具调用 · 第 ' + data.round + ' 轮 ——';
          bubble.appendChild(roundLine);
        }
        currentThink = createThinkBlock(bubble);
        currentThink.block.dataset.started = String(currentThink.startedAt);
        chatBox.scrollTop = chatBox.scrollHeight;
      } else if (event === 'reasoning') {
        if (!currentThink) {
          currentThink = createThinkBlock(bubble);
          currentThink.block.dataset.started = String(currentThink.startedAt);
        }
        currentThink.append(data.delta);
      } else if (event === 'tool_start') {
        currentThink && currentThink.collapse();
        collapseThinking(bubble);
        appendToolLine(bubble, data);
        chatBox.scrollTop = chatBox.scrollHeight;
      } else if (event === 'tool_end') {
        toolTrace.push({
          name: data.name, summary: data.summary, elapsed_ms: data.elapsed_ms,
          arguments: {},
        });
        const lines = bubble.querySelectorAll('.tool-line.running');
        if (lines.length) lines[lines.length - 1].__done(data);
      } else if (event === 'image') {
        // 工具产出的图片（如塔罗牌阵）：插入气泡渲染
        const img = document.createElement('img');
        img.className = 'tool-image';
        img.src = data.data_url;
        img.alt = data.name || '工具生成图片';
        bubble.appendChild(img);
        chatBox.scrollTop = chatBox.scrollHeight;
      } else if (event === 'content') {
        if (currentThink) currentThink.collapse();
        collapseThinking(bubble);
        if (!contentEl) {
          contentEl = document.createElement('div');
          contentEl.className = 'md-content';
          bubble.appendChild(contentEl);
        }
        contentText += data.delta;
        if (!renderTimer) {
          renderTimer = setTimeout(function () {
            renderTimer = null;
            flushRender();
          }, 80);
        }
      } else if (event === 'final') {
        finalData = data;
      } else if (event === 'degraded') {
        finalData = data;
      } else if (event === 'error') {
        pushMsg('bot', '<span style="color:var(--warn)">请求失败: ' + esc(data.message) + '</span>');
      }
    };

    try {
      // SSE 流式：POST + ReadableStream 手动解析（EventSource 不支持 POST）
      const resp = await fetch('/api/chat/stream', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: text, scope: $('chatScope').value, history: chatHistory.slice(-6),
          model: $('chatModel').value || undefined,
          persona: ($('chatPersona') ? $('chatPersona').value : '') || undefined,
        }),
      });
      if (!resp.ok || !resp.body) {
        throw new Error('流式接口不可用（' + resp.status + '）');
      }

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let sep;
        while ((sep = buffer.indexOf('\n\n')) >= 0) {
          const rawEvent = buffer.slice(0, sep);
          buffer = buffer.slice(sep + 2);
          let eventName = 'message', dataStr = '';
          rawEvent.split('\n').forEach(function (line) {
            if (line.startsWith('event:')) eventName = line.slice(6).trim();
            else if (line.startsWith('data:')) dataStr += line.slice(5).trim();
          });
          if (!dataStr) continue;
          try { handleEvent(eventName, JSON.parse(dataStr)); } catch (e) { /* 单事件容错 */ }
        }
      }
      if (renderTimer) { clearTimeout(renderTimer); renderTimer = null; }
      clearInterval(searchTimer);

      // 定稿：完整 markdown 重渲染 + 收尾信息
      bubble.classList.remove('streaming');  // 移除流式光标
      collapseThinking(bubble);
      if (finalData && finalData.degraded) {
        const note = document.createElement('div');
        note.className = 'degrade-note';
        note.textContent = '⚠ 已降级为仅检索结果：' + (finalData.degrade_reason || '');
        bubble.appendChild(note);
        if (finalData.citations && finalData.citations.length) {
          const dv = document.createElement('div');
          dv.className = 'md-content';
          dv.innerHTML = finalData.citations.map(function (c, i) {
            return '<p>[资料' + (i + 1) + ']《' + esc(c.title) + '》</p>';
          }).join('');
          bubble.appendChild(dv);
        }
      } else if (contentEl) {
        contentEl.innerHTML = mdRender(contentText, citations);  // 最终定稿渲染
        if (finalData && (finalData.prompt_tokens || finalData.completion_tokens)) {
          const usage = document.createElement('div');
          usage.className = 'usage-line';
          usage.textContent = '⏱ ' + (finalData.elapsed_ms != null ? finalData.elapsed_ms + 'ms' : '')
            + ' · 📈 ' + (finalData.prompt_tokens || 0) + ' in / '
            + (finalData.completion_tokens || 0) + ' out tokens';
          bubble.appendChild(usage);
        }
      }
      if (contentText) {
        chatHistory.push({ role: 'user', content: text });
        chatHistory.push({ role: 'assistant', content: contentText });
      }
    } catch (e) {
      if (renderTimer) clearTimeout(renderTimer);
      clearInterval(searchTimer);
      statusLine.remove();
      shell.remove();
      pushMsg('bot', '<span style="color:var(--warn)">请求失败: ' + esc(e.message) + '</span>');
    } finally {
      send.disabled = false;
    }
  }
  function pushMsg(role, html, opts) {
    const div = document.createElement('div');
    div.className = 'msg ' + role;
    const bubbleCls = 'bubble' + (opts && opts.markdown ? ' md' : '');
    div.innerHTML = '<div class="who"' + (role === 'user' ? ' style="text-align:right"' : '') + '>'
      + (role === 'user' ? '管理员' : '秒回喵 · Reply-Core') + '</div>'
      + '<div class="' + bubbleCls + '">' + html + '</div>';
    const box = $('chatMsgs');
    box.appendChild(div);
    box.scrollTop = box.scrollHeight;
    return div;
  }
  /* Markdown 渲染：marked 解析 → DOMPurify 净化（防 XSS）→ 引用徽章替换。
     库未加载时降级为纯文本（转义 + 换行）。
     注：数字间的波浪号（如 6~7、25~28，常见于气象/数值范围）先转义，
     防止被 GFM 误解析为删除线。 */
  function mdRender(text, citations) {
    let html;
    if (window.marked && window.DOMPurify) {
      marked.setOptions({ breaks: true, gfm: true });
      const safe = (text || '').replace(/(\d)~(\d)/g, '$1\\~$2');
      html = DOMPurify.sanitize(marked.parse(safe));
    } else {
      html = esc(text).replace(/\n/g, '<br>');
    }
    return linkCitations(html, citations);
  }
  /* 把回复中的 [资料N] 替换为引用徽章（悬停显示来源标题）。
     输入应为已转义/已净化的 HTML，不做二次转义。 */
  function linkCitations(html, citations) {
    return html.replace(/\[资料(\d+)\]/g, function (m, n) {
      const c = citations[Number(n) - 1];
      const tip = c ? ' title="' + esc(c.title) + '"' : '';
      return '<span class="cite"' + tip + '>资料' + n + '</span>';
    });
  }

  /* ================= 工具 ================= */
  /* 左栏折叠 + 棱彩星可拖动开关（GSAP Draggable，限定屏幕内） */
  const navToggleBtn = document.getElementById('navToggle');
  if (navToggleBtn) {
    navToggleBtn.addEventListener('click', function () {
      document.querySelector('.app').classList.toggle('nav-collapsed');
    });
  }
  const gemFab = document.getElementById('gemToggle');
  if (gemFab && window.gsap && window.Draggable) {
    gsap.registerPlugin(Draggable);
    let drawerHidden = false;
    Draggable.create(gemFab, {
      type: 'x,y',
      bounds: document.body,
      edgeResistance: .75,
      onClick: function () {
        drawerHidden = !drawerHidden;
        document.querySelector('.app').classList.toggle('drawer-hidden', drawerHidden);
        gemFab.classList.toggle('hidden-state', drawerHidden);
      },
    });
  }

  /* 问答抽屉左缘拖拽调宽：宽度存 CSS 变量并持久化到 localStorage */
  const ASIDE_W_KEY = 'rc-aside-w';
  const appEl = document.querySelector('.app');
  const resizer = document.getElementById('asideResizer');
  (function restoreAsideWidth() {
    let saved;
    try { saved = Number(localStorage.getItem(ASIDE_W_KEY)); } catch (e) { saved = 0; }
    if (saved >= 320 && saved <= 760) appEl.style.setProperty('--aside-w', saved + 'px');
  })();
  if (resizer) {
    let startX = 0, startW = 0, curW = 0;
    const clampW = function (w) {
      return Math.round(Math.min(Math.max(w, 320), Math.min(760, window.innerWidth - 520)));
    };
    resizer.addEventListener('pointerdown', function (e) {
      e.preventDefault();
      try { resizer.setPointerCapture(e.pointerId); } catch (err) { /* 合成事件无活跃指针 */ }
      resizer.classList.add('dragging');
      document.querySelector('aside').classList.add('resizing'); // 拖拽中关闭宽度动画
      startX = e.clientX;
      startW = document.querySelector('aside').getBoundingClientRect().width;
      curW = startW;
    });
    resizer.addEventListener('pointermove', function (e) {
      if (!resizer.classList.contains('dragging')) return;
      curW = clampW(startW + (startX - e.clientX)); // 抽屉在右侧：左拖增宽
      appEl.style.setProperty('--aside-w', curW + 'px');
    });
    const finishDrag = function () {
      if (!resizer.classList.contains('dragging')) return;
      resizer.classList.remove('dragging');
      document.querySelector('aside').classList.remove('resizing');
      // 存拖拽目标值而非实时 rect：松手时宽度过渡动画可能尚未结束
      try { localStorage.setItem(ASIDE_W_KEY, curW); } catch (err) {}
    };
    resizer.addEventListener('pointerup', finishDrag);
    resizer.addEventListener('pointercancel', finishDrag);
  }

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
