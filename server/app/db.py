from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, List, Optional, Tuple, Union

from .config import get_settings

# RLock：connect / fetch* / cursor 可嵌套持锁；所有读写必须经锁，避免多线程共用
# 同一 Connection 时出现 Cursor reset / binding parameter 错误。
_lock = threading.RLock()
_conn = None  # type: Optional[sqlite3.Connection]

SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  username TEXT NOT NULL UNIQUE COLLATE NOCASE,
  password_hash TEXT NOT NULL,
  display_name TEXT NOT NULL,
  is_admin INTEGER NOT NULL DEFAULT 0,
  is_disabled INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS devices (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  device_name TEXT NOT NULL,
  refresh_token_hash TEXT NOT NULL,
  created_at TEXT NOT NULL,
  last_active_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS invites (
  code TEXT PRIMARY KEY,
  created_by TEXT REFERENCES users(id),
  used_by TEXT REFERENCES users(id),
  expires_at TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS server_settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS personas (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  color INTEGER NOT NULL DEFAULT 3105791,
  bio TEXT NOT NULL DEFAULT '',
  channel_id TEXT NOT NULL DEFAULT '',
  state TEXT NOT NULL DEFAULT 'friend',
  is_bot INTEGER NOT NULL DEFAULT 1,
  verify TEXT NOT NULL DEFAULT '',
  remark TEXT NOT NULL DEFAULT '',
  greet INTEGER NOT NULL DEFAULT 0,
  moments_on INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_personas_user ON personas(user_id);

CREATE TABLE IF NOT EXISTS conversations (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  persona_id TEXT REFERENCES personas(id) ON DELETE SET NULL,
  group_id TEXT,
  preview TEXT NOT NULL DEFAULT '',
  last_time TEXT NOT NULL,
  unread INTEGER NOT NULL DEFAULT 0,
  pinned INTEGER NOT NULL DEFAULT 0,
  muted INTEGER NOT NULL DEFAULT 0,
  draft TEXT NOT NULL DEFAULT '',
  last_seq INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations(user_id, last_time DESC);

CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY,
  client_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  conv_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  sender_id TEXT NOT NULL,
  mine INTEGER NOT NULL DEFAULT 0,
  kind TEXT NOT NULL DEFAULT 'text',
  text TEXT NOT NULL DEFAULT '',
  quote_id TEXT,
  seconds INTEGER NOT NULL DEFAULT 0,
  bytes INTEGER NOT NULL DEFAULT 0,
  state TEXT NOT NULL DEFAULT 'sent',
  created_at TEXT NOT NULL,
  UNIQUE(user_id, client_id)
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(user_id, conv_id, seq DESC);
CREATE INDEX IF NOT EXISTS idx_messages_seq ON messages(user_id, seq);

CREATE TABLE IF NOT EXISTS memories (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  persona_id TEXT NOT NULL REFERENCES personas(id) ON DELETE CASCADE,
  text TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memories_persona ON memories(user_id, persona_id);
"""

SCHEMA_V2_CHANNELS = """
CREATE TABLE IF NOT EXISTS channels (
  id TEXT PRIMARY KEY,
  user_id TEXT REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  model TEXT NOT NULL,
  base_url TEXT NOT NULL,
  key_enc TEXT NOT NULL DEFAULT '',
  is_shared INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_channels_user ON channels(user_id);
"""

# 预设模板（无 Key）；用户可复制为自己的渠道或管理员填公共 Key
DEFAULT_CHANNEL_PRESETS = [
    ("DeepSeek", "deepseek-chat", "https://api.deepseek.com"),
    ("通义千问", "qwen-plus", "https://dashscope.aliyuncs.com/compatible-mode"),
    ("智谱", "glm-4-flash", "https://open.bigmodel.cn/api/paas"),
    ("Kimi", "moonshot-v1-8k", "https://api.moonshot.cn"),
    ("MiniMax", "abab6.5s-chat", "https://api.minimax.chat/v1"),
    ("豆包", "doubao-pro", "https://ark.cn-beijing.volces.com/api/v3"),
]


def db_path() -> Path:
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings.data_dir / "chat.db"


def connect() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            path = db_path()
            _conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA foreign_keys = ON")
            _conn.execute("PRAGMA journal_mode = WAL")
            _conn.execute("PRAGMA synchronous = NORMAL")
            _conn.execute("PRAGMA busy_timeout = 5000")
            _migrate(_conn)
        return _conn


def _migrate(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_V1)
    row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    if row is None:
        conn.execute("INSERT INTO meta(key, value) VALUES('schema_version', '1')")
        conn.execute(
            "INSERT OR IGNORE INTO server_settings(key, value) VALUES('registration_mode', ?)",
            (get_settings().registration_mode,),
        )
        conn.commit()
        row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()

    version = int(row["value"]) if row else 1
    if version < 2:
        import secrets
        from datetime import datetime, timezone

        conn.executescript(SCHEMA_V2_CHANNELS)
        now = datetime.now(timezone.utc).isoformat()
        for name, model, base in DEFAULT_CHANNEL_PRESETS:
            conn.execute(
                """
                INSERT INTO channels(id, user_id, name, model, base_url, key_enc, is_shared, created_at)
                VALUES (?, NULL, ?, ?, ?, '', 1, ?)
                """,
                (f"ch_{secrets.token_hex(12)}", name, model, base, now),
            )
        conn.execute("UPDATE meta SET value = '2' WHERE key = 'schema_version'")
        conn.commit()
        version = 2

    if version < 3:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS usage_logs (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL,
              persona_id TEXT,
              channel_id TEXT,
              conv_id TEXT,
              model TEXT NOT NULL DEFAULT '',
              prompt_tokens INTEGER NOT NULL DEFAULT 0,
              completion_tokens INTEGER NOT NULL DEFAULT 0,
              total_tokens INTEGER NOT NULL DEFAULT 0,
              client TEXT NOT NULL DEFAULT 'app',
              status TEXT NOT NULL DEFAULT 'ok',
              error TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_usage_user_time ON usage_logs(user_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_usage_client ON usage_logs(client, created_at DESC);
            """
        )
        conn.execute("UPDATE meta SET value = '3' WHERE key = 'schema_version'")
        conn.commit()
        version = 3

    if version < 4:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS extract_jobs (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL,
              persona_id TEXT NOT NULL,
              kind TEXT NOT NULL,
              result_json TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_extract_persona ON extract_jobs(user_id, persona_id, kind);

            CREATE TABLE IF NOT EXISTS voice_profiles (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL,
              persona_id TEXT NOT NULL,
              filename TEXT NOT NULL,
              path TEXT NOT NULL,
              duration_sec REAL NOT NULL DEFAULT 0,
              sample_rate INTEGER NOT NULL DEFAULT 0,
              channels INTEGER NOT NULL DEFAULT 0,
              embed_status TEXT NOT NULL DEFAULT 'pending',
              embed_note TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_voice_persona ON voice_profiles(user_id, persona_id);

            CREATE TABLE IF NOT EXISTS user_prefs (
              user_id TEXT PRIMARY KEY,
              active_persona_id TEXT,
              updated_at TEXT NOT NULL
            );
            """
        )
        conn.execute("UPDATE meta SET value = '4' WHERE key = 'schema_version'")
        conn.commit()
        version = 4

    if version < 5:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(usage_logs)").fetchall()}
        if "estimated" not in cols:
            conn.execute(
                "ALTER TABLE usage_logs ADD COLUMN estimated INTEGER NOT NULL DEFAULT 0"
            )
        conn.execute("UPDATE meta SET value = '5' WHERE key = 'schema_version'")
        conn.commit()
        version = 5

    if version < 6:
        # 能力网关：workers / 实例 / 任务 / 文件仓库（集群控制面）
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS workers (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              base_url TEXT NOT NULL,
              token TEXT NOT NULL,
              enabled INTEGER NOT NULL DEFAULT 1,
              last_heartbeat_at TEXT,
              gpus_json TEXT NOT NULL DEFAULT '[]',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS plugin_instances (
              id TEXT PRIMARY KEY,
              worker_id TEXT REFERENCES workers(id) ON DELETE SET NULL,
              plugin_id TEXT NOT NULL,
              kind TEXT NOT NULL DEFAULT 'local',
              base_url TEXT NOT NULL DEFAULT '',
              token TEXT NOT NULL DEFAULT '',
              gpu_index INTEGER,
              state TEXT NOT NULL DEFAULT 'stopped',
              vram_mb INTEGER NOT NULL DEFAULT 0,
              max_concurrency INTEGER NOT NULL DEFAULT 1,
              busy_count INTEGER NOT NULL DEFAULT 0,
              keep_warm INTEGER NOT NULL DEFAULT 0,
              idle_ttl_seconds INTEGER NOT NULL DEFAULT 600,
              last_used_at TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_plugin_inst_plugin
              ON plugin_instances(plugin_id, state);

            CREATE TABLE IF NOT EXISTS plugin_jobs (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              persona_id TEXT,
              conversation_id TEXT,
              worker_id TEXT,
              instance_id TEXT,
              plugin_id TEXT NOT NULL,
              capability TEXT NOT NULL,
              remote_job_id TEXT NOT NULL DEFAULT '',
              input_json TEXT NOT NULL DEFAULT '{}',
              status TEXT NOT NULL DEFAULT 'queued',
              progress_pct INTEGER NOT NULL DEFAULT 0,
              progress_msg TEXT NOT NULL DEFAULT '',
              result_json TEXT NOT NULL DEFAULT '{}',
              error TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_plugin_jobs_user
              ON plugin_jobs(user_id, created_at DESC);

            CREATE TABLE IF NOT EXISTS media_files (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              path TEXT NOT NULL,
              content_type TEXT NOT NULL DEFAULT 'application/octet-stream',
              size INTEGER NOT NULL DEFAULT 0,
              sha256 TEXT NOT NULL DEFAULT '',
              source TEXT NOT NULL DEFAULT 'upload',
              created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_media_user ON media_files(user_id);
            """
        )
        conn.execute("UPDATE meta SET value = '6' WHERE key = 'schema_version'")
        conn.commit()
        version = 6

    if version < 7:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS share_links (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              file_id TEXT NOT NULL REFERENCES media_files(id) ON DELETE CASCADE,
              token TEXT NOT NULL UNIQUE,
              expires_at TEXT NOT NULL,
              max_downloads INTEGER NOT NULL DEFAULT 20,
              downloads INTEGER NOT NULL DEFAULT 0,
              revoked INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_share_user ON share_links(user_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_share_token ON share_links(token);
            """
        )
        conn.execute("UPDATE meta SET value = '7' WHERE key = 'schema_version'")
        conn.commit()
        version = 7

    if version < 8:
        # 头像：存 media_files.id
        pcols = {r[1] for r in conn.execute("PRAGMA table_info(personas)").fetchall()}
        if "avatar_file_id" not in pcols:
            conn.execute(
                "ALTER TABLE personas ADD COLUMN avatar_file_id TEXT NOT NULL DEFAULT ''"
            )
        ucols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
        if "avatar_file_id" not in ucols:
            conn.execute(
                "ALTER TABLE users ADD COLUMN avatar_file_id TEXT NOT NULL DEFAULT ''"
            )
        conn.execute("UPDATE meta SET value = '8' WHERE key = 'schema_version'")
        conn.commit()
        version = 8

    if version < 9:
        # 人设级自定义橡皮擦（出站）
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS eraser_rules (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              persona_id TEXT NOT NULL REFERENCES personas(id) ON DELETE CASCADE,
              pattern TEXT NOT NULL,
              mode TEXT NOT NULL DEFAULT 'word',
              enabled INTEGER NOT NULL DEFAULT 1,
              hits INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_eraser_persona
              ON eraser_rules(user_id, persona_id);
            """
        )
        conn.execute("UPDATE meta SET value = '9' WHERE key = 'schema_version'")
        conn.commit()
        version = 9

    if version < 10:
        # 整形偏好 + 消息是否排除出模型上下文（清理格式残渣）
        up_cols = {r[1] for r in conn.execute("PRAGMA table_info(user_prefs)").fetchall()}
        for col, decl in (
            ("shape_enabled", "INTEGER NOT NULL DEFAULT 1"),
            ("split_send", "INTEGER NOT NULL DEFAULT 1"),
            ("echo_streak", "INTEGER NOT NULL DEFAULT 0"),
        ):
            if col not in up_cols:
                conn.execute(f"ALTER TABLE user_prefs ADD COLUMN {col} {decl}")
        m_cols = {r[1] for r in conn.execute("PRAGMA table_info(messages)").fetchall()}
        if "context_skip" not in m_cols:
            conn.execute(
                "ALTER TABLE messages ADD COLUMN context_skip INTEGER NOT NULL DEFAULT 0"
            )
        conn.execute("UPDATE meta SET value = '10' WHERE key = 'schema_version'")
        conn.commit()
        version = 10

    if version < 11:
        # 增量同步：消息 updated_at；任务结果落库：plugin_jobs.message_id
        m_cols = {r[1] for r in conn.execute("PRAGMA table_info(messages)").fetchall()}
        if "updated_at" not in m_cols:
            conn.execute("ALTER TABLE messages ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''")
            conn.execute(
                "UPDATE messages SET updated_at = created_at WHERE updated_at = '' OR updated_at IS NULL"
            )
        j_cols = {r[1] for r in conn.execute("PRAGMA table_info(plugin_jobs)").fetchall()}
        if "message_id" not in j_cols:
            conn.execute(
                "ALTER TABLE plugin_jobs ADD COLUMN message_id TEXT NOT NULL DEFAULT ''"
            )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_user_updated "
            "ON messages(user_id, updated_at)"
        )
        conn.execute("UPDATE meta SET value = '11' WHERE key = 'schema_version'")
        conn.commit()
        version = 11

    if version < 12:
        # 主动问候 / 事件书
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS schedules (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              persona_id TEXT NOT NULL REFERENCES personas(id) ON DELETE CASCADE,
              kind TEXT NOT NULL,
              title TEXT NOT NULL DEFAULT '',
              enabled INTEGER NOT NULL DEFAULT 1,
              paused INTEGER NOT NULL DEFAULT 0,
              hour INTEGER NOT NULL DEFAULT 0,
              minute INTEGER NOT NULL DEFAULT 0,
              jitter_minutes INTEGER NOT NULL DEFAULT 10,
              min_interval_minutes INTEGER NOT NULL DEFAULT 60,
              daily_limit INTEGER NOT NULL DEFAULT 1,
              prompt TEXT NOT NULL DEFAULT '',
              require_chatted_today INTEGER NOT NULL DEFAULT 0,
              config_json TEXT NOT NULL DEFAULT '{}',
              next_run_at TEXT,
              last_run_at TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_schedules_due
              ON schedules(enabled, paused, next_run_at);
            CREATE INDEX IF NOT EXISTS idx_schedules_user
              ON schedules(user_id, persona_id);

            CREATE TABLE IF NOT EXISTS schedule_runs (
              id TEXT PRIMARY KEY,
              schedule_id TEXT NOT NULL REFERENCES schedules(id) ON DELETE CASCADE,
              user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              status TEXT NOT NULL,
              detail TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_schedule_runs_sched
              ON schedule_runs(schedule_id, created_at DESC);
            """
        )
        up_cols = {r[1] for r in conn.execute("PRAGMA table_info(user_prefs)").fetchall()}
        for col, decl in (
            ("quiet_start", "TEXT NOT NULL DEFAULT '23:00'"),
            ("quiet_end", "TEXT NOT NULL DEFAULT '08:00'"),
        ):
            if col not in up_cols:
                conn.execute(f"ALTER TABLE user_prefs ADD COLUMN {col} {decl}")
        conn.execute("UPDATE meta SET value = '12' WHERE key = 'schema_version'")
        conn.commit()
        version = 12

    if version < 13:
        # 朋友圈（用户发 + 角色自发）
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS moments (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              author_id TEXT NOT NULL,
              author_kind TEXT NOT NULL DEFAULT 'user',
              text TEXT NOT NULL DEFAULT '',
              media_json TEXT NOT NULL DEFAULT '[]',
              created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_moments_user
              ON moments(user_id, created_at DESC);

            CREATE TABLE IF NOT EXISTS moment_likes (
              moment_id TEXT NOT NULL REFERENCES moments(id) ON DELETE CASCADE,
              user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              actor_id TEXT NOT NULL,
              created_at TEXT NOT NULL,
              PRIMARY KEY (moment_id, actor_id)
            );

            CREATE TABLE IF NOT EXISTS moment_comments (
              id TEXT PRIMARY KEY,
              moment_id TEXT NOT NULL REFERENCES moments(id) ON DELETE CASCADE,
              user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              actor_id TEXT NOT NULL,
              text TEXT NOT NULL,
              reply_to TEXT,
              created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_moment_comments
              ON moment_comments(moment_id, created_at);
            """
        )
        conn.execute("UPDATE meta SET value = '13' WHERE key = 'schema_version'")
        conn.commit()
        version = 13

    if version < 14:
        # 表情包收藏（用户 / 人设各自一份）
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS stickers (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              owner_kind TEXT NOT NULL DEFAULT 'user',
              owner_id TEXT NOT NULL,
              file_id TEXT NOT NULL,
              label TEXT NOT NULL DEFAULT '',
              emotion TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_stickers_owner
              ON stickers(user_id, owner_kind, owner_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_stickers_emotion
              ON stickers(user_id, emotion);
            """
        )
        conn.execute("UPDATE meta SET value = '14' WHERE key = 'schema_version'")
        conn.commit()
        version = 14

    if version < 15:
        # 个性签名 / 朋友圈封面 / 聊天背景
        ucols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
        for col, decl in (
            ("signature", "TEXT NOT NULL DEFAULT ''"),
            ("moments_cover_file_id", "TEXT NOT NULL DEFAULT ''"),
        ):
            if col not in ucols:
                conn.execute(f"ALTER TABLE users ADD COLUMN {col} {decl}")
        ccols = {r[1] for r in conn.execute("PRAGMA table_info(conversations)").fetchall()}
        for col, decl in (
            ("bg_name", "TEXT NOT NULL DEFAULT '默认'"),
            ("bg_file_id", "TEXT NOT NULL DEFAULT ''"),
        ):
            if col not in ccols:
                conn.execute(f"ALTER TABLE conversations ADD COLUMN {col} {decl}")
        conn.execute("UPDATE meta SET value = '15' WHERE key = 'schema_version'")
        conn.commit()
        version = 15

    if version < 16:
        # 闲聊默认分句发送（更像微信连气泡）
        up_cols = {r[1] for r in conn.execute("PRAGMA table_info(user_prefs)").fetchall()}
        if "split_send" in up_cols:
            conn.execute("UPDATE user_prefs SET split_send = 1 WHERE split_send = 0 OR split_send IS NULL")
        conn.execute("UPDATE meta SET value = '16' WHERE key = 'schema_version'")
        conn.commit()
        version = 16

    if version < 17:
        # P3：虚拟电脑 + 观看/控制租约（fencing）
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS virtual_devices (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              label TEXT NOT NULL DEFAULT '',
              kind TEXT NOT NULL DEFAULT 'desktop',
              backend TEXT NOT NULL DEFAULT 'mock',
              external_id TEXT NOT NULL DEFAULT '',
              status TEXT NOT NULL DEFAULT 'stopped',
              view_url TEXT NOT NULL DEFAULT '',
              control_url TEXT NOT NULL DEFAULT '',
              meta_json TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_virtual_devices_user
              ON virtual_devices(user_id, status, created_at DESC);

            CREATE TABLE IF NOT EXISTS device_leases (
              id TEXT PRIMARY KEY,
              device_id TEXT NOT NULL REFERENCES virtual_devices(id) ON DELETE CASCADE,
              user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              role TEXT NOT NULL,
              token TEXT NOT NULL,
              expires_at TEXT NOT NULL,
              created_at TEXT NOT NULL,
              heartbeat_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_device_leases_device
              ON device_leases(device_id, role, expires_at);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_device_leases_one_control
              ON device_leases(device_id) WHERE role = 'control';
            """
        )
        conn.execute("UPDATE meta SET value = '17' WHERE key = 'schema_version'")
        conn.commit()
        version = 17

    if version < 18:
        # M2 人物档 / 记忆运行日志；M4 listen；贴纸 hash；免打扰挡聊天
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS person_notes (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              persona_id TEXT NOT NULL,
              text TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_person_notes_persona
              ON person_notes(user_id, persona_id, updated_at DESC);

            CREATE TABLE IF NOT EXISTS memory_runs (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL,
              persona_id TEXT NOT NULL DEFAULT '',
              kind TEXT NOT NULL DEFAULT 'nightly',
              status TEXT NOT NULL DEFAULT 'ok',
              summary TEXT NOT NULL DEFAULT '',
              detail_json TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_memory_runs_time
              ON memory_runs(user_id, created_at DESC);

            CREATE TABLE IF NOT EXISTS memory_injections (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL,
              persona_id TEXT NOT NULL,
              conv_id TEXT NOT NULL DEFAULT '',
              evidence_json TEXT NOT NULL DEFAULT '[]',
              char_count INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL
            );
            """
        )
        up_cols = {r[1] for r in conn.execute("PRAGMA table_info(user_prefs)").fetchall()}
        if "quiet_blocks_chat" not in up_cols:
            conn.execute(
                "ALTER TABLE user_prefs ADD COLUMN quiet_blocks_chat INTEGER NOT NULL DEFAULT 0"
            )
        if "memory_inject" not in up_cols:
            conn.execute(
                "ALTER TABLE user_prefs ADD COLUMN memory_inject INTEGER NOT NULL DEFAULT 1"
            )
        st_cols = {r[1] for r in conn.execute("PRAGMA table_info(stickers)").fetchall()}
        if "file_hash" not in st_cols:
            conn.execute("ALTER TABLE stickers ADD COLUMN file_hash TEXT NOT NULL DEFAULT ''")
        # schedules listen window
        try:
            sc = {r[1] for r in conn.execute("PRAGMA table_info(schedules)").fetchall()}
            if "listen_minutes" not in sc:
                conn.execute(
                    "ALTER TABLE schedules ADD COLUMN listen_minutes INTEGER NOT NULL DEFAULT 5"
                )
        except Exception:
            pass
        try:
            cc = {r[1] for r in conn.execute("PRAGMA table_info(conversations)").fetchall()}
            if "listen_until" not in cc:
                conn.execute(
                    "ALTER TABLE conversations ADD COLUMN listen_until TEXT NOT NULL DEFAULT ''"
                )
        except Exception:
            pass
        conn.execute("UPDATE meta SET value = '18' WHERE key = 'schema_version'")
        conn.commit()
        version = 18

    if version < 19:
        # 人设抽卡资产：语音 / 形象候选 + 固定选用
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS persona_assets (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL,
              persona_id TEXT NOT NULL,
              kind TEXT NOT NULL,
              file_id TEXT NOT NULL DEFAULT '',
              path TEXT NOT NULL DEFAULT '',
              prompt TEXT NOT NULL DEFAULT '',
              sample_text TEXT NOT NULL DEFAULT '',
              fixed INTEGER NOT NULL DEFAULT 0,
              meta_json TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_persona_assets_owner
              ON persona_assets(user_id, persona_id, kind, created_at DESC);
            """
        )
        conn.execute("UPDATE meta SET value = '19' WHERE key = 'schema_version'")
        conn.commit()
        version = 19

    if version < 20:
        # 陪伴：原子事实 / 情绪 / 成长 / 跟进 / FTS / 抑制 / 温度
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS memory_facts (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL,
              persona_id TEXT NOT NULL,
              text TEXT NOT NULL,
              mem_type TEXT NOT NULL DEFAULT 'fact',
              status TEXT NOT NULL DEFAULT 'active',
              subject TEXT NOT NULL DEFAULT '',
              attribute TEXT NOT NULL DEFAULT '',
              source_message_id TEXT NOT NULL DEFAULT '',
              source_revision INTEGER NOT NULL DEFAULT 1,
              root_evidence_id TEXT NOT NULL DEFAULT '',
              fact_period_id TEXT NOT NULL DEFAULT '',
              storage_revision INTEGER NOT NULL DEFAULT 1,
              heat_anchor REAL NOT NULL DEFAULT 20,
              heat_at TEXT NOT NULL DEFAULT '',
              valid_from TEXT NOT NULL DEFAULT '',
              valid_to TEXT NOT NULL DEFAULT '',
              supersedes TEXT NOT NULL DEFAULT '',
              meta_json TEXT NOT NULL DEFAULT '{}',
              policy_version INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_memory_facts_owner
              ON memory_facts(user_id, persona_id, status, updated_at DESC);

            CREATE VIRTUAL TABLE IF NOT EXISTS memory_facts_fts USING fts5(
              body,
              memory_id UNINDEXED,
              user_id UNINDEXED,
              persona_id UNINDEXED,
              tokenize = 'unicode61'
            );

            CREATE TABLE IF NOT EXISTS memory_source_suppress (
              user_id TEXT NOT NULL,
              persona_id TEXT NOT NULL,
              source_id TEXT NOT NULL,
              reason TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              PRIMARY KEY (user_id, persona_id, source_id)
            );

            CREATE TABLE IF NOT EXISTS memory_heat_events (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL,
              persona_id TEXT NOT NULL,
              fact_period_id TEXT NOT NULL,
              root_fragment_id TEXT NOT NULL,
              gain REAL NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL,
              UNIQUE(user_id, persona_id, fact_period_id, root_fragment_id)
            );

            CREATE TABLE IF NOT EXISTS persona_affect (
              user_id TEXT NOT NULL,
              persona_id TEXT NOT NULL,
              joy REAL NOT NULL DEFAULT 35,
              sadness REAL NOT NULL DEFAULT 35,
              excitement REAL NOT NULL DEFAULT 35,
              worry REAL NOT NULL DEFAULT 35,
              annoyance REAL NOT NULL DEFAULT 35,
              curiosity REAL NOT NULL DEFAULT 35,
              baseline_json TEXT NOT NULL DEFAULT '{}',
              heat_at TEXT NOT NULL,
              state_version INTEGER NOT NULL DEFAULT 1,
              reason TEXT NOT NULL DEFAULT '',
              source_event_id TEXT NOT NULL DEFAULT '',
              updated_at TEXT NOT NULL,
              PRIMARY KEY (user_id, persona_id)
            );

            CREATE TABLE IF NOT EXISTS persona_affect_events (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL,
              persona_id TEXT NOT NULL,
              event_id TEXT NOT NULL,
              deltas_json TEXT NOT NULL DEFAULT '{}',
              reason TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_affect_events
              ON persona_affect_events(user_id, persona_id, created_at DESC);

            CREATE TABLE IF NOT EXISTS persona_growth (
              user_id TEXT NOT NULL,
              persona_id TEXT NOT NULL,
              mode TEXT NOT NULL DEFAULT 'adaptive',
              growth_epoch INTEGER NOT NULL DEFAULT 1,
              baseline_bio TEXT NOT NULL DEFAULT '',
              traits_json TEXT NOT NULL DEFAULT '{}',
              active_revision_id TEXT NOT NULL DEFAULT '',
              frozen_lesson_ids_json TEXT NOT NULL DEFAULT '[]',
              updated_at TEXT NOT NULL,
              PRIMARY KEY (user_id, persona_id)
            );

            CREATE TABLE IF NOT EXISTS persona_growth_revisions (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL,
              persona_id TEXT NOT NULL,
              traits_json TEXT NOT NULL DEFAULT '{}',
              lesson_ids_json TEXT NOT NULL DEFAULT '[]',
              note TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS companion_followups (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL,
              persona_id TEXT NOT NULL,
              reason TEXT NOT NULL DEFAULT '',
              source TEXT NOT NULL DEFAULT '',
              window_start TEXT NOT NULL DEFAULT '',
              window_end TEXT NOT NULL DEFAULT '',
              expire_at TEXT NOT NULL DEFAULT '',
              cancel_when TEXT NOT NULL DEFAULT '',
              state TEXT NOT NULL DEFAULT 'pending',
              idempotency_key TEXT NOT NULL UNIQUE,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_followups_owner
              ON companion_followups(user_id, persona_id, state, created_at DESC);

            CREATE TABLE IF NOT EXISTS companion_policy (
              user_id TEXT NOT NULL,
              persona_id TEXT NOT NULL,
              memory_policy_version INTEGER NOT NULL DEFAULT 1,
              pause_memory_write INTEGER NOT NULL DEFAULT 0,
              pause_memory_read INTEGER NOT NULL DEFAULT 0,
              updated_at TEXT NOT NULL,
              PRIMARY KEY (user_id, persona_id)
            );
            """
        )
        # 迁移旧 memories → memory_facts（legacy_unverified）
        try:
            olds = conn.execute("SELECT * FROM memories").fetchall()
            now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
            for r in olds:
                keys = r.keys() if hasattr(r, "keys") else range(len(r))
                # Row
                rid = r["id"] if "id" in r.keys() else r[0]
                uid = r["user_id"]
                pid = r["persona_id"]
                text = r["text"]
                updated = r["updated_at"] if "updated_at" in r.keys() else now
                conn.execute(
                    """
                    INSERT OR IGNORE INTO memory_facts(
                      id, user_id, persona_id, text, mem_type, status, subject, attribute,
                      source_message_id, source_revision, root_evidence_id, fact_period_id,
                      storage_revision, heat_anchor, heat_at, valid_from, valid_to, supersedes,
                      meta_json, policy_version, created_at, updated_at
                    ) VALUES (?,?,?,?, 'fact', 'legacy_unverified', '', '',
                      '', 1, ?, ?, 1, 20, ?, ?, '', '', '{}', 0, ?, ?)
                    """,
                    (rid, uid, pid, text, rid, rid, updated, updated, updated, updated),
                )
                conn.execute(
                    "INSERT OR IGNORE INTO memory_facts_fts(memory_id, user_id, persona_id, body) VALUES (?,?,?,?)",
                    (rid, uid, pid, text),
                )
        except Exception:
            pass
        conn.execute("UPDATE meta SET value = '20' WHERE key = 'schema_version'")
        conn.commit()
        version = 20

    if version < 21:
        # 全局暂停：主动问候/朋友圈、自动抽记忆
        up_cols = {r[1] for r in conn.execute("PRAGMA table_info(user_prefs)").fetchall()}
        if "pause_proactive" not in up_cols:
            conn.execute(
                "ALTER TABLE user_prefs ADD COLUMN pause_proactive INTEGER NOT NULL DEFAULT 0"
            )
        if "pause_memory_extract" not in up_cols:
            conn.execute(
                "ALTER TABLE user_prefs ADD COLUMN pause_memory_extract INTEGER NOT NULL DEFAULT 0"
            )
        conn.execute("UPDATE meta SET value = '21' WHERE key = 'schema_version'")
        conn.commit()
        version = 21

    if version < 22:
        # 语音回复偏好 + 曲库/歌单
        pcols = {r[1] for r in conn.execute("PRAGMA table_info(personas)").fetchall()}
        if "voice_reply_pref" not in pcols:
            conn.execute(
                "ALTER TABLE personas ADD COLUMN voice_reply_pref TEXT NOT NULL DEFAULT 'sometimes'"
            )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS music_tracks (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL,
              source TEXT NOT NULL DEFAULT 'cache',
              title TEXT NOT NULL DEFAULT '',
              artist TEXT NOT NULL DEFAULT '',
              path TEXT NOT NULL DEFAULT '',
              rel_path TEXT NOT NULL DEFAULT '',
              duration_sec REAL NOT NULL DEFAULT 0,
              root_label TEXT NOT NULL DEFAULT '',
              meta_json TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_music_tracks_user_title ON music_tracks(user_id, title)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS music_playlists (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL,
              name TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS music_playlist_items (
              id TEXT PRIMARY KEY,
              playlist_id TEXT NOT NULL,
              track_id TEXT NOT NULL,
              position INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_music_pl_items ON music_playlist_items(playlist_id, position)"
        )
        conn.execute("UPDATE meta SET value = '22' WHERE key = 'schema_version'")
        conn.commit()
        version = 22

    if version < 23:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS listen_sessions (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL,
              conv_id TEXT NOT NULL,
              persona_id TEXT NOT NULL DEFAULT '',
              playlist_id TEXT NOT NULL DEFAULT '',
              status TEXT NOT NULL DEFAULT 'invited',
              mood TEXT NOT NULL DEFAULT '',
              reason TEXT NOT NULL DEFAULT '',
              track_index INTEGER NOT NULL DEFAULT 0,
              position_ms INTEGER NOT NULL DEFAULT 0,
              invited_by TEXT NOT NULL DEFAULT 'ai',
              meta_json TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_listen_user_status ON listen_sessions(user_id, status, updated_at DESC)"
        )
        conn.execute("UPDATE meta SET value = '23' WHERE key = 'schema_version'")
        conn.commit()
        version = 23

    if version < 24:
        # 曲库：账号级全局池（非人设）；歌单区分 library / session / network
        pl_cols = {r[1] for r in conn.execute("PRAGMA table_info(music_playlists)").fetchall()}
        if "kind" not in pl_cols:
            conn.execute(
                "ALTER TABLE music_playlists ADD COLUMN kind TEXT NOT NULL DEFAULT 'library'"
            )
        if "source_url" not in pl_cols:
            conn.execute(
                "ALTER TABLE music_playlists ADD COLUMN source_url TEXT NOT NULL DEFAULT ''"
            )
        if "meta_json" not in pl_cols:
            conn.execute(
                "ALTER TABLE music_playlists ADD COLUMN meta_json TEXT NOT NULL DEFAULT '{}'"
            )
        # 已有「一起听」临时歌单：名字常见「一起听」或由 curate 写入；统一标 session 留给后续清理
        # 不强制改名；仅对新写入打 kind。库内存量默认为 library 即可。
        conn.execute("UPDATE meta SET value = '24' WHERE key = 'schema_version'")
        conn.commit()
        version = 24

    if version < 25:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS call_sessions (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL,
              conv_id TEXT NOT NULL,
              persona_id TEXT NOT NULL DEFAULT '',
              media TEXT NOT NULL DEFAULT 'audio',
              status TEXT NOT NULL DEFAULT 'ringing',
              meta_json TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_calls_user ON call_sessions(user_id, created_at DESC);
            """
        )
        tcols = {r[1] for r in conn.execute("PRAGMA table_info(music_tracks)").fetchall()}
        if "lyrics" not in tcols:
            conn.execute("ALTER TABLE music_tracks ADD COLUMN lyrics TEXT NOT NULL DEFAULT ''")
        if "file_hash" not in tcols:
            conn.execute(
                "ALTER TABLE music_tracks ADD COLUMN file_hash TEXT NOT NULL DEFAULT ''"
            )
        conn.execute("UPDATE meta SET value = '25' WHERE key = 'schema_version'")
        conn.commit()
        version = 25

    if version < 26:
        # 会话隐藏/删除（微信：删聊天不删人设；列表不再出现）
        ccols = {r[1] for r in conn.execute("PRAGMA table_info(conversations)").fetchall()}
        if "hidden" not in ccols:
            conn.execute(
                "ALTER TABLE conversations ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0"
            )
        if "deleted_at" not in ccols:
            conn.execute(
                "ALTER TABLE conversations ADD COLUMN deleted_at TEXT NOT NULL DEFAULT ''"
            )
        conn.execute("UPDATE meta SET value = '26' WHERE key = 'schema_version'")
        conn.commit()
        version = 26

    if version < 27:
        # 设备多租户：人设/会话绑定（虚拟机为主；USB 仅为扩展位）
        vcols = {r[1] for r in conn.execute("PRAGMA table_info(virtual_devices)").fetchall()}
        if "owner_persona_id" not in vcols:
            conn.execute(
                "ALTER TABLE virtual_devices ADD COLUMN owner_persona_id TEXT NOT NULL DEFAULT ''"
            )
        if "bind_conv_id" not in vcols:
            conn.execute(
                "ALTER TABLE virtual_devices ADD COLUMN bind_conv_id TEXT NOT NULL DEFAULT ''"
            )
        if "source" not in vcols:
            conn.execute(
                "ALTER TABLE virtual_devices ADD COLUMN source TEXT NOT NULL DEFAULT ''"
            )
        lcols = {r[1] for r in conn.execute("PRAGMA table_info(device_leases)").fetchall()}
        if "persona_id" not in lcols:
            conn.execute(
                "ALTER TABLE device_leases ADD COLUMN persona_id TEXT NOT NULL DEFAULT ''"
            )
        if "conv_id" not in lcols:
            conn.execute(
                "ALTER TABLE device_leases ADD COLUMN conv_id TEXT NOT NULL DEFAULT ''"
            )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_virtual_devices_user_kind "
            "ON virtual_devices(user_id, kind, status)"
        )
        conn.execute("UPDATE meta SET value = '27' WHERE key = 'schema_version'")
        conn.commit()
        version = 27

    if version < 28:
        # 每轮对话上下文 / 缓存命中记录
        ucols = {r[1] for r in conn.execute("PRAGMA table_info(usage_logs)").fetchall()}
        for col, decl in (
            ("input_tokens", "INTEGER NOT NULL DEFAULT 0"),
            ("cache_read_tokens", "INTEGER NOT NULL DEFAULT 0"),
            ("cache_write_tokens", "INTEGER NOT NULL DEFAULT 0"),
            ("context_tokens", "INTEGER NOT NULL DEFAULT 0"),
            ("cache_hit_rate", "REAL NOT NULL DEFAULT 0"),
            ("prompt_chars", "INTEGER NOT NULL DEFAULT 0"),
            ("history_messages", "INTEGER NOT NULL DEFAULT 0"),
            ("model_calls", "INTEGER NOT NULL DEFAULT 0"),
            ("dsh_session_id", "TEXT NOT NULL DEFAULT ''"),
            ("metrics_json", "TEXT NOT NULL DEFAULT '{}'"),
        ):
            if col not in ucols:
                conn.execute(f"ALTER TABLE usage_logs ADD COLUMN {col} {decl}")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_usage_conv_time ON usage_logs(conv_id, created_at DESC)"
        )
        conn.execute("UPDATE meta SET value = '28' WHERE key = 'schema_version'")
        conn.commit()
        version = 28

    if version < 29:
        # 子用户产品能力开关（以系统已有能力为准；无行=默认启用）
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_capability_grants (
              user_id TEXT NOT NULL,
              capability TEXT NOT NULL,
              enabled INTEGER NOT NULL DEFAULT 1,
              updated_at TEXT NOT NULL,
              PRIMARY KEY (user_id, capability)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_user_cap_grants_user "
            "ON user_capability_grants(user_id)"
        )
        conn.execute("UPDATE meta SET value = '29' WHERE key = 'schema_version'")
        conn.commit()
        version = 29

    if version < 30:
        # 表情包分组：global=AI 收录全站可见；account=用户自建
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sticker_packs (
              id TEXT PRIMARY KEY,
              scope TEXT NOT NULL,
              owner_user_id TEXT NOT NULL DEFAULT '',
              title TEXT NOT NULL DEFAULT '',
              tags TEXT NOT NULL DEFAULT '',
              source TEXT NOT NULL DEFAULT 'user',
              created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sticker_packs_scope "
            "ON sticker_packs(scope, created_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sticker_packs_owner "
            "ON sticker_packs(owner_user_id, created_at DESC)"
        )
        st_cols = {r[1] for r in conn.execute("PRAGMA table_info(stickers)").fetchall()}
        if "pack_id" not in st_cols:
            conn.execute(
                "ALTER TABLE stickers ADD COLUMN pack_id TEXT NOT NULL DEFAULT ''"
            )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_stickers_pack "
            "ON stickers(pack_id, created_at DESC)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_pack_subscriptions (
              user_id TEXT NOT NULL,
              pack_id TEXT NOT NULL,
              pinned INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL,
              PRIMARY KEY (user_id, pack_id)
            )
            """
        )
        conn.execute("UPDATE meta SET value = '30' WHERE key = 'schema_version'")
        conn.commit()
        version = 30

    if version < 31:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_media_user_sha "
            "ON media_files(user_id, sha256, size)"
        )
        conn.execute("UPDATE meta SET value = '31' WHERE key = 'schema_version'")
        conn.commit()
        version = 31

    if version < 32:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS device_create_requests (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL,
              persona_id TEXT NOT NULL DEFAULT '',
              conv_id TEXT NOT NULL DEFAULT '',
              kind TEXT NOT NULL DEFAULT 'desktop',
              label TEXT NOT NULL DEFAULT '',
              status TEXT NOT NULL DEFAULT 'pending',
              message_id TEXT NOT NULL DEFAULT '',
              device_id TEXT NOT NULL DEFAULT '',
              reason TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_device_req_user
              ON device_create_requests(user_id, status, created_at DESC);
            """
        )
        conn.execute("UPDATE meta SET value = '32' WHERE key = 'schema_version'")
        conn.commit()


def reset_connection() -> None:
    """测试用：关闭并丢弃全局连接。"""
    global _conn
    with _lock:
        if _conn is not None:
            try:
                _conn.close()
            except Exception:
                pass
            _conn = None


@contextmanager
def cursor() -> Iterator[sqlite3.Cursor]:
    with _lock:
        conn = connect()
        cur = conn.cursor()
        try:
            yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()


def fetchone(sql: str, params: Union[Tuple, List] = ()) -> Optional[sqlite3.Row]:
    with _lock:
        conn = connect()
        return conn.execute(sql, params).fetchone()


def fetchall(sql: str, params: Union[Tuple, List] = ()) -> List[sqlite3.Row]:
    with _lock:
        conn = connect()
        return list(conn.execute(sql, params).fetchall())


def execute(sql: str, params: Union[Tuple, List] = ()) -> None:
    with cursor() as cur:
        cur.execute(sql, params)


def executemany(sql: str, seq: List) -> None:
    with cursor() as cur:
        cur.executemany(sql, seq)
