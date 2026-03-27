/**
 * tasks.js — 定时任务管理模块
 */
const API_BASE = window.API_BASE || window.location.origin;

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

// ── 导出到全局 ──
Object.assign(window, {
    openTaskModal, closeTaskModal, onTriggerTypeChange,
    createTask, loadTasks, deleteTask,
    pauseTask, resumeTask, formatTriggerArgs,
});
