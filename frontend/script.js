const chatHistory = document.getElementById("chat-history");
const userInput = document.getElementById("user-input");
const sendBtn = document.getElementById("send-btn");
const API_BASE = window.location.origin;

function toggleSubMenu() { document.getElementById('session-list').classList.toggle('open'); }
function switchView(viewName) {
    document.querySelectorAll('.view-container').forEach(v => v.classList.remove('active'));
    document.querySelectorAll('.menu-item').forEach(m => m.classList.remove('active'));
    document.getElementById(viewName + '-view').classList.add('active');
    const menuItems = document.querySelectorAll('.menu-item');
    const viewMap = { chat: 0, task: 1, files: 2, calendar: 3, music: 4, settings: 5 };
    if (viewMap[viewName] !== undefined) menuItems[viewMap[viewName]].classList.add('active');
    if (viewName === 'task') loadTasks();
    if (viewName === 'files') loadFiles();
    if (viewName === 'settings') loadSettings();
    if (viewName === 'music') loadMusic();
    if (viewName === 'calendar') calendarInit();
}

function handleEnter(event) {
    if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        sendMessage(); z
    }
}

// ====== 会话管理 ======

// ====== 模型切换和配置获取 ======
let systemSettingsData = null;
let globalProviders = [];

async function loadProviders() {
    try {
        const res = await fetch(API_BASE + "/providers");
        const data = await res.json();

        globalProviders = data.providers;
        currentChatProvider = data.current_provider;
        currentChatModel = data.current_model;

        // 更新输入框旁的模型标签
        updateModelLabel();
    } catch (e) {
        console.error("加载模型列表失败:", e);
    }
}

let currentChatProvider = '';
let currentChatModel = '';

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

async function loadSettings() {
    try {
        const res = await fetch(API_BASE + "/api/settings");
        const data = await res.json();
        systemSettingsData = data.settings;

        // 渲染 API Keys
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

        // 渲染 MCP 配置
        document.getElementById('bailian-api-key').placeholder = data.settings.bailian_api_key
            ? `(已配置, 留空不修改)`
            : `用于接入官方 MCP 服务 (画图/地图/搜索/语音)`;
        document.getElementById('enable-mcp-checkbox').checked = !!data.settings.enable_mcp_for_chat;
        const loopsVal = data.settings.max_tool_loops || 6;
        document.getElementById('max-tool-loops-slider').value = loopsVal;
        document.getElementById('max-tool-loops-val').textContent = loopsVal;

        // 填充所有模型选择器（对话/总结/判断）
        const chatSelect = document.getElementById('chat-model-select');
        const sumSelect = document.getElementById('summary-model-select');
        const judgeSelect = document.getElementById('judge-model-select');
        chatSelect.innerHTML = '';
        sumSelect.innerHTML = '';
        judgeSelect.innerHTML = '';

        globalProviders.forEach(p => {
            const makeGroup = () => { let g = document.createElement('optgroup'); g.label = p.name; return g; };
            const gc = makeGroup(), gs = makeGroup(), gj = makeGroup();
            p.models.forEach(m => {
                const capsMap = { text: '文本', vision: '视觉', reasoning: '推理' };
                const capsStr = (m.caps || ['text']).map(c => capsMap[c] || c).join(' ');
                const getOpt = () => { let o = document.createElement('option'); o.value = p.id + '|' + m.id; o.textContent = m.name + ' ' + capsStr; return o; };
                gc.appendChild(getOpt());
                gs.appendChild(getOpt());
                gj.appendChild(getOpt());
            });
            chatSelect.appendChild(gc);
            sumSelect.appendChild(gs);
            judgeSelect.appendChild(gj);
        });

        // 设置当前值
        chatSelect.value = currentChatProvider + '|' + currentChatModel;
        if (systemSettingsData.summary_model) {
            sumSelect.value = systemSettingsData.summary_provider + '|' + systemSettingsData.summary_model;
        }
        if (systemSettingsData.judge_model) {
            judgeSelect.value = systemSettingsData.judge_provider + '|' + systemSettingsData.judge_model;
        }

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

    const sumVal = document.getElementById('summary-model-select').value.split('|');
    const judVal = document.getElementById('judge-model-select').value.split('|');

    const payload = {
        api_keys: api_keys,
        summary_provider: sumVal[0],
        summary_model: sumVal[1],
        judge_provider: judVal[0],
        judge_model: judVal[1],
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

let pendingChatFiles = [];

function handleChatFileSelect(event) {
    const files = event.target.files;
    if (!files.length) return;
    Array.from(files).slice(0, 4).forEach(f => pendingChatFiles.push(f));
    renderAttachPreview();
    event.target.value = ''; // 允许再次选择同一文件
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

// 加载会话列表
async function loadSessions() {
    try {
        const res = await fetch(API_BASE + "/sessions");
        const data = await res.json();
        const sessionList = document.getElementById("session-list");

        // 保留"新建会话"按钮，清除其他项
        const newBtn = sessionList.querySelector('.new-session-btn');
        sessionList.innerHTML = '';
        sessionList.appendChild(newBtn);

        // 添加历史会话
        data.sessions.forEach(s => {
            const div = document.createElement("div");
            div.className = "sub-menu-item session-item" + (s.active ? " active-session" : "");
            div.textContent = s.name;
            div.onclick = () => switchSession(s.filename);
            sessionList.appendChild(div);
        });
    } catch (e) {
        console.error("加载会话列表失败:", e);
    }
}

// 新建会话
async function createNewSession() {
    try {
        const res = await fetch(API_BASE + "/new_session", { method: "POST" });
        const data = await res.json();
        if (data.status === "ok") {
            // 清空聊天区域
            chatHistory.innerHTML = '';
            loadSessions();  // 刷新列表
            switchView('chat');
        }
    } catch (e) {
        appendMessage("❌ 新建会话失败", "system-msg");
    }
}

// 切换到已有会话
async function switchSession(filename) {
    try {
        const res = await fetch(API_BASE + "/switch_session", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ filename: filename })
        });
        const data = await res.json();
        if (data.status === "ok") {
            // 重建聊天界面
            chatHistory.innerHTML = '';
            if (data.messages.length === 0) {
                appendMessage("你好，我是小鱼！今天需要处理什么工作？", "ai-msg");
            } else {
                // 构建事件索引：按 after_msg_index 分组
                const eventsByIndex = {};
                if (data.events && data.events.length > 0) {
                    data.events.forEach(evt => {
                        const idx = evt.after_msg_index;
                        if (!eventsByIndex[idx]) eventsByIndex[idx] = [];
                        eventsByIndex[idx].push(evt);
                    });
                }
                // 渲染消息，在正确位置插入事件卡片
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
                    // 在当前消息之后插入对应的事件卡片
                    if (eventsByIndex[msgIndex]) {
                        eventsByIndex[msgIndex].forEach(evt => {
                            if (evt.type === 'auto_task') {
                                appendMessage(renderAutoTaskCard(evt.data), "ai-msg event-msg");
                            } else if (evt.type === 'file_search') {
                                appendMessage(renderFileSearchCard(evt.data), "ai-msg event-msg");
                            } else if (evt.type === 'chat_attach') {
                                // 在对应的用户消息后追加附件预览
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
            loadSessions();  // 刷新高亮
            switchView('chat');

            // 处理 render_events：在对应的 AI 消息中注入渲染数据
            if (data.render_events && data.render_events.length > 0) {
                const aiMsgEls = chatHistory.querySelectorAll('.ai-msg:not(.event-msg):not(.system-msg)');
                // 按 turn 分组
                const byTurn = {};
                data.render_events.forEach(evt => {
                    if (!byTurn[evt.turn]) byTurn[evt.turn] = [];
                    byTurn[evt.turn].push(evt);
                });
                // turn 从 1 开始，对应 aiMsgEls 索引 turn-1
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
                                mediaHtml += `<a href="${p}" target="_blank"><img src="${thumbP}" alt="AI 生成图片" /></a>`;
                            });
                            mediaHtml += '</div><!-- END_MEDIA_GRID -->';
                        }
                    });
                    // 注入到消息体
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

// ====== 聊天功能 ======

// 构建思考折叠块 HTML
function renderThinkingBlock(thinkingText, isOpen) {
    const openAttr = isOpen ? ' open' : '';
    return `<details class="thinking-block"${openAttr}><summary>💭 深度思考</summary><div class="thinking-content">${thinkingText.replace(/\n/g, '<br>')}</div></details>`;
}

// Markdown + LaTeX 渲染
function renderMarkdown(text) {
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

// 对已渲染的 DOM 元素应用 KaTeX 公式渲染
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

async function sendMessage() {
    const text = userInput.value.trim();
    const hasFiles = pendingChatFiles.length > 0;
    if (!text && !hasFiles) return;

    // 构建用户消息气泡（含附件预览）
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

    // 读取附件为 base64
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

    // 创建空 AI 回复气泡
    const aiBubble = document.createElement("div");
    aiBubble.className = "msg-bubble ai-msg streaming-cursor";
    aiBubble.innerHTML = '';
    chatHistory.appendChild(aiBubble);

    let thinkingEl = null;  // <details> 元素
    let thinkingContentEl = null;  // .thinking-content 元素
    let replyEl = null;  // 正文容器
    let hasThinking = false;

    // 节流 Markdown 渲染：避免每个 delta 都重新 parse 整段文字导致卡顿
    let _mdRenderTimer = null;
    let _mdDirty = false;
    const MD_THROTTLE_MS = 80; // 最多每 80ms 渲染一次

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
            buffer = lines.pop(); // 保留不完整行

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
                            // 累积原始文本，节流渲染
                            replyEl.setAttribute('data-raw', (replyEl.getAttribute('data-raw') || '') + data.text);
                            scheduleRender();
                        } else if (eventType === 'replace_all') {
                            if (!replyEl) {
                                replyEl = document.createElement('div');
                                aiBubble.appendChild(replyEl);
                                if (hasThinking && thinkingEl) thinkingEl.open = false;
                                sendBtn.innerText = "输出中...";
                            }
                            // replace_all 立即渲染（出现频率低，通常是工具卡片）
                            replyEl.setAttribute('data-raw', data.text);
                            replyEl.innerHTML = renderMarkdown(data.text);
                        } else if (eventType === 'loop_status') {
                            // 循环进度指示器
                            sendBtn.innerText = `推理中(${data.loop}/${data.max})...`;
                        } else if (eventType === 'done') {
                            // 处理附加元数据
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
        // 清除节流计时器，确保最终完整渲染
        if (_mdRenderTimer) { clearTimeout(_mdRenderTimer); _mdRenderTimer = null; }
        aiBubble.classList.remove('streaming-cursor');
        // 流式完成后, 对整个气泡做最终渲染 (Markdown + KaTeX)
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

// ====== 可复用的卡片渲染函数 ======

function renderAutoTaskCard(t) {
    const triggerLabel = t.trigger_type === 'interval' ? '⏱ 间隔' :
        t.trigger_type === 'cron' ? '📅 周期' : '📌 一次性';
    const triggerInfo = formatTriggerArgs(t.trigger_type, t.trigger_args);
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


function renderFileSearchCard(search_res) {
    const fileCards = search_res.files.map(f => {
        const icon = getFileIcon(f.content_type);
        const size = formatSize(f.size || 0);
        const name = f.original_name;

        const scoreHtml = f._score ? `<span class="file-score-badge">✨ ${f._score}分</span>` : '';

        return `<div class="file-search-item" title="${name}" onclick="${f.download_url ? `window.open('${f.download_url}', '_blank')` : ''}">
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

function appendMessage(text, className, skipEscape) {
    const div = document.createElement("div");
    div.className = "msg-bubble " + className;
    if (skipEscape) {
        div.innerHTML = text;  // 已经渲染过的 HTML
    } else {
        div.innerHTML = text.replace(/\n/g, '<br>');
    }
    chatHistory.appendChild(div);
    // 对 AI 消息应用 KaTeX 公式渲染
    if (className.includes('ai-msg')) {
        renderKaTeX(div);
    }
    chatHistory.scrollTop = chatHistory.scrollHeight;
}

// 页面加载时获取会话列表和模型列表
loadProviders();
loadSessions();

// ====== 定时任务管理 ======

function openTaskModal() {
    document.getElementById('task-modal').style.display = 'flex';
    onTriggerTypeChange();
}

function closeTaskModal(event) {
    if (event.target === document.getElementById('task-modal')) {
        document.getElementById('task-modal').style.display = 'none';
    }
}

function onTriggerTypeChange() {
    const type = document.getElementById('trigger-type-select').value;
    document.querySelectorAll('.trigger-fields').forEach(el => el.style.display = 'none');
    document.getElementById('trigger-' + type + '-fields').style.display = 'block';
}

function buildTriggerArgs() {
    const type = document.getElementById('trigger-type-select').value;
    if (type === 'interval') {
        const val = parseInt(document.getElementById('interval-value').value) || 60;
        const unit = document.getElementById('interval-unit').value;
        return { [unit]: val };
    } else if (type === 'cron') {
        const h = document.getElementById('cron-hour').value || '*';
        const m = document.getElementById('cron-minute').value || '0';
        const dow = document.getElementById('cron-dow').value || '*';
        return { hour: h, minute: m, day_of_week: dow };
    } else if (type === 'date') {
        const dt = document.getElementById('date-run-time').value;
        return { run_date: dt ? dt.replace('T', ' ') : '' };
    }
    return {};
}

async function createTask() {
    const name = document.getElementById('task-name-input').value.trim();
    const prompt = document.getElementById('task-prompt-input').value.trim();
    if (!name) { alert('请输入任务名称'); return; }
    if (!prompt) { alert('请输入 AI 指令'); return; }

    const payload = {
        task_name: name,
        trigger_type: document.getElementById('trigger-type-select').value,
        trigger_args: buildTriggerArgs(),
        action_prompt: prompt,
    };

    try {
        const res = await fetch(API_BASE + '/api/tasks', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (data.status === 'ok') {
            document.getElementById('task-modal').style.display = 'none';
            document.getElementById('task-name-input').value = '';
            document.getElementById('task-prompt-input').value = '';
            loadTasks();
        } else {
            alert('创建失败: ' + JSON.stringify(data));
        }
    } catch (e) {
        alert('网络错误: ' + e);
    }
}

async function loadTasks() {
    try {
        const res = await fetch(API_BASE + '/api/tasks');
        const data = await res.json();
        renderTaskCards(data.tasks || []);
    } catch (e) {
        console.error('加载任务失败:', e);
    }
}

function renderTaskCards(tasks) {
    const grid = document.getElementById('task-grid');
    grid.innerHTML = '';

    if (tasks.length === 0) {
        grid.innerHTML = '<div style="grid-column: 1/-1; text-align:center; color:#999; padding:40px; font-size:16px;">暂无定时任务，点击上方按钮创建一个吧 ✨</div>';
        return;
    }

    tasks.forEach(task => {
        const statusClass = task.status === 'running' ? 'badge-running' :
            task.status === 'paused' ? 'badge-paused' : 'badge-completed';
        const statusText = task.status === 'running' ? '▶ 运行中' :
            task.status === 'paused' ? '⏸ 已暂停' : '✅ 已完成';

        const triggerLabel = task.trigger_type === 'interval' ? '⏱ 间隔' :
            task.trigger_type === 'cron' ? '📅 周期' : '📌 一次性';
        const triggerDetail = formatTriggerArgs(task.trigger_type, task.trigger_args);

        const lastRunHtml = task.last_run
            ? `<div class="task-meta">🕐 上次运行: ${task.last_run}</div>`
            : `<div class="task-meta">🕐 尚未执行</div>`;

        const resultHtml = task.last_result
            ? `<div class="task-result">${task.last_result.substring(0, 120)}${task.last_result.length > 120 ? '...' : ''}</div>`
            : '';

        const pauseBtn = task.status === 'running'
            ? `<button class="task-btn task-btn-pause" onclick="pauseTask('${task.task_id}')">⏸ 暂停</button>`
            : task.status === 'paused'
                ? `<button class="task-btn task-btn-resume" onclick="resumeTask('${task.task_id}')">▶ 恢复</button>`
                : '';

        const card = document.createElement('div');
        card.className = 'task-card';
        card.innerHTML = `
                    <div class="task-card-header">
                        <span class="task-card-title">${task.task_name}</span>
                        <span class="task-badge ${statusClass}">${statusText}</span>
                    </div>
                    <div class="task-meta">${triggerLabel} ${triggerDetail}</div>
                    <div class="task-meta" style="color:#666; font-size:12px; margin-top:4px;">📝 ${task.action_prompt.substring(0, 60)}${task.action_prompt.length > 60 ? '...' : ''}</div>
                    ${lastRunHtml}
                    ${resultHtml}
                    <div class="task-actions">
                        ${pauseBtn}
                        <button class="task-btn task-btn-delete" onclick="deleteTask('${task.task_id}')">🗑 删除</button>
                    </div>
                `;
        grid.appendChild(card);
    });
}

function formatTriggerArgs(type, args) {
    if (type === 'interval') {
        if (args.seconds) return `每 ${args.seconds} 秒`;
        if (args.minutes) return `每 ${args.minutes} 分钟`;
        if (args.hours) return `每 ${args.hours} 小时`;
        return JSON.stringify(args);
    } else if (type === 'cron') {
        const h = args.hour || '*';
        const m = args.minute || '0';
        const dow = args.day_of_week || '*';
        return `${h}:${String(m).padStart(2, '0')} (${dow})`;
    } else if (type === 'date') {
        return args.run_date || '未设置';
    }
    return JSON.stringify(args);
}

async function pauseTask(taskId) {
    try {
        await fetch(API_BASE + `/api/tasks/${taskId}/pause`, { method: 'POST' });
        loadTasks();
    } catch (e) { alert('暂停失败: ' + e); }
}

async function resumeTask(taskId) {
    try {
        await fetch(API_BASE + `/api/tasks/${taskId}/resume`, { method: 'POST' });
        loadTasks();
    } catch (e) { alert('恢复失败: ' + e); }
}

async function deleteTask(taskId) {
    if (!confirm('确定要删除这个任务吗？')) return;
    try {
        await fetch(API_BASE + `/api/tasks/${taskId}`, { method: 'DELETE' });
        loadTasks();
    } catch (e) { alert('删除失败: ' + e); }
}

// ====== WebSocket 实时推送 + Toast 弹窗 ======

let ws = null;
let wsReconnectDelay = 1000;  // 初始重连延迟 1s
const WS_MAX_DELAY = 30000;   // 最大重连延迟 30s
let wsHeartbeatTimer = null;

function connectWebSocket() {
    const wsUrl = 'ws://' + window.location.host + '/ws';
    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
        console.log('[WS] 连接成功');
        wsReconnectDelay = 1000;  // 重置退避计时器
        // 心跳检测：每 25 秒发一次 ping
        clearInterval(wsHeartbeatTimer);
        wsHeartbeatTimer = setInterval(() => {
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send('ping');
            }
        }, 25000);
    };

    ws.onmessage = (event) => {
        if (event.data === 'pong') return;  // 心跳响应忽略
        try {
            const data = JSON.parse(event.data);
            if (data.type === 'task_result') {
                showToast(data);
                // 如果当前在任务看板，刷新卡片
                if (document.getElementById('task-view').classList.contains('active')) {
                    loadTasks();
                }
            } else if (data.type === 'task_created') {
                showToast({
                    task_name: data.task.task_name,
                    result: '✅ 已自动创建新定时任务',
                    time: data.task.created_at || '',
                });
                if (document.getElementById('task-view').classList.contains('active')) {
                    loadTasks();
                }
            } else if (data.type === 'schedule_reminder' || data.type === 'schedule_created') {
                showToast({
                    task_name: data.task_name || (data.type === 'schedule_reminder' ? '📅 日程提醒' : '📅 日程创建'),
                    result: data.result || '日程操作成功',
                    time: data.time || '',
                });
                if (document.getElementById('calendar-view').classList.contains('active')) {
                    calRender();
                }
            } else if (data.type === 'file_tags_ready') {
                // 异步标签生成完成 — 更新文件卡片
                const tagMsg = data.tags && data.tags.length > 0
                    ? `标签: ${data.tags.join(', ')}`
                    : (data.error ? `标签生成失败: ${data.error}` : '无标签');
                showToast({
                    task_name: '🏷️ 标签就绪',
                    result: `${data.original_name}\n${tagMsg}`,
                    time: '',
                });
                // 如果当前在文件管理页，刷新列表
                if (document.getElementById('files-view').classList.contains('active')) {
                    loadFiles();
                }
                // 如果在音乐页，也刷新（标签可能影响音乐列表）
                if (document.getElementById('music-view').classList.contains('active')) {
                    loadMusic();
                }
            }
        } catch (e) {
            console.warn('[WS] 解析消息失败', e);
        }
    };

    ws.onclose = () => {
        console.log(`[WS] 连接关闭，${wsReconnectDelay / 1000}s 后重连...`);
        clearInterval(wsHeartbeatTimer);
        // 指数退避重连
        setTimeout(() => {
            wsReconnectDelay = Math.min(wsReconnectDelay * 2, WS_MAX_DELAY);
            connectWebSocket();
        }, wsReconnectDelay);
    };

    ws.onerror = (err) => {
        console.error('[WS] 错误', err);
        ws.close();  // 触发 onclose 重连
    };
}

function showToast(data) {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = 'toast-item toast-enter';

    const isError = data.result && data.result.startsWith('❌');
    toast.innerHTML = `
                <div class="toast-header">
                    <span class="toast-icon">${isError ? '❌' : '🔔'}</span>
                    <span class="toast-title">${data.task_name}</span>
                    <span class="toast-time">${data.time}</span>
                    <button class="toast-close" onclick="this.closest('.toast-item').remove()">×</button>
                </div>
                <div class="toast-body">${data.result.substring(0, 150).replace(/\n/g, '<br>')}${data.result.length > 150 ? '...' : ''}</div>
            `;

    container.appendChild(toast);

    // 动画进入
    requestAnimationFrame(() => toast.classList.remove('toast-enter'));

    // 8 秒后自动淡出并移除 DOM
    setTimeout(() => {
        toast.classList.add('toast-exit');
        toast.addEventListener('animationend', () => toast.remove());
    }, 8000);

    // 最多显示 5 个 toast，超出时移除最早的
    const toasts = container.querySelectorAll('.toast-item');
    if (toasts.length > 5) {
        toasts[0].remove();
    }
}

// ====== 文件管理 ======

let pendingFiles = [];

function handleFileDrop(event) {
    event.preventDefault();
    document.getElementById('file-upload-zone').classList.remove('drag-over');
    const files = event.dataTransfer.files;
    if (files.length > 0) selectFiles(files);
}

function handleFileSelect(event) {
    const files = event.target.files;
    if (files.length > 0) selectFiles(files);
}

function selectFiles(files) {
    pendingFiles = Array.from(files);
    let names = pendingFiles.map(f => f.name).join(', ');
    let totalSize = pendingFiles.reduce((acc, f) => acc + f.size, 0);
    document.getElementById('file-selected-name').textContent = `📄 ${pendingFiles.length}个文件: ${names} (${formatSize(totalSize)})`;
    document.getElementById('file-desc-row').style.display = 'flex';
    document.getElementById('file-description').focus();
}

function cancelUpload() {
    pendingFiles = [];
    document.getElementById('file-desc-row').style.display = 'none';
    document.getElementById('file-description').value = '';
    document.getElementById('file-input-hidden').value = '';
}

async function uploadFile() {
    if (pendingFiles.length === 0) return;
    const btn = document.getElementById('file-upload-btn');
    btn.disabled = true;
    btn.textContent = '上传中...';

    const formData = new FormData();
    pendingFiles.forEach(file => {
        formData.append('files', file);
    });
    formData.append('description', document.getElementById('file-description').value);

    try {
        const res = await fetch(API_BASE + '/api/files/upload_batch', { method: 'POST', body: formData });
        const data = await res.json();
        if (data.status === 'ok') {
            showToast({ task_name: '文件上传', result: `✅ ${data.uploaded} 个文件上传成功\n⏳ AI 标签生成中...`, time: '' });
            pendingFiles = [];
            document.getElementById('file-desc-row').style.display = 'none';
            document.getElementById('file-description').value = '';
            document.getElementById('file-input-hidden').value = '';
            loadFiles();
        } else {
            alert('上传失败: ' + data.message);
        }
    } catch (e) {
        alert('网络错误: ' + e);
    } finally {
        btn.disabled = false;
        btn.textContent = '🚀 上传';
    }
}

let searchTimer = null;
function searchFiles() {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(async () => {
        const q = document.getElementById('file-search-input').value.trim();
        try {
            const res = await fetch(API_BASE + '/api/files/search?q=' + encodeURIComponent(q));
            const data = await res.json();
            renderFileCards(data.files || []);
        } catch (e) { console.error('搜索失败:', e); }
    }, 300);
}

async function aiSearchFiles() {
    const prompt = document.getElementById('file-search-input').value.trim();
    if (!prompt) {
        alert('请输入你要找的文件的描述（例如："找一张下雨的照片"或"文档"）');
        return;
    }

    const btn = document.getElementById('btn-file-ai-search');
    btn.disabled = true;
    btn.innerHTML = '<span class="btn-loading">✨</span> 语义检索中...';

    try {
        const res = await fetch(API_BASE + '/api/files/ai_search', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ prompt })
        });
        const data = await res.json();
        if (data.status === 'ok') {
            renderFileCards(data.files || []);
            let toastMsg = data.reason || '';
            if (data.search_tags) {
                const tagSummary = Object.entries(data.search_tags)
                    .filter(([_, tags]) => tags.length > 0)
                    .map(([cat, tags]) => `${cat}: ${tags.join(', ')}`)
                    .join(' | ');
                if (tagSummary) toastMsg += '\n🏷️ ' + tagSummary;
            }
            if (toastMsg) {
                showToast({ task_name: '✨ AI 检索完毕', result: toastMsg, time: '' });
            }
        } else {
            alert('AI 检索失败: ' + (data.message || '未知错误'));
        }
    } catch (e) {
        alert('网络错误: ' + e);
    } finally {
        btn.disabled = false;
        btn.innerHTML = '✨ AI 语义过滤';
    }
}

async function loadFiles() {
    try {
        const res = await fetch(API_BASE + '/api/files');
        const data = await res.json();
        renderFileCards(data.files || []);
    } catch (e) { console.error('加载文件失败:', e); }
}

function renderFileCards(files) {
    const grid = document.getElementById('file-grid');
    grid.innerHTML = '';
    if (files.length === 0) {
        grid.innerHTML = '<div style="grid-column:1/-1; text-align:center; color:#999; padding:40px; font-size:16px;">暂无文件，拖拽文件到上方区域上传 ☁️</div>';
        return;
    }
    files.forEach(f => {
        // 优先用分类标签，兜底用扁平标签
        const cats = f.categorized_tags || {};
        const hasCats = cats && (Object.values(cats).some(arr => arr && arr.length > 0));
        let tagsHtml;
        if (hasCats) {
            const catIcons = { file_type: '📄', author: '👤', location: '📍', description: '💬' };
            tagsHtml = Object.entries(cats).map(([cat, tags]) => {
                if (!tags || tags.length === 0) return '';
                const icon = catIcons[cat] || '🏷️';
                return tags.map(t => `<span class="file-tag" title="${cat}">${icon} ${t}</span>`).join('');
            }).join('');
        } else if (f.tags && f.tags.length > 0) {
            tagsHtml = f.tags.map(t => `<span class="file-tag">${t}</span>`).join('');
        } else {
            tagsHtml = '<span class="file-tag" style="opacity:0.6; animation: pulse 1.5s infinite;">⏳ 标签生成中...</span>';
        }
        const scoreHtml = f._score ? `<span style="font-size:10px; color:#0078FF; font-weight:600;">🎯 ${f._score}分</span>` : '';
        const card = document.createElement('div');
        card.className = 'file-card';
        card.setAttribute('data-object-name', f.object_name);
        card.innerHTML = `
                    <div class="file-card-header">
                        <span class="file-card-icon">${getFileIcon(f.content_type)}</span>
                        <span class="file-card-name" title="${f.original_name}">${f.original_name}</span>
                        ${scoreHtml}
                    </div>
                    <div class="file-card-desc">${f.description || '无描述'}</div>
                    <div class="file-card-tags">${tagsHtml}</div>
                    <div class="file-card-meta">
                        <span>${formatSize(f.size)}</span>
                        ${f.file_date ? `<span>📅 ${f.file_date}</span>` : ''}
                        <span>${(f.uploaded_at || '').substring(0, 16).replace('T', ' ')}</span>
                    </div>
                    <div class="file-card-actions">
                        <a class="task-btn task-btn-resume" href="${f.download_url}" download="${f.original_name}" target="_blank" rel="noopener">⬇ 下载</a>
                        <button class="task-btn task-btn-delete" onclick="deleteFile('${f.object_name}')">🗑 删除</button>
                    </div>
                `;
        grid.appendChild(card);
    });
}

function getFileIcon(contentType) {
    if (!contentType) return '📄';
    if (contentType.startsWith('image/')) return '🖼️';
    if (contentType.startsWith('video/')) return '🎬';
    if (contentType.startsWith('audio/')) return '🎵';
    if (contentType.includes('pdf')) return '📕';
    if (contentType.includes('zip') || contentType.includes('rar') || contentType.includes('7z')) return '📦';
    if (contentType.includes('word') || contentType.includes('document')) return '📝';
    if (contentType.includes('sheet') || contentType.includes('excel')) return '📊';
    if (contentType.includes('presentation') || contentType.includes('powerpoint')) return '📽️';
    return '📄';
}

function formatSize(bytes) {
    if (!bytes) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB'];
    let i = 0;
    let size = bytes;
    while (size >= 1024 && i < units.length - 1) { size /= 1024; i++; }
    return size.toFixed(i > 0 ? 1 : 0) + ' ' + units[i];
}

async function deleteFile(objectName) {
    if (!confirm('确定要删除这个文件吗？')) return;
    try {
        await fetch(API_BASE + '/api/files/' + encodeURIComponent(objectName), { method: 'DELETE' });
        loadFiles();
    } catch (e) { alert('删除失败: ' + e); }
}

// 页面加载时连接 WebSocket
connectWebSocket();

// ====== 音乐播放器 ======

let allSongs = [];   // 所有歌曲（缓存）
let musicQueue = [];   // 播放队列
let currentSearchMode = 'name';
let currentSongIndex = -1;
let currentLyrics = [];
let fullscreenOpen = false;
const audioPlayer = document.getElementById('audio-player');

// 搜索模式切换
function setSearchMode(mode) {
    currentSearchMode = mode;
    document.querySelectorAll('.search-mode-btn').forEach(b => b.classList.toggle('active', b.dataset.mode === mode));
    const input = document.getElementById('music-search-input');
    const placeholders = { name: '输入歌曲名称...', ai: '描述你的心情或场景，如：下雨天适合听的安静歌曲...', tag: '输入标签关键词，如：电子、流行...' };
    input.placeholder = placeholders[mode] || '';
    input.value = '';
}

// 生成基于字符串的渐变色（用于封面占位）
function generateCoverGradient(str) {
    let hash = 0;
    for (let i = 0; i < str.length; i++) {
        hash = str.charCodeAt(i) + ((hash << 5) - hash);
    }
    const h1 = Math.abs(hash % 360);
    const h2 = (h1 + 40) % 360;
    return `linear-gradient(135deg, hsl(${h1},55%,35%), hsl(${h2},65%,25%))`;
}

// 获取歌曲显示信息
function getSongDisplayInfo(song) {
    const meta = song.file_meta || {};
    const title = meta.title || song.original_name.replace(/\.[^.]+$/, '');
    const artist = (meta.artists || []).join('、') || '';
    return { title, artist };
}

// 加载所有音乐
async function loadMusic() {
    try {
        const res = await fetch(API_BASE + '/api/music');
        const data = await res.json();
        allSongs = (data.songs || []);
        allSongs.forEach(s => { s.download_url = s.download_url || ''; });
        renderBrowseList(allSongs);
    } catch (e) {
        console.error('加载音乐失败:', e);
    }
}

// 搜索
async function doMusicSearch() {
    const query = document.getElementById('music-search-input').value.trim();
    if (!query) { renderBrowseList(allSongs); return; }

    if (currentSearchMode === 'name') {
        // 本地过滤名字
        const q = query.toLowerCase();
        const filtered = allSongs.filter(s => {
            const info = getSongDisplayInfo(s);
            return info.title.toLowerCase().includes(q) || info.artist.toLowerCase().includes(q) || s.original_name.toLowerCase().includes(q);
        });
        renderBrowseList(filtered);
    } else if (currentSearchMode === 'tag') {
        // 本地过滤标签
        const q = query.toLowerCase();
        const filtered = allSongs.filter(s => (s.tags || []).some(t => t.toLowerCase().includes(q)));
        renderBrowseList(filtered);
    } else if (currentSearchMode === 'ai') {
        // AI 推荐
        const btn = document.querySelector('.music-search-input-row .btn-send');
        btn.disabled = true; btn.textContent = '生成中...';
        try {
            const res = await fetch(API_BASE + '/api/music/playlist', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ prompt: query })
            });
            const data = await res.json();
            if (data.status === 'ok' && data.playlist && data.playlist.songs) {
                renderBrowseList(data.playlist.songs);
                showToast({ task_name: '✨ AI 推荐', result: `${data.playlist.playlist_name}\n${data.playlist.description}`, time: '' });
            } else {
                renderBrowseList([]);
            }
        } catch (e) {
            alert('AI 推荐失败: ' + e);
        } finally {
            btn.disabled = false; btn.textContent = '搜索';
        }
    }
}

// 渲染左侧浏览列表
function renderBrowseList(songs) {
    const list = document.getElementById('music-browse-list');
    list.innerHTML = '';
    if (!songs || songs.length === 0) {
        list.innerHTML = '<div class="song-list-empty">🎵 没有找到匹配的歌曲</div>';
        return;
    }
    songs.forEach((song, idx) => {
        const info = getSongDisplayInfo(song);
        const item = document.createElement('div');
        item.className = 'browse-song-item';
        item.innerHTML = `
                    <div class="song-icon">🎵</div>
                    <div class="song-info">
                        <div class="song-title">${info.title}</div>
                        ${info.artist ? `<div class="song-artist">${info.artist}</div>` : ''}
                    </div>
                    <button class="song-add-btn" onclick="event.stopPropagation(); addToQueue(this)" data-song-idx="${idx}">+ 添加</button>
                `;
        item.onclick = () => { addToQueue(item.querySelector('.song-add-btn')); };
        // 存储歌曲数据引用
        item._songData = song;
        list.appendChild(item);
    });
}

// 添加到队列
function addToQueue(btnEl) {
    const item = btnEl.closest('.browse-song-item');
    const song = item._songData;
    if (!song) return;
    // 避免重复添加
    if (musicQueue.some(s => s.object_name === song.object_name)) {
        btnEl.textContent = '已添加';
        setTimeout(() => { btnEl.textContent = '+ 添加'; }, 1000);
        return;
    }
    musicQueue.push(song);
    btnEl.textContent = '✓';
    setTimeout(() => { btnEl.textContent = '+ 添加'; }, 800);
    renderQueue();
}

// 从队列移除
function removeFromQueue(idx) {
    // 如果移除的是正在播放的或之前的歌，调整索引
    if (idx < currentSongIndex) currentSongIndex--;
    else if (idx === currentSongIndex) {
        audioPlayer.pause();
        currentSongIndex = -1;
    }
    musicQueue.splice(idx, 1);
    renderQueue();
}

// 清空队列
function clearQueue() {
    musicQueue = [];
    currentSongIndex = -1;
    audioPlayer.pause();
    document.getElementById('music-player-bar').style.display = 'none';
    renderQueue();
}

// 渲染右侧队列
function renderQueue() {
    const list = document.getElementById('music-queue-list');
    const actions = document.getElementById('music-queue-actions');
    document.getElementById('queue-count').textContent = musicQueue.length + ' 首';

    if (musicQueue.length === 0) {
        list.innerHTML = '<div class="song-list-empty">🎵 从左侧添加歌曲到队列<br>歌单播完自动停止</div>';
        actions.style.display = 'none';
        return;
    }
    actions.style.display = 'flex';
    list.innerHTML = '';
    musicQueue.forEach((song, idx) => {
        const info = getSongDisplayInfo(song);
        const isPlaying = idx === currentSongIndex;
        const item = document.createElement('div');
        item.className = 'queue-song-item' + (isPlaying ? ' now-playing' : '');
        item.innerHTML = `
                    <div class="song-info">
                        <div class="song-title">${isPlaying ? '♫ ' : ''}${info.artist ? info.artist + ' - ' : ''}${info.title}</div>
                    </div>
                    <button class="queue-remove-btn" onclick="event.stopPropagation(); removeFromQueue(${idx})">✕</button>
                `;
        item.onclick = () => playSong(idx);
        list.appendChild(item);
    });
}

// 播放队列
function playQueue() {
    if (musicQueue.length > 0) {
        currentPlaylist = musicQueue;
        playSong(0);
    }
}

// 兼容旧 currentPlaylist 引用（保持 playSong/playNext 等正常工作）
let currentPlaylist = [];

function playSong(index) {
    currentPlaylist = musicQueue; // 保持同步
    if (index < 0 || index >= musicQueue.length) return;
    const song = musicQueue[index];
    currentSongIndex = index;

    audioPlayer.src = song.download_url;
    audioPlayer.play();

    const info = getSongDisplayInfo(song);
    const coverArt = `/api/music/cover/${encodeURIComponent(song.object_name)}`;
    const coverGrad = generateCoverGradient(song.original_name);

    // 迷你播放器
    const bar = document.getElementById('music-player-bar');
    bar.style.display = 'block';
    document.getElementById('player-song-name').textContent = info.title;
    document.getElementById('player-song-artist').textContent = info.artist;
    document.getElementById('player-play-btn').textContent = '⏸';

    // 全屏播放器
    document.getElementById('fp-song-title').textContent = info.title;
    document.getElementById('fp-song-artist').textContent = info.artist;
    document.getElementById('fp-play-btn').textContent = '⏸';

    // 封面
    const coverEl = document.getElementById('fp-cover-img');
    coverEl.innerHTML = `<img src="${coverArt}" style="width:100%;height:100%;object-fit:cover;" onerror="this.outerHTML='🎵'; this.parentElement.style.background='${coverGrad}'">`;
    coverEl.style.background = 'transparent';

    // 加载歌词
    loadLyrics(song);

    renderQueue();
}

function togglePlay() {
    if (!audioPlayer.src) return;
    if (audioPlayer.paused) {
        audioPlayer.play();
        document.getElementById('player-play-btn').textContent = '⏸';
        document.getElementById('fp-play-btn').textContent = '⏸';
    } else {
        audioPlayer.pause();
        document.getElementById('player-play-btn').textContent = '▶';
        document.getElementById('fp-play-btn').textContent = '▶';
    }
}

function playPrev() {
    if (musicQueue.length === 0 || currentSongIndex <= 0) return;
    playSong(currentSongIndex - 1);
}

function playNext() {
    if (musicQueue.length === 0) return;
    const nextIdx = currentSongIndex + 1;
    if (nextIdx >= musicQueue.length) {
        // 播完了，不循环
        audioPlayer.pause();
        document.getElementById('player-play-btn').textContent = '▶';
        document.getElementById('fp-play-btn').textContent = '▶';
        return;
    }
    playSong(nextIdx);
}

// 歌曲播完自动下一首（不循环）
audioPlayer.onended = () => { playNext(); };

function seekTo(event) {
    if (!audioPlayer.duration) return;
    // 寻找点击的进度条（迷你或全屏）
    const bar = event.currentTarget;
    const rect = bar.getBoundingClientRect();
    const pct = (event.clientX - rect.left) / rect.width;
    audioPlayer.currentTime = pct * audioPlayer.duration;
    event.stopPropagation();
}

function setVolume(val) {
    audioPlayer.volume = val / 100;
    // 同步两个音量滑块
    document.getElementById('player-volume').value = val;
    document.getElementById('fp-volume').value = val;
    const icon = val == 0 ? '🔇' : val < 40 ? '🔉' : '🔊';
    document.querySelectorAll('.player-volume-icon, .fp-volume-icon').forEach(el => el.textContent = icon);
}

function toggleMute() {
    audioPlayer.muted = !audioPlayer.muted;
    const icon = audioPlayer.muted ? '🔇' : '🔊';
    document.querySelectorAll('.player-volume-icon, .fp-volume-icon').forEach(el => el.textContent = icon);
}

function formatTime(s) {
    if (!s || isNaN(s)) return '0:00';
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return m + ':' + String(sec).padStart(2, '0');
}

// 音频事件
audioPlayer.addEventListener('timeupdate', () => {
    if (!audioPlayer.duration) return;
    const pct = (audioPlayer.currentTime / audioPlayer.duration) * 100;
    // 迷你播放器进度
    document.getElementById('player-progress-fill').style.width = pct + '%';
    document.getElementById('player-time').textContent =
        formatTime(audioPlayer.currentTime) + ' / ' + formatTime(audioPlayer.duration);
    // 全屏播放器进度
    document.getElementById('fp-progress-fill').style.width = pct + '%';
    document.getElementById('fp-time-current').textContent = formatTime(audioPlayer.currentTime);
    document.getElementById('fp-time-total').textContent = formatTime(audioPlayer.duration);

    // 歌词同步
    if (currentLyrics.length > 0 && fullscreenOpen) {
        syncLyrics(audioPlayer.currentTime);
    }
});

audioPlayer.addEventListener('ended', () => {
    playNext();
});

audioPlayer.addEventListener('pause', () => {
    document.getElementById('player-play-btn').textContent = '▶';
    document.getElementById('fp-play-btn').textContent = '▶';
    document.getElementById('music-player-bar').classList.remove('is-playing');
    const sqCover = document.getElementById('fp-square-cover');
    if (sqCover) sqCover.classList.remove('is-playing');
});

audioPlayer.addEventListener('play', () => {
    document.getElementById('player-play-btn').textContent = '⏸';
    document.getElementById('fp-play-btn').textContent = '⏸';
    document.getElementById('music-player-bar').classList.add('is-playing');
    const sqCover = document.getElementById('fp-square-cover');
    if (sqCover) sqCover.classList.add('is-playing');
});

audioPlayer.volume = 0.8;

// ====== 全屏播放器 ======

function openFullscreenPlayer(event) {
    // 不在控件内触发
    if (event && event.target.closest('.player-controls, .player-right, .player-progress-bar')) return;
    const fp = document.getElementById('fullscreen-player');
    fp.classList.add('fp-open');
    fullscreenOpen = true;
    // 如果有歌词则开始同步
    if (currentLyrics.length > 0) {
        syncLyrics(audioPlayer.currentTime);
    }
}

function closeFullscreenPlayer() {
    const fp = document.getElementById('fullscreen-player');
    fp.classList.remove('fp-open');
    fullscreenOpen = false;
}

// ====== 歌词功能 ======

let currentActiveLyricIndex = -1;

function parseLRC(lrcText) {
    const lines = lrcText.split('\n');
    const timeMap = new Map();
    const timeRegex = /\[(\d{1,2}):(\d{2})(?:\.(\d{1,3}))?\]/g;
    for (const line of lines) {
        const matches = [...line.matchAll(timeRegex)];
        if (matches.length === 0) continue;
        const text = line.replace(timeRegex, '').trim();
        if (!text) continue;
        for (const m of matches) {
            const min = parseInt(m[1]);
            const sec = parseInt(m[2]);
            const ms = m[3] ? parseInt(m[3].padEnd(3, '0')) : 0;
            const time = min * 60 + sec + ms / 1000;
            if (timeMap.has(time)) {
                timeMap.get(time).translation = text;
            } else {
                timeMap.set(time, { time, text, translation: '' });
            }
        }
    }
    const result = Array.from(timeMap.values());
    result.sort((a, b) => a.time - b.time);
    return result;
}

async function loadLyrics(song) {
    const content = document.getElementById('fp-lyrics-content');
    currentLyrics = [];
    currentActiveLyricIndex = -1;
    content.style.transform = 'translateY(0px)';
    content.innerHTML = '<div class="lyrics-empty">⏳ 正在提取歌词...</div>';

    try {
        const res = await fetch(`/api/music/lyrics/${encodeURIComponent(song.object_name)}`);
        const data = await res.json();
        const lyricsText = data.lyrics || '';

        if (!lyricsText.trim()) {
            content.innerHTML = '<div class="lyrics-empty">🎵 当前歌曲暂无歌词</div>';
            return;
        }

        const parsed = parseLRC(lyricsText);
        if (parsed.length > 0) {
            currentLyrics = parsed;
            content.innerHTML = parsed.map((l, i) => {
                let html = `<div class="lyrics-line" data-index="${i}"><div class="lyrics-text">${l.text}</div>`;
                if (l.translation) html += `<div class="lyrics-trans">${l.translation}</div>`;
                html += `</div>`;
                return html;
            }).join('');
            if (fullscreenOpen && audioPlayer.duration) syncLyrics(audioPlayer.currentTime);
        } else {
            content.innerHTML = lyricsText.split('\n')
                .filter(l => l.trim())
                .map(l => `<div class="lyrics-line lyrics-plain">${l}</div>`)
                .join('');
        }
    } catch (e) {
        console.error('加载歌词失败:', e);
        content.innerHTML = '<div class="lyrics-empty">❌ 获取歌词失败</div>';
    }
}

function syncLyrics(currentTime) {
    if (currentLyrics.length === 0) return;
    let activeIdx = -1;
    for (let i = currentLyrics.length - 1; i >= 0; i--) {
        if (currentTime >= currentLyrics[i].time) {
            activeIdx = i;
            break;
        }
    }

    if (activeIdx === currentActiveLyricIndex) return;
    currentActiveLyricIndex = activeIdx;

    const lines = document.querySelectorAll('#fp-lyrics-content .lyrics-line');
    lines.forEach((el, i) => {
        const dist = activeIdx === -1 ? i : i - activeIdx;
        el.setAttribute('data-dist', dist);
        el.style.setProperty('--abs-dist', Math.abs(dist));

        if (i === activeIdx) {
            el.classList.add('lyrics-active');
            const scroll = document.getElementById('fp-lyrics-scroll');
            const content = document.getElementById('fp-lyrics-content');
            // offsetTop 相对 fp-lyrics-content 计算
            const offset = el.offsetTop + el.clientHeight / 2;
            const center = scroll.clientHeight / 2;
            content.style.transform = `translateY(${center - offset}px)`;
        } else {
            el.classList.remove('lyrics-active');
        }
    });
}

// 全局拦截聊天区域内的 A 标签点击事件，强制在新标签页打开（修复部分标头环境下 target="_blank" 失效的问题）
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

// ====== 日历模块 ======
let calYear, calMonth, calViewMode = 'month', calSchedules = [];
const CAT_COLORS = {
    '工作': '#4F86F7', '个人': '#34C759', '学习': '#AF52DE',
    '其他': '#FF9500', '会议': '#FF3B30', '健康': '#30D158'
};
const CAT_CLASS = {
    '工作': 'cal-cat-work', '个人': 'cal-cat-personal', '学习': 'cal-cat-study',
    '其他': 'cal-cat-other', '会议': 'cal-cat-meeting', '健康': 'cal-cat-health'
};

function calendarInit() {
    const now = new Date();
    calYear = now.getFullYear();
    calMonth = now.getMonth();
    calRender();
}
function calPrev() {
    if (calViewMode === 'month') { calMonth--; if (calMonth < 0) { calMonth = 11; calYear--; } }
    else { const d = new Date(calYear, calMonth, calWeekStart.getDate() - 7); calYear = d.getFullYear(); calMonth = d.getMonth(); calWeekStart = d; }
    calRender();
}
function calNext() {
    if (calViewMode === 'month') { calMonth++; if (calMonth > 11) { calMonth = 0; calYear++; } }
    else { const d = new Date(calYear, calMonth, calWeekStart.getDate() + 7); calYear = d.getFullYear(); calMonth = d.getMonth(); calWeekStart = d; }
    calRender();
}
function calToday() { const n = new Date(); calYear = n.getFullYear(); calMonth = n.getMonth(); calRender(); }
function calSwitchView(mode) {
    calViewMode = mode;
    document.querySelectorAll('.cal-view-btn').forEach(b => b.classList.toggle('active', b.dataset.view === mode));
    calRender();
}

let calWeekStart = null;
async function calRender() {
    const body = document.getElementById('cal-body');
    const title = document.getElementById('cal-title');
    // Fetch schedules
    let start, end;
    if (calViewMode === 'month') {
        const first = new Date(calYear, calMonth, 1);
        const startPad = new Date(first); startPad.setDate(1 - first.getDay());
        const last = new Date(calYear, calMonth + 1, 0);
        const endPad = new Date(last); endPad.setDate(last.getDate() + (6 - last.getDay()));
        start = startPad.toISOString().slice(0, 10);
        end = endPad.toISOString().slice(0, 10);
        title.textContent = `${calYear}年${calMonth + 1}月`;
    } else {
        const now = new Date();
        if (!calWeekStart || calWeekStart.getMonth() !== calMonth) {
            const d = new Date(calYear, calMonth, now.getDate());
            d.setDate(d.getDate() - d.getDay());
            calWeekStart = d;
        }
        const ws = new Date(calWeekStart);
        const we = new Date(ws); we.setDate(ws.getDate() + 6);
        start = ws.toISOString().slice(0, 10);
        end = we.toISOString().slice(0, 10);
        title.textContent = `${ws.getMonth() + 1}月${ws.getDate()}日 - ${we.getMonth() + 1}月${we.getDate()}日`;
    }
    try {
        const res = await fetch(`${API_BASE}/api/schedules?start=${start}&end=${end}`);
        const data = await res.json();
        calSchedules = data.schedules || [];
    } catch (e) { console.error('加载日程失败:', e); calSchedules = []; }

    if (calViewMode === 'month') renderMonth(body);
    else renderWeek(body);
}

function renderMonth(body) {
    const weekdays = ['日', '一', '二', '三', '四', '五', '六'];
    const first = new Date(calYear, calMonth, 1);
    const startDay = first.getDay();
    const daysInMonth = new Date(calYear, calMonth + 1, 0).getDate();
    const today = new Date();
    const todayStr = today.toISOString().slice(0, 10);

    let html = '<div class="cal-month-grid">';
    weekdays.forEach(w => html += `<div class="cal-weekday-header">${w}</div>`);

    const totalCells = Math.ceil((startDay + daysInMonth) / 7) * 7;
    for (let i = 0; i < totalCells; i++) {
        const dayNum = i - startDay + 1;
        const isOther = dayNum < 1 || dayNum > daysInMonth;
        let dateObj;
        if (dayNum < 1) dateObj = new Date(calYear, calMonth, dayNum);
        else if (dayNum > daysInMonth) dateObj = new Date(calYear, calMonth, dayNum);
        else dateObj = new Date(calYear, calMonth, dayNum);
        const dateStr = dateObj.toISOString().slice(0, 10);
        const isToday = dateStr === todayStr;
        const cls = ['cal-day-cell'];
        if (isOther) cls.push('cal-other-month');
        if (isToday) cls.push('cal-today');

        const dayEvents = calSchedules.filter(s => s.start_time.slice(0, 10) <= dateStr && s.end_time.slice(0, 10) >= dateStr);

        html += `<div class="${cls.join(' ')}" onclick="openScheduleModal(null,'${dateStr}')">`;
        html += `<div class="cal-day-num">${dateObj.getDate()}</div>`;
        const maxShow = 3;
        dayEvents.slice(0, maxShow).forEach(ev => {
            const catCls = CAT_CLASS[ev.category] || 'cal-cat-other';
            const t = ev.all_day ? '' : (ev.start_time.slice(11, 16) + ' ');
            html += `<div class="cal-event-pill ${catCls}" onclick="event.stopPropagation();openScheduleModal('${ev.id}')" title="${ev.title}">${t}${ev.title}</div>`;
        });
        if (dayEvents.length > maxShow) html += `<div class="cal-event-more">+${dayEvents.length - maxShow} 更多</div>`;
        html += '</div>';
    }
    html += '</div>';
    body.innerHTML = html;
}

function renderWeek(body) {
    const ws = new Date(calWeekStart);
    const today = new Date();
    const todayStr = today.toISOString().slice(0, 10);
    const weekdays = ['日', '一', '二', '三', '四', '五', '六'];

    let html = '<div class="cal-week-grid">';
    html += '<div class="cal-week-header-cell"></div>';
    for (let d = 0; d < 7; d++) {
        const dd = new Date(ws); dd.setDate(ws.getDate() + d);
        const ds = dd.toISOString().slice(0, 10);
        const cls = ds === todayStr ? 'cal-week-header-cell cal-today-col' : 'cal-week-header-cell';
        html += `<div class="${cls}">${weekdays[d]} ${dd.getDate()}</div>`;
    }
    for (let h = 6; h < 23; h++) {
        html += `<div class="cal-week-time-label">${String(h).padStart(2, '0')}:00</div>`;
        for (let d = 0; d < 7; d++) {
            const dd = new Date(ws); dd.setDate(ws.getDate() + d);
            const ds = dd.toISOString().slice(0, 10);
            const cls = ds === todayStr ? 'cal-week-cell cal-today-col' : 'cal-week-cell';
            // Find events in this hour
            const cellEvents = calSchedules.filter(s => {
                if (s.start_time.slice(0, 10) !== ds) return false;
                const sh = parseInt(s.start_time.slice(11, 13));
                return sh === h;
            });
            html += `<div class="${cls}" onclick="openScheduleModal(null,'${ds}T${String(h).padStart(2, '0')}:00')">`;
            cellEvents.forEach(ev => {
                const sh = parseInt(ev.start_time.slice(11, 13));
                const eh = parseInt(ev.end_time.slice(11, 13)) || (sh + 1);
                const span = Math.max(1, eh - sh);
                const catCls = CAT_CLASS[ev.category] || 'cal-cat-other';
                html += `<div class="cal-week-event ${catCls}" style="height:${span * 48 - 4}px;" onclick="event.stopPropagation();openScheduleModal('${ev.id}')">${ev.start_time.slice(11, 16)} ${ev.title}</div>`;
            });
            html += '</div>';
        }
    }
    html += '</div>';
    body.innerHTML = html;
}

// 日程弹窗
function openScheduleModal(editId, defaultDate) {
    const modal = document.getElementById('schedule-modal');
    const titleEl = document.getElementById('schedule-modal-title');
    const deleteBtn = document.getElementById('sch-delete-btn');
    document.getElementById('schedule-edit-id').value = '';
    document.getElementById('sch-conflict-warning').style.display = 'none';

    if (editId) {
        // 编辑模式
        const sch = calSchedules.find(s => s.id === editId);
        if (!sch) return;
        titleEl.textContent = '✏️ 编辑日程';
        deleteBtn.style.display = 'inline-flex';
        document.getElementById('schedule-edit-id').value = editId;
        document.getElementById('sch-title').value = sch.title || '';
        document.getElementById('sch-category').value = sch.category || '其他';
        document.getElementById('sch-allday').checked = !!sch.all_day;
        document.getElementById('sch-start').value = (sch.start_time || '').slice(0, 16);
        document.getElementById('sch-end').value = (sch.end_time || '').slice(0, 16);
        document.getElementById('sch-location').value = sch.location || '';
        document.getElementById('sch-desc').value = sch.description || '';
        document.getElementById('sch-reminder').value = String(sch.reminder_minutes || 15);
    } else {
        // 新建模式
        titleEl.textContent = '📅 新建日程';
        deleteBtn.style.display = 'none';
        document.getElementById('sch-title').value = '';
        document.getElementById('sch-category').value = '其他';
        document.getElementById('sch-allday').checked = false;
        document.getElementById('sch-location').value = '';
        document.getElementById('sch-desc').value = '';
        document.getElementById('sch-reminder').value = '15';
        // 默认时间
        let dt = defaultDate || new Date().toISOString().slice(0, 10);
        if (dt.length === 10) dt += 'T09:00';
        document.getElementById('sch-start').value = dt.slice(0, 16);
        const endDt = new Date(dt); endDt.setHours(endDt.getHours() + 1);
        document.getElementById('sch-end').value = endDt.toISOString().slice(0, 16);
    }
    onSchAlldayChange();
    modal.style.display = 'flex';
}
function closeScheduleModal(e) {
    if (e && e.target !== e.currentTarget) return;
    document.getElementById('schedule-modal').style.display = 'none';
}
function onSchAlldayChange() {
    const allday = document.getElementById('sch-allday').checked;
    document.getElementById('sch-time-row').style.display = allday ? 'none' : '';
    document.getElementById('sch-end-row').style.display = allday ? 'none' : '';
}
function onSchCategoryChange() { /* reserved for future color preview */ }

async function saveSchedule() {
    const editId = document.getElementById('schedule-edit-id').value;
    const allDay = document.getElementById('sch-allday').checked;
    const data = {
        title: document.getElementById('sch-title').value.trim(),
        category: document.getElementById('sch-category').value,
        all_day: allDay,
        start_time: allDay ? document.getElementById('sch-start').value.slice(0, 10) + 'T00:00:00' : document.getElementById('sch-start').value + ':00',
        end_time: allDay ? document.getElementById('sch-start').value.slice(0, 10) + 'T23:59:59' : document.getElementById('sch-end').value + ':00',
        location: document.getElementById('sch-location').value.trim(),
        description: document.getElementById('sch-desc').value.trim(),
        reminder_minutes: parseInt(document.getElementById('sch-reminder').value) || 15
    };
    if (!data.title) { alert('请输入日程标题'); return; }

    try {
        if (editId) {
            await fetch(`${API_BASE}/api/schedules/${editId}`, {
                method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data)
            });
        } else {
            const res = await fetch(`${API_BASE}/api/schedules`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data)
            });
            const result = await res.json();
            if (result.schedule && result.schedule.conflicts && result.schedule.conflicts.length > 0) {
                const warn = document.getElementById('sch-conflict-warning');
                warn.style.display = 'block';
                warn.textContent = `⚠️ 与 ${result.schedule.conflicts.length} 个日程时间冲突`;
            }
        }
        closeScheduleModal();
        calRender();
        if (editId) {
            showToast({ task_name: '✅ 日程已更新', result: '日程更新成功', time: '' });
        } else {
            showToast({ task_name: '📅 日程已创建', result: '新日程创建成功', time: '' });
        }
    } catch (e) {
        alert('保存失败: ' + e.message);
    }
}

async function deleteScheduleFromModal() {
    const editId = document.getElementById('schedule-edit-id').value;
    if (!editId) return;
    if (!confirm('确定删除该日程？')) return;
    try {
        await fetch(`${API_BASE}/api/schedules/${editId}`, { method: 'DELETE' });
        closeScheduleModal();
        calRender();
        showToast({ task_name: '🗑️ 日程已删除', result: '日程删除成功', time: '' });
    } catch (e) { alert('删除失败: ' + e.message); }
}

