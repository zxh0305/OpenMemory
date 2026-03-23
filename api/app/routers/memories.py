from datetime import datetime, UTC
from typing import List, Optional, Set
from uuid import UUID, uuid4
import json
import logging
import os
import re
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from fastapi_pagination import Page, Params
from fastapi_pagination.ext.sqlalchemy import paginate as sqlalchemy_paginate
from pydantic import BaseModel
from sqlalchemy import or_, func
from app.utils.memory import get_memory_client

from app.database import get_db
from app.models import (
    Memory, MemoryState, MemoryAccessLog, App,
    RawMemoryInput,
    MemoryStatusHistory, User, Category, AccessControl, Config as ConfigModel
)
from app.schemas import MemoryResponse, PaginatedMemoryResponse
from app.utils.permissions import check_memory_access_permissions
from app.utils.db import get_or_create_user
from custom_memory_prompt import (
    STRUCTURED_MEMORY_EXTRACTION_PROMPT,
    LONG_TEXT_STRUCTURED_MEMORY_EXTRACTION_PROMPT,
)

"""
记忆管理API路由

提供记忆的创建、查询、更新、删除等功能：
- 创建新记忆
- 查询记忆列表（支持过滤、搜索、分页）
- 获取单个记忆详情
- 更新记忆内容
- 删除记忆
- 更新记忆状态（暂停/归档）
- 获取记忆访问日志
- 获取相关记忆
"""
router = APIRouter(prefix="/api/v1/memories", tags=["memories"])


def get_memory_or_404(db: Session, memory_id: UUID) -> Memory:
    memory = db.query(Memory).filter(Memory.id == memory_id).first()
    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")
    return memory


def update_memory_state(db: Session, memory_id: UUID, new_state: MemoryState, user_id: UUID):
    memory = get_memory_or_404(db, memory_id)
    old_state = memory.state

    # Update memory state
    memory.state = new_state
    if new_state == MemoryState.archived:
        memory.archived_at = datetime.now(UTC)
    elif new_state == MemoryState.deleted:
        memory.deleted_at = datetime.now(UTC)

    # Record state change
    history = MemoryStatusHistory(
        memory_id=memory_id,
        changed_by=user_id,
        old_state=old_state,
        new_state=new_state
    )
    db.add(history)
    db.commit()
    return memory


def get_accessible_memory_ids(db: Session, app_id: UUID) -> Set[UUID]:
    """
    Get the set of memory IDs that the app has access to based on app-level ACL rules.
    Returns all memory IDs if no specific restrictions are found.
    """
    # Get app-level access controls
    app_access = db.query(AccessControl).filter(
        AccessControl.subject_type == "app",
        AccessControl.subject_id == app_id,
        AccessControl.object_type == "memory"
    ).all()

    # If no app-level rules exist, return None to indicate all memories are accessible
    if not app_access:
        return None

    # Initialize sets for allowed and denied memory IDs
    allowed_memory_ids = set()
    denied_memory_ids = set()

    # Process app-level rules
    for rule in app_access:
        if rule.effect == "allow":
            if rule.object_id:  # Specific memory access
                allowed_memory_ids.add(rule.object_id)
            else:  # All memories access
                return None  # All memories allowed
        elif rule.effect == "deny":
            if rule.object_id:  # Specific memory denied
                denied_memory_ids.add(rule.object_id)
            else:  # All memories denied
                return set()  # No memories accessible

    # Remove denied memories from allowed set
    if allowed_memory_ids:
        allowed_memory_ids -= denied_memory_ids

    return allowed_memory_ids


# List all memories with filtering
@router.get("/", response_model=Page[MemoryResponse])
async def list_memories(
    user_id: str,
    app_id: Optional[UUID] = None,
    from_date: Optional[int] = Query(
        None,
        description="Filter memories created after this date (timestamp)",
        examples=[1718505600]
    ),
    to_date: Optional[int] = Query(
        None,
        description="Filter memories created before this date (timestamp)",
        examples=[1718505600]
    ),
    categories: Optional[str] = None,
    params: Params = Depends(),
    search_query: Optional[str] = None,
    sort_column: Optional[str] = Query(None, description="Column to sort by (memory, categories, app_name, created_at)"),
    sort_direction: Optional[str] = Query(None, description="Sort direction (asc or desc)"),
    db: Session = Depends(get_db)
):
    """
    获取用户的记忆列表，支持多种过滤和排序选项。
    
    功能特性：
    - 支持按应用、分类、时间范围过滤
    - 支持关键词搜索
    - 支持多种排序方式
    - 支持分页查询
    - 自动排除已删除和已归档的记忆
    
    参数:
    - user_id: 用户ID（必填）
    - app_id: 应用ID（可选，过滤特定应用的记忆）
    - from_date: 起始时间戳（可选）
    - to_date: 结束时间戳（可选）
    - categories: 分类名称，逗号分隔（可选）
    - search_query: 搜索关键词（可选）
    - sort_column: 排序字段（可选：memory, app_name, created_at）
    - sort_direction: 排序方向（可选：asc, desc）
    - page: 页码（默认1）
    - size: 每页数量（默认10）
    """
    # 使用 get_or_create_user 自动创建用户（如果不存在）
    user = get_or_create_user(db, user_id)

    # Build base query
    query = db.query(Memory).filter(
        Memory.user_id == user.id,
        Memory.state != MemoryState.deleted,
        Memory.state != MemoryState.archived,
        Memory.content.ilike(f"%{search_query}%") if search_query else True
    )

    # Apply filters
    if app_id:
        query = query.filter(Memory.app_id == app_id)

    if from_date:
        from_datetime = datetime.fromtimestamp(from_date, tz=UTC)
        query = query.filter(Memory.created_at >= from_datetime)

    if to_date:
        to_datetime = datetime.fromtimestamp(to_date, tz=UTC)
        query = query.filter(Memory.created_at <= to_datetime)

    # Add joins for app and categories after filtering
    query = query.outerjoin(App, Memory.app_id == App.id)

    # Apply category filter if provided
    if categories:
        category_list = [c.strip() for c in categories.split(",")]
        query = query.join(Memory.categories).filter(Category.name.in_(category_list))
    else:
        query = query.outerjoin(Memory.categories)

    # Apply sorting if specified
    if sort_column:
        sort_direction_lower = sort_direction.lower() if sort_direction else "asc"
        sort_mapping = {
            'memory': Memory.content,
            'app_name': App.name,
            'created_at': Memory.created_at
        }
        if sort_column in sort_mapping:
            sort_field = sort_mapping[sort_column]
            if sort_direction_lower == "desc":
                query = query.order_by(sort_field.desc())
            else:
                query = query.order_by(sort_field.asc())
    else:
        # Default sorting
        query = query.order_by(Memory.created_at.desc())

    # Add eager loading for app and categories, and make the query distinct
    query = query.options(
        joinedload(Memory.app),
        joinedload(Memory.categories)
    ).distinct(Memory.id)

    # Use fastapi-pagination's paginate function with transformer
    # Only apply permission filtering if app_id is provided
    if app_id:
        # With app_id, filter by permissions
        return sqlalchemy_paginate(
            query,
            params,
            transformer=lambda items: [
                MemoryResponse(
                    id=memory.id,
                    content=memory.content,
                    created_at=memory.created_at,
                    state=memory.state.value,
                    app_id=memory.app_id,
                    app_name=memory.app.name if memory.app else "Unknown",
                    categories=[category.name for category in memory.categories],
                    metadata_=memory.metadata_,
                    # 衰退相关字段
                    decay_score=getattr(memory, 'decay_score', 1.0),
                    importance_score=getattr(memory, 'importance_score', 0.5),
                    access_count=getattr(memory, 'access_count', 0),
                    last_accessed_at=getattr(memory, 'last_accessed_at', None)
                )
                for memory in items
                if check_memory_access_permissions(db, memory, app_id)
            ]
        )
    else:
        # Without app_id, return all memories (no permission filtering)
        return sqlalchemy_paginate(
            query,
            params,
            transformer=lambda items: [
                MemoryResponse(
                    id=memory.id,
                    content=memory.content,
                    created_at=memory.created_at,
                    state=memory.state.value,
                    app_id=memory.app_id,
                    app_name=memory.app.name if memory.app else "Unknown",
                    categories=[category.name for category in memory.categories],
                    metadata_=memory.metadata_,
                    # 衰退相关字段
                    decay_score=getattr(memory, 'decay_score', 1.0),
                    importance_score=getattr(memory, 'importance_score', 0.5),
                    access_count=getattr(memory, 'access_count', 0),
                    last_accessed_at=getattr(memory, 'last_accessed_at', None)
                )
                for memory in items
            ]
        )


# Get all categories
@router.get("/categories")
async def get_categories(
    user_id: str,
    db: Session = Depends(get_db)
):
    """
    获取用户所有记忆的分类列表。
    
    返回该用户所有记忆的唯一分类列表，自动排除已删除和已归档的记忆。
    """
    user = db.query(User).filter(User.user_id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Get unique categories associated with the user's memories
    # Get all memories
    memories = db.query(Memory).filter(Memory.user_id == user.id, Memory.state != MemoryState.deleted, Memory.state != MemoryState.archived).all()
    # Get all categories from memories
    categories = [category for memory in memories for category in memory.categories]
    # Get unique categories
    unique_categories = list(set(categories))

    return {
        "categories": unique_categories,
        "total": len(unique_categories)
    }


class CreateMemoryRequest(BaseModel):
    user_id: str
    text: str
    metadata: dict = {}
    infer: bool = False
    app: str = "openmemory"


@router.get("/raw-inputs")
async def list_raw_memory_inputs(
    user_id: str,
    app: Optional[str] = None,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db)
):
    user = get_or_create_user(db, user_id)

    query = db.query(RawMemoryInput).filter(RawMemoryInput.user_id == user.id)

    if app:
        app_obj = db.query(App).filter(
            App.name == app,
            App.owner_id == user.id
        ).first()
        if not app_obj:
            return {
                "items": [],
                "total": 0,
                "page": page,
                "size": size,
            }
        query = query.filter(RawMemoryInput.app_id == app_obj.id)

    total = query.count()
    items = query.order_by(RawMemoryInput.created_at.desc()).offset((page - 1) * size).limit(size).all()

    return {
        "items": [
            {
                "id": str(item.id),
                "user_id": user_id,
                "user_uuid": str(item.user_id),
                "app_id": str(item.app_id),
                "original_text": item.original_text,
                "summary": item.summary,
                "extracted_facts": item.extracted_facts or [],
                "infer": item.infer,
                "processing_status": item.processing_status,
                "error_reason": item.error_reason,
                "metadata_": item.metadata_,
                "processed_at": int(item.processed_at.timestamp()) if item.processed_at else None,
                "created_at": int(item.created_at.timestamp()) if item.created_at else None,
            }
            for item in items
        ],
        "total": total,
        "page": page,
        "size": size,
    }


@router.get("/raw-inputs/{raw_input_id}")
async def get_raw_memory_input(
    raw_input_id: UUID,
    db: Session = Depends(get_db)
):
    raw_input = db.query(RawMemoryInput).filter(RawMemoryInput.id == raw_input_id).first()
    if not raw_input:
        raise HTTPException(status_code=404, detail="Raw memory input not found")

    linked_memories = []
    candidate_memories = db.query(Memory).filter(
        Memory.user_id == raw_input.user_id,
        Memory.app_id == raw_input.app_id,
        Memory.state != MemoryState.deleted,
    ).order_by(Memory.created_at.asc()).all()

    for memory in candidate_memories:
        metadata = memory.metadata_ or {}
        if metadata.get("raw_record_id") == str(raw_input.id):
            linked_memories.append(
                {
                    "id": str(memory.id),
                    "content": memory.content,
                    "fact_index": metadata.get("fact_index"),
                    "metadata_": metadata,
                    "state": memory.state.value if hasattr(memory.state, "value") else str(memory.state),
                    "created_at": int(memory.created_at.timestamp()) if memory.created_at else None,
                }
            )

    return {
        "id": str(raw_input.id),
        "user_uuid": str(raw_input.user_id),
        "app_id": str(raw_input.app_id),
        "original_text": raw_input.original_text,
        "summary": raw_input.summary,
        "extracted_facts": raw_input.extracted_facts or [],
        "infer": raw_input.infer,
        "processing_status": raw_input.processing_status,
        "error_reason": raw_input.error_reason,
        "metadata_": raw_input.metadata_,
        "processed_at": int(raw_input.processed_at.timestamp()) if raw_input.processed_at else None,
        "created_at": int(raw_input.created_at.timestamp()) if raw_input.created_at else None,
        "linked_memories": linked_memories,
    }


# Create new memory
@router.post("/")
async def create_memory(
    request: CreateMemoryRequest,
    db: Session = Depends(get_db)
):
    """
    创建一条新的记忆。
    
    功能说明：
    - 自动创建用户和应用（如果不存在）
    - 使用大模型提取事实信息
    - 存储到向量数据库（Qdrant）和关系数据库
    - 自动分类记忆
    
    参数:
    - user_id: 用户ID（必填）
    - text: 记忆内容（必填）
    - metadata: 元数据（可选，字典格式）
    - infer: 是否使用大模型推理（默认false）
    - app: 应用名称（默认"openmemory"）
    
    注意事项：
    - 如果应用处于暂停状态，将无法创建记忆
    - 系统会自动提取事实信息并分类
    """
    user = get_or_create_user(db, request.user_id)
    app_obj = db.query(App).filter(
        App.name == request.app,
        App.owner_id == user.id
    ).first()
    if not app_obj:
        app_obj = App(name=request.app, owner_id=user.id)
        db.add(app_obj)
        db.commit()
        db.refresh(app_obj)

    if not app_obj.is_active:
        raise HTTPException(
            status_code=403,
            detail=f"App {request.app} is currently paused on OpenMemory. Cannot create new memories."
        )

    logging.info(f"Creating memory for user_id: {request.user_id} with app: {request.app}")

    raw_input = RawMemoryInput(
        id=uuid4(),
        user_id=user.id,
        app_id=app_obj.id,
        original_text=request.text,
        metadata_=request.metadata,
        infer=request.infer,
        processing_status="pending",
    )
    db.add(raw_input)
    db.commit()
    db.refresh(raw_input)

    def memory_to_dict(memory: Memory) -> dict:
        return {
            "id": str(memory.id),
            "content": memory.content,
            "metadata_": memory.metadata_,
            "state": memory.state.value if hasattr(memory.state, "value") else str(memory.state),
            "created_at": int(memory.created_at.timestamp()) if memory.created_at else None,
        }

    def build_create_response(
        memories: List[Memory],
        ai_used: bool,
        reason: str,
        message: str
    ) -> dict:
        return {
            "ai_extraction_used": ai_used,
            "reason": reason,
            "message": message,
            "raw_record_id": str(raw_input.id),
            "user_id": request.user_id,
            "user_uuid": str(user.id),
            "app_id": str(app_obj.id),
            "original_text": request.text,
            "summary": raw_input.summary,
            "extracted_facts": raw_input.extracted_facts or [],
            "created_count": len(memories),
            "created_memories": [memory_to_dict(memory) for memory in memories],
        }

    def update_raw_input(
        status: str,
        summary: str | None = None,
        facts: List[str] | None = None,
        error_reason: str | None = None
    ) -> None:
        raw_input.processing_status = status
        raw_input.summary = summary
        raw_input.extracted_facts = facts or []
        raw_input.error_reason = error_reason
        raw_input.processed_at = datetime.now(UTC)
        db.add(raw_input)
        db.commit()
        db.refresh(raw_input)

    def normalize_fact_text(fact: str) -> str:
        fact = re.sub(r"^\s*\d+[\.\:：、]\s*", "", fact).strip()
        if fact.startswith("我的"):
            fact = "用户的" + fact[2:]
        elif fact.startswith("我"):
            fact = "用户" + fact[1:]
        fact = fact.replace("用户妻子", "用户的妻子")
        fact = fact.replace("用户儿子", "用户的儿子")
        fact = fact.replace("用户女儿", "用户的女儿")
        return fact.strip(" \n\t，,。；;")

    def dedupe_facts(facts: List[str]) -> List[str]:
        normalized = []
        seen = set()
        for fact in facts:
            clean_fact = normalize_fact_text(fact)
            if not clean_fact or clean_fact in seen:
                continue
            seen.add(clean_fact)
            normalized.append(clean_fact)
        return normalized

    def normalize_chat_speaker(raw_speaker: str) -> str | None:
        normalized = re.sub(r"\s+", "", (raw_speaker or "")).upper()
        if "用户" in normalized:
            return "用户"
        if "AI" in normalized or "助手" in normalized:
            return "AI"
        return None

    def normalize_chat_log(text: str) -> str:
        if not text:
            return ""

        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        normalized = re.sub(r"^\s*以下是用户与AI助手的对话记录[:：]\s*", "", normalized)
        normalized = re.sub(r"总计\s*\d+\s*条对话记录[。.]?\s*$", "", normalized, flags=re.M)
        normalized = re.sub(r"\n?\s*[-—]{5,}\s*\n?", "\n", normalized)
        normalized = re.sub(
            r"\[(\d{2}:\d{2}:\d{2})\s*(用户|用\s*户|AI助手|AI助\s*手|A\s*I|AI)\]\s*(用户|用\s*户|AI助手|AI助\s*手|A\s*I|AI)\s*[:：]\s*",
            r"[\1] \3: ",
            normalized,
            flags=re.I,
        )
        normalized = re.sub(
            r"\[(\d{2}:\d{2}:\d{2})\s*(用户|用\s*户|AI助手|AI助\s*手|A\s*I|AI)\]\s*[:：]\s*",
            r"[\1] \2: ",
            normalized,
            flags=re.I,
        )
        normalized = re.sub(r"(?<!\n)\[(\d{2}:\d{2}:\d{2})", r"\n[\1", normalized)
        normalized = re.sub(r"[ \t]+", " ", normalized)
        normalized = re.sub(r"\n{2,}", "\n", normalized)
        return normalized.strip()

    def extract_user_messages(text: str) -> List[dict]:
        messages = []
        line_pattern = re.compile(
            r"^\s*(?:\[(?P<ts>\d{2}:\d{2}:\d{2})\]\s*)?(?P<speaker>用户|用\s*户|AI助手|AI助\s*手|A\s*I|AI)\s*[:：]\s*(?P<content>.*)$",
            flags=re.I,
        )

        for raw_line in normalize_chat_log(text).splitlines():
            line = raw_line.strip()
            if not line:
                continue

            match = line_pattern.match(line)
            if not match:
                continue

            speaker = normalize_chat_speaker(match.group("speaker"))
            if not speaker:
                continue
            content = re.sub(r"\s+", " ", (match.group("content") or "")).strip()
            if not content:
                continue

            messages.append(
                {
                    "timestamp": match.group("ts"),
                    "speaker": speaker,
                    "content": content,
                }
            )

        return messages

    def build_user_only_messages(text: str) -> List[dict]:
        return [msg for msg in extract_user_messages(text) if msg["speaker"] == "用户"]

    def render_user_messages(messages: List[dict]) -> str:
        return "\n".join(
            f"[{msg['timestamp']}] 用户: {msg['content']}" if msg["timestamp"] else f"用户: {msg['content']}"
            for msg in messages
        )

    def is_system_ai_message(content: str) -> bool:
        keywords = [
            "正在联系",
            "已短信通知",
            "已通知",
            "正在为您拍照",
            "正在拍照",
            "已拍照",
            "正在报警",
            "已报警",
            "正在拨打",
            "已拨打",
            "紧急联系人",
        ]
        return any(keyword in content for keyword in keywords)

    def is_contextual_ai_message(content: str) -> bool:
        keywords = [
            "您是说",
            "是不是",
            "我听着像是",
            "充电站",
            "供电",
            "付款",
            "退款",
            "转账",
            "紧急联系人",
            "拍照",
            "报警",
            "风险",
            "联系人",
        ]
        return any(keyword in content for keyword in keywords)

    def build_summary_context_messages(text: str) -> List[dict]:
        messages = extract_user_messages(text)
        selected = []
        for msg in messages:
            if msg["speaker"] == "用户":
                selected.append(msg)
            elif msg["speaker"] == "AI" and (is_system_ai_message(msg["content"]) or is_contextual_ai_message(msg["content"])):
                selected.append(msg)
        return selected

    def render_messages(messages: List[dict]) -> str:
        rendered = []
        for msg in messages:
            prefix = f"[{msg['timestamp']}] " if msg.get("timestamp") else ""
            rendered.append(f"{prefix}{msg['speaker']}: {msg['content']}")
        return "\n".join(rendered)

    def get_memory_date_prefix() -> str | None:
        sync_time = (request.metadata or {}).get("sync_time")
        if not sync_time or not isinstance(sync_time, str):
            return None

        try:
            normalized = sync_time.replace("Z", "+00:00")
            parsed = datetime.fromisoformat(normalized)
            return f"{parsed.month}.{parsed.day}日"
        except ValueError:
            return None

    def format_memory_content(content: str, fact_type: str | None = None) -> str:
        normalized = re.sub(r"\s+", " ", content).strip()
        if not normalized:
            return normalized

        date_prefix = get_memory_date_prefix()
        if fact_type == "session_summary":
            normalized = re.sub(r"^(整段摘要：|摘要：)", "", normalized).strip()
            normalized = f"摘要：{normalized}"
        elif fact_type == "segment_summary":
            normalized = re.sub(r"^(第\d+段(?:（.*?）)?：)", "", normalized).strip()
            normalized = f"摘要：{normalized}"

        if date_prefix and not normalized.startswith(date_prefix):
            return f"{date_prefix}{normalized}"
        return normalized

    def segment_user_messages(messages: List[dict], max_items: int = 20, max_chars: int = 2200) -> List[dict]:
        if not messages:
            return []

        segments = []
        current = []
        current_chars = 0

        for message in messages:
            rendered = f"[{message['timestamp']}] 用户: {message['content']}" if message["timestamp"] else f"用户: {message['content']}"
            projected_chars = current_chars + len(rendered) + 1
            if current and (len(current) >= max_items or projected_chars > max_chars):
                start_ts = current[0].get("timestamp")
                end_ts = current[-1].get("timestamp")
                segments.append(
                    {
                        "segment_index": len(segments) + 1,
                        "time_range": f"{start_ts}-{end_ts}" if start_ts and end_ts else None,
                        "messages": current,
                        "text": render_user_messages(current),
                    }
                )
                current = []
                current_chars = 0

            current.append(message)
            current_chars += len(rendered) + 1

        if current:
            start_ts = current[0].get("timestamp")
            end_ts = current[-1].get("timestamp")
            segments.append(
                {
                    "segment_index": len(segments) + 1,
                    "time_range": f"{start_ts}-{end_ts}" if start_ts and end_ts else None,
                    "messages": current,
                    "text": render_user_messages(current),
                }
            )

        return segments

    def extract_summary_from_long_text(text: str) -> str | None:
        user_line_re = re.compile(r"\[(\d{2}:\d{2}:\d{2})\]\s*用户[:：]\s*(.+)")
        first_user = None

        for line in text.splitlines():
            match = user_line_re.search(line)
            if not match:
                continue
            content = match.group(2).strip()
            if not content:
                continue
            first_user = (match.group(1), content)
            break

        if first_user:
            return f"{first_user[0]} 用户提到：{first_user[1][:100].rstrip()}"

        if text.strip():
            return f"用户提到：{text.strip().replace(chr(10), ' ')[:120].rstrip()}"
        return None

    def should_use_long_text_pipeline(text: str) -> bool:
        user_messages = build_user_only_messages(text)
        if len(text) > 1000:
            return True
        if len(user_messages) >= 8:
            return True
        return len(user_messages) >= 4 and len(text) > 400

    def build_fact_payload(
        content: str,
        subject: str | None = None,
        fact_type: str = "fact",
        confidence: str = "medium",
        segment_index: int | None = None,
    ) -> dict | None:
        normalized_content = normalize_fact_text(content)
        if not normalized_content:
            return None
        return {
            "content": normalized_content,
            "subject": subject,
            "type": fact_type or "fact",
            "confidence": (confidence or "medium").lower(),
            "segment_index": segment_index,
        }

    def extract_ai_system_event_payloads(text: str, segment_index: int | None = None) -> List[dict]:
        payloads = []
        for msg in extract_user_messages(text):
            if msg["speaker"] != "AI":
                continue
            if not is_system_ai_message(msg["content"]):
                continue
            normalized_content = msg["content"]
            if "紧急联系人" in normalized_content and ("已短信通知" in normalized_content or "已通知" in normalized_content):
                normalized_content = normalized_content.replace("：", "").replace("，", "")
                normalized_content = normalized_content.replace("已短信通知", "系统已短信通知")
            elif "正在联系" in normalized_content and "紧急联系人" in normalized_content:
                normalized_content = "系统正在联系用户的紧急联系人，请稍后"
            elif "拍照" in normalized_content:
                normalized_content = "系统正在为用户拍照"
            payload = build_fact_payload(
                normalized_content,
                subject="系统",
                fact_type="system_event",
                confidence="high",
                segment_index=segment_index,
            )
            if payload:
                payloads.append(payload)
        return dedupe_fact_payloads(payloads)

    def heuristic_extract_facts(text: str) -> List[str]:
        user_messages = build_user_only_messages(text)
        if not user_messages:
            cleaned_text = re.sub(r"\s+", " ", text.strip())
            return [f"用户提到：{cleaned_text[:120].rstrip()}"] if cleaned_text else []

        base_segments = []
        for message in user_messages:
            base_segments.extend(re.split(r"[。！？；;\n]+", message["content"]))

        clauses = []
        for segment in base_segments:
            for clause in re.split(r"[，,]", segment):
                clean_clause = clause.strip()
                if clean_clause:
                    clauses.append(clean_clause)

        contextualized = []
        current_subject = "用户"

        for clause in clauses:
            candidate = clause
            if candidate.startswith("我妻子") or candidate.startswith("妻子"):
                if candidate.startswith("我妻子"):
                    candidate = "用户的妻子" + candidate[3:]
                else:
                    candidate = "用户的妻子" + candidate[2:]
                current_subject = "用户的妻子"
            elif candidate.startswith("我儿子") or candidate.startswith("儿子"):
                if candidate.startswith("我儿子"):
                    candidate = "用户的儿子" + candidate[3:]
                else:
                    candidate = "用户的儿子" + candidate[2:]
                current_subject = "用户的儿子"
            elif candidate.startswith("我女儿") or candidate.startswith("女儿"):
                if candidate.startswith("我女儿"):
                    candidate = "用户的女儿" + candidate[3:]
                else:
                    candidate = "用户的女儿" + candidate[2:]
                current_subject = "用户的女儿"
            elif candidate.startswith("我"):
                candidate = "用户" + candidate[1:]
                current_subject = "用户"
            elif candidate.startswith("今年") and current_subject:
                candidate = current_subject + candidate
            elif candidate.startswith("叫") and current_subject:
                candidate = current_subject + candidate

            contextualized.append(candidate)
        cleaned_text = re.sub(r"\s+", " ", text.strip())
        return dedupe_facts(contextualized) or [f"用户提到：{cleaned_text[:120].rstrip()}"]

    def heuristic_extract_fact_payloads(text: str, segment_index: int | None = None) -> List[dict]:
        facts = heuristic_extract_facts(text)
        payloads = []
        for fact in facts:
            payload = build_fact_payload(
                fact,
                subject="用户",
                fact_type="fact",
                confidence="medium",
                segment_index=segment_index,
            )
            if payload:
                payloads.append(payload)
        return payloads

    def dedupe_fact_payloads(fact_payloads: List[dict]) -> List[dict]:
        def canonicalize_fact_for_dedupe(content: str, fact_type: str) -> str:
            canonical = normalize_fact_text(content)
            canonical = re.sub(r"\s+", "", canonical)
            canonical = canonical.strip("，,。；;:：")

            # Normalize common wrappers generated by extraction models.
            canonical = re.sub(r"^用户(?:请求|发出|要求|希望|想要|提到要|提到|表示)", "用户", canonical)
            canonical = re.sub(r"(的指令|的请求)$", "", canonical)

            # Normalize highly repetitive intents.
            if re.search(r"用户(?:询问|问).*(现在|当前).*(几点|时间)", canonical):
                return "用户询问当前时间"
            if re.search(r"用户(?:询问|问).*(天气)", canonical):
                return "用户询问天气"
            if re.search(r"用户(?:请求|要求|发出)?停止(讲话|说话)?", canonical):
                return "用户请求停止"

            return canonical or f"{fact_type}:{content}"

        deduped = []
        seen = set()
        for payload in fact_payloads:
            content = normalize_fact_text(str(payload.get("content", "")))
            fact_type = str(payload.get("type") or "fact").strip() or "fact"
            canonical_key = canonicalize_fact_for_dedupe(content, fact_type)
            if not content or canonical_key in seen:
                continue
            seen.add(canonical_key)
            normalized_payload = dict(payload)
            normalized_payload["content"] = content
            normalized_payload["type"] = fact_type
            normalized_payload["confidence"] = str(normalized_payload.get("confidence") or "medium").strip().lower() or "medium"
            deduped.append(normalized_payload)
        return deduped

    def filter_memory_fact_payloads(fact_payloads: List[dict]) -> List[dict]:
        def is_low_signal_fact(content: str, fact_type: str) -> bool:
            compact = re.sub(r"\s+", "", content).strip("，,。；;:：")
            if not compact:
                return True

            # Ignore single-token fillers and acknowledgements.
            if re.fullmatch(r"用户(?:提到|表示|说)?(hi|hello|嗨|嗯+|啊+|哦+|好|好的|行|对|谢谢|拜|一)", compact, flags=re.I):
                return True

            # "stop" commands are usually operational noise for memory timeline.
            if fact_type in {"request", "event"} and re.fullmatch(r"用户(?:请求)?停止(?:讲话|说话)?", compact):
                return True

            return False

        filtered = []
        for payload in dedupe_fact_payloads(fact_payloads):
            if payload["confidence"] == "low":
                continue
            if is_low_signal_fact(payload["content"], payload["type"]):
                continue
            filtered.append(payload)
        return filtered

    def is_informative_segment_summary(summary: str | None) -> bool:
        if not summary:
            return False

        normalized = re.sub(r"\s+", " ", summary).strip()
        if not normalized:
            return False

        negative_patterns = [
            "未提取到有效信息",
            "没有明确",
            "无意义碎片",
            "内容不连贯",
            "表达不清",
            "不完整或无法理解",
            "模糊不清",
            "零散且不完整",
            "未提供明确的信息",
            "未提供明确的事实信息",
            "噪声词",
            "无法判断",
        ]
        if any(pattern in normalized for pattern in negative_patterns):
            return False

        positive_patterns = [
            "提到",
            "问题",
            "风险",
            "联系人",
            "付款",
            "转账",
            "退款",
            "供电",
            "充电",
            "设备",
            "家人",
            "妻子",
            "儿子",
            "女儿",
            "关系",
            "事件",
            "困惑",
            "求助",
            "异常",
            "担心",
            "操作",
        ]
        return any(pattern in normalized for pattern in positive_patterns)

    def extract_long_text_memories(text: str) -> tuple[str | None, List[dict], str | None, List[dict], List[dict]]:
        user_messages = build_user_only_messages(text)
        if not user_messages:
            raise RuntimeError("No user messages found in long-text input")

        user_only_text = render_user_messages(user_messages)
        summary_context_text = render_messages(build_summary_context_messages(text))
        segments = segment_user_messages(user_messages)
        if not segments:
            raise RuntimeError("Failed to segment long-text input")

        session_summary = None
        session_summary_payloads = []
        segment_summaries = []
        segment_summary_payloads = []
        combined_facts = []
        extraction_errors = []

        try:
            overall_summary, overall_facts = extract_structured_memories_with_ai(summary_context_text or user_only_text, long_text=True)
            if overall_summary:
                session_summary = overall_summary
                if is_informative_segment_summary(overall_summary):
                    session_summary_payloads.append(
                        {
                            "content": f"整段摘要：{overall_summary}",
                            "subject": "用户",
                            "type": "session_summary",
                            "confidence": "medium",
                            "segment_index": None,
                        }
                    )
            if overall_facts:
                combined_facts.extend(filter_memory_fact_payloads(overall_facts))
        except Exception as overall_error:
            extraction_errors.append(f"session: {overall_error}")
            session_summary = extract_summary_from_long_text(text)

        for segment in segments:
            try:
                summary, facts = extract_structured_memories_with_ai(segment["text"], long_text=True)
                accepted_facts = filter_memory_fact_payloads(
                    [
                        {
                            **fact,
                            "segment_index": segment["segment_index"],
                        }
                        for fact in facts
                    ]
                )
                if accepted_facts:
                    combined_facts.extend(accepted_facts)
                if summary:
                    prefix = f"第{segment['segment_index']}段"
                    if segment.get("time_range"):
                        prefix += f"（{segment['time_range']}）"
                    rendered_summary = f"{prefix}：{summary}"
                    segment_summaries.append(rendered_summary)
                    if is_informative_segment_summary(summary):
                        segment_summary_payloads.append(
                            {
                                "content": rendered_summary,
                                "subject": "用户",
                                "type": "segment_summary",
                                "confidence": "medium",
                                "segment_index": segment["segment_index"],
                            }
                        )
            except Exception as segment_error:
                extraction_errors.append(f"segment_{segment['segment_index']}: {segment_error}")
                fallback_facts = heuristic_extract_fact_payloads(
                    segment["text"],
                    segment_index=segment["segment_index"],
                )
                combined_facts.extend(fallback_facts)
                fallback_summary = extract_summary_from_long_text(segment["text"])
                if fallback_summary:
                    prefix = f"第{segment['segment_index']}段"
                    if segment.get("time_range"):
                        prefix += f"（{segment['time_range']}）"
                    rendered_summary = f"{prefix}：{fallback_summary}"
                    segment_summaries.append(rendered_summary)
                    if is_informative_segment_summary(fallback_summary):
                        segment_summary_payloads.append(
                            {
                                "content": rendered_summary,
                                "subject": "用户",
                                "type": "segment_summary",
                                "confidence": "medium",
                                "segment_index": segment["segment_index"],
                            }
                        )
        summary_parts = []
        if session_summary:
            summary_parts.append(f"整段摘要：{session_summary}")
        summary_parts.extend(segment_summaries)
        summary = "\n".join(summary_parts) if summary_parts else extract_summary_from_long_text(text)
        accepted_facts = filter_memory_fact_payloads(combined_facts)
        if not accepted_facts:
            raise RuntimeError("Long-text extraction produced no usable facts")

        error_reason = "; ".join(extraction_errors) if extraction_errors else None
        return (
            summary,
            accepted_facts,
            error_reason,
            dedupe_fact_payloads(segment_summary_payloads),
            dedupe_fact_payloads(session_summary_payloads),
        )

    def parse_structured_response(content: str) -> tuple[str | None, List[dict]]:
        json_match = re.search(r"```json\s*(\{.*?\})\s*```", content, flags=re.S)
        json_candidate = json_match.group(1) if json_match else content.strip()
        data = json.loads(json_candidate)
        summary = data.get("summary")
        facts = data.get("facts") or []
        if not isinstance(facts, list):
            facts = []
        parsed_facts = []
        for item in facts:
            if isinstance(item, dict):
                content_value = normalize_fact_text(str(item.get("content", "")))
                if not content_value:
                    continue
                parsed_facts.append(
                    {
                        "content": content_value,
                        "subject": str(item.get("subject", "")).strip() or None,
                        "type": str(item.get("type", "")).strip() or "fact",
                        "confidence": str(item.get("confidence", "")).strip().lower() or "medium",
                    }
                )
            else:
                content_value = normalize_fact_text(str(item))
                if not content_value:
                    continue
                parsed_facts.append(
                    {
                        "content": content_value,
                        "subject": None,
                        "type": "fact",
                        "confidence": "medium",
                    }
                )

        deduped = []
        seen = set()
        for fact in parsed_facts:
            content_value = fact["content"]
            if content_value in seen:
                continue
            seen.add(content_value)
            deduped.append(fact)
        return summary, deduped

    def extract_structured_memories_with_ai(text: str, long_text: bool = False) -> tuple[str | None, List[dict]]:
        from openai import OpenAI
        from app.utils.memory import OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL

        if not OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY is not configured")

        client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)
        prompt_template = LONG_TEXT_STRUCTURED_MEMORY_EXTRACTION_PROMPT if long_text else STRUCTURED_MEMORY_EXTRACTION_PROMPT
        prompt = prompt_template.format(input=text)
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": "你必须输出合法 JSON，不要输出额外解释。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=1200,
        )
        content = (response.choices[0].message.content or "").strip()
        if not content:
            raise RuntimeError("AI returned empty structured extraction content")
        return parse_structured_response(content)

    def create_memory_record(
        content: str,
        fact_index: int,
        segment_index: int | None = None,
        confidence: str | None = None,
        fact_type: str | None = None
    ) -> Memory:
        content = format_memory_content(content, fact_type=fact_type)
        memory_metadata = dict(request.metadata or {})
        memory_metadata.update(
            {
                "raw_record_id": str(raw_input.id),
                "fact_index": fact_index,
                "source_app": request.app,
            }
        )
        if segment_index is not None:
            memory_metadata["segment_index"] = segment_index
        if confidence:
            memory_metadata["confidence"] = confidence
        if fact_type:
            memory_metadata["fact_type"] = fact_type
        memory = Memory(
            id=uuid4(),
            user_id=user.id,
            app_id=app_obj.id,
            content=content,
            metadata_=memory_metadata,
            state=MemoryState.active,
        )
        db.add(memory)
        db.commit()
        db.refresh(memory)

        history = MemoryStatusHistory(
            memory_id=memory.id,
            changed_by=user.id,
            old_state=MemoryState.deleted,
            new_state=MemoryState.active,
        )
        db.add(history)
        db.commit()
        return memory

    def save_memory_via_client(
        content: str,
        fact_index: int,
        memory_client,
        segment_index: int | None = None,
        confidence: str | None = None,
        fact_type: str | None = None
    ) -> Memory:
        content = format_memory_content(content, fact_type=fact_type)
        response = memory_client.add(
            content,
            user_id=request.user_id,
            metadata={
                "source_app": "openmemory",
                "mcp_client": request.app,
                "raw_record_id": str(raw_input.id),
                "fact_index": fact_index,
                "segment_index": segment_index,
                "confidence": confidence,
                "fact_type": fact_type,
            },
        )
        logging.info(f"Qdrant response for fact #{fact_index}: {response}")

        results = response.get("results") if isinstance(response, dict) else None
        if not results:
            return create_memory_record(
                content,
                fact_index,
                segment_index=segment_index,
                confidence=confidence,
                fact_type=fact_type,
            )

        for result in results:
            result_text = result.get("memory") or result.get("text") or content
            event_type = result.get("event")
            if event_type in ["ADD", "UPDATE"] and result.get("id"):
                memory_id = UUID(result["id"])
                existing_memory = db.query(Memory).filter(Memory.id == memory_id).first()
                if existing_memory:
                    existing_memory.content = result_text
                    existing_memory.metadata_ = {
                        **(existing_memory.metadata_ or {}),
                        **(request.metadata or {}),
                        "raw_record_id": str(raw_input.id),
                        "fact_index": fact_index,
                        "source_app": request.app,
                        "segment_index": segment_index,
                        "confidence": confidence,
                        "fact_type": fact_type,
                    }
                    existing_memory.state = MemoryState.active
                    db.add(existing_memory)
                    memory = existing_memory
                else:
                    memory = Memory(
                        id=memory_id,
                        user_id=user.id,
                        app_id=app_obj.id,
                        content=result_text,
                        metadata_={
                            **(request.metadata or {}),
                            "raw_record_id": str(raw_input.id),
                            "fact_index": fact_index,
                            "source_app": request.app,
                            "segment_index": segment_index,
                            "confidence": confidence,
                            "fact_type": fact_type,
                        },
                        state=MemoryState.active,
                    )
                    db.add(memory)
                db.commit()
                db.refresh(memory)

                history = MemoryStatusHistory(
                    memory_id=memory.id,
                    changed_by=user.id,
                    old_state=MemoryState.deleted,
                    new_state=MemoryState.active,
                )
                db.add(history)
                db.commit()
                return memory

        return create_memory_record(
            content,
            fact_index,
            segment_index=segment_index,
            confidence=confidence,
            fact_type=fact_type,
        )

    long_text_db_only = False

    def persist_fact_payloads(fact_payloads: List[dict]) -> List[Memory]:
        memories = []
        for index, fact_payload in enumerate(fact_payloads, start=1):
            content = fact_payload["content"]
            fact_type = fact_payload.get("type")
            use_memory_client = memory_client and not long_text_db_only and fact_type not in {"session_summary"}
            if memory_client:
                if use_memory_client:
                    try:
                        memories.append(
                            save_memory_via_client(
                                content,
                                index,
                                memory_client,
                                segment_index=fact_payload.get("segment_index"),
                                confidence=fact_payload.get("confidence"),
                                fact_type=fact_type,
                            )
                        )
                    except Exception as memory_client_error:
                        logging.error(
                            f"Memory client add failed for fact #{index}, fallback to DB-only: {memory_client_error}"
                        )
                        memories.append(
                            create_memory_record(
                                content,
                                index,
                                segment_index=fact_payload.get("segment_index"),
                                confidence=fact_payload.get("confidence"),
                                fact_type=fact_type,
                            )
                        )
                    continue
                memories.append(
                    create_memory_record(
                        content,
                        index,
                        segment_index=fact_payload.get("segment_index"),
                        confidence=fact_payload.get("confidence"),
                        fact_type=fact_type,
                    )
                )
                continue
            memories.append(
                create_memory_record(
                    content,
                    index,
                    segment_index=fact_payload.get("segment_index"),
                    confidence=fact_payload.get("confidence"),
                    fact_type=fact_type,
                )
            )
        return memories

    created_memories: List[Memory] = []

    if not request.infer:
        facts = [request.text.strip()]
        update_raw_input(
            status="stored_without_ai",
            summary=None,
            facts=facts,
            error_reason=None,
        )
        created_memories.append(create_memory_record(facts[0], 1))
        return build_create_response(
            created_memories,
            ai_used=False,
            reason="ai_disabled",
            message="原始信息已入库，未启用AI提取，已按原文存入记忆表",
        )

    memory_client = None
    client_error_message = None
    try:
        memory_client = get_memory_client()
        if not memory_client:
            raise RuntimeError("Memory client is not available")
    except Exception as client_error:
        client_error_message = str(client_error)
        logging.error(f"Memory client unavailable: {client_error}")

    try:
        use_long_text_pipeline = should_use_long_text_pipeline(request.text)
        extraction_warning = None
        extracted_facts = []
        memory_payloads = []

        if use_long_text_pipeline:
            long_text_db_only = True
            summary, fact_payloads, extraction_warning, segment_summary_payloads, session_summary_payloads = extract_long_text_memories(request.text)
            reason = "long_text_structured_facts_created"
            status = "processed_long_text"
            message = "已保存原始信息，并按长文本规则提取整段摘要与原子事实后写入记忆表"
            extracted_facts = [fact["content"] for fact in fact_payloads]
            memory_payloads = fact_payloads + session_summary_payloads
        else:
            summary, fact_payloads = extract_structured_memories_with_ai(request.text)
            fact_payloads = filter_memory_fact_payloads(fact_payloads)
            if not fact_payloads:
                raise RuntimeError("AI returned no usable facts")
            reason = "structured_facts_created"
            status = "processed"
            message = "已保存原始信息，并将AI提取的事实逐条写入记忆表"
            extracted_facts = [fact["content"] for fact in fact_payloads]
            memory_payloads = fact_payloads

        update_raw_input(
            status=status,
            summary=summary,
            facts=extracted_facts,
            error_reason=extraction_warning,
        )
        created_memories.extend(persist_fact_payloads(memory_payloads))

        if client_error_message:
            message += "（向量存储不可用，本次仅写入数据库）"
        if extraction_warning:
            message += "（部分分段使用了兜底提取）"
        return build_create_response(
            created_memories,
            ai_used=True,
            reason=reason,
            message=message,
        )
    except Exception as extraction_error:
        logging.error(f"Structured extraction failed: {extraction_error}")
        fallback_summary = extract_summary_from_long_text(request.text)
        fallback_payloads = heuristic_extract_fact_payloads(request.text)
        fallback_payloads = filter_memory_fact_payloads(fallback_payloads)
        fallback_facts = [fact["content"] for fact in fallback_payloads]
        update_raw_input(
            status="processed_with_fallback",
            summary=fallback_summary,
            facts=fallback_facts,
            error_reason=str(extraction_error),
        )
        created_memories.extend(persist_fact_payloads(fallback_payloads))
        return build_create_response(
            created_memories,
            ai_used=False,
            reason="structured_extraction_failed",
            message="原始信息已入库，AI提取失败，已按兜底拆分结果写入记忆表",
        )




# Get memory by ID
@router.get("/{memory_id}")
async def get_memory(
    memory_id: UUID,
    db: Session = Depends(get_db)
):
    """
    根据记忆ID获取记忆的详细信息。
    
    返回内容：
    - 记忆ID、内容、创建时间
    - 记忆状态（active/paused/archived/deleted）
    - 所属应用信息
    - 分类列表
    - 元数据
    - 衰退相关字段（衰退分数、重要性分数、访问次数等）
    
    参数:
    - memory_id: 记忆ID（UUID格式，必填）
    - user_id: 用户ID（查询参数，必填）
    """
    memory = get_memory_or_404(db, memory_id)
    return {
        "id": memory.id,
        "text": memory.content,
        "created_at": int(memory.created_at.timestamp()),
        "state": memory.state.value,
        "app_id": memory.app_id,
        "app_name": memory.app.name if memory.app else None,
        "categories": [category.name for category in memory.categories],
        "metadata_": memory.metadata_,
        # 衰退相关字段
        "decay_score": getattr(memory, 'decay_score', 1.0),
        "importance_score": getattr(memory, 'importance_score', 0.5),
        "access_count": getattr(memory, 'access_count', 0),
        "last_accessed_at": int(memory.last_accessed_at.timestamp()) if memory.last_accessed_at else None
    }

class DeleteMemoriesRequest(BaseModel):
    memory_ids: List[UUID]
    user_id: str

# Delete multiple memories
@router.delete("/")
async def delete_memories(
    request: DeleteMemoriesRequest,
    db: Session = Depends(get_db)
):
    """
    批量删除记忆。
    
    功能说明：
    - 支持一次删除多个记忆
    - 记忆状态将变为"deleted"
    - 删除操作会记录到历史记录中
    
    参数:
    - memory_ids: 记忆ID列表（必填）
    - user_id: 用户ID（必填）
    
    注意事项：
    - 删除操作不可恢复
    - 建议先使用暂停功能，确认后再删除
    """
    user = db.query(User).filter(User.user_id == request.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    for memory_id in request.memory_ids:
        update_memory_state(db, memory_id, MemoryState.deleted, user.id)
    return {"message": f"Successfully deleted {len(request.memory_ids)} memories"}


# Archive memories
@router.post("/actions/archive")
async def archive_memories(
    memory_ids: List[UUID],
    user_id: UUID,
    db: Session = Depends(get_db)
):
    for memory_id in memory_ids:
        update_memory_state(db, memory_id, MemoryState.archived, user_id)
    return {"message": f"Successfully archived {len(memory_ids)} memories"}


class PauseMemoriesRequest(BaseModel):
    memory_ids: Optional[List[UUID]] = None
    category_ids: Optional[List[UUID]] = None
    app_id: Optional[UUID] = None
    all_for_app: bool = False
    global_pause: bool = False
    state: Optional[MemoryState] = None
    user_id: str

# Pause access to memories
@router.post("/actions/pause")
async def pause_memories(
    request: PauseMemoriesRequest,
    db: Session = Depends(get_db)
):
    """
    暂停或恢复记忆的访问。
    
    功能说明：
    - 可以暂停特定记忆、应用的所有记忆、或全局所有记忆
    - 暂停的记忆不会被查询返回
    - 支持恢复记忆为活跃状态
    
    参数:
    - memory_ids: 记忆ID列表（可选）
    - category_ids: 分类ID列表（可选）
    - app_id: 应用ID（可选）
    - all_for_app: 是否暂停应用的所有记忆（可选）
    - global_pause: 是否全局暂停（可选）
    - state: 目标状态（可选：active, paused，默认paused）
    - user_id: 用户ID（必填）
    
    使用场景：
    - 临时禁用某些记忆
    - 批量管理记忆状态
    """
    global_pause = request.global_pause
    all_for_app = request.all_for_app
    app_id = request.app_id
    memory_ids = request.memory_ids
    category_ids = request.category_ids
    state = request.state or MemoryState.paused

    user = db.query(User).filter(User.user_id == request.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    user_id = user.id
    
    if global_pause:
        # Pause all memories
        memories = db.query(Memory).filter(
            Memory.state != MemoryState.deleted,
            Memory.state != MemoryState.archived
        ).all()
        for memory in memories:
            update_memory_state(db, memory.id, state, user_id)
        return {"message": "Successfully paused all memories"}

    if app_id:
        # Pause all memories for an app
        memories = db.query(Memory).filter(
            Memory.app_id == app_id,
            Memory.user_id == user.id,
            Memory.state != MemoryState.deleted,
            Memory.state != MemoryState.archived
        ).all()
        for memory in memories:
            update_memory_state(db, memory.id, state, user_id)
        return {"message": f"Successfully paused all memories for app {app_id}"}
    
    if all_for_app and memory_ids:
        # Pause all memories for an app
        memories = db.query(Memory).filter(
            Memory.user_id == user.id,
            Memory.state != MemoryState.deleted,
            Memory.id.in_(memory_ids)
        ).all()
        for memory in memories:
            update_memory_state(db, memory.id, state, user_id)
        return {"message": f"Successfully paused all memories"}

    if memory_ids:
        # Pause specific memories
        for memory_id in memory_ids:
            update_memory_state(db, memory_id, state, user_id)
        return {"message": f"Successfully paused {len(memory_ids)} memories"}

    if category_ids:
        # Pause memories by category
        memories = db.query(Memory).join(Memory.categories).filter(
            Category.id.in_(category_ids),
            Memory.state != MemoryState.deleted,
            Memory.state != MemoryState.archived
        ).all()
        for memory in memories:
            update_memory_state(db, memory.id, state, user_id)
        return {"message": f"Successfully paused memories in {len(category_ids)} categories"}

    raise HTTPException(status_code=400, detail="Invalid pause request parameters")


# Get memory access logs
@router.get("/{memory_id}/access-log")
async def get_memory_access_log(
    memory_id: UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db)
):
    query = db.query(MemoryAccessLog).filter(MemoryAccessLog.memory_id == memory_id)
    total = query.count()
    logs = query.order_by(MemoryAccessLog.accessed_at.desc()).offset((page - 1) * page_size).limit(page_size).all()

    # Get app name
    for log in logs:
        app = db.query(App).filter(App.id == log.app_id).first()
        log.app_name = app.name if app else None

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "logs": logs
    }


class UpdateMemoryRequest(BaseModel):
    memory_content: str
    user_id: str

# Update a memory
@router.put("/{memory_id}")
async def update_memory(
    memory_id: UUID,
    request: UpdateMemoryRequest,
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.user_id == request.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    memory = get_memory_or_404(db, memory_id)
    memory.content = request.memory_content
    db.commit()
    db.refresh(memory)
    return memory

class FilterMemoriesRequest(BaseModel):
    user_id: str
    page: int = 1
    size: int = 10
    search_query: Optional[str] = None
    app_ids: Optional[List[UUID]] = None
    category_ids: Optional[List[UUID]] = None
    sort_column: Optional[str] = None
    sort_direction: Optional[str] = None
    from_date: Optional[int] = None
    to_date: Optional[int] = None
    show_archived: Optional[bool] = False

@router.post("/filter", response_model=Page[MemoryResponse])
async def filter_memories(
    request: FilterMemoriesRequest,
    db: Session = Depends(get_db)
):
    """
    使用POST方式过滤查询记忆，功能更强大。
    
    功能特性：
    - 支持多条件组合过滤
    - 支持按应用、分类过滤
    - 支持关键词搜索
    - 支持时间范围过滤
    - 支持排序和分页
    - 可选择是否包含归档记忆
    
    参数:
    - user_id: 用户ID（必填）
    - page: 页码（默认1）
    - size: 每页数量（默认10）
    - search_query: 搜索关键词（可选）
    - app_ids: 应用ID列表（可选）
    - category_ids: 分类ID列表（可选）
    - sort_column: 排序字段（可选：memory, app_name, created_at）
    - sort_direction: 排序方向（可选：asc, desc）
    - from_date: 起始时间戳（可选）
    - to_date: 结束时间戳（可选）
    - show_archived: 是否包含归档记忆（默认false）
    
    推荐使用：
    前端应用推荐使用此接口，功能更全面，参数更灵活。
    """
    user = db.query(User).filter(User.user_id == request.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Build base query
    query = db.query(Memory).filter(
        Memory.user_id == user.id,
        Memory.state != MemoryState.deleted,
    )

    # Filter archived memories based on show_archived parameter
    if not request.show_archived:
        query = query.filter(Memory.state != MemoryState.archived)

    # Apply search filter
    if request.search_query:
        query = query.filter(Memory.content.ilike(f"%{request.search_query}%"))

    # Apply app filter
    if request.app_ids:
        query = query.filter(Memory.app_id.in_(request.app_ids))

    # Add joins for app and categories
    query = query.outerjoin(App, Memory.app_id == App.id)

    # Apply category filter
    if request.category_ids:
        query = query.join(Memory.categories).filter(Category.id.in_(request.category_ids))
    else:
        query = query.outerjoin(Memory.categories)

    # Apply date filters
    if request.from_date:
        from_datetime = datetime.fromtimestamp(request.from_date, tz=UTC)
        query = query.filter(Memory.created_at >= from_datetime)

    if request.to_date:
        to_datetime = datetime.fromtimestamp(request.to_date, tz=UTC)
        query = query.filter(Memory.created_at <= to_datetime)

    # Apply sorting
    if request.sort_column and request.sort_direction:
        sort_direction = request.sort_direction.lower()
        if sort_direction not in ['asc', 'desc']:
            raise HTTPException(status_code=400, detail="Invalid sort direction")

        sort_mapping = {
            'memory': Memory.content,
            'app_name': App.name,
            'created_at': Memory.created_at
        }

        if request.sort_column not in sort_mapping:
            raise HTTPException(status_code=400, detail="Invalid sort column")

        sort_field = sort_mapping[request.sort_column]
        if sort_direction == 'desc':
            query = query.order_by(sort_field.desc())
        else:
            query = query.order_by(sort_field.asc())
    else:
        # Default sorting
        query = query.order_by(Memory.created_at.desc())

    # Add eager loading for categories and make the query distinct
    query = query.options(
        joinedload(Memory.categories)
    ).distinct(Memory.id)

    # Use fastapi-pagination's paginate function
    return sqlalchemy_paginate(
        query,
        Params(page=request.page, size=request.size),
        transformer=lambda items: [
            MemoryResponse(
                id=memory.id,
                content=memory.content,
                created_at=memory.created_at,
                state=memory.state.value,
                app_id=memory.app_id,
                app_name=memory.app.name if memory.app else None,
                categories=[category.name for category in memory.categories],
                metadata_=memory.metadata_,
                # 衰退相关字段
                decay_score=getattr(memory, 'decay_score', 1.0),
                importance_score=getattr(memory, 'importance_score', 0.5),
                access_count=getattr(memory, 'access_count', 0),
                last_accessed_at=getattr(memory, 'last_accessed_at', None)
            )
            for memory in items
        ]
    )


@router.get("/{memory_id}/related", response_model=Page[MemoryResponse])
async def get_related_memories(
    memory_id: UUID,
    user_id: str,
    params: Params = Depends(),
    db: Session = Depends(get_db)
):
    """
    根据分类获取与指定记忆相关的其他记忆。
    
    功能说明：
    - 基于分类相似度查找相关记忆
    - 返回最多5条相关记忆
    - 按分类匹配度和创建时间排序
    
    参数:
    - memory_id: 源记忆ID（必填）
    - user_id: 用户ID（查询参数，必填）
    - page: 页码（默认1）
    - size: 每页数量（固定为5）
    
    使用场景：
    - 查看与当前记忆相关的其他记忆
    - 发现记忆之间的关联性
    """
    # Validate user
    user = db.query(User).filter(User.user_id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Get the source memory
    memory = get_memory_or_404(db, memory_id)
    
    # Extract category IDs from the source memory
    category_ids = [category.id for category in memory.categories]
    
    if not category_ids:
        return Page.create([], total=0, params=params)
    
    # Build query for related memories
    query = db.query(Memory).distinct(Memory.id).filter(
        Memory.user_id == user.id,
        Memory.id != memory_id,
        Memory.state != MemoryState.deleted
    ).join(Memory.categories).filter(
        Category.id.in_(category_ids)
    ).options(
        joinedload(Memory.categories),
        joinedload(Memory.app)
    ).order_by(
        func.count(Category.id).desc(),
        Memory.created_at.desc()
    ).group_by(Memory.id)
    
    # ⚡ Force page size to be 5
    params = Params(page=params.page, size=5)
    
    return sqlalchemy_paginate(
        query,
        params,
        transformer=lambda items: [
            MemoryResponse(
                id=memory.id,
                content=memory.content,
                created_at=memory.created_at,
                state=memory.state.value,
                app_id=memory.app_id,
                app_name=memory.app.name if memory.app else None,
                categories=[category.name for category in memory.categories],
                metadata_=memory.metadata_,
                # 衰退相关字段
                decay_score=getattr(memory, 'decay_score', 1.0),
                importance_score=getattr(memory, 'importance_score', 0.5),
                access_count=getattr(memory, 'access_count', 0),
                last_accessed_at=getattr(memory, 'last_accessed_at', None)
            )
            for memory in items
        ]
    )
