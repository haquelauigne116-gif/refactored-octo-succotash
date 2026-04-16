/**
 * chat.js — 聊天功能模块
 * 包含: 会话管理、消息发送/接收(SSE)、Markdown/KaTeX渲染、附件处理、模型切换
 */

const chatHistory = document.getElementById("chat-history");
const userInput = document.getElementById("user-input");
const sendBtn = document.getElementById("send-btn");
const API_BASE = window.location.origin;

// ── 公共 API_BASE 供其他模块使用 ──
export { API_BASE, chatHistory };

// ── 视图切换 ──
function toggleSubMenu() { document.getElementById('session-list').classList.toggle('open'); }
function switchView(viewName) {
    document.querySelectorAll('.view-container').forEach(v => v.classList.remove('active'));
    document.querySelectorAll('.menu-item').forEach(m => m.classList.remove('active'));
    document.getElementById(viewName + '-view').classList.add('active');
    const menuItems = document.querySelectorAll('.menu-item');
    const viewMap = { chat: 0, task: 1, files: 2, calendar: 3, music: 4, settings: 5 };
    if (viewMap[viewName] !== undefined) menuItems[viewMap[viewName]].classList.add('active');
    if (viewName === 'task') window.loadTasks();
    if (viewName === 'files') window.loadFiles();
    if (viewName === 'settings') window.loadSettings();
    if (viewName === 'music') window.loadMusic();
    if (viewName === 'calendar') window.calendarInit();
}

function handleEnter(event) {
    if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        sendMessage();
    }
}

// ── 模型切换和配置获取 ──
let systemSettingsData = null;
let globalProviders = [];
let currentChatProvider = '';
let currentChatModel = '';

export { globalProviders, currentChatProvider, currentChatModel };

async function loadProviders() {
    try {
        const res = await fetch(API_BASE + "/providers");
        const data = await res.json();
        globalProviders = data.providers;
        currentChatProvider = data.current_provider;
        currentChatModel = data.current_model;
        updateModelLabel();
    } catch (e) {
        console.error("加载模型列表失败:", e);
    }
}

function updateModelLabel() {
    const label = document.getElementById('current-model-label');
    if (!label) return;
    let name = currentChatModel;
    let caps = [];
    globalProviders.forEach(p => {
        p.models.forEach(m => {
            if (p.id === currentChatProvider && m.id === currentChatModel) {
                name = m.name;
                caps = m.caps || ['text'];
            }
        });
    });
    const capsMap = { text: '文本', vision: '视觉', reasoning: '推理' };
    const capsStr = caps.map(c => capsMap[c] || '').join(' ');
    label.textContent = name + ' ' + capsStr;
}

// ── 附件处理 ──
let pendingChatFiles = [];

function handleChatFileSelect(event) {
    const files = event.target.files;
    if (!files.length) return;
    Array.from(files).slice(0, 4).forEach(f => pendingChatFiles.push(f));
    renderAttachPreview();
    event.target.value = '';
}

function removePendingFile(idx) {
    pendingChatFiles.splice(idx, 1);
    renderAttachPreview();
}

function renderAttachPreview() {
    const bar = document.getElementById('chat-attach-preview');
    if (pendingChatFiles.length === 0) {
        bar.style.display = 'none';
        bar.innerHTML = '';
        return;
    }
    bar.style.display = 'flex';
    bar.innerHTML = '';
    pendingChatFiles.forEach((f, i) => {
        const isImage = f.type.startsWith('image/');
        const wrap = document.createElement('div');
        wrap.style.cssText = 'position:relative; display:inline-flex; align-items:center; gap:4px; background:#f1f5f9; border-radius:6px; border:1px solid #e2e8f0; padding:2px 4px;';
        if (isImage) {
            const url = URL.createObjectURL(f);
            wrap.innerHTML = `<img src="${url}" style="width:40px; height:40px; object-fit:cover; border-radius:4px;">`;
        } else {
            const icon = f.name.endsWith('.pdf') ? '📕' : '📄';
            wrap.innerHTML = `<span style="font-size:14px;">${icon}</span><span style="font-size:11px; max-width:80px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">${f.name}</span>`;
        }
        const closeBtn = document.createElement('button');
        closeBtn.textContent = '✕';
        closeBtn.style.cssText = 'position:absolute; top:-4px; right:-4px; width:16px; height:16px; border-radius:50%; border:none; background:#ef4444; color:#fff; font-size:10px; cursor:pointer; line-height:16px; padding:0;';
        closeBtn.onclick = () => removePendingFile(i);
        wrap.appendChild(closeBtn);
        bar.appendChild(wrap);
    });
}

function fileToBase64(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => {
            const dataUrl = reader.result;
            const base64 = dataUrl.split(',')[1];
            resolve({ data: base64, mime: file.type || 'application/octet-stream', name: file.name });
        };
        reader.onerror = reject;
        reader.readAsDataURL(file);
    });
}

// ── 会话管理 ──
async function loadSessions() {
    try {
        const res = await fetch(API_BASE + "/sessions");
        const data = await res.json();
        const sessionList = document.getElementById("session-list");
        const newBtn = sessionList.querySelector('.new-session-btn');
        sessionList.innerHTML = '';
        sessionList.appendChild(newBtn);
        data.sessions.forEach(s => {
            const div = document.createElement("div");
            div.className = "sub-menu-item session-item" + (s.active ? " active-session" : "");
            const nameSpan = document.createElement("span");
            nameSpan.className = "session-name";
            nameSpan.textContent = s.name;
            nameSpan.onclick = () => switchSession(s.filename);
            div.appendChild(nameSpan);
            const delBtn = document.createElement("button");
            delBtn.className = "session-delete-btn";
            delBtn.innerHTML = `<svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><line x1="2" y1="2" x2="10" y2="10"/><line x1="10" y1="2" x2="2" y2="10"/></svg>`;
            delBtn.title = "删除此会话";
            delBtn.onclick = (e) => { e.stopPropagation(); deleteSession(s.filename, s.name); };
            div.appendChild(delBtn);
            sessionList.appendChild(div);
        });
    } catch (e) {
        console.error("加载会话列表失败:", e);
    }
}

async function createNewSession() {
    try {
        const res = await fetch(API_BASE + "/new_session", { method: "POST" });
        const data = await res.json();
        if (data.status === "ok") {
            chatHistory.innerHTML = '';
            loadSessions();
            switchView('chat');
        }
    } catch (e) {
        appendMessage("❌ 新建会话失败", "system-msg");
    }
}

async function deleteSession(filename, name) {
    if (!confirm(`确定要删除会话「${name}」吗？\n删除后无法恢复。`)) return;
    try {
        const res = await fetch(API_BASE + "/delete_session", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ filename: filename })
        });
        const data = await res.json();
        if (data.status === "ok") {
            chatHistory.innerHTML = '';
            appendMessage("你好，我是小鱼！今天需要处理什么工作？", "ai-msg");
            loadSessions();
        }
    } catch (e) {
        console.error("删除会话失败:", e);
    }
}

async function switchSession(filename) {
    try {
        const res = await fetch(API_BASE + "/switch_session", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ filename: filename })
        });
        const data = await res.json();
        if (data.status === "ok") {
            chatHistory.innerHTML = '';
            if (data.messages.length === 0) {
                appendMessage("你好，我是小鱼！今天需要处理什么工作？", "ai-msg");
            } else {
                const eventsByIndex = {};
                if (data.events && data.events.length > 0) {
                    data.events.forEach(evt => {
                        const idx = evt.after_msg_index;
                        if (!eventsByIndex[idx]) eventsByIndex[idx] = [];
                        eventsByIndex[idx].push(evt);
                    });
                }
                let msgIndex = 0;
                data.messages.forEach(msg => {
                    if (msg.role === "user") {
                        appendMessage(msg.content, "user-msg");
                        msgIndex++;
                    } else if (msg.role === "assistant") {
                        let aiHtml = '';
                        if (msg.thinking) {
                            aiHtml += renderThinkingBlock(msg.thinking, false);
                        }
                        aiHtml += renderMarkdown(msg.content);
                        appendMessage(aiHtml, "ai-msg", true);
                        msgIndex++;
                    }
                    if (eventsByIndex[msgIndex]) {
                        eventsByIndex[msgIndex].forEach(evt => {
                            if (evt.type === 'auto_task') {
                                appendMessage(renderAutoTaskCard(evt.data), "ai-msg event-msg");
                            } else if (evt.type === 'file_search') {
                                appendMessage(renderFileSearchCard(evt.data), "ai-msg event-msg");
                            } else if (evt.type === 'chat_attach') {
                                const files = evt.data.files || [];
                                if (files.length > 0) {
                                    let html = '<div style="display:flex; gap:4px; flex-wrap:wrap;">';
                                    files.forEach(f => {
                                        if (f.mime && f.mime.startsWith('image/')) {
                                            html += `<img src="${API_BASE}${f.url}" style="max-width:120px; max-height:80px; border-radius:6px; border:1px solid #e2e8f0; cursor:pointer;" onclick="window.open('${API_BASE}${f.url}','_blank')">`;
                                        } else {
                                            const icon = (f.original_name || '').endsWith('.pdf') ? '📕' : '📄';
                                            html += `<a href="${API_BASE}${f.url}" target="_blank" style="background:#f1f5f9; border:1px solid #e2e8f0; border-radius:4px; padding:2px 6px; font-size:11px; text-decoration:none; color:#334155;">${icon} ${f.original_name || f.filename}</a>`;
                                        }
                                    });
                                    html += '</div>';
                                    appendMessage(html, "user-msg attach-preview");
                                }
                            }
                        });
                    }
                });
            }
            loadSessions();
            switchView('chat');

            if (data.render_events && data.render_events.length > 0) {
                const aiMsgEls = chatHistory.querySelectorAll('.ai-msg:not(.event-msg):not(.system-msg)');
                const byTurn = {};
                data.render_events.forEach(evt => {
                    if (!byTurn[evt.turn]) byTurn[evt.turn] = [];
                    byTurn[evt.turn].push(evt);
                });
                Object.entries(byTurn).forEach(([turn, evts]) => {
                    const msgEl = aiMsgEls[parseInt(turn) - 1];
                    if (!msgEl) return;
                    let toolCardsHtml = '';
                    let mediaHtml = '';
                    evts.forEach(evt => {
                        if (evt.type === 'tool_cards' && evt.cards) {
                            toolCardsHtml = '<div class="tool-cards-row">';
                            evt.cards.forEach(c => {
                                toolCardsHtml += `<div class="tool-call-card ${c.status}">✅ 成功执行技能：${c.name}</div>`;
                            });
                            toolCardsHtml += '</div><!-- END_TOOLS -->';
                        }
                        if (evt.type === 'media' && evt.paths) {
                            mediaHtml = '<div class="media-grid">';
                            evt.paths.forEach(p => {
                                const thumbP = p.replace(/(\.\w+)$/, '_thumb$1');
                                mediaHtml += `<a href="${p}" target="_blank"><img src="${thumbP}" alt="AI 生成图片" onerror="if(this.src!=='${p}'){this.src='${p}';}" /></a>`;
                            });
                            mediaHtml += '</div><!-- END_MEDIA_GRID -->';
                        }
                    });
                    if (toolCardsHtml || mediaHtml) {
                        const contentEl = msgEl.querySelector('.message-content') || msgEl;
                        if (toolCardsHtml) contentEl.innerHTML = toolCardsHtml + contentEl.innerHTML;
                        if (mediaHtml) contentEl.innerHTML += mediaHtml;
                    }
                });
            }
        }
    } catch (e) {
        appendMessage("❌ 切换会话失败", "system-msg");
    }
}

// ── Markdown + LaTeX 渲染 ──
function renderThinkingBlock(thinkingText, isOpen) {
    const openAttr = isOpen ? ' open' : '';
    return `<details class="thinking-block"${openAttr}><summary>💭 深度思考</summary><div class="thinking-content">${thinkingText.replace(/\n/g, '<br>')}</div></details>`;
}

export function renderMarkdown(text) {
    if (!text) return '';
    try {
        if (typeof marked !== 'undefined') {
            marked.setOptions({ breaks: true, gfm: true });
            return marked.parse(text);
        }
    } catch (e) {
        console.warn('Markdown render error:', e);
    }
    return text.replace(/\n/g, '<br>');
}

function renderKaTeX(element) {
    if (typeof renderMathInElement === 'undefined') return;
    try {
        renderMathInElement(element, {
            delimiters: [
                { left: '$$', right: '$$', display: true },
                { left: '$', right: '$', display: false },
                { left: '\\(', right: '\\)', display: false },
                { left: '\\[', right: '\\]', display: true }
            ],
            throwOnError: false
        });
    } catch (e) {
        console.warn('KaTeX render error:', e);
    }
}

// ── 发送消息 (SSE) ──
async function sendMessage() {
    const text = userInput.value.trim();
    const hasFiles = pendingChatFiles.length > 0;
    if (!text && !hasFiles) return;

    let bubbleHtml = text ? text.replace(/\n/g, '<br>') : '';
    if (hasFiles) {
        let attachHtml = '<div style="display:flex; gap:4px; flex-wrap:wrap; margin-top:6px;">';
        pendingChatFiles.forEach(f => {
            if (f.type.startsWith('image/')) {
                const url = URL.createObjectURL(f);
                attachHtml += `<img src="${url}" style="max-width:120px; max-height:80px; border-radius:6px; border:1px solid #e2e8f0;">`;
            } else {
                const icon = f.name.endsWith('.pdf') ? '📕' : '📄';
                attachHtml += `<span style="background:#f1f5f9; border:1px solid #e2e8f0; border-radius:4px; padding:2px 6px; font-size:11px;">${icon} ${f.name}</span>`;
            }
        });
        attachHtml += '</div>';
        bubbleHtml += attachHtml;
    }
    appendMessage(bubbleHtml, "user-msg");
    userInput.value = "";

    let attachments = [];
    if (hasFiles) {
        try {
            attachments = await Promise.all(pendingChatFiles.map(f => fileToBase64(f)));
        } catch (e) {
            console.error('读取文件失败:', e);
        }
    }
    pendingChatFiles = [];
    renderAttachPreview();

    sendBtn.disabled = true;
    sendBtn.innerText = "思考中...";

    const aiBubble = document.createElement("div");
    aiBubble.className = "msg-bubble ai-msg streaming-cursor";
    aiBubble.innerHTML = '';
    chatHistory.appendChild(aiBubble);

    let thinkingEl = null;
    let thinkingContentEl = null;
    let replyEl = null;
    let hasThinking = false;

    let _mdRenderTimer = null;
    let _mdDirty = false;
    const MD_THROTTLE_MS = 80;

    function scheduleRender() {
        _mdDirty = true;
        if (!_mdRenderTimer) {
            _mdRenderTimer = setTimeout(() => {
                _mdRenderTimer = null;
                if (_mdDirty && replyEl) {
                    _mdDirty = false;
                    replyEl.innerHTML = renderMarkdown(replyEl.getAttribute('data-raw') || '');
                    chatHistory.scrollTop = chatHistory.scrollHeight;
                }
            }, MD_THROTTLE_MS);
        }
    }

    try {
        const payload = { user_word: text || '' };
        if (attachments.length > 0) payload.attachments = attachments;

        const response = await fetch(API_BASE + "/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop();

            let eventType = '';
            for (const line of lines) {
                if (line.startsWith('event: ')) {
                    eventType = line.slice(7).trim();
                } else if (line.startsWith('data: ') && eventType) {
                    try {
                        const data = JSON.parse(line.slice(6));

                        if (eventType === 'thinking') {
                            if (!hasThinking) {
                                hasThinking = true;
                                thinkingEl = document.createElement('details');
                                thinkingEl.className = 'thinking-block';
                                thinkingEl.open = true;
                                thinkingEl.innerHTML = '<summary>💭 深度思考</summary><div class="thinking-content"></div>';
                                aiBubble.appendChild(thinkingEl);
                                thinkingContentEl = thinkingEl.querySelector('.thinking-content');
                                sendBtn.innerText = "思考中...";
                            }
                            thinkingContentEl.textContent += data.text;
                        } else if (eventType === 'delta') {
                            if (!replyEl) {
                                replyEl = document.createElement('div');
                                aiBubble.appendChild(replyEl);
                                if (hasThinking && thinkingEl) thinkingEl.open = false;
                                sendBtn.innerText = "输出中...";
                            }
                            replyEl.setAttribute('data-raw', (replyEl.getAttribute('data-raw') || '') + data.text);
                            scheduleRender();
                        } else if (eventType === 'replace_all') {
                            if (!replyEl) {
                                replyEl = document.createElement('div');
                                aiBubble.appendChild(replyEl);
                                if (hasThinking && thinkingEl) thinkingEl.open = false;
                                sendBtn.innerText = "输出中...";
                            }
                            replyEl.setAttribute('data-raw', data.text);
                            replyEl.innerHTML = renderMarkdown(data.text);
                        } else if (eventType === 'loop_status') {
                            sendBtn.innerText = `推理中(${data.loop}/${data.max})...`;
                        } else if (eventType === 'done') {
                            if (data.auto_task) {
                                appendMessage(renderAutoTaskCard(data.auto_task), "ai-msg event-msg");
                            }
                            if (data.auto_schedule) {
                                appendMessage(renderAutoScheduleCard(data.auto_schedule), "ai-msg event-msg");
                            }
                            if (data.file_search_result && data.file_search_result.files && data.file_search_result.files.length > 0) {
                                appendMessage(renderFileSearchCard(data.file_search_result), "ai-msg event-msg");
                            }
                        } else if (eventType === 'error') {
                            aiBubble.innerHTML = `❌ <strong>AI 接口错误</strong><br>${data.message}`;
                        }
                    } catch (e) {
                        console.warn('SSE parse error:', e, line);
                    }
                    eventType = '';
                }
            }
            chatHistory.scrollTop = chatHistory.scrollHeight;
        }
    } catch (error) {
        aiBubble.innerHTML = "❌ 网络连接失败：请确保你的终端已经运行了 `uvicorn backend.server:app`！";
        aiBubble.className = "msg-bubble system-msg";
    } finally {
        if (_mdRenderTimer) { clearTimeout(_mdRenderTimer); _mdRenderTimer = null; }
        aiBubble.classList.remove('streaming-cursor');
        if (replyEl) {
            const rawText = replyEl.getAttribute('data-raw') || '';
            replyEl.innerHTML = renderMarkdown(rawText);
            renderKaTeX(replyEl);
        }
        sendBtn.disabled = false;
        sendBtn.innerText = "发送";
        chatHistory.scrollTop = chatHistory.scrollHeight;
    }
}

// ── 可复用的卡片渲染函数 ──
export function renderAutoTaskCard(t) {
    const triggerLabel = t.trigger_type === 'interval' ? '⏱ 间隔' :
        t.trigger_type === 'cron' ? '📅 周期' : '📌 一次性';
    const triggerInfo = window.formatTriggerArgs(t.trigger_type, t.trigger_args);
    return `<div class="auto-task-card">
                <span class="auto-task-header">✅ 已创建任务</span>
                <span class="auto-task-name">${t.task_name}</span>
                <span class="auto-task-info">${triggerLabel} ${triggerInfo}</span>
                <button class="task-btn task-btn-delete" style="margin-left:auto;" onclick="deleteTask('${t.task_id}'); this.closest('.auto-task-card').innerHTML='<span style=color:#999>✖ 已取消</span>'">取消</button>
            </div>`;
}

function renderAutoScheduleCard(s) {
    const catEmoji = { '工作': '💼', '会议': '🤝', '学习': '📚', '个人': '🏠', '健康': '💪', '其他': '📌' };
    const emoji = catEmoji[s.category] || '📅';
    const time = s.all_day ? '全天' : ((s.start_time || '').slice(11, 16) + ' - ' + (s.end_time || '').slice(11, 16));
    const date = (s.start_time || '').slice(0, 10);
    const loc = s.location ? ` 📍${s.location}` : '';
    return `<div class="auto-task-card" style="border-left-color:${s.color || '#4F86F7'}">
                <span class="auto-task-header">📅 已创建日程</span>
                <span class="auto-task-name">${emoji} ${s.title}</span>
                <span class="auto-task-info">📆 ${date} · 🕒 ${time}${loc}</span>
                <button class="task-btn task-btn-delete" style="margin-left:auto;" onclick="fetch(API_BASE+'/api/schedules/${s.id}',{method:'DELETE'});this.closest('.auto-task-card').innerHTML='<span style=color:#999>✖ 已取消</span>'">取消</button>
            </div>`;
}

export function renderFileSearchCard(search_res) {
    const fileCards = search_res.files.map(f => {
        const icon = window.getFileIcon(f.content_type);
        const size = window.formatSize(f.size || 0);
        const name = f.original_name;
        const scoreHtml = f._score ? `<span class="file-score-badge">✨ ${f._score}分</span>` : '';
        return `<div class="file-search-item" title="${name}" onclick="window._downloadFile('${f.download_url}', '${name}')">
                    <div class="file-icon-wrapper">${icon}</div>
                    <span class="file-name">${name}</span>
                    <div class="file-size">
                        <span class="file-size-text">${size}</span>
                        ${scoreHtml}
                    </div>
                </div>`;
    }).join('');

    return `<div class="auto-task-card file-search-container">
                <div class="file-search-header">
                    <span class="header-icon">🧠</span> 
                    <span>AI 为你找到了 ${search_res.files.length} 个文件</span>
                </div>
                <div class="file-search-grid">${fileCards}</div>
            </div>`;
}

export function appendMessage(text, className, skipEscape) {
    const div = document.createElement("div");
    div.className = "msg-bubble " + className;
    if (skipEscape) {
        div.innerHTML = text;
    } else {
        div.innerHTML = text.replace(/\n/g, '<br>');
    }
    chatHistory.appendChild(div);
    if (className.includes('ai-msg')) {
        renderKaTeX(div);
    }
    chatHistory.scrollTop = chatHistory.scrollHeight;
}

// ── 设置面板 ──
async function loadSettings() {
    try {
        const res = await fetch(API_BASE + "/api/settings");
        const data = await res.json();
        systemSettingsData = data.settings;

        const keysContainer = document.getElementById('api-keys-container');
        keysContainer.innerHTML = '';
        for (const [pid, pinfo] of Object.entries(data.providers_status)) {
            keysContainer.innerHTML += `
                        <div class="settings-form-row">
                            <label style="width: 150px;">${pinfo.name} Key:</label>
                            <input type="text" id="apikey-${pid}" class="settings-input" placeholder="留空则不修改 (当前: ${pinfo.api_key_masked})">
                        </div>
                    `;
        }

        document.getElementById('bailian-api-key').placeholder = data.settings.bailian_api_key
            ? `(已配置, 留空不修改)`
            : `用于接入官方 MCP 服务 (画图/地图/搜索/语音)`;
        document.getElementById('enable-mcp-checkbox').checked = !!data.settings.enable_mcp_for_chat;
        const loopsVal = data.settings.max_tool_loops || 6;
        document.getElementById('max-tool-loops-slider').value = loopsVal;
        document.getElementById('max-tool-loops-val').textContent = loopsVal;

        const chatSelect = document.getElementById('chat-model-select');
        const sumSelect = document.getElementById('summary-model-select');
        const judgeSelect = document.getElementById('judge-model-select');
        const fileSelect = document.getElementById('file-model-select');
        const taskSelect = document.getElementById('task-model-select');
        chatSelect.innerHTML = '';
        sumSelect.innerHTML = '';
        judgeSelect.innerHTML = '';
        fileSelect.innerHTML = '';
        taskSelect.innerHTML = '';

        globalProviders.forEach(p => {
            const makeGroup = () => { let g = document.createElement('optgroup'); g.label = p.name; return g; };
            const gc = makeGroup(), gs = makeGroup(), gj = makeGroup(), gf = makeGroup(), gt = makeGroup();
            p.models.forEach(m => {
                const capsMap = { text: '文本', vision: '视觉', reasoning: '推理' };
                const capsStr = (m.caps || ['text']).map(c => capsMap[c] || c).join(' ');
                const getOpt = () => { let o = document.createElement('option'); o.value = p.id + '|' + m.id; o.textContent = m.name + ' ' + capsStr; return o; };
                gc.appendChild(getOpt());
                gs.appendChild(getOpt());
                gj.appendChild(getOpt());
                gf.appendChild(getOpt());
                gt.appendChild(getOpt());
            });
            chatSelect.appendChild(gc);
            sumSelect.appendChild(gs);
            judgeSelect.appendChild(gj);
            fileSelect.appendChild(gf);
            taskSelect.appendChild(gt);
        });

        chatSelect.value = (systemSettingsData.chat_provider || currentChatProvider) + '|' + (systemSettingsData.chat_model || currentChatModel);
        if (systemSettingsData.summary_model) {
            sumSelect.value = systemSettingsData.summary_provider + '|' + systemSettingsData.summary_model;
        }
        if (systemSettingsData.judge_model) {
            judgeSelect.value = systemSettingsData.judge_provider + '|' + systemSettingsData.judge_model;
        }
        if (systemSettingsData.file_model) {
            fileSelect.value = systemSettingsData.file_provider + '|' + systemSettingsData.file_model;
        }
        if (systemSettingsData.task_model) {
            taskSelect.value = systemSettingsData.task_provider + '|' + systemSettingsData.task_model;
        }

        // 加载记忆和系统提示词
        loadMemory();
        loadSystemPrompt();

    } catch (err) {
        console.error("加载设置失败", err);
    }
}

async function saveSettings() {
    const api_keys = {};
    const keysContainer = document.getElementById('api-keys-container');
    const inputs = keysContainer.querySelectorAll('input[type="text"]');
    inputs.forEach(inp => {
        const pid = inp.id.replace('apikey-', '');
        api_keys[pid] = inp.value;
    });

    const chatVal = document.getElementById('chat-model-select').value.split('|');
    const sumVal = document.getElementById('summary-model-select').value.split('|');
    const judVal = document.getElementById('judge-model-select').value.split('|');
    const fileVal = document.getElementById('file-model-select').value.split('|');
    const taskVal = document.getElementById('task-model-select').value.split('|');

    const payload = {
        api_keys: api_keys,
        chat_provider: chatVal[0],
        chat_model: chatVal[1],
        summary_provider: sumVal[0],
        summary_model: sumVal[1],
        judge_provider: judVal[0],
        judge_model: judVal[1],
        file_provider: fileVal[0],
        file_model: fileVal[1],
        task_provider: taskVal[0],
        task_model: taskVal[1],
        bailian_api_key: document.getElementById('bailian-api-key').value,
        enable_mcp_for_chat: document.getElementById('enable-mcp-checkbox').checked,
        max_tool_loops: parseInt(document.getElementById('max-tool-loops-slider').value) || 6
    };

    try {
        await fetch(API_BASE + '/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        const msg = document.getElementById('settings-msg');
        msg.style.display = 'block';
        setTimeout(() => msg.style.display = 'none', 3000);

        inputs.forEach(inp => inp.value = '');
        document.getElementById('bailian-api-key').value = '';
        loadSettings();

    } catch (err) {
        alert('保存失败: ' + err);
    }
}

async function switchChatModel() {
    const select = document.getElementById('chat-model-select');
    const val = select.value.split('|');
    try {
        const res = await fetch(API_BASE + '/switch_provider', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ provider_id: val[0], model_id: val[1] })
        });
        const data = await res.json();
        if (data.status === 'ok') {
            currentChatProvider = val[0];
            currentChatModel = val[1];
            updateModelLabel();
        }
    } catch (e) {
        console.error('切换模型失败:', e);
    }
}

// ── 记忆管理 ──
async function loadMemory() {
    try {
        const res = await fetch(API_BASE + '/api/memory');
        const data = await res.json();
        if (data.status === 'ok') {
            document.getElementById('memory-editor').value = data.content || '';
        }
    } catch (e) {
        console.error('加载记忆失败:', e);
    }
}

async function saveMemory() {
    const content = document.getElementById('memory-editor').value;
    try {
        const res = await fetch(API_BASE + '/api/memory', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ content })
        });
        const data = await res.json();
        if (data.status === 'ok') {
            const msg = document.getElementById('memory-save-msg');
            msg.style.display = 'block';
            setTimeout(() => msg.style.display = 'none', 3000);
        } else {
            alert('保存失败: ' + (data.message || '未知错误'));
        }
    } catch (e) {
        alert('保存失败: ' + e);
    }
}

// ── 系统提示词管理 ──
async function loadSystemPrompt() {
    try {
        const res = await fetch(API_BASE + '/api/system_prompt');
        const data = await res.json();
        if (data.status === 'ok') {
            document.getElementById('prompt-editor').value = data.content || '';
        }
    } catch (e) {
        console.error('加载系统提示词失败:', e);
    }
}

async function saveSystemPrompt() {
    const content = document.getElementById('prompt-editor').value;
    try {
        const res = await fetch(API_BASE + '/api/system_prompt', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ content })
        });
        const data = await res.json();
        if (data.status === 'ok') {
            const msg = document.getElementById('prompt-save-msg');
            msg.style.display = 'block';
            setTimeout(() => msg.style.display = 'none', 3000);
        } else {
            alert('保存失败: ' + (data.message || '未知错误'));
        }
    } catch (e) {
        alert('保存失败: ' + e);
    }
}

// ── 全局拦截聊天区域内的 A 标签点击事件 ──
document.getElementById('chat-history').addEventListener('click', function (e) {
    let target = e.target;
    while (target && target !== this) {
        if (target.tagName === 'A') {
            const href = target.getAttribute('href');
            if (href && !href.startsWith('#') && !href.startsWith('javascript:')) {
                e.preventDefault();
                window.open(href, '_blank');
            }
            return;
        }
        target = target.parentNode;
    }
});

// ── 初始化 ──
export function initChat() {
    loadProviders();
    loadSessions();
}

// ── 无闪烁下载辅助 ──
function _downloadFile(url, filename) {
    if (!url) return;
    const a = document.createElement('a');
    a.href = url;
    a.download = filename || '';
    a.target = '_blank';
    a.rel = 'noopener';
    a.style.display = 'none';
    document.body.appendChild(a);
    a.click();
    setTimeout(() => document.body.removeChild(a), 200);
}

// ── 导出到全局 (供 HTML 内联 onclick 使用) ──
Object.assign(window, {
    toggleSubMenu, switchView, handleEnter,
    sendMessage, handleChatFileSelect,
    createNewSession, switchSession, deleteSession,
    loadProviders, loadSessions,
    loadSettings, saveSettings, switchChatModel,
    loadMemory, saveMemory,
    loadSystemPrompt, saveSystemPrompt,
    appendMessage, renderMarkdown,
    renderAutoTaskCard, renderAutoScheduleCard, renderFileSearchCard,
    _downloadFile,
    API_BASE,
});
