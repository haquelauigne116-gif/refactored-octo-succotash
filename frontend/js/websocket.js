/**
 * websocket.js — WebSocket 实时推送 + Toast 弹窗模块
 */
const API_BASE = window.API_BASE || window.location.origin;

let ws = null;
let wsReconnectDelay = 1000;
const WS_MAX_DELAY = 30000;
let wsHeartbeatTimer = null;

function connectWebSocket() {
    const wsUrl = 'ws://' + window.location.host + '/ws';
    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
        console.log('[WS] 连接成功');
        wsReconnectDelay = 1000;
        clearInterval(wsHeartbeatTimer);
        wsHeartbeatTimer = setInterval(() => {
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send('ping');
            }
        }, 25000);
    };

    ws.onmessage = (event) => {
        if (event.data === 'pong') return;
        try {
            const data = JSON.parse(event.data);
            if (data.type === 'task_result') {
                showToast(data);
                if (document.getElementById('task-view').classList.contains('active')) {
                    window.loadTasks();
                }
            } else if (data.type === 'task_created') {
                showToast({
                    task_name: data.task.task_name,
                    result: '✅ 已自动创建新定时任务',
                    time: data.task.created_at || '',
                });
                if (document.getElementById('task-view').classList.contains('active')) {
                    window.loadTasks();
                }
            } else if (data.type === 'schedule_reminder' || data.type === 'schedule_created') {
                showToast({
                    task_name: data.task_name || (data.type === 'schedule_reminder' ? '📅 日程提醒' : '📅 日程创建'),
                    result: data.result || '日程操作成功',
                    time: data.time || '',
                });
                if (document.getElementById('calendar-view').classList.contains('active')) {
                    window.calRender();
                }
            } else if (data.type === 'file_tags_ready') {
                const tagMsg = data.tags && data.tags.length > 0
                    ? `标签: ${data.tags.join(', ')}`
                    : (data.error ? `标签生成失败: ${data.error}` : '无标签');
                showToast({
                    task_name: '🏷️ 标签就绪',
                    result: `${data.original_name}\n${tagMsg}`,
                    time: '',
                });
                if (document.getElementById('files-view').classList.contains('active')) {
                    window.loadFiles();
                }
                if (document.getElementById('music-view').classList.contains('active')) {
                    window.loadMusic();
                }
            }
        } catch (e) {
            console.warn('[WS] 解析消息失败', e);
        }
    };

    ws.onclose = () => {
        console.log(`[WS] 连接关闭，${wsReconnectDelay / 1000}s 后重连...`);
        clearInterval(wsHeartbeatTimer);
        setTimeout(() => {
            wsReconnectDelay = Math.min(wsReconnectDelay * 2, WS_MAX_DELAY);
            connectWebSocket();
        }, wsReconnectDelay);
    };

    ws.onerror = (err) => {
        console.error('[WS] 错误', err);
        ws.close();
    };
}

export function showToast(data) {
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
    requestAnimationFrame(() => toast.classList.remove('toast-enter'));

    setTimeout(() => {
        toast.classList.add('toast-exit');
        toast.addEventListener('animationend', () => toast.remove());
    }, 8000);

    const toasts = container.querySelectorAll('.toast-item');
    if (toasts.length > 5) {
        toasts[0].remove();
    }
}

// ── 初始化 & 导出 ──
export function initWebSocket() {
    connectWebSocket();
}

Object.assign(window, { showToast, connectWebSocket });
