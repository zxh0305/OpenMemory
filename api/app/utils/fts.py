"""
FTS5 全文搜索模块

使用 SQLite FTS5 实现 BM25 关键词检索。
在 SQLite 上创建虚拟表，支持中文和英文的全文搜索。
"""

import re
import logging
from sqlalchemy import text
from app.database import engine, SessionLocal

logger = logging.getLogger(__name__)

# FTS5 特殊字符正则
_FTS_SPECIAL_CHARS = re.compile(r'[+\\\-*()~:^"\'`]')

# CJK 字符检测
_CJK_RE = re.compile(r'[一-鿿㐀-䶿豈-﫿]')

# CJK 字符前后插入空格，使 FTS5 unicode61 能正确分词
_CJK_BOUNDARY_RE = re.compile(
    r'(?<=[一-鿿㐀-䶿豈-﫿])(?=[一-鿿㐀-䶿豈-﫿])'
    r'|'
    r'(?<=[一-鿿㐀-䶿豈-﫿])(?=[a-zA-Z0-9])'
    r'|'
    r'(?<=[a-zA-Z0-9])(?=[一-鿿㐀-䶿豈-﫿])'
)


def _space_cjk_for_fts(text: str) -> str:
    """在 CJK 字符和 ASCII 之间插入空格，确保 FTS5 能正确分词"""
    if not text:
        return text
    result = _CJK_BOUNDARY_RE.sub(' ', text)
    result = re.sub(r' {2,}', ' ', result)
    return result.strip()


def _is_sqlite() -> bool:
    """检测是否使用 SQLite 数据库"""
    return engine.url.drivername == "sqlite" or "sqlite" in str(engine.url)


def init_fts_table():
    """初始化 FTS5 虚拟表（仅在 SQLite 下生效）"""
    if not _is_sqlite():
        logger.info("Non-SQLite database detected, FTS5 is not available")
        return False

    with engine.connect() as conn:
        conn.execute(text("""
            CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                memory_id UNINDEXED,
                content,
                tokenize='unicode61'
            )
        """))
        conn.commit()
    logger.info("FTS5 table initialized")
    return True


def rebuild_fts_index():
    """从 memories 表重建 FTS 全文索引"""
    if not _is_sqlite():
        return

    from app.models import Memory, MemoryState

    db = SessionLocal()
    try:
        with engine.connect() as conn:
            conn.execute(text("DELETE FROM memories_fts"))
            conn.commit()

        memories = db.query(Memory).filter(
            Memory.state != MemoryState.deleted
        ).all()

        with engine.connect() as conn:
            for memory in memories:
                conn.execute(
                    text("INSERT INTO memories_fts(memory_id, content) VALUES (:mid, :content)"),
                    {"mid": memory.id, "content": _space_cjk_for_fts(memory.content)}
                )
            conn.commit()

        logger.info("FTS index rebuilt with %d memories", len(memories))
    finally:
        db.close()


def sync_memory_to_fts(memory_id: str, content: str):
    """同步单条记忆到 FTS 索引（创建或更新）"""
    if not _is_sqlite():
        return

    with engine.connect() as conn:
        conn.execute(
            text("DELETE FROM memories_fts WHERE memory_id = :mid"),
            {"mid": memory_id}
        )
        conn.execute(
            text("INSERT INTO memories_fts(memory_id, content) VALUES (:mid, :content)"),
            {"mid": memory_id, "content": _space_cjk_for_fts(content)}
        )
        conn.commit()


def remove_memory_from_fts(memory_id: str):
    """从 FTS 索引中删除记忆"""
    if not _is_sqlite():
        return

    with engine.connect() as conn:
        conn.execute(
            text("DELETE FROM memories_fts WHERE memory_id = :mid"),
            {"mid": memory_id}
        )
        conn.commit()


def _build_fts_query(query: str) -> str:
    """将用户查询转换为 FTS5 MATCH 支持的查询字符串"""
    cleaned = _FTS_SPECIAL_CHARS.sub(' ', query).strip()
    if not cleaned:
        return ""

    # 对查询也做 CJK 空格分隔，与存储时保持一致
    spaced = _space_cjk_for_fts(cleaned)
    tokens = []
    for t in spaced.split():
        if not t:
            continue
        # 保留所有非空 token（包括单字符 CJK）
        if len(t) >= 1 and (len(t) > 1 or _CJK_RE.search(t)):
            tokens.append(t)
    if not tokens:
        return ""
    return ' OR '.join(f'"{t}"' for t in tokens)


def bm25_search(query: str, user_id: str, limit: int = 20) -> list:
    """
    BM25 全文搜索

    Args:
        query: 搜索关键词
        user_id: 用户 ID（用于权限过滤）
        limit: 返回结果数上限

    Returns:
        list[dict]: [{"memory_id": str, "bm25_score": float, "content": str}, ...]
    """
    if not _is_sqlite():
        return []

    from app.models import Memory, MemoryState

    fts_query = _build_fts_query(query)
    if not fts_query:
        return []

    sql = text("""
        SELECT f.memory_id, f.content, rank.bm25_score
        FROM memories_fts f
        JOIN (
            SELECT rowid, bm25(memories_fts, 0, 0.0, 1.0) AS bm25_score
            FROM memories_fts
            WHERE memories_fts MATCH :query
        ) rank ON f.rowid = rank.rowid
        ORDER BY rank.bm25_score ASC
        LIMIT :lim
    """)

    db = SessionLocal()
    try:
        # 获取该用户可访问的记忆 ID 集合
        user_memory_ids = set(
            row[0] for row in db.query(Memory.id).filter(
                Memory.user_id == user_id,
                Memory.state != MemoryState.deleted,
            ).all()
        )

        with engine.connect() as conn:
            rows = conn.execute(sql, {"query": fts_query, "lim": limit}).fetchall()

        return [
            {
                "memory_id": row[0],
                "bm25_score": float(row[2]),
                "content": row[1],
            }
            for row in rows
            if row[0] in user_memory_ids
        ]
    except Exception as e:
        logger.warning("FTS5 search error: %s", e)
        return []
    finally:
        db.close()
