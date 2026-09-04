(() => {
    'use strict';
    const root = document.getElementById('assistant-app');
    if (!root) return;
    const base = root.dataset.api;
    const $ = (id) => document.getElementById(id);
    const csrf = root.querySelector('[name=csrfmiddlewaretoken]').value;
    const turns = new Map();
    const traces = new Map();
    let current = null, sending = false, generation = 0, nextHistory = null, nextMessages = null;
    let pendingRequest = null, timer = null, reconnecting = false;
    let workerStartingUntil = 0, workerMessage = '', workerIsOnline = false;
    const labels = {long: '偏多', short: '偏空', wait: '观望'};
    const sceneLabels = {long: '做多怎么看', short: '做空怎么看', wait: '为什么观望'};
    const horizonLabel = (minutes) => minutes % 60 === 0 ? `${minutes / 60} 小时` : `${minutes} 分钟`;
    const selectHorizon = (minutes) => { $('horizon').value = [240, 480, 1440].includes(Number(minutes)) ? String(minutes) : '240'; };
    const fmt = (value) => typeof value === 'number' ? value.toLocaleString('zh-CN', {maximumFractionDigits: Math.abs(value) >= 100 ? 2 : 4}) : '—';
    const time = (value) => value ? new Date(value).toLocaleString('zh-CN', {timeZone: 'Asia/Shanghai', hour12: false}) : '—';
    function randomUuid() {
        const webCrypto = globalThis.crypto;
        if (typeof webCrypto?.randomUUID === 'function') return webCrypto.randomUUID();

        const bytes = new Uint8Array(16);
        if (typeof webCrypto?.getRandomValues === 'function') webCrypto.getRandomValues(bytes);
        else {
            // Older browsers and non-secure HTTP origins may expose neither API.
            // The UUID is an idempotency key, not a security credential.
            for (let index = 0; index < bytes.length; index++) bytes[index] = Math.floor(Math.random() * 256);
        }
        bytes[6] = (bytes[6] & 0x0f) | 0x40;
        bytes[8] = (bytes[8] & 0x3f) | 0x80;
        const hex = [...bytes].map(value => value.toString(16).padStart(2, '0'));
        return `${hex.slice(0, 4).join('')}-${hex.slice(4, 6).join('')}-${hex.slice(6, 8).join('')}-${hex.slice(8, 10).join('')}-${hex.slice(10).join('')}`;
    }
    const node = (tag, cls, text) => {
        const element = document.createElement(tag);
        if (cls) element.className = cls;
        if (text !== undefined) element.textContent = text;
        return element;
    };
    async function api(url, body) {
        const response = await fetch(url, {
            method: body ? 'POST' : 'GET', credentials: 'same-origin',
            headers: body ? {'Content-Type': 'application/json', 'X-CSRFToken': csrf} : {},
            ...(body ? {body: JSON.stringify(body)} : {}),
        });
        let payload;
        try { payload = await response.json(); } catch { throw new Error('服务暂不可用，问题可能已保存，请稍后刷新查看。'); }
        if (!response.ok) throw new Error(payload.error || '请求未完成，请稍后重试。');
        return payload;
    }
    function error(message) {
        $('form-error').textContent = message;
        $('form-error').hidden = !message;
    }
    function online(value) {
        workerIsOnline = value;
        if (value) { workerStartingUntil = 0; workerMessage = ''; }
        const starting = workerStartingUntil > Date.now();
        $('worker-dot').classList.toggle('online', value);
        $('worker-status').textContent = value ? '分析服务在线' : starting ? '分析服务启动中…' : '分析服务未在线';
        $('worker-notice').hidden = value;
        $('start-worker').disabled = starting;
        $('start-worker').textContent = starting ? '正在检查并启动…' : '启动分析服务';
        $('worker-message').textContent = starting ? (workerMessage || '正在检查服务并清理符合条件的失效连接，请稍候。') : workerStartingUntil ? '服务暂未就绪。可再次点击启动，系统会重新检查并尝试恢复。' : (workerMessage || '后台分析进程未在线。点击启动会自动检查并尝试恢复服务。');
        updateControls();
    }
    $('start-worker').addEventListener('click', async () => {
        workerStartingUntil = Date.now() + 30000;
        workerMessage = '';
        online(false);
        try {
            const result = await api(root.dataset.workerApi, {});
            workerMessage = result.recovered ? '已清理失效连接，正在等待分析服务就绪。' : '正在等待分析服务就绪，页面会自动更新。';
            online(result.worker_online);
        } catch (e) {
            workerStartingUntil = 0;
            workerMessage = e.message;
            online(false);
        }
    });
    function updateControls() {
        const busy = [...turns.values()].some(turn => ['queued', 'running'].includes(turn.status));
        $('send').disabled = busy || sending;
        const running = [...turns.values()].some(turn => turn.status === 'running');
        $('send').textContent = busy ? (!workerIsOnline ? '等待服务启动' : running ? '分析中…' : '等待分析…') : sending ? '保存中…' : '发送 ↑';
        $('symbol').disabled = Boolean(current) || sending;
        $('horizon').disabled = busy || sending;
        $('new-chat').disabled = sending;
        $('empty-state').hidden = turns.size > 0;
    }
    function scrollBottom() { $('chat-scroll').scrollTop = $('chat-scroll').scrollHeight; }
    function scrollLatest() {
        const last = $('turn-list').lastElementChild;
        if (last) $('chat-scroll').scrollTop += last.getBoundingClientRect().top - $('chat-scroll').getBoundingClientRect().top - 16;
    }
    function readingPosition() {
        const scroll = $('chat-scroll'), viewport = scroll.getBoundingClientRect();
        const anchor = [...$('turn-list').children].find(item => item.getBoundingClientRect().bottom > viewport.top);
        return {id: anchor?.id, offset: anchor ? anchor.getBoundingClientRect().top - viewport.top : 0, top: scroll.scrollTop};
    }
    function restoreReadingPosition(position) {
        const scroll = $('chat-scroll'), anchor = position.id && $(position.id);
        if (anchor) scroll.scrollTop += anchor.getBoundingClientRect().top - scroll.getBoundingClientRect().top - position.offset;
        else scroll.scrollTop = position.top;
    }
    function ask(text, refresh = true) {
        $('question').value = text;
        $('refresh-data').checked = refresh;
        $('question').focus();
    }
    function renderPlan(plan) {
        const section = node('section', 'ta-plan');
        section.append(node('h3', '', `${plan.direction === 'long' ? '做多' : '做空'}候选方案 · ${horizonLabel(plan.horizon_minutes)}情景`));
        const grid = node('div', 'ta-plan-grid');
        [['参考入场（USDT）', fmt(plan.entry_price)], ['止损参考区间', plan.stop_zone.map(fmt).join(' – ')], ['止盈参考区间', plan.take_profit_zone.map(fmt).join(' – ')]].forEach(([label, value]) => {
            const cell = node('div'); cell.append(node('small', '', label), node('strong', '', value)); grid.append(cell);
        });
        section.append(grid);
        const rr = plan.risk_reward_after_cost_range.map(v => Number(v).toFixed(2)).join(' – ');
        [plan.entry_basis, `成本后收益风险比：${rr}。${plan.assessment}`, `止损依据：${plan.stop_basis}`, `止盈依据：${plan.target_basis}`, `ATR：${fmt(plan.atr)} USDT，${plan.atr_bar_minutes} 分钟 K 线。`, plan.cost_assumption, plan.expiry_note].forEach(text => section.append(node('p', '', text)));
        return section;
    }
    function renderReport(turn) {
        const report = turn.report;
        const card = node('div', 'ta-report');
        const header = node('div', 'ta-report-top');
        header.append(node('span', `ta-badge ${report.stance}`, labels[report.stance] || '观望'));
        header.append(node('span', '', `${report.symbol} · 参考价 ${fmt(report.reference_price)} · ${horizonLabel(report.horizon_minutes)}情景`));
        card.append(header);
        if (report.guard_notes?.length) card.append(node('div', 'ta-notice', report.guard_notes.join(' ')));
        card.append(node('p', 'ta-summary', report.summary));
        const scenarios = node('div', 'ta-scenarios');
        for (const key of ['long', 'short', 'wait']) {
            const scenario = report[key];
            const section = node('section', 'ta-scenario');
            section.append(node('h3', '', sceneLabels[key]), node('p', '', scenario.assessment));
            for (const [field, title] of [['supporting', '支持证据'], ['opposing', '反对或不足']]) {
                if (!scenario[field]?.length) continue;
                section.append(node('label', '', title));
                const list = node('ul'); scenario[field].forEach(text => list.append(node('li', '', text))); section.append(list);
            }
            section.append(node('label', '', '观察与确认'), node('p', '', scenario.condition));
            scenarios.append(section);
        }
        card.append(scenarios);
        (report.plans || []).forEach(plan => card.append(renderPlan(plan)));
        const footer = node('div', 'ta-report-footer');
        footer.append(node('p', '', `数据截至 ${time(report.cutoff)} 北京时间 · ${turn.refresh_data ? '本轮更新行情' : '沿用原快照'}`));
        footer.append(node('p', '', report.win_rate_note));
        if (report.follow_up) footer.append(node('p', '', report.follow_up));
        card.append(footer);
        return card;
    }
    function paintTrace(id) {
        const state = traces.get(id), panel = $(`trace-${id}`), button = $(`trace-button-${id}`);
        if (!state || !panel || !button) return;
        panel.hidden = !state.open;
        button.textContent = state.open ? '收起调用链' : '查看调用链';
        button.setAttribute('aria-expanded', String(state.open));
        if (!state.open) return;
        const signature = JSON.stringify([state.data, state.error]);
        if (panel.dataset.signature === signature) return;
        const position = readingPosition();
        const expanded = new Set([...panel.querySelectorAll('details[open]')].map(item => item.dataset.step));
        panel.replaceChildren(); panel.dataset.signature = signature;
        if (state.error) panel.append(node('p', 'ta-error', `${state.error} 收起后再次展开可重试。`));
        if (!state.data) { if (!state.error) panel.append(node('p', '', '正在读取调用记录…')); restoreReadingPosition(position); return; }
        const trace = state.data.trace;
        panel.append(node('h3', '', '本轮调用链'));
        const stats = `${trace.model || '模型尚未调用'} · 模型调用 ${trace.model_calls} 次 · 工具记录 ${trace.tool_records} 条${trace.elapsed_seconds !== null ? ` · 处理耗时 ${trace.elapsed_seconds} 秒` : ''}`;
        panel.append(node('p', 'ta-trace-meta', stats), node('p', 'ta-trace-note', trace.note));
        const steps = node('ol', 'ta-trace-steps');
        trace.steps.forEach(step => {
            const li = node('li', `ta-trace-step ${step.status}`);
            li.append(node('strong', '', step.title), node('small', '', step.actor));
            li.append(node('p', '', step.description));
            if (step.recorded_at) li.append(node('small', '', `记录时间：${time(step.recorded_at)} 北京时间`));
            if (Object.keys(step.details || {}).length) {
                const detail = node('details', 'ta-evidence'); detail.dataset.step = step.id;
                detail.open = expanded.has(step.id);
                detail.append(node('summary', '', step.id.startsWith('tool-') ? '查看参数与返回结果' : '查看记录详情'), node('pre', '', JSON.stringify(step.details, null, 2)));
                li.append(detail);
            }
            steps.append(li);
        });
        panel.append(steps);
        const raw = node('details', 'ta-evidence'); raw.dataset.step = 'raw'; raw.open = expanded.has('raw');
        raw.append(node('summary', '', '查看完整数据依据与用量'), node('pre', '', JSON.stringify(state.data, null, 2)));
        panel.append(raw);
        restoreReadingPosition(position);
    }
    async function loadTrace(id) {
        const state = traces.get(id);
        if (!state?.open || state.loading) return;
        state.loading = true; state.error = '';
        try { state.data = await api(`/trading-assistant/api/turns/${id}/evidence/`); }
        catch (e) { state.error = e.message; }
        finally {
            state.loading = false;
            if (traces.get(id) === state) paintTrace(id);
        }
    }
    function renderTurn(turn) {
        const wrap = node('article', 'ta-turn'); wrap.id = `turn-${turn.id}`;
        wrap.dataset.signature = JSON.stringify(turn);
        wrap.append(node('div', 'ta-user', turn.question));
        wrap.append(node('div', 'ta-agent-label', '开仓分析助手'));
        if (turn.status === 'succeeded') {
            wrap.append(renderReport(turn));
            const actions = node('div', 'ta-actions');
            [['如果开多，什么价格合适？止盈止损如何设置？', '讨论做多方案'], ['如果开空，什么价格合适？止盈止损如何设置？', '讨论做空方案'], ['请解释最近一份报告最关键的反对证据。', '解释最近报告']].forEach(([question, label], index) => {
                const button = node('button', '', label); button.type = 'button';
                button.addEventListener('click', () => {
                    ask(question, index !== 2);
                });
                actions.append(button);
            });
            wrap.append(actions);
        } else if (turn.status === 'failed') {
            wrap.append(node('div', 'ta-error', turn.safe_error || '本轮未完成，问题已保存。'));
            const retry = node('button', 'ta-text-button', '重新提问'); retry.type = 'button';
            retry.addEventListener('click', () => ask(turn.question)); wrap.append(retry);
        } else {
            const pending = node('div', 'ta-pending', turn.progress + '。你可以离开页面，稍后回来查看。');
            pending.setAttribute('role', 'status'); wrap.append(pending);
        }
        wrap.append(node('div', 'ta-meta', `${time(turn.created_at)} 北京时间${turn.prompt_version ? ` · ${turn.model_name} · ${turn.prompt_version}` : ''}`));
        if (!traces.has(turn.id)) traces.set(turn.id, {open: false, data: null, error: '', loading: false});
        const toggle = node('button', 'ta-trace-toggle', '查看调用链'); toggle.type = 'button';
        toggle.id = `trace-button-${turn.id}`; toggle.setAttribute('aria-expanded', 'false');
        toggle.setAttribute('aria-controls', `trace-${turn.id}`);
        const panel = node('section', 'ta-trace'); panel.id = `trace-${turn.id}`; panel.hidden = true;
        panel.setAttribute('aria-label', '本轮调用链');
        toggle.addEventListener('click', () => {
            const state = traces.get(turn.id); state.open = !state.open;
            paintTrace(turn.id);
            if (state.open && (!state.data || state.error || ['queued', 'running'].includes(state.data.trace.status))) loadTrace(turn.id);
        });
        wrap.append(toggle, panel);
        return wrap;
    }
    function mergeTurns(items) {
        const container = $('turn-list');
        items.forEach(turn => {
            turns.set(turn.id, turn);
            const old = $(`turn-${turn.id}`);
            if (old?.dataset.signature === JSON.stringify(turn)) return;
            const element = renderTurn(turn);
            const previousPanel = old?.querySelector('.ta-trace');
            if (previousPanel && traces.get(turn.id)?.open) element.querySelector('.ta-trace').replaceWith(previousPanel);
            if (old) old.replaceWith(element); else container.append(element);
            paintTrace(turn.id);
        });
        // Moving every existing node on each poll disrupts focus, selection and
        // browser scroll anchoring even when the response has not changed.
        [...turns.values()].sort((a, b) => a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id)).forEach((turn, index) => {
            const element = $(`turn-${turn.id}`);
            if (container.children[index] !== element) container.insertBefore(element, container.children[index] || null);
        });
        updateControls();
    }
    async function loadHistory(page = 1) {
        const payload = await api(`${base}?page=${page}`);
        online(payload.worker_online);
        if (page === 1) $('conversation-list').replaceChildren();
        payload.items.forEach(item => {
            const button = node('button', `ta-conversation${current?.id === item.id ? ' active' : ''}`);
            button.type = 'button'; button.dataset.conversation = item.id;
            button.append(node('strong', '', item.title), node('small', '', `${item.symbol} · ${new Date(item.updated_at).toLocaleDateString('zh-CN', {timeZone: 'Asia/Shanghai'})}`));
            button.addEventListener('click', () => { if (!sending) selectConversation(item.id).catch(e => error(e.message)); });
            $('conversation-list').append(button);
        });
        nextHistory = payload.next_page;
        $('more-conversations').hidden = !nextHistory;
    }
    async function loadMessages(page = 1) {
        if (!current) return;
        const epoch = generation, id = current.id;
        const payload = await api(`${base}${id}/?page=${page}`);
        if (epoch !== generation) return;
        // Capture after the request, so scrolling while it was in flight wins.
        const position = readingPosition();
        if (current.horizon_minutes !== payload.conversation.horizon_minutes) selectHorizon(payload.conversation.horizon_minutes);
        current = payload.conversation;
        mergeTurns(payload.turns);
        online(payload.worker_online);
        if (page > 1 || nextMessages === null) {
            nextMessages = payload.next_page;
            $('older-messages').hidden = !nextMessages;
        }
        restoreReadingPosition(position);
    }
    async function selectConversation(id, changeUrl = true) {
        generation++;
        turns.clear(); traces.clear(); $('turn-list').replaceChildren(); nextMessages = null;
        current = id ? {id} : null;
        pendingRequest = null; error('');
        $('question').value = ''; $('refresh-data').checked = true;
        selectHorizon(240);
        if (changeUrl) history.pushState({}, '', id ? `?conversation=${id}` : location.pathname);
        if (id) {
            await loadMessages();
            if (current?.symbol) $('symbol').value = current.symbol;
            if (current?.horizon_minutes) selectHorizon(current.horizon_minutes);
        }
        updateControls();
        document.querySelectorAll('[data-conversation]').forEach(button => button.classList.toggle('active', button.dataset.conversation === id));
        scrollLatest();
    }
    $('chat-form').addEventListener('submit', async event => {
        event.preventDefault();
        if ($('send').disabled) return;
        const question = $('question').value.trim();
        if (!question) return;
        sending = true; updateControls(); error('');
        try {
            if (!current) {
                current = await api(base, {symbol: $('symbol').value, horizon_minutes: Number($('horizon').value)});
                history.replaceState({}, '', `?conversation=${current.id}`);
            }
            const body = {question, refresh_data: $('refresh-data').checked, horizon_minutes: Number($('horizon').value)};
            const key = JSON.stringify([current.id, body]);
            if (!pendingRequest || pendingRequest.key !== key) pendingRequest = {key, id: randomUuid()};
            const payload = await api(`${base}${current.id}/messages/`, {...body, request_id: pendingRequest.id});
            pendingRequest = null;
            $('question').value = '';
            mergeTurns([payload.turn]); online(payload.worker_online); scrollBottom();
            await loadHistory();
        } catch (e) { error(e.message); }
        finally { sending = false; updateControls(); }
    });
    $('question').addEventListener('keydown', event => {
        if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); $('chat-form').requestSubmit(); }
    });
    $('new-chat').addEventListener('click', () => selectConversation(null));
    $('older-messages').addEventListener('click', () => loadMessages(nextMessages).catch(e => error(e.message)));
    $('more-conversations').addEventListener('click', () => loadHistory(nextHistory).catch(e => error(e.message)));
    document.querySelectorAll('[data-question]').forEach(button => button.addEventListener('click', () => ask(button.dataset.question)));
    window.addEventListener('popstate', () => selectConversation(new URLSearchParams(location.search).get('conversation'), false).catch(e => error(e.message)));
    async function poll() {
        try {
            if (current) await loadMessages();
            else online((await api(root.dataset.workerApi)).worker_online);
            await Promise.all([...traces].filter(([, state]) => state.open && (!state.data || ['queued', 'running'].includes(state.data.trace.status))).map(([id]) => loadTrace(id)));
            if (reconnecting) { error(''); reconnecting = false; }
        }
        catch (e) { reconnecting = true; error('连接暂时中断，正在自动重连。已保存的问题不会丢失。'); }
        timer = setTimeout(poll, 3000);
    }
    window.addEventListener('pagehide', () => clearTimeout(timer));
    async function start() {
        await loadHistory();
        const id = new URLSearchParams(location.search).get('conversation');
        if (id && /^[0-9a-f-]{36}$/i.test(id)) await selectConversation(id, false);
        updateControls(); poll();
    }
    start().catch(e => error(e.message));
})();
