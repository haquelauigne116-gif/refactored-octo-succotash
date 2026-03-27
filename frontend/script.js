/**
 * script.js — 入口模块
 *
 * 负责按顺序导入各功能模块，并触发初始化。
 * 各模块通过 Object.assign(window, {...}) 将函数挂载到全局，
 * 以保持与 index.html 内联 onclick 的兼容性。
 */

import { initChat } from './js/chat.js';
import './js/tasks.js';
import { initWebSocket } from './js/websocket.js';
import './js/files.js';
import './js/music.js';
import './js/calendar.js';

// ── 启动 ──
initChat();        // 加载 providers + sessions
initWebSocket();   // 连接 WebSocket 推送
