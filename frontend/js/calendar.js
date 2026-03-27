/**
 * calendar.js — 日历模块
 */
const API_BASE = window.API_BASE || window.location.origin;

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

// ── 日程弹窗 ──
function openScheduleModal(editId, defaultDate) {
    const modal = document.getElementById('schedule-modal');
    const titleEl = document.getElementById('schedule-modal-title');
    const deleteBtn = document.getElementById('sch-delete-btn');
    document.getElementById('schedule-edit-id').value = '';
    document.getElementById('sch-conflict-warning').style.display = 'none';

    if (editId) {
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
        titleEl.textContent = '📅 新建日程';
        deleteBtn.style.display = 'none';
        document.getElementById('sch-title').value = '';
        document.getElementById('sch-category').value = '其他';
        document.getElementById('sch-allday').checked = false;
        document.getElementById('sch-location').value = '';
        document.getElementById('sch-desc').value = '';
        document.getElementById('sch-reminder').value = '15';
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
            window.showToast({ task_name: '✅ 日程已更新', result: '日程更新成功', time: '' });
        } else {
            window.showToast({ task_name: '📅 日程已创建', result: '新日程创建成功', time: '' });
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
        window.showToast({ task_name: '🗑️ 日程已删除', result: '日程删除成功', time: '' });
    } catch (e) { alert('删除失败: ' + e.message); }
}

// ── 导出到全局 ──
Object.assign(window, {
    calendarInit, calPrev, calNext, calToday, calSwitchView, calRender,
    openScheduleModal, closeScheduleModal, saveSchedule, deleteScheduleFromModal,
    onSchAlldayChange, onSchCategoryChange,
});
