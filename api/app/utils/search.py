"""
多信号融合检索模块

融合三路搜索结果: Qdrant 向量搜索 + FTS5 BM25 关键词 + 实体匹配
使用 Reciprocal Rank Fusion (RRF) 算法合并排序。
"""

import logging
from typing import List, Optional, Dict, Any
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# RRF 常数（标准值）
_RRF_K = 60


def rrf_fuse(
    vector_results: List[Dict[str, Any]],
    bm25_results: List[Dict[str, Any]],
    entity_results: List[Dict[str, Any]],
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """
    Reciprocal Rank Fusion: 融合多路搜索结果。

    每路结果传入 (memory_id, rank) 对，RRF 公式:
        score(d) = Σ 1 / (k + rank_i(d))
    其中 k=60, rank_i(d) 是文档 d 在第 i 路中的排名。

    Args:
        vector_results: 向量搜索结果，每项含 memory_id
        bm25_results: BM25 搜索结果，每项含 memory_id
        entity_results: 实体匹配结果，每项含 memory_id
        limit: 返回结果数上限

    Returns:
        融合排序后的结果列表，每项含 memory_id、rrf_score、sources
    """
    # 构建排名映射
    fusion_scores: Dict[str, float] = {}
    result_details: Dict[str, Dict] = {}

    # 处理各路结果
    _process_ranked_list(vector_results, 'vector', fusion_scores, result_details)
    _process_ranked_list(bm25_results, 'bm25', fusion_scores, result_details)
    _process_ranked_list(entity_results, 'entity', fusion_scores, result_details)

    # 按 RRF 分数降序排列
    sorted_items = sorted(fusion_scores.items(), key=lambda x: x[1], reverse=True)

    # 构建返回结果
    results = []
    for memory_id, rrf_score in sorted_items[:limit]:
        detail = result_details.get(memory_id, {})
        results.append({
            'memory_id': memory_id,
            'rrf_score': round(rrf_score, 4),
            'vector_rank': detail.get('vector_rank'),
            'bm25_rank': detail.get('bm25_rank'),
            'bm25_score': detail.get('bm25_score'),
            'entity_rank': detail.get('entity_rank'),
            'entity_match_count': detail.get('entity_match_count'),
            'sources': detail.get('sources', []),
        })

    return results


def _process_ranked_list(
    items: List[Dict[str, Any]],
    source_name: str,
    fusion_scores: Dict[str, float],
    result_details: Dict[str, Dict],
):
    """处理一路搜索结果，累积 RRF 分数"""
    for rank, item in enumerate(items):
        memory_id = item.get('memory_id')
        if not memory_id:
            continue

        # RRF 累积
        fusion_scores[memory_id] = fusion_scores.get(memory_id, 0) + 1.0 / (_RRF_K + rank + 1)

        # 记录详情
        if memory_id not in result_details:
            result_details[memory_id] = {
                'vector_rank': None,
                'bm25_rank': None,
                'bm25_score': None,
                'entity_rank': None,
                'entity_match_count': None,
                'sources': [],
            }

        detail = result_details[memory_id]

        if source_name == 'vector':
            detail['vector_rank'] = rank + 1
        elif source_name == 'bm25':
            detail['bm25_rank'] = rank + 1
            detail['bm25_score'] = item.get('bm25_score')
        elif source_name == 'entity':
            detail['entity_rank'] = rank + 1
            detail['entity_match_count'] = item.get('entity_score')

        detail['sources'].append(source_name)


def hybrid_search(
    query: str,
    user_id: str,
    limit: int = 20,
    memory_client=None,
    db: Optional[Session] = None,
    accessible_memory_ids: Optional[set] = None,
) -> List[Dict[str, Any]]:
    """
    混合搜索入口：依次获取三路结果，RRF 融合后返回。

    Args:
        query: 搜索查询
        user_id: 用户 ID（可以是 User.id 或 User.user_id）
        limit: 返回结果数
        memory_client: Mem0 客户端实例（用于向量搜索）
        db: 数据库会话（可选）
        accessible_memory_ids: 可访问记忆 ID 集合（可选）

    Returns:
        list[dict]: 融合排序后的结果
    """
    # 导入（延迟导入避免循环依赖）
    from app.utils.fts import bm25_search
    from app.utils.entity_index import search_by_entity

    # 1. 向量搜索（Qdrant）
    vector_results = _vector_search(
        query, user_id, limit * 2,
        memory_client, accessible_memory_ids
    )

    # 2. BM25 搜索（FTS5）
    bm25_results = bm25_search(query, user_id, limit * 2)

    # 3. 实体匹配搜索
    entity_results = search_by_entity(query, user_id, limit * 2)

    logger.info(
        "hybrid_search: vector=%d, bm25=%d, entity=%d results for query=%s",
        len(vector_results), len(bm25_results), len(entity_results), query
    )

    # 4. RRF 融合
    fused = rrf_fuse(vector_results, bm25_results, entity_results, limit)

    return fused


def _vector_search(
    query: str,
    user_id: str,
    limit: int,
    memory_client,
    accessible_memory_ids: Optional[set] = None,
) -> List[Dict[str, Any]]:
    """Qdrant 向量搜索"""
    if not memory_client:
        return []

    try:
        from qdrant_client import models as qdrant_models
        import uuid

        # 生成查询的嵌入向量
        embeddings = memory_client.embedding_model.embed(query, "search")

        # Qdrant 中 user_id 以标准 UUID 格式存储
        # 尝试将 user_id 转为 UUID；如果失败则直接当字符串用
        try:
            uid_uuid = str(uuid.UUID(user_id))
        except (ValueError, AttributeError):
            uid_uuid = user_id

        conditions = [
            qdrant_models.FieldCondition(
                key="user_id",
                match=qdrant_models.MatchValue(value=uid_uuid)
            )
        ]
        filters = qdrant_models.Filter(must=conditions)

        hits = memory_client.vector_store.client.query_points(
            collection_name=memory_client.vector_store.collection_name,
            query=embeddings,
            query_filter=filters,
            limit=limit,
        )

        results = []
        for point in hits.points:
            # Qdrant ID 带连字符，转为无连字符格式
            qdrant_id = point.id
            if isinstance(qdrant_id, str):
                memory_id = qdrant_id.replace('-', '')
            else:
                memory_id = str(qdrant_id).replace('-', '')

            # 权限过滤
            if accessible_memory_ids is not None and memory_id not in accessible_memory_ids:
                continue

            results.append({
                'memory_id': memory_id,
                'vector_score': point.score,
                'content': point.payload.get('data', ''),
            })

        return results

    except Exception as e:
        logger.warning("Vector search failed: %s", e)
        return []
