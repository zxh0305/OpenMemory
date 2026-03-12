from datetime import datetime, UTC
from typing import List, Optional, Set
from uuid import UUID, uuid4
import logging
import os
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
    MemoryStatusHistory, User, Category, AccessControl, Config as ConfigModel
)
from app.schemas import MemoryResponse, PaginatedMemoryResponse
from app.utils.permissions import check_memory_access_permissions
from app.utils.db import get_or_create_user

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
    # 使用 get_or_create_user 确保用户存在，如果不存在则自动创建
    user = get_or_create_user(db, request.user_id)
    # Get or create app
    app_obj = db.query(App).filter(App.name == request.app,
                                   App.owner_id == user.id).first()
    if not app_obj:
        app_obj = App(name=request.app, owner_id=user.id)
        db.add(app_obj)
        db.commit()
        db.refresh(app_obj)

    # Check if app is active
    if not app_obj.is_active:
        raise HTTPException(status_code=403, detail=f"App {request.app} is currently paused on OpenMemory. Cannot create new memories.")

    # Log what we're about to do
    logging.info(f"Creating memory for user_id: {request.user_id} with app: {request.app}")

    def build_create_response(memory: Memory) -> dict:
        return {
            "ai_extraction_used": True,
            "message": "已提取信息并存入",
            "id": str(memory.id),
            # user_id in DB is internal UUID; return both external and internal IDs
            "user_id": request.user_id,
            "user_uuid": str(memory.user_id),
            "app_id": str(memory.app_id),
            "original_text": request.text,
            "extracted_text": memory.content,
            "content": memory.content,
            "metadata_": memory.metadata_,
            "state": memory.state.value if hasattr(memory.state, "value") else str(memory.state),
            "created_at": int(memory.created_at.timestamp()) if memory.created_at else None
        }

    def build_fallback_response(memory: Memory, reason: str, message: str) -> dict:
        return {
            "ai_extraction_used": False,
            "reason": reason,
            "message": message,
            "id": str(memory.id),
            "user_id": request.user_id,
            "user_uuid": str(memory.user_id),
            "app_id": str(memory.app_id),
            "original_text": request.text,
            "extracted_text": None,
            "content": memory.content,
            "metadata_": memory.metadata_,
            "state": memory.state.value if hasattr(memory.state, "value") else str(memory.state),
            "created_at": int(memory.created_at.timestamp()) if memory.created_at else None
        }

    def save_fallback(reason: str, message: str) -> dict:
        memory = Memory(
            id=uuid4(),
            user_id=user.id,
            app_id=app_obj.id,
            content=request.text,
            metadata_=request.metadata,
            state=MemoryState.active
        )
        db.add(memory)
        db.commit()
        db.refresh(memory)

        history = MemoryStatusHistory(
            memory_id=memory.id,
            changed_by=user.id,
            old_state=MemoryState.deleted,
            new_state=MemoryState.active
        )
        db.add(history)
        db.commit()

        logging.info(f"Memory created via fallback: {memory.id} (reason={reason})")
        return build_fallback_response(memory, reason, message)

    # If infer is disabled, skip AI extraction and store original text
    if not request.infer:
        return save_fallback("ai_disabled", "未启用AI提取，已存入原文")

    # Try to get memory client for extraction
    try:
        memory_client = get_memory_client()
        if not memory_client:
            raise Exception("Memory client is not available")
    except Exception as client_error:
        logging.error(f"Memory client unavailable: {client_error}")
        return save_fallback("memory_client_unavailable", "未使用AI提取：memory_client不可用，已使用原文存入")

    # Try to save to Qdrant via memory_client
    try:
        qdrant_response = memory_client.add(
            request.text,
            user_id=request.user_id,  # Use string user_id to match search
            metadata={
                "source_app": "openmemory",
                "mcp_client": request.app,
            }
        )
        
        # Log the response for debugging
        logging.info(f"Qdrant response: {qdrant_response}")
        
        # Process Qdrant response
        if isinstance(qdrant_response, dict) and 'results' in qdrant_response:
            results = qdrant_response.get('results') or []
            for result in results:
                event_type = result.get('event')
                # 优化提示：建议 LLM 输出格式，明确 event 字段要求
                if event_type not in ['ADD', 'UPDATE', 'DELETE', 'NONE']:
                    logging.warning(f"""LLM返回的 event 字段不规范: {event_type}，
                    建议输出如下格式: {{\"memory\": [{{\"id\": \"0\", \"text\": \"xxx\", \"event\": \"ADD\"}}]}}。
                    请确保 event 字段为 'ADD' 或 'UPDATE'，否则记忆将无法被正常写入。建议 LLM prompt 示例：你需要返回如下 
                    JSON 格式：{{\"memory\":[{{\"id\":\"0\",\"text\":\"xxx\",\"event\":\"ADD\"}}]}}，其中 event 只能为 'ADD' 或 'UPDATE'。""")
                if event_type in ['ADD', 'UPDATE']:
                    # Get the Qdrant-generated ID
                    memory_id = UUID(result['id'])
                    # Check if memory already exists
                    existing_memory = db.query(Memory).filter(Memory.id == memory_id).first()
                    if existing_memory:
                        # Update existing memory
                        existing_memory.state = MemoryState.active
                        existing_memory.content = result.get('memory', result.get('text', ''))
                        memory = existing_memory
                    else:
                        # Create memory with the EXACT SAME ID from Qdrant
                        memory = Memory(
                            id=memory_id,  # Use the same ID that Qdrant generated
                            user_id=user.id,
                            app_id=app_obj.id,
                            content=result.get('memory', result.get('text', '')),
                            metadata_=request.metadata,
                            state=MemoryState.active
                        )
                        db.add(memory)
                    # Create history entry
                    history = MemoryStatusHistory(
                        memory_id=memory_id,
                        changed_by=user.id,
                        old_state=MemoryState.deleted if existing_memory else MemoryState.deleted,
                        new_state=MemoryState.active
                    )
                    db.add(history)
                    db.commit()
                    db.refresh(memory)
                    return build_create_response(memory)
                else:
                    # 兜底：只要有 text 或 memory 字段就强制写入
                    if result.get('memory') or result.get('text'):
                        logging.warning(f"未知event: {event_type}，自动兜底为ADD，内容: {result}")
                        memory_id = UUID(result['id']) if 'id' in result else uuid.uuid4()
                        memory = Memory(
                            id=memory_id,
                            user_id=user.id,
                            app_id=app_obj.id,
                            content=result.get('memory', result.get('text', '')),
                            metadata_=request.metadata,
                            state=MemoryState.active
                        )
                        db.add(memory)
                        history = MemoryStatusHistory(
                            memory_id=memory_id,
                            changed_by=user.id,
                            old_state=MemoryState.deleted,
                            new_state=MemoryState.active
                        )
                        db.add(history)
                        db.commit()
                        db.refresh(memory)
                        return build_create_response(memory)
            # AI ran but produced no usable memory -> fallback to original text
            if not results:
                return save_fallback("ai_extraction_empty", "AI提取未返回可写入结果，已使用原文存入")
            return save_fallback("ai_extraction_no_valid_event", "AI提取结果无法写入（event不合规），已使用原文存入")
        return save_fallback("ai_extraction_invalid_response", "AI提取返回格式不正确，已使用原文存入")
    except Exception as qdrant_error:
        logging.error(f"Qdrant/LLM operation failed: {qdrant_error}")
        return save_fallback("ai_extraction_failed", "未使用AI提取：AI/向量存储写入失败，已使用原文存入")




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
