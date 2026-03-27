/**
 * music.js — 音乐播放器模块
 */
const API_BASE = window.API_BASE || window.location.origin;

let allSongs = [];
let musicQueue = [];
let currentSearchMode = 'name';
let currentSongIndex = -1;
let currentLyrics = [];
let fullscreenOpen = false;
let currentPlaylist = [];
let currentActiveLyricIndex = -1;
const audioPlayer = document.getElementById('audio-player');

// ── 搜索 ──
function setSearchMode(mode) {
    currentSearchMode = mode;
    document.querySelectorAll('.search-mode-btn').forEach(b => b.classList.toggle('active', b.dataset.mode === mode));
    const input = document.getElementById('music-search-input');
    const placeholders = { name: '输入歌曲名称...', ai: '描述你的心情或场景，如：下雨天适合听的安静歌曲...', tag: '输入标签关键词，如：电子、流行...' };
    input.placeholder = placeholders[mode] || '';
    input.value = '';
}

function generateCoverGradient(str) {
    let hash = 0;
    for (let i = 0; i < str.length; i++) {
        hash = str.charCodeAt(i) + ((hash << 5) - hash);
    }
    const h1 = Math.abs(hash % 360);
    const h2 = (h1 + 40) % 360;
    return `linear-gradient(135deg, hsl(${h1},55%,35%), hsl(${h2},65%,25%))`;
}

function getSongDisplayInfo(song) {
    const meta = song.file_meta || {};
    const title = meta.title || song.original_name.replace(/\.[^.]+$/, '');
    const artist = (meta.artists || []).join('、') || '';
    return { title, artist };
}

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

async function doMusicSearch() {
    const query = document.getElementById('music-search-input').value.trim();
    if (!query) { renderBrowseList(allSongs); return; }

    if (currentSearchMode === 'name') {
        const q = query.toLowerCase();
        const filtered = allSongs.filter(s => {
            const info = getSongDisplayInfo(s);
            return info.title.toLowerCase().includes(q) || info.artist.toLowerCase().includes(q) || s.original_name.toLowerCase().includes(q);
        });
        renderBrowseList(filtered);
    } else if (currentSearchMode === 'tag') {
        const q = query.toLowerCase();
        const filtered = allSongs.filter(s => (s.tags || []).some(t => t.toLowerCase().includes(q)));
        renderBrowseList(filtered);
    } else if (currentSearchMode === 'ai') {
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
                window.showToast({ task_name: '✨ AI 推荐', result: `${data.playlist.playlist_name}\n${data.playlist.description}`, time: '' });
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
        item._songData = song;
        list.appendChild(item);
    });
}

function addToQueue(btnEl) {
    const item = btnEl.closest('.browse-song-item');
    const song = item._songData;
    if (!song) return;
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

function removeFromQueue(idx) {
    if (idx < currentSongIndex) currentSongIndex--;
    else if (idx === currentSongIndex) {
        audioPlayer.pause();
        currentSongIndex = -1;
    }
    musicQueue.splice(idx, 1);
    renderQueue();
}

function clearQueue() {
    musicQueue = [];
    currentSongIndex = -1;
    audioPlayer.pause();
    document.getElementById('music-player-bar').style.display = 'none';
    renderQueue();
}

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

function playQueue() {
    if (musicQueue.length > 0) {
        currentPlaylist = musicQueue;
        playSong(0);
    }
}

function playSong(index) {
    currentPlaylist = musicQueue;
    if (index < 0 || index >= musicQueue.length) return;
    const song = musicQueue[index];
    currentSongIndex = index;

    audioPlayer.src = song.download_url;
    audioPlayer.play();

    const info = getSongDisplayInfo(song);
    const coverArt = `/api/music/cover/${encodeURIComponent(song.object_name)}`;
    const coverGrad = generateCoverGradient(song.original_name);

    const bar = document.getElementById('music-player-bar');
    bar.style.display = 'block';
    document.getElementById('player-song-name').textContent = info.title;
    document.getElementById('player-song-artist').textContent = info.artist;
    document.getElementById('player-play-btn').textContent = '⏸';

    document.getElementById('fp-song-title').textContent = info.title;
    document.getElementById('fp-song-artist').textContent = info.artist;
    document.getElementById('fp-play-btn').textContent = '⏸';

    const coverEl = document.getElementById('fp-cover-img');
    coverEl.innerHTML = `<img src="${coverArt}" style="width:100%;height:100%;object-fit:cover;" onerror="this.outerHTML='🎵'; this.parentElement.style.background='${coverGrad}'">`;
    coverEl.style.background = 'transparent';

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
        audioPlayer.pause();
        document.getElementById('player-play-btn').textContent = '▶';
        document.getElementById('fp-play-btn').textContent = '▶';
        return;
    }
    playSong(nextIdx);
}

audioPlayer.onended = () => { playNext(); };

function seekTo(event) {
    if (!audioPlayer.duration) return;
    const bar = event.currentTarget;
    const rect = bar.getBoundingClientRect();
    const pct = (event.clientX - rect.left) / rect.width;
    audioPlayer.currentTime = pct * audioPlayer.duration;
    event.stopPropagation();
}

function setVolume(val) {
    audioPlayer.volume = val / 100;
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

// ── 音频事件 ──
audioPlayer.addEventListener('timeupdate', () => {
    if (!audioPlayer.duration) return;
    const pct = (audioPlayer.currentTime / audioPlayer.duration) * 100;
    document.getElementById('player-progress-fill').style.width = pct + '%';
    document.getElementById('player-time').textContent =
        formatTime(audioPlayer.currentTime) + ' / ' + formatTime(audioPlayer.duration);
    document.getElementById('fp-progress-fill').style.width = pct + '%';
    document.getElementById('fp-time-current').textContent = formatTime(audioPlayer.currentTime);
    document.getElementById('fp-time-total').textContent = formatTime(audioPlayer.duration);

    if (currentLyrics.length > 0 && fullscreenOpen) {
        syncLyrics(audioPlayer.currentTime);
    }
});

audioPlayer.addEventListener('ended', () => { playNext(); });

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

// ── 全屏播放器 ──
function openFullscreenPlayer(event) {
    if (event && event.target.closest('.player-controls, .player-right, .player-progress-bar')) return;
    const fp = document.getElementById('fullscreen-player');
    fp.classList.add('fp-open');
    fullscreenOpen = true;
    if (currentLyrics.length > 0) {
        syncLyrics(audioPlayer.currentTime);
    }
}

function closeFullscreenPlayer() {
    const fp = document.getElementById('fullscreen-player');
    fp.classList.remove('fp-open');
    fullscreenOpen = false;
}

// ── 歌词 ──
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
            const offset = el.offsetTop + el.clientHeight / 2;
            const center = scroll.clientHeight / 2;
            content.style.transform = `translateY(${center - offset}px)`;
        } else {
            el.classList.remove('lyrics-active');
        }
    });
}

// ── 导出到全局 ──
Object.assign(window, {
    setSearchMode, loadMusic, doMusicSearch,
    addToQueue, removeFromQueue, clearQueue, playQueue,
    playSong, togglePlay, playPrev, playNext,
    seekTo, setVolume, toggleMute,
    openFullscreenPlayer, closeFullscreenPlayer,
});
