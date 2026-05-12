"""
实体索引模块

在记忆创建时提取实体（人物、地点、偏好等），建立轻量级实体索引。
搜索时通过实体匹配提升相关性排序。
"""

import re
import logging
from typing import List, Optional
from app.database import SessionLocal

logger = logging.getLogger(__name__)

# 中文实体提取模式
_CN_ENTITY_PATTERNS = [
    (r'([^\s，。、；：]{1,10})的([^\s，。、；：]{1,10})', 1),     # "用户的职业"
    (r'用户([^\s，。、；：]{1,15})', 0),                            # "用户喜欢编程"
    (r'用户是([^\s，。、；：]{2,20})', 0),                          # "用户是一名老师"
]


def extract_entities_from_facts(facts: list) -> list:
    """
    从 AI 提取的事实列表中提取实体。

    长文本模式: facts 包含 content/subject/type/confidence 字段
    短文本模式: facts 是字符串列表

    Returns:
        list[dict]: [{"entity_text": str, "entity_type": str}, ...]
    """
    entities = []
    seen_texts = set()

    for fact in facts:
        if isinstance(fact, dict):
            # 长文本模式: 有 subject 字段
            subject = fact.get('subject', '').strip()
            if subject and len(subject) >= 2 and subject not in seen_texts:
                seen_texts.add(subject)
                entities.append({
                    'entity_text': subject,
                    'entity_type': fact.get('type', 'general')
                })

            # 从 content 中提取额外实体
            content = fact.get('content', '')
            extracted = _extract_from_text(content, seen_texts)
            entities.extend(extracted)

        elif isinstance(fact, str) and len(fact) >= 4:
            extracted = _extract_from_text(fact, seen_texts)
            entities.extend(extracted)

    return entities


def extract_entities_from_content(content: str) -> list:
    """
    从记忆内容文本中提取实体（非 AI 模式时使用）。

    Returns:
        list[dict]: [{"entity_text": str, "entity_type": str}, ...]
    """
    seen_texts = set()
    return _extract_from_text(content, seen_texts)


def _extract_from_text(text: str, seen: set) -> list:
    """从文本中提取实体"""
    entities = []
    for pattern, group_idx in _CN_ENTITY_PATTERNS:
        for match in re.finditer(pattern, text):
            candidate = match.group(group_idx).strip()
            if candidate and len(candidate) >= 2 and candidate not in seen:
                # 过滤掉明显不是实体的词
                if _is_valid_entity(candidate):
                    seen.add(candidate)
                    entities.append({
                        'entity_text': candidate,
                        'entity_type': 'general'
                    })
    return entities


def _is_valid_entity(text: str) -> bool:
    """判断文本是否可能是有效实体"""
    # 过滤纯数字
    if text.isdigit():
        return False
    # 过滤过长的文本
    if len(text) > 30:
        return False
    # 过滤标点符号
    if re.match(r'^[^\w]+$', text):
        return False
    return True


def save_entities_for_memory(memory_id: str, entities: list):
    """
    保存实体的索引到数据库。

    Args:
        memory_id: 记忆 ID
        entities: [{"entity_text": str, "entity_type": str}, ...]
    """
    if not entities:
        return

    from app.models import MemoryEntity

    db = SessionLocal()
    try:
        # 删除该记忆的旧实体
        db.query(MemoryEntity).filter(MemoryEntity.memory_id == memory_id).delete()

        # 插入新实体
        for ent in entities:
            entity = MemoryEntity(
                memory_id=memory_id,
                entity_text=ent['entity_text'],
                entity_type=ent.get('entity_type', 'general'),
            )
            db.add(entity)

        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning("save_entities_for_memory error: %s", e)
    finally:
        db.close()


def search_by_entity(query: str, user_id: str, limit: int = 20) -> list:
    """
    基于实体匹配的搜索。

    将查询分词后匹配 MemoryEntity 表，返回匹配的记忆。

    Returns:
        list[dict]: [{"memory_id": str, "entity_score": int, "matched_entities": list}, ...]
    """
    from app.models import MemoryEntity, Memory, MemoryState

    # 将查询拆分为候选实体
    candidates = _tokenize_for_entity_match(query)
    if not candidates:
        return []

    db = SessionLocal()
    try:
        # 获取用户可访问的记忆 ID
        accessible_ids = set(
            row[0] for row in db.query(Memory.id).filter(
                Memory.user_id == user_id,
                Memory.state != MemoryState.deleted,
            ).all()
        )
        if not accessible_ids:
            return []

        # 查询匹配的实体
        matched = db.query(
            MemoryEntity.memory_id,
            MemoryEntity.entity_text,
        ).filter(
            MemoryEntity.entity_text.in_(candidates),
            MemoryEntity.memory_id.in_(accessible_ids),
        ).all()

        # 按 memory_id 聚合，统计匹配实体数
        entity_matches = {}
        for memory_id, entity_text in matched:
            if memory_id not in entity_matches:
                entity_matches[memory_id] = {
                    "memory_id": memory_id,
                    "entity_score": 0,
                    "matched_entities": [],
                }
            entity_matches[memory_id]["entity_score"] += 1
            entity_matches[memory_id]["matched_entities"].append(entity_text)

        results = list(entity_matches.values())
        results.sort(key=lambda x: x["entity_score"], reverse=True)
        return results[:limit]

    except Exception as e:
        logger.warning("search_by_entity error: %s", e)
        return []
    finally:
        db.close()


def _tokenize_for_entity_match(query: str) -> list:
    """将查询文本拆分为候选实体列表"""
    cleaned = re.sub(r'[^\w一-鿿]', ' ', query).strip()
    if not cleaned:
        return []

    candidates = set()

    # 添加完整查询作为候选
    if len(cleaned) >= 2:
        candidates.add(cleaned.lower())

    # 按空格分词（英文场景）
    for token in cleaned.split():
        token = token.strip().lower()
        if len(token) >= 2:
            candidates.add(token)

    # 中文场景：2-4 字滑动窗口
    cjk_chars = re.findall(r'[一-鿿]', cleaned)
    if len(cjk_chars) >= 2:
        text = ''.join(cjk_chars)
        for win_size in [2, 3, 4]:
            for i in range(len(text) - win_size + 1):
                candidates.add(text[i:i + win_size])

    return list(candidates)
