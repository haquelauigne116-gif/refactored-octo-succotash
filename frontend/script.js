
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
            const viewMap = { chat: 0, task: 1, files: 2, music: 3, settings: 4 };
            if (viewMap[viewName] !== undefined) menuItems[viewMap[viewName]].classList.add('active');
            if (viewName === 'task') loadTasks();
            if (viewName === 'files') loadFiles();
            if (viewName === 'settings') loadSettings();
            if (viewName === 'music') loadMusic();
        }

        function handleEnter(event) {
            if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                sendMessage();
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
                const select = document.getElementById("provider-select");
                
                globalProviders = data.providers; // 保存全局以供设置页面复用
                
                select.innerHTML = '';
                data.providers.forEach(p => {
                    const group = document.createElement("optgroup");
                    group.label = p.name;
                    p.models.forEach(m => {
                        const opt = document.createElement("option");
                        opt.value = p.id + "|" + m.id;
                        opt.textContent = m.name;
                        group.appendChild(opt);
                    });
                    select.appendChild(group);
                });
                select.value = data.current_provider + "|" + data.current_model;
            } catch (e) {
                console.error("加载模型列表失败:", e);
            }
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

                // 填充总结和判断模型的选项
                const sumSelect = document.getElementById("summary-model-select");
                const judgeSelect = document.getElementById("judge-model-select");
                sumSelect.innerHTML = '';
                judgeSelect.innerHTML = '';
                
                globalProviders.forEach(p => {
                    const group_sum = document.createElement("optgroup");
                    const group_jud = document.createElement("optgroup");
                    group_sum.label = p.name;
                    group_jud.label = p.name;
                    p.models.forEach(m => {
                        const getOpt = () => { let o = document.createElement('option'); o.value = p.id+"|"+m.id; o.textContent = m.name; return o;};
                        group_sum.appendChild(getOpt());
                        group_jud.appendChild(getOpt());
                    });
                    sumSelect.appendChild(group_sum);
                    judgeSelect.appendChild(group_jud);
                });

                if(systemSettingsData.summary_model) {
                    sumSelect.value = systemSettingsData.summary_provider + "|" + systemSettingsData.summary_model;
                }
                if(systemSettingsData.judge_model) {
                    judgeSelect.value = systemSettingsData.judge_provider + "|" + systemSettingsData.judge_model;
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

            const sumVal = document.getElementById("summary-model-select").value.split("|");
            const judVal = document.getElementById("judge-model-select").value.split("|");

            const payload = {
                api_keys: api_keys,
                summary_provider: sumVal[0],
                summary_model: sumVal[1],
                judge_provider: judVal[0],
                judge_model: judVal[1]
            };

            try {
                await fetch(API_BASE + "/api/settings", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload)
                });
                
                const msg = document.getElementById('settings-msg');
                msg.style.display = 'block';
                setTimeout(() => msg.style.display = 'none', 3000);
                
                // 清空输入框并重新加载状态
                inputs.forEach(inp => inp.value = '');
                loadSettings();
                
            } catch (err) {
                alert("保存失败: " + err);
            }
        }

        async function switchProvider() {
            const select = document.getElementById("provider-select");
            const val = select.value.split("|");
            try {
                const res = await fetch(API_BASE + "/switch_provider", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ provider_id: val[0], model_id: val[1] })
                });
                const data = await res.json();
                if (data.status === "ok") {
                    appendMessage("🔧 **[系统提示]** 已切换至 " + select.options[select.selectedIndex].text + " 模型！", "system-msg");
                }
            } catch (e) {
                console.error("切换模型失败:", e);
            }
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
                                appendMessage(msg.content, "ai-msg");
                                msgIndex++;
                            }
                            // 在当前消息之后插入对应的事件卡片
                            if (eventsByIndex[msgIndex]) {
                                eventsByIndex[msgIndex].forEach(evt => {
                                    if (evt.type === 'auto_task') {
                                        appendMessage(renderAutoTaskCard(evt.data), "ai-msg event-msg");
                                    } else if (evt.type === 'file_search') {
                                        appendMessage(renderFileSearchCard(evt.data), "ai-msg event-msg");
                                    }
                                });
                            }
                        });
                    }
                    loadSessions();  // 刷新高亮
                    switchView('chat');
                }
            } catch (e) {
                appendMessage("❌ 切换会话失败", "system-msg");
            }
        }

        // ====== 聊天功能 ======

        async function sendMessage() {
            const text = userInput.value.trim();
            if (!text) return;

            appendMessage(text, "user-msg");
            userInput.value = "";

            sendBtn.disabled = true;
            sendBtn.innerText = "发送中...";

            try {
                const response = await fetch(API_BASE + "/chat", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ user_word: text })
                });

                const data = await response.json();

                if (text.toLowerCase() === 'exit') {
                    appendMessage(data.reply, "system-msg");
                } else {
                    appendMessage(data.reply, "ai-msg");
                }

                // 自动任务确认卡片
                if (data.auto_task) {
                    appendMessage(renderAutoTaskCard(data.auto_task), "ai-msg event-msg");
                }

                // AI 查找文件卡片
                if (data.file_search_result && data.file_search_result.files && data.file_search_result.files.length > 0) {
                    appendMessage(renderFileSearchCard(data.file_search_result), "ai-msg event-msg");
                }
            } catch (error) {
                appendMessage("❌ 网络连接失败：请确保你的终端已经运行了 `uvicorn backend.server:app`！", "system-msg");
            } finally {
                sendBtn.disabled = false;
                sendBtn.innerText = "发送";
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

        function renderFileSearchCard(search_res) {
            let fileCards = search_res.files.map(f => {
                const icon = getFileIcon(f.content_type);
                const size = formatSize(f.size || 0);
                return `<a href="${f.download_url}" target="_blank" rel="noopener" style="border:1px solid #e8e8e8; padding:4px 6px; border-radius:4px; display:flex; align-items:center; gap:4px; background:#fff; text-decoration:none; color:inherit; min-width:0;">
                    <span style="font-size:12px; flex-shrink:0;">${icon}</span>
                    <div style="flex:1; min-width:0; overflow:hidden;">
                        <div style="font-size:10px; font-weight:600; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; color:#333;">${f.original_name}</div>
                        <div style="font-size:9px; color:#aaa;">${size}</div>
                    </div>
                </a>`;
            }).join("");

            return `<div class="auto-task-card" style="flex-direction:column; align-items:stretch; padding:6px 8px;">
                <div style="font-size:11px; font-weight:600; color:#0078FF; margin-bottom:3px;">📁 找到 ${search_res.files.length} 个文件</div>
                <div style="display:grid; grid-template-columns:repeat(auto-fill, minmax(100px, 1fr)); gap:3px; max-width:100%;">${fileCards}</div>
            </div>`;
        }

        function appendMessage(text, className) {
            const div = document.createElement("div");
            div.className = "msg-bubble " + className;
            div.innerHTML = text.replace(/\n/g, '<br>');
            chatHistory.appendChild(div);
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

        let pendingFile = null;

        function handleFileDrop(event) {
            event.preventDefault();
            document.getElementById('file-upload-zone').classList.remove('drag-over');
            const files = event.dataTransfer.files;
            if (files.length > 0) selectFile(files[0]);
        }

        function handleFileSelect(event) {
            const files = event.target.files;
            if (files.length > 0) selectFile(files[0]);
        }

        function selectFile(file) {
            pendingFile = file;
            document.getElementById('file-selected-name').textContent = `📄 ${file.name} (${formatSize(file.size)})`;
            document.getElementById('file-desc-row').style.display = 'flex';
            document.getElementById('file-description').focus();
        }

        async function uploadFile() {
            if (!pendingFile) return;
            const btn = document.getElementById('file-upload-btn');
            btn.disabled = true;
            btn.textContent = '上传中...';

            const formData = new FormData();
            formData.append('file', pendingFile);
            formData.append('description', document.getElementById('file-description').value);

            try {
                const res = await fetch(API_BASE + '/api/files/upload', { method: 'POST', body: formData });
                const data = await res.json();
                if (data.status === 'ok') {
                    showToast({ task_name: '文件上传', result: `✅ ${data.file.original_name} 上传成功\n⏳ AI 标签生成中...`, time: '' });
                    pendingFile = null;
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
                const hasTags = f.tags && f.tags.length > 0;
                const tagsHtml = hasTags
                    ? (f.tags || []).map(t => `<span class="file-tag">${t}</span>`).join('')
                    : '<span class="file-tag" style="opacity:0.6; animation: pulse 1.5s infinite;">⏳ 标签生成中...</span>';
                const card = document.createElement('div');
                card.className = 'file-card';
                card.setAttribute('data-object-name', f.object_name);
                card.innerHTML = `
                    <div class="file-card-header">
                        <span class="file-card-icon">${getFileIcon(f.content_type)}</span>
                        <span class="file-card-name" title="${f.original_name}">${f.original_name}</span>
                    </div>
                    <div class="file-card-desc">${f.description || '无描述'}</div>
                    <div class="file-card-tags">${tagsHtml}</div>
                    <div class="file-card-meta">
                        <span>${formatSize(f.size)}</span>
                        <span>${(f.uploaded_at || '').substring(0, 16).replace('T', ' ')}</span>
                    </div>
                    <div class="file-card-actions">
                        <a class="task-btn task-btn-resume" href="${f.download_url}" target="_blank" rel="noopener">⬇ 下载</a>
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

        let currentPlaylist = [];
        let currentSongIndex = -1;
        let currentLyrics = [];
        let fullscreenOpen = false;
        const audioPlayer = document.getElementById('audio-player');

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

        async function loadMusic() {
            try {
                const res = await fetch(API_BASE + '/api/music');
                const data = await res.json();
                if (data.songs && data.songs.length > 0) {
                    currentPlaylist = data.songs;
                    renderSongList(data.songs);
                    document.getElementById('playlist-info').style.display = 'flex';
                    document.getElementById('playlist-name').textContent = '全部音乐';
                    document.getElementById('playlist-desc').textContent = `共 ${data.songs.length} 首`;
                } else {
                    document.getElementById('song-list').innerHTML = '<div class="song-list-empty">🎵 MinIO 中暂无音频文件<br>请先到文件管理页面上传音乐</div>';
                    document.getElementById('playlist-info').style.display = 'none';
                }
            } catch (e) {
                console.error('加载音乐失败:', e);
            }
        }

        async function generatePlaylist() {
            const prompt = document.getElementById('playlist-prompt').value.trim();
            if (!prompt) { alert('请输入心情或场景描述'); return; }

            const btn = document.getElementById('btn-generate-playlist');
            btn.disabled = true;
            btn.innerHTML = '<span class="btn-loading">✨</span> 生成中...';

            try {
                const res = await fetch(API_BASE + '/api/music/playlist', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ prompt })
                });
                const data = await res.json();
                if (data.status === 'ok' && data.playlist) {
                    const pl = data.playlist;
                    currentPlaylist = pl.songs || [];
                    document.getElementById('playlist-info').style.display = 'flex';
                    document.getElementById('playlist-name').textContent = pl.playlist_name;
                    document.getElementById('playlist-desc').textContent = pl.description;
                    renderSongList(currentPlaylist);
                    if (currentPlaylist.length > 0) {
                        showToast({ task_name: '🎵 AI 歌单', result: `已生成「${pl.playlist_name}」\n${pl.description}\n共 ${currentPlaylist.length} 首歌`, time: '' });
                    }
                } else {
                    alert('生成失败: ' + (data.message || '未知错误'));
                }
            } catch (e) {
                alert('网络错误: ' + e);
            } finally {
                btn.disabled = false;
                btn.innerHTML = '🎵 AI 生成歌单';
            }
        }

        function renderSongList(songs) {
            const list = document.getElementById('song-list');
            list.innerHTML = '';
            if (songs.length === 0) {
                list.innerHTML = '<div class="song-list-empty">🎵 没有匹配的歌曲</div>';
                return;
            }
            songs.forEach((song, idx) => {
                const isPlaying = idx === currentSongIndex;
                const meta = song.file_meta || {};
                const title = meta.title || song.original_name.replace(/\.[^.]+$/, '');
                const artist = (meta.artists || []).join('、') || '';
                const tags = (song.tags || []).filter(t => t !== title && !((meta.artists||[]).includes(t)));
                const tagsHtml = tags.slice(0, 4).map(t => `<span class="song-tag">${t}</span>`).join('');
                const coverArt = `/api/music/cover/${encodeURIComponent(song.object_name)}`;
                const coverGrad = generateCoverGradient(song.original_name);

                const fallbackIcon = isPlaying ? "<div class='song-card-playing-icon'>♫</div>" : "<div class='song-card-cover-icon'>🎵</div>";
                let coverHtml = `<img src="${coverArt}" style="width:100%;height:100%;object-fit:cover;border-radius:10px;" onerror="this.outerHTML=\`${fallbackIcon}\`">`;

                const item = document.createElement('div');
                item.className = 'song-card' + (isPlaying ? ' song-card-playing' : '');
                item.id = 'song-item-' + idx;
                item.innerHTML = `
                    <div class="song-card-cover" style="background:${coverArt ? '#000' : coverGrad}">
                        ${coverHtml}
                    </div>
                    <div class="song-card-info">
                        <div class="song-card-title">${artist ? artist + ' - ' : ''}${title}</div>
                        <div class="song-card-tags">${tagsHtml}</div>
                    </div>
                `;
                item.onclick = () => playSong(idx);
                list.appendChild(item);
            });
        }

        function playSong(index) {
            if (index < 0 || index >= currentPlaylist.length) return;
            const song = currentPlaylist[index];
            currentSongIndex = index;

            audioPlayer.src = song.download_url;
            audioPlayer.play();

            const meta = song.file_meta || {};
            const title = meta.title || song.original_name.replace(/\.[^.]+$/, '');
            const artist = (meta.artists || []).join('、') || (song.tags || []).join(' · ');
            const coverArt = `/api/music/cover/${encodeURIComponent(song.object_name)}`;
            const coverGrad = generateCoverGradient(song.original_name);

            // 迷你播放器
            const bar = document.getElementById('music-player-bar');
            bar.style.display = 'block';
            document.getElementById('player-song-name').textContent = title;
            document.getElementById('player-song-artist').textContent = artist;
            document.getElementById('player-play-btn').textContent = '⏸';

            // 全屏播放器
            document.getElementById('fp-song-title').textContent = title;
            document.getElementById('fp-song-artist').textContent = artist;
            document.getElementById('fp-play-btn').textContent = '⏸';

            // 封面
            const coverEl = document.getElementById('fp-cover-img');
            coverEl.innerHTML = `<img src="${coverArt}" style="width:100%;height:100%;object-fit:cover;border-radius:50%;" onerror="this.outerHTML='🎵'; this.parentElement.style.background='${coverGrad}'">`;
            coverEl.style.background = 'transparent';

            // 加载歌词
            loadLyrics(song);

            renderSongList(currentPlaylist);
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
            if (currentPlaylist.length === 0) return;
            const newIdx = (currentSongIndex - 1 + currentPlaylist.length) % currentPlaylist.length;
            playSong(newIdx);
        }

        function playNext() {
            if (currentPlaylist.length === 0) return;
            const newIdx = (currentSongIndex + 1) % currentPlaylist.length;
            playSong(newIdx);
        }

        function playAll() {
            if (currentPlaylist.length > 0) playSong(0);
        }

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
            document.getElementById('fp-vinyl').classList.remove('fp-spinning');
        });

        audioPlayer.addEventListener('play', () => {
            document.getElementById('player-play-btn').textContent = '⏸';
            document.getElementById('fp-play-btn').textContent = '⏸';
            document.getElementById('music-player-bar').classList.add('is-playing');
            document.getElementById('fp-vinyl').classList.add('fp-spinning');
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

        function parseLRC(lrcText) {
            const lines = lrcText.split('\n');
            const result = [];
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
                    result.push({ time: min * 60 + sec + ms / 1000, text });
                }
            }
            result.sort((a, b) => a.time - b.time);
            return result;
        }

        function loadLyrics(song) {
            const lyricsText = (song.file_meta && song.file_meta.lyrics) || '';
            const content = document.getElementById('fp-lyrics-content');

            if (!lyricsText.trim()) {
                currentLyrics = [];
                content.innerHTML = '<div class="lyrics-empty">🎵 当前歌曲暂无歌词</div>';
                return;
            }

            const parsed = parseLRC(lyricsText);
            if (parsed.length > 0) {
                currentLyrics = parsed;
                content.innerHTML = parsed.map((l, i) =>
                    `<div class="lyrics-line" data-index="${i}">${l.text}</div>`
                ).join('');
            } else {
                currentLyrics = [];
                content.innerHTML = lyricsText.split('\n')
                    .filter(l => l.trim())
                    .map(l => `<div class="lyrics-line lyrics-plain">${l}</div>`)
                    .join('');
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
            const lines = document.querySelectorAll('#fp-lyrics-content .lyrics-line');
            lines.forEach((el, i) => {
                if (i === activeIdx) {
                    el.classList.add('lyrics-active');
                    const scroll = document.getElementById('fp-lyrics-scroll');
                    const offset = el.offsetTop - scroll.offsetTop - scroll.clientHeight / 2 + el.clientHeight / 2;
                    scroll.scrollTo({ top: offset, behavior: 'smooth' });
                } else {
                    el.classList.remove('lyrics-active');
                }
            });
        }

    