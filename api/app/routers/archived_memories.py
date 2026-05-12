"""
归档记忆管理API路由

提供归档记忆的查询、恢复、删除等功能。
归档记忆存储在独立的 archived_memories 表中，与活跃记忆分离管理。

**主要功能：**
- 查询归档记忆列表
- 获取归档记忆详情
- 恢复归档记忆到活跃状态
- 永久删除归档记忆
- 获取归档统计信息
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
from typing import Optional, List
import logging

from app.database import get_db
from app.models import ArchivedMemory, User
from app.utils.decay_with_archive_table import (
    restore_archived_memory,
    get_archived_memories_list
)

router = APIRouter(prefix="/api/v1/archived-memories", tags=["archived-memories"])
logger = logging.getLogger(__name__)


class RestoreMemoryRequest(BaseModel):
    """恢复归档记忆请求"""
    memory_id: str
    user_id: str


class ArchivedMemoryResponse(BaseModel):
    """归档记忆响应"""
    id: str
    content: str
    user_id: str
    app_id: str
    created_at: str
    archived_at: str
    decay_score_at_archive: float
    importance_score: float
    access_count: int
    categories: List[str]


@router.get("/", response_model=List[ArchivedMemoryResponse])
async def list_archived_memories(
    user_id: str = Query(..., description="用户ID"),
    limit: int = Query(50, ge=1, le=500, description="返回数量限制"),
    offset: int = Query(0, ge=0, description="偏移量"),
    db: Session = Depends(get_db)
):
    """
    获取归档记忆列表
    
    归档记忆存储在独立的 archived_memories 表中
    """
    try:
        # 验证用户
        user = db.query(User).filter(User.user_id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")
        
        # 获取归档记忆
        archived_memories = get_archived_memories_list(
            db,
            user_id=user.id,
            limit=limit,
            offset=offset
        )
        
        # 转换为响应格式
        result = [
            ArchivedMemoryResponse(
                id=str(memory.id),
                content=memory.content[:200] + "..." if len(memory.content) > 200 else memory.content,
                user_id=str(memory.user_id),
                app_id=str(memory.app_id),
                created_at=memory.created_at.isoformat(),
                archived_at=memory.archived_at.isoformat(),
                decay_score_at_archive=memory.decay_score_at_archive,
                importance_score=memory.importance_score,
                access_count=memory.access_count,
                categories=memory.categories_snapshot or []
            )
            for memory in archived_memories
        ]
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取归档记忆列表失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"查询失败: {str(e)}")


@router.get("/stats/summary")
async def get_archived_stats(
    user_id: str = Query(..., description="用户ID"),
    db: Session = Depends(get_db)
):
    """
    获取归档记忆的统计信息。

    返回内容：
    - total_archived: 总归档数量
    - by_decay_score: 按衰退分数分类统计
      - very_low: 衰退分数 < 0.05 的数量
      - low: 衰退分数 0.05-0.1 的数量
      - medium: 衰退分数 >= 0.1 的数量
    - by_app: 按应用分类统计

    参数:
    - user_id: 用户ID（查询参数，必填）

    使用场景：
    - 了解归档记忆的分布情况
    - 分析记忆衰退情况
    """
    try:
        # 验证用户
        user = db.query(User).filter(User.user_id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")

        # 统计归档记忆
        total_archived = db.query(ArchivedMemory).filter(
            ArchivedMemory.user_id == user.id
        ).count()

        # 按应用统计
        from sqlalchemy import func
        app_stats = db.query(
            ArchivedMemory.app_id,
            func.count(ArchivedMemory.id).label('count')
        ).filter(
            ArchivedMemory.user_id == user.id
        ).group_by(ArchivedMemory.app_id).all()

        # 按衰退分数统计
        very_low = db.query(ArchivedMemory).filter(
            ArchivedMemory.user_id == user.id,
            ArchivedMemory.decay_score_at_archive < 0.05
        ).count()

        low = db.query(ArchivedMemory).filter(
            ArchivedMemory.user_id == user.id,
            ArchivedMemory.decay_score_at_archive >= 0.05,
            ArchivedMemory.decay_score_at_archive < 0.1
        ).count()

        medium = db.query(ArchivedMemory).filter(
            ArchivedMemory.user_id == user.id,
            ArchivedMemory.decay_score_at_archive >= 0.1
        ).count()

        return {
            "success": True,
            "total_archived": total_archived,
            "by_decay_score": {
                "very_low": very_low,  # < 0.05
                "low": low,  # 0.05 - 0.1
                "medium": medium  # >= 0.1
            },
            "by_app": [
                {"app_id": str(app_id), "count": count}
                for app_id, count in app_stats
            ]
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取归档统计失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"统计失败: {str(e)}")


@router.get("/{memory_id}")
async def get_archived_memory(
    memory_id: str,
    user_id: str = Query(..., description="用户ID"),
    db: Session = Depends(get_db)
):
    """
    获取单个归档记忆的详细信息。
    
    返回内容：
    - 记忆的完整内容
    - 归档时间
    - 归档时的衰退分数
    - 归档前的状态
    - 重要性分数
    - 访问次数和最后访问时间
    - 分类快照
    
    参数:
    - memory_id: 归档记忆ID（必填）
    - user_id: 用户ID（查询参数，必填）
    """
    try:
        # 验证用户
        user = db.query(User).filter(User.user_id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")
        
        # 查找归档记忆
        archived_memory = db.query(ArchivedMemory).filter(
            ArchivedMemory.id == memory_id,
            ArchivedMemory.user_id == user.id
        ).first()
        
        if not archived_memory:
            raise HTTPException(status_code=404, detail="归档记忆不存在")
        
        return {
            "id": str(archived_memory.id),
            "content": archived_memory.content,
            "user_id": str(archived_memory.user_id),
            "app_id": str(archived_memory.app_id),
            "metadata_": archived_memory.metadata_,
            "created_at": archived_memory.created_at.isoformat(),
            "updated_at": archived_memory.updated_at.isoformat(),
            "archived_at": archived_memory.archived_at.isoformat(),
            "archived_from_state": archived_memory.archived_from_state.value,
            "decay_score_at_archive": archived_memory.decay_score_at_archive,
            "last_accessed_at": archived_memory.last_accessed_at.isoformat() if archived_memory.last_accessed_at else None,
            "access_count": archived_memory.access_count,
            "importance_score": archived_memory.importance_score,
            "categories": archived_memory.categories_snapshot or []
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取归档记忆详情失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"查询失败: {str(e)}")


@router.post("/restore")
async def restore_memory(
    request: RestoreMemoryRequest,
    db: Session = Depends(get_db)
):
    """
    从归档表恢复记忆到活跃表。
    
    功能说明：
    - 将归档记忆恢复为活跃状态
    - 从 archived_memories 表删除记录
    - 在 memories 表创建新记录
    - 重置衰退分数为 1.0
    - 恢复分类关联
    
    参数:
    - memory_id: 归档记忆ID（必填）
    - user_id: 用户ID（必填）
    
    注意事项：
    - 恢复后的记忆会重新出现在活跃记忆列表中
    - 衰退分数会重置为初始值
    """
    try:
        # 验证用户
        user = db.query(User).filter(User.user_id == request.user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")
        
        # 恢复记忆
        success = restore_archived_memory(db, request.memory_id, user.id)
        
        if not success:
            raise HTTPException(status_code=404, detail="归档记忆不存在")
        
        return {
            "success": True,
            "message": "记忆已从归档表恢复到活跃表",
            "memory_id": request.memory_id,
            "details": {
                "from_table": "archived_memories",
                "to_table": "memories",
                "new_decay_score": 1.0
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"恢复归档记忆失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"恢复失败: {str(e)}")


@router.delete("/{memory_id}")
async def delete_archived_memory(
    memory_id: str,
    user_id: str = Query(..., description="用户ID"),
    db: Session = Depends(get_db)
):
    """
    永久删除归档记忆。
    
    功能说明：
    - 从归档表中永久删除记忆
    - 此操作不可恢复
    - 建议在删除前先确认
    
    参数:
    - memory_id: 归档记忆ID（必填）
    - user_id: 用户ID（查询参数，必填）
    
    警告：
    - 此操作不可恢复！
    - 删除前请确认不再需要该记忆
    """
    try:
        # 验证用户
        user = db.query(User).filter(User.user_id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")
        
        # 查找归档记忆
        archived_memory = db.query(ArchivedMemory).filter(
            ArchivedMemory.id == memory_id,
            ArchivedMemory.user_id == user.id
        ).first()
        
        if not archived_memory:
            raise HTTPException(status_code=404, detail="归档记忆不存在")
        
        # 永久删除
        db.delete(archived_memory)
        db.commit()
        
        logger.info(f"归档记忆 {memory_id} 已永久删除")
        
        return {
            "success": True,
            "message": "归档记忆已永久删除",
            "memory_id": memory_id,
            "warning": "此操作不可恢复"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除归档记忆失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"删除失败: {str(e)}")


