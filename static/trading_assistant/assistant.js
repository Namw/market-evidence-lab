(() => {
    'use strict';
    const root = document.getElementById('assistant-app');
    if (!root) return;
    const base = root.dataset.api;
    const $ = (id) => document.getElementById(id);
    const csrf = root.querySelector('[name=csrfmiddlewaretoken]').value;
    const turns = new Map();
    let current = null, sending = false, generation = 0, nextHistory = null, nextMessages = null;
    let pendingRequest = null, timer = null, reconnecting = false;
    let workerStartingUntil = 0, workerMessage = '', workerIsOnline = false;
    const labels = {long: '偏多', short: '偏空', wait: '观望'};
    const sceneLabels = {long: '做多怎么看', short: '做空怎么看', wait: '为什么观望'};
    const horizonLabel = (minutes) => minutes % 60 === 0 ? `${minutes / 60} 小时` : `${minutes} 分钟`;
    const selectHorizon = (minutes) => { $('horizon').value = [240, 480, 1440].includes(Number(minutes)) ? String(minutes) : '240'; };
    const fmt = (value) => typeof value === 'number' ? value.toLocaleString('zh-CN', {maximumFractionDigits: Math.abs(value) >= 100 ? 2 : 4}) : '—';
    const time = (value) => value ? new Date(value).toLocaleString('zh-CN', {timeZone: 'Asia/Shanghai', hour12: false}) : '—';
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
        const details = node('details', 'ta-evidence');
        details.append(node('summary', '', '查看数据依据与计算记录'));
        details.addEventListener('toggle', async () => {
            if (!details.open || details.dataset.loaded === 'true' || details.dataset.loading === 'true') return;
            details.querySelector('pre')?.remove();
            details.dataset.loading = 'true';
            const pre = node('pre', '', '正在读取…'); details.append(pre);
            try {
                const result = await api(`/trading-assistant/api/turns/${turn.id}/evidence/`);
                pre.textContent = JSON.stringify(result, null, 2);
                details.dataset.loaded = 'true';
            } catch (e) { pre.textContent = `${e.message} 收起后再次展开可重试。`; }
            finally { details.dataset.loading = 'false'; }
        });
        wrap.append(details);
        return wrap;
    }
    function mergeTurns(items) {
        const container = $('turn-list');
        items.forEach(turn => {
            turns.set(turn.id, turn);
            const old = $(`turn-${turn.id}`);
            if (old?.dataset.signature === JSON.stringify(turn)) return;
            const element = renderTurn(turn);
            if (old) old.replaceWith(element); else container.append(element);
        });
        [...turns.values()].sort((a, b) => a.created_at.localeCompare(b.created_at)).forEach(turn => container.append($(`turn-${turn.id}`)));
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
        const atBottom = $('chat-scroll').scrollHeight - $('chat-scroll').scrollTop - $('chat-scroll').clientHeight < 150;
        const completed = payload.turns.some(turn => turn.status === 'succeeded' && turns.get(turn.id)?.status !== 'succeeded');
        if (current.horizon_minutes !== payload.conversation.horizon_minutes) selectHorizon(payload.conversation.horizon_minutes);
        current = payload.conversation;
        mergeTurns(payload.turns);
        online(payload.worker_online);
        if (page === 1 && atBottom) { if (completed) scrollLatest(); else scrollBottom(); }
        if (page > 1 || nextMessages === null) {
            nextMessages = payload.next_page;
            $('older-messages').hidden = !nextMessages;
        }
    }
    async function selectConversation(id, changeUrl = true) {
        generation++;
        turns.clear(); $('turn-list').replaceChildren(); nextMessages = null;
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
            if (!pendingRequest || pendingRequest.key !== key) pendingRequest = {key, id: crypto.randomUUID()};
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
