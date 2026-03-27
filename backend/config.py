"""
config.py — 全局配置、路径常量、模型提供商管理

所有私密配置（API Key、MinIO 凭据、通知渠道密钥等）统一存放在 backend/secrets.json
"""
import os
import json
from openai import OpenAI  # type: ignore[import]

# ====== 路径常量 ======
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # learn_ai/

DATA_DIR = os.path.join(BASE_DIR, "data")
MEMORY_DIR = os.path.join(DATA_DIR, "memory")
SESSION_DIR = os.path.join(DATA_DIR, "session")
KNOWLEDGE_DIR = os.path.join(DATA_DIR, "knowledge_base")
TASKS_FILE = os.path.join(DATA_DIR, "tasks.json")
SCHEDULE_DB = os.path.join(DATA_DIR, "schedules.db")

MEMORY_FILE = os.path.join(MEMORY_DIR, "memory.md")
SYSTEM_PROMPT_FILE = os.path.join(MEMORY_DIR, "system_prompt.md")
SESSIONS_META_FILE = os.path.join(SESSION_DIR, "_meta.json")

# 统一密钥/配置文件
SECRETS_FILE = os.path.join(BASE_DIR, "backend", "secrets.json")

# 兼容旧路径（用于迁移检测）
_OLD_PROVIDERS_FILE = os.path.join(BASE_DIR, "backend", "providers.json")
_OLD_MINIO_FILE = os.path.join(BASE_DIR, "backend", "minio_config.json")
_OLD_NOTIFICATION_FILE = os.path.join(BASE_DIR, "backend", "notification.json")
_OLD_SETTINGS_FILE = os.path.join(BASE_DIR, "backend", "settings.json")

MINIO_INDEX_FILE = os.path.join(DATA_DIR, "minio_index.json")  # 旧 JSON 索引（迁移检测用）
MINIO_INDEX_DB = os.path.join(DATA_DIR, "minio_index.db")      # 新 SQLite 索引

FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")
INDEX_HTML = os.path.join(FRONTEND_DIR, "index.html")

# ====== 初始化必要目录 ======
for d in [MEMORY_DIR, SESSION_DIR, KNOWLEDGE_DIR]:
    os.makedirs(d, exist_ok=True)

# ====== 加载系统提示词 ======
with open(SYSTEM_PROMPT_FILE, "r", encoding="utf-8") as f:
    SYSTEM_PROMPT = f.read()


# ====== 统一密钥文件读写 ======

def _load_secrets() -> dict:
    """加载 secrets.json，如果不存在则尝试从旧文件迁移"""
    if os.path.exists(SECRETS_FILE):
        with open(SECRETS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)

    # 自动迁移：从旧的独立文件合并成 secrets.json
    secrets: dict = {"providers": {}, "minio": {}, "notification": {}, "settings": {}}
    for old_file, key in [
        (_OLD_PROVIDERS_FILE, "providers"),
        (_OLD_MINIO_FILE, "minio"),
        (_OLD_NOTIFICATION_FILE, "notification"),
        (_OLD_SETTINGS_FILE, "settings"),
    ]:
        if os.path.exists(old_file):
            try:
                with open(old_file, "r", encoding="utf-8") as f:
                    secrets[key] = json.load(f)
            except Exception:
                pass
    _save_secrets(secrets)
    return secrets


def _save_secrets(secrets: dict):
    """保存整个 secrets.json"""
    with open(SECRETS_FILE, "w", encoding="utf-8") as f:
        json.dump(secrets, f, ensure_ascii=False, indent=2)


_SECRETS = _load_secrets()


# ====== 模型提供商配置 ======

def load_providers_config() -> dict:
    """从 secrets.json 的 providers 部分加载"""
    return _SECRETS.get("providers", {})

def save_providers_config(providers: dict):
    """保存 providers 到 secrets.json"""
    _SECRETS["providers"] = providers
    _save_secrets(_SECRETS)

API_PROVIDERS = load_providers_config()


# ====== 系统设置 (总结模型、判断模型) ======

_DEFAULT_SETTINGS = {
    "chat_provider": "deepseek",
    "chat_model": "deepseek-chat",
    "summary_provider": "deepseek",
    "summary_model": "deepseek-chat",
    "judge_provider": "deepseek",
    "judge_model": "deepseek-chat",
    "file_provider": "deepseek",
    "file_model": "deepseek-chat",
    "task_provider": "deepseek",
    "task_model": "deepseek-chat",
    "bailian_api_key": "",
    "enable_mcp_for_chat": False,
    "max_tool_loops": 6,
}

def load_settings() -> dict:
    """加载系统设置，缺失项用默认值填充"""
    settings = dict(_DEFAULT_SETTINGS)
    settings.update(_SECRETS.get("settings", {}))
    return settings

def save_settings(settings_data: dict):
    """保存系统设置到 secrets.json"""
    _SECRETS["settings"] = settings_data
    _save_secrets(_SECRETS)

APP_SETTINGS = load_settings()


# ====== OpenAI 客户端工厂 ======

def get_client(provider_id: str) -> OpenAI:
    """根据 provider_id 创建 OpenAI 客户端"""
    cfg = API_PROVIDERS[provider_id]
    return OpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"])


def get_model_caps(provider_id: str, model_id: str) -> list[str]:
    """查询指定模型的能力列表 (text / vision / reasoning)"""
    cfg = API_PROVIDERS.get(provider_id, {})
    for m in cfg.get("models", []):
        if m["id"] == model_id:
            return m.get("caps", ["text"])
    return ["text"]


# ====== MinIO 配置 ======

def load_minio_config() -> dict:
    """从 secrets.json 的 minio 部分加载"""
    return _SECRETS.get("minio", {})


def load_volcengine_config() -> dict:
    """从 secrets.json 的 volcengine 部分加载"""
    return _SECRETS.get("volcengine", {})


def load_lastfm_config() -> dict:
    """从 secrets.json 的 lastfm 部分加载 API key 和 shared secret"""
    return _SECRETS.get("lastfm", {})


# ====== 通知通道配置 ======

_DEFAULT_NOTIFICATION_CONFIG: dict = {
    "channels": {
        "websocket": {"enabled": True},
        "dingtalk": {
            "enabled": False,
            "app_key": "",
            "app_secret": "",
            "agent_id": "",
            "robot_code": "",
            "msg_type": "single",
            "user_ids": [],
            "open_conversation_id": "",
        },
    }
}

def load_notification_config() -> dict:
    """加载通知通道配置，缺失项用默认值填充"""
    config = json.loads(json.dumps(_DEFAULT_NOTIFICATION_CONFIG))  # deep copy
    saved = _SECRETS.get("notification", {})
    for ch_name, ch_cfg in saved.get("channels", {}).items():
        if ch_name in config["channels"]:
            config["channels"][ch_name].update(ch_cfg)
        else:
            config["channels"][ch_name] = ch_cfg
    return config

def save_notification_config(config: dict):
    """保存通知通道配置到 secrets.json"""
    _SECRETS["notification"] = config
    _save_secrets(_SECRETS)
