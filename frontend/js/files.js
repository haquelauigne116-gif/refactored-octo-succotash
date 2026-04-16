/**
 * files.js — 文件管理模块
 */
const API_BASE = window.API_BASE || window.location.origin;

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
    const cancelBtn = document.getElementById('file-cancel-btn');
    const progressWrapper = document.getElementById('upload-progress-wrapper');
    const progressFill = document.getElementById('upload-progress-fill');
    const progressText = document.getElementById('upload-progress-text');

    btn.disabled = true;
    btn.textContent = '准备上传...';
    cancelBtn.style.display = 'none';
    progressWrapper.style.display = 'flex';
    progressFill.style.width = '0%';
    progressText.textContent = '0%';

    const formData = new FormData();
    pendingFiles.forEach(file => {
        formData.append('files', file);
    });
    formData.append('description', document.getElementById('file-description').value);

    const xhr = new XMLHttpRequest();

    // 上传进度
    xhr.upload.addEventListener('progress', (e) => {
        if (e.lengthComputable) {
            const pct = Math.round((e.loaded / e.total) * 100);
            progressFill.style.width = pct + '%';
            progressText.textContent = pct + '%';
            btn.textContent = `上传中 ${pct}%`;
        }
    });

    // 上传完成
    xhr.addEventListener('load', () => {
        if (xhr.status === 200) {
            try {
                const data = JSON.parse(xhr.responseText);
                if (data.status === 'ok') {
                    window.showToast({ task_name: '文件上传', result: `✅ ${data.uploaded} 个文件上传成功\n⏳ AI 标签生成中...`, time: '' });
                    pendingFiles = [];
                    document.getElementById('file-desc-row').style.display = 'none';
                    document.getElementById('file-description').value = '';
                    document.getElementById('file-input-hidden').value = '';
                    loadFiles();
                } else {
                    alert('上传失败: ' + data.message);
                }
            } catch (e) {
                alert('解析响应失败: ' + e);
            }
        } else {
            alert('上传失败: HTTP ' + xhr.status);
        }
        _resetUploadUI();
    });

    // 网络错误
    xhr.addEventListener('error', () => {
        alert('网络错误，上传失败。请检查网络连接后重试。');
        _resetUploadUI();
    });

    // 超时（10 分钟）
    xhr.timeout = 10 * 60 * 1000;
    xhr.addEventListener('timeout', () => {
        alert('上传超时（超过 10 分钟），请检查网络或尝试上传较小的文件。');
        _resetUploadUI();
    });

    xhr.open('POST', API_BASE + '/api/files/upload_batch');
    xhr.send(formData);

    function _resetUploadUI() {
        btn.disabled = false;
        btn.textContent = '🚀 上传';
        cancelBtn.style.display = '';
        progressWrapper.style.display = 'none';
        progressFill.style.width = '0%';
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
                window.showToast({ task_name: '✨ AI 检索完毕', result: toastMsg, time: '' });
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

// ── 导出到全局 ──
Object.assign(window, {
    handleFileDrop, handleFileSelect, cancelUpload, uploadFile,
    searchFiles, aiSearchFiles, loadFiles, deleteFile,
    getFileIcon, formatSize,
});
