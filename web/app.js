'use strict';

/* ==================== 小工具 ==================== */
const $ = (sel) => document.querySelector(sel);

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));

// 模型爱用 **加粗**。先转义再只放开 <b>，其他标记原样留着。
const mdLite = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>');

const pct = (v, d = 1) => (v == null ? '—' : (v * 100).toFixed(d) + '%');
const signed = (v, d = 1) => (v == null ? '—' : (v >= 0 ? '+' : '') + (v * 100).toFixed(d) + '%');
const num = (v) => (v == null ? '—' : v.toLocaleString('zh-CN'));

const RARITY = { prismatic: '棱彩', gold: '金色', silver: '银色' };

const loading = (text) => `<div class="loading"><span class="spinner"></span>${esc(text)}</div>`;
const showError = (el, msg) => { el.innerHTML = `<div class="error">${esc(msg)}</div>`; };

async function postJSON(url, body) {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  let data = null;
  try { data = await res.json(); } catch { /* 不是 JSON */ }
  if (!res.ok) {
    const detail = data && data.detail;
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail || `HTTP ${res.status}`));
  }
  return data;
}

/* ==================== 搜索下拉 ==================== */
class Picker {
  constructor(root, opts) {
    this.root = root;
    this.items = opts.items || [];
    this.multi = !!opts.multi;
    this.placeholder = opts.placeholder || '搜索…';
    this.labelOf = opts.labelOf || ((it) => it.name);
    this.subOf = opts.subOf || (() => '');
    this.searchFields = opts.searchFields || ['name'];
    this.exclude = opts.exclude || (() => false);
    this.onChange = opts.onChange || (() => {});

    this.selected = this.multi ? [] : null;
    this.active = -1;
    this.results = [];
    this._build();
  }

  get value() { return this.multi ? [...this.selected] : this.selected; }

  _build() {
    this.root.classList.add('picker');

    this.input = document.createElement('input');
    this.input.type = 'text';
    this.input.autocomplete = 'off';
    this.input.spellcheck = false;
    this.input.placeholder = this.placeholder;

    this.list = document.createElement('div');
    this.list.className = 'picker-list';

    this.clearBtn = document.createElement('button');
    this.clearBtn.type = 'button';
    this.clearBtn.className = 'picker-clear';
    this.clearBtn.textContent = '×';
    this.clearBtn.title = '清除';
    this.clearBtn.style.display = 'none';
    // mousedown + preventDefault：不让输入框失焦，否则 blur 会先关掉列表
    this.clearBtn.addEventListener('mousedown', (e) => {
      e.preventDefault();
      this.clear();
      this.input.focus();
    });

    this.root.append(this.input, this.list, this.clearBtn);

    this.input.addEventListener('focus', () => {
      this.input.select();
      this._renderList();
      this.root.classList.add('open');
    });
    this.input.addEventListener('input', () => {
      this.active = -1;
      this._renderList();
      this.root.classList.add('open');
    });
    this.input.addEventListener('keydown', (e) => this._onKey(e));
    this.input.addEventListener('blur', () => {
      this._close();
      this._restoreLabel();
    });
  }

  _close() { this.root.classList.remove('open'); }

  _restoreLabel() {
    if (this.multi) { this.input.value = ''; return; }
    const item = this.selected != null
      ? this.items.find((it) => it.id === this.selected) : null;
    this.input.value = item ? this.labelOf(item) : '';
  }

  _currentQuery() {
    const v = this.input.value.trim();
    if (!this.multi && this.selected != null) {
      const item = this.items.find((it) => it.id === this.selected);
      if (item && v === this.labelOf(item)) return '';
    }
    return v.toLowerCase();
  }

  _visible() {
    const q = this._currentQuery();
    const chosen = new Set(this.multi ? this.selected : []);
    return this.items.filter((it) => {
      if (chosen.has(it.id)) return false;
      if (this.exclude(it)) return false;
      if (!q) return true;
      return this.searchFields.some((f) => String(it[f] ?? '').toLowerCase().includes(q));
    });
  }

  _renderList() {
    const items = this._visible();
    this.results = items.slice(0, 60);

    if (!this.results.length) {
      this.list.innerHTML = '<div class="picker-empty">没有匹配项</div>';
      return;
    }

    this.list.innerHTML = this.results.map((it, i) => `
      <div class="picker-item ${i === this.active ? 'active' : ''}" data-i="${i}">
        <span>${esc(this.labelOf(it))}</span>
        <span class="sub">${esc(this.subOf(it))}</span>
      </div>`).join('');

    this.list.querySelectorAll('.picker-item').forEach((el) => {
      el.addEventListener('mousedown', (e) => {
        e.preventDefault();
        this._pick(this.results[Number(el.dataset.i)]);
      });
    });
  }

  _onKey(e) {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      if (!this.results.length) return;
      const delta = e.key === 'ArrowDown' ? 1 : -1;
      this.active = (this.active + delta + this.results.length) % this.results.length;
      this._renderList();
      const active = this.list.querySelector('.picker-item.active');
      if (active) active.scrollIntoView({ block: 'nearest' });
      return;
    }
    if (e.key === 'Enter') {
      e.preventDefault();
      const pick = this.results[this.active >= 0 ? this.active : 0];
      if (pick) this._pick(pick);
      return;
    }
    if (e.key === 'Escape') {
      this._close();
      this._restoreLabel();
      this.input.blur();
    }
  }

  _pick(item) {
    if (this.multi) {
      if (!this.selected.includes(item.id)) this.selected.push(item.id);
      this.input.value = '';
      this._renderList();
      this.root.classList.add('open');
    } else {
      this.selected = item.id;
      this.input.value = this.labelOf(item);
      this._close();
      this.input.blur();
    }
    this._syncClear();
    this.onChange(this.value);
  }

  setValue(id) {
    const item = this.items.find((it) => it.id === id);
    if (!item) { this.clear(); return; }
    if (this.multi) {
      if (!this.selected.includes(id)) this.selected.push(id);
      this.input.value = '';
    } else {
      this.selected = id;
      this.input.value = this.labelOf(item);
    }
    this._syncClear();
    this.onChange(this.value);
  }

  remove(id) {
    if (this.multi) this.selected = this.selected.filter((x) => x !== id);
    else if (this.selected === id) this.selected = null;
    this._restoreLabel();
    this._syncClear();
    this.onChange(this.value);
  }

  clear() {
    this.selected = this.multi ? [] : null;
    this.input.value = '';
    this._syncClear();
    this.onChange(this.value);
  }

  _syncClear() {
    const has = this.multi ? this.selected.length > 0 : this.selected != null;
    this.clearBtn.style.display = has && !this.multi ? 'block' : 'none';
    this.root.classList.toggle('has-value', has && !this.multi);
  }
}

/* ==================== 全局状态 ==================== */
const state = {
  champions: [],
  augments: [],
  championById: new Map(),
  augmentById: new Map(),
  champion: null,          // slug
  slots: [null, null, null],
  picked: [],
  llm: false,
};

let championPicker = null;
let slotPickers = [];
let pickedPicker = null;

/* ==================== 初始化 ==================== */
async function init() {
  bindTabs();

  try {
    const res = await fetch('/api/bootstrap');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    state.champions = data.champions;
    state.augments = data.augments;
    state.llm = data.llm_available;
    state.championById = new Map(data.champions.map((c) => [c.id, c]));
    state.augmentById = new Map(data.augments.map((a) => [a.id, a]));

    buildPickers();
    bindActions();

    const status = $('#status');
    if (state.llm) {
      status.textContent = `${data.champions.length} 英雄 · ${data.augments.length} 符文 · ${data.model}`;
      status.className = 'status ok';
    } else {
      status.textContent = `${data.champions.length} 英雄 · ${data.augments.length} 符文 · 未配置 API key`;
      status.className = 'status bad';
      status.title = '没设 MIMO_API_KEY：RAG 只返回检索结果，Agent 不可用';
    }
  } catch (e) {
    const status = $('#status');
    status.textContent = '连接失败：' + e.message;
    status.className = 'status bad';
  }
}

function buildPickers() {
  championPicker = new Picker($('#champion-picker'), {
    items: state.champions,
    placeholder: '输入英雄名或称号，比如：亚索 / 剑圣',
    labelOf: (c) => `${c.title} · ${c.name}`,
    subOf: (c) => pct(c.win_rate),
    searchFields: ['name', 'title', 'slug', 'riot_id'],
    onChange: (id) => {
      const c = state.championById.get(id);
      state.champion = c ? c.slug : null;
    },
  });

  slotPickers = [0, 1, 2].map((i) => new Picker($('#slot-' + i), {
    items: state.augments,
    placeholder: '搜索符文名或效果',
    labelOf: (a) => a.name,
    subOf: (a) => RARITY[a.rarity] || a.rarity || '',
    searchFields: ['name', 'description'],
    exclude: (a) => state.slots.some((id, j) => j !== i && id === a.id),
    onChange: (id) => { state.slots[i] = id; },
  }));

  pickedPicker = new Picker($('#picked-picker'), {
    items: state.augments,
    multi: true,
    placeholder: '搜索并添加已选符文',
    labelOf: (a) => a.name,
    subOf: (a) => RARITY[a.rarity] || a.rarity || '',
    searchFields: ['name', 'description'],
    onChange: (ids) => { state.picked = ids; renderChips(); },
  });
}

function renderChips() {
  const box = $('#picked-chips');
  box.innerHTML = state.picked.map((id) => {
    const a = state.augmentById.get(id);
    return `<span class="chip">${esc(a ? a.name : id)}
      <button type="button" data-id="${id}" title="移除">×</button></span>`;
  }).join('');

  box.querySelectorAll('button').forEach((btn) => {
    btn.addEventListener('click', () => pickedPicker.remove(Number(btn.dataset.id)));
  });
}

/* ==================== 事件绑定 ==================== */
function bindTabs() {
  document.querySelectorAll('.tab').forEach((tab) => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.tab').forEach((t) => t.classList.toggle('active', t === tab));
      document.querySelectorAll('.panel').forEach((p) => {
        p.classList.toggle('active', p.id === 'panel-' + tab.dataset.tab);
      });
    });
  });
}

function bindActions() {
  $('#advise-btn').addEventListener('click', runAdvise);
  $('#rag-btn').addEventListener('click', runRag);
  $('#agent-btn').addEventListener('click', runAgent);

  $('#level').addEventListener('keydown', (e) => { if (e.key === 'Enter') runAdvise(); });
  $('#rag-query').addEventListener('keydown', (e) => { if (e.key === 'Enter') runRag(); });
  $('#agent-question').addEventListener('keydown', (e) => { if (e.key === 'Enter') runAgent(); });

  $('#advise-example').addEventListener('click', fillAdviseExample);
  $('#rag-example').addEventListener('click', () => { $('#rag-query').value = '能回血的符文'; });
  $('#agent-example').addEventListener('click', () => {
    $('#agent-question').value = '盖伦和剑圣谁更适合秘术冲拳';
  });
}

function fillAdviseExample() {
  // README 里跑通过的那组：疾风剑豪 · 3 级，三个银色符文
  championPicker.setValue(157);
  $('#level').value = 3;
  slotPickers[0].setValue(1170);   // 万用瞄准镜
  slotPickers[1].setValue(1007);   // 大力
  slotPickers[2].setValue(2024);   // 由暴生急
}

/* ==================== ① 选符文 ==================== */
async function runAdvise() {
  const out = $('#advise-result');
  if (!state.champion) return showError(out, '先选一个英雄');
  const ids = state.slots.filter((x) => x != null);
  if (!ids.length) return showError(out, '至少选一个候选符文');

  const levelRaw = $('#level').value.trim();
  const level = levelRaw ? Number(levelRaw) : null;

  out.innerHTML = loading('正在查数据库…');
  $('#advise-btn').disabled = true;
  try {
    const data = await postJSON('/api/advise', {
      champion_slug: state.champion,
      augment_ids: ids,
      level,
      picked: state.picked,
    });
    out.innerHTML = renderAdvice(data);
  } catch (e) {
    showError(out, e.message);
  } finally {
    $('#advise-btn').disabled = false;
  }
}

function renderAdvice(data) {
  const withDelta = data.suggestions.filter((s) => s.delta != null);
  const allBelow = withDelta.length > 0 && withDelta.every((s) => s.delta < 0);

  const cards = data.suggestions.map((s, i) => {
    const top = i === 0;
    const cls = s.delta == null ? 'flat' : s.delta > 0 ? 'pos' : s.delta < 0 ? 'neg' : 'flat';
    const notes = s.notes.length
      ? `<ul class="notes">${s.notes.map((n) => `<li>⚠ ${esc(n)}</li>`).join('')}</ul>` : '';
    const combo = s.combo
      ? `<div class="combo">⚑ 和已选的「${esc(s.combo.partner)}」是热门组合：
           组合胜率 ${pct(s.combo.win_rate)}（样本 ${num(s.combo.sample_count)}）</div>` : '';

    return `
      <li class="advice-card ${top ? 'top' : ''}">
        <div class="rank">${i + 1}</div>
        <div class="advice-main">
          <div class="advice-name">${esc(s.name)}
            ${s.tier ? `<span class="tier">${esc(s.tier)}</span>` : ''}</div>
          <div class="metrics">
            <span>胜率 <b>${pct(s.win_rate)}</b></span>
            <span class="delta ${cls}">比平均 ${signed(s.delta)}</span>
            <span>样本 ${num(s.sample_count)}</span>
            <span>符文全局 ${pct(s.global_win_rate)}</span>
          </div>
          ${combo}
          ${notes}
        </div>
      </li>`;
  }).join('');

  const title = data.champion_title
    ? `${esc(data.champion_title)} · ${esc(data.champion)}`
    : esc(data.champion);

  return `
    <div class="result-head">
      <h2>${title}${data.level ? ` · ${data.level} 级` : ''}</h2>
      <span class="baseline">本赛季平均胜率 ${pct(data.baseline)}</span>
      ${data.stage ? `<span class="baseline">第 ${data.stage} 阶段</span>` : ''}
    </div>
    ${allBelow ? '<p class="hint" style="margin-bottom:12px">⚠ 这三个都低于该英雄的平均水平</p>' : ''}
    <ol class="advice-list">${cards}</ol>
    <details class="output"><summary>命令行输出（和 watch.py 一致）</summary>
      <pre>${esc(data.text)}</pre></details>`;
}

/* ==================== ② 自然语言查 ==================== */
async function runRag() {
  const out = $('#rag-result');
  const query = $('#rag-query').value.trim();
  if (!query) return showError(out, '先输入一个问题');

  const mode = document.querySelector('input[name="rag-mode"]:checked').value;
  const rewrite = $('#rag-rewrite').checked && mode === 'semantic';

  out.innerHTML = loading(mode === 'semantic' ? '加载语义模型 / 检索中（第一次要等几秒）…' : '检索中…');
  $('#rag-btn').disabled = true;
  try {
    const data = await postJSON('/api/rag', { query, mode, rewrite });
    out.innerHTML = renderRag(data);
  } catch (e) {
    showError(out, e.message);
  } finally {
    $('#rag-btn').disabled = false;
  }
}

function renderRag(data) {
  const rewrite = data.rewritten && data.rewritten !== data.query
    ? `<div class="rewrite">查询改写：<b>${esc(data.query)}</b> → ${esc(data.rewritten)}</div>`
    : '';

  const hits = data.hits.map((h, i) => `
    <div class="hit">
      <div class="hit-top">
        <span class="hit-rank">${i + 1}</span>
        <span class="hit-name">${esc(h.name)}</span>
        <span class="hit-score">${h.score.toFixed(4)}</span>
      </div>
      <div class="scorebar"><i style="width:${Math.max(2, h.score * 100).toFixed(1)}%"></i></div>
      <div class="hit-desc">${esc(h.text)}</div>
    </div>`).join('');

  const dimLabel = data.mode === 'semantic' ? '向量维度' : '词表特征数';

  const answer = data.answer
    ? `<div class="answer"><h3>模型基于以上资料的回答</h3><p>${mdLite(data.answer)}</p></div>`
    : `<div class="error" style="margin-top:16px">${esc(data.answer_error || '没有生成回答')}</div>`;

  return `
    ${rewrite}
    <div class="meta-line">
      <span>文档库 ${data.doc_count} 个符文</span>
      <span>${dimLabel} ${num(data.vector_dim)}</span>
      <span>检索方式 ${data.mode === 'semantic' ? '语义向量' : 'TF-IDF'}</span>
    </div>
    ${hits}
    ${answer}`;
}

/* ==================== ③ Agent ==================== */
async function runAgent() {
  const out = $('#agent-result');
  const question = $('#agent-question').value.trim();
  if (!question) return showError(out, '先输入一个问题');

  out.innerHTML = loading('模型思考中，可能要十几秒…');
  $('#agent-btn').disabled = true;
  try {
    const data = await postJSON('/api/agent', { question });
    out.innerHTML = renderAgent(data);
  } catch (e) {
    showError(out, e.message);
  } finally {
    $('#agent-btn').disabled = false;
  }
}

function renderAgent(data) {
  const trace = data.trace.map((t) => `
    <div class="trace-step">
      <div class="trace-head">
        <span class="trace-round">第 ${t.step} 轮</span>
        <span class="trace-tool">${esc(t.tool)}</span>
        <span class="trace-args">${esc(JSON.stringify(t.args))}</span>
      </div>
      <div class="trace-result">${esc(JSON.stringify(t.result))}</div>
    </div>`).join('');

  return `
    <div class="meta-line">
      <span>共 ${data.tool_calls} 次工具调用</span>
      <span>步数上限 ${data.max_steps}</span>
    </div>
    ${trace ? `<div class="trace">${trace}</div>` : ''}
    <div class="answer"><h3>回答</h3><p>${mdLite(data.answer)}</p></div>`;
}

/* ==================== 启动 ==================== */
init();
