import enum
import uuid
import datetime
import sqlalchemy as sa
from sqlalchemy import (
    Column,
    String,
    Boolean,
    ForeignKey,
    Enum,
    Table,
    DateTime,
    JSON,
    Integer,
    UUID,
    Index,
    Text,
    event,
)
from sqlalchemy.orm import relationship
from app.database import Base
from sqlalchemy.orm import Session
from app.utils.categorization import get_categories_for_memory


def get_current_utc_time():
    """Get current UTC time"""
    return datetime.datetime.now(datetime.UTC)


def generate_uuid_without_hyphens():
    """Generate UUID without hyphens"""
    return uuid.uuid4().hex


class MemoryState(enum.Enum):
    active = "active"
    paused = "paused"
    archived = "archived"
    deleted = "deleted"


class User(Base):
    __tablename__ = "users"
    id = Column(
        String(32), primary_key=True, default=generate_uuid_without_hyphens
    )  # Modified: specify length, UUID without hyphens
    user_id = Column(
        String(255), nullable=False, unique=True, index=True
    )  # Modified: specify length
    name = Column(String(255), nullable=True, index=True)  # Modified: specify length
    email = Column(
        String(255), unique=True, nullable=True, index=True
    )  # Modified: specify length
    metadata_ = Column("metadata", JSON, default=dict)
    created_at = Column(DateTime, default=get_current_utc_time, index=True)
    updated_at = Column(
        DateTime, default=get_current_utc_time, onupdate=get_current_utc_time
    )

    apps = relationship("App", back_populates="owner")
    memories = relationship("Memory", back_populates="user")


class App(Base):
    __tablename__ = "apps"
    id = Column(
        String(32), primary_key=True, default=generate_uuid_without_hyphens
    )  # Modified
    owner_id = Column(
        String(32), ForeignKey("users.id"), nullable=False, index=True
    )  # Modified
    name = Column(String(255), nullable=False, index=True)  # Modified: specify length
    description = Column(Text)  # Changed to Text instead of String(1000)
    metadata_ = Column("metadata", JSON, default=dict)
    is_active = Column(Boolean, default=True, index=True)
    created_at = Column(DateTime, default=get_current_utc_time, index=True)
    updated_at = Column(
        DateTime, default=get_current_utc_time, onupdate=get_current_utc_time
    )

    owner = relationship("User", back_populates="apps")
    memories = relationship("Memory", back_populates="app")

    __table_args__ = (
        sa.UniqueConstraint("owner_id", "name", name="idx_app_owner_name"),
    )


class Config(Base):
    __tablename__ = "configs"
    id = Column(
        String(32), primary_key=True, default=generate_uuid_without_hyphens
    )  # Modified
    key = Column(
        String(255), unique=True, nullable=False, index=True
    )  # Modified: specify length
    value = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=get_current_utc_time)
    updated_at = Column(
        DateTime, default=get_current_utc_time, onupdate=get_current_utc_time
    )


class Memory(Base):
    __tablename__ = "memories"
    id = Column(String(32), primary_key=True, default=generate_uuid_without_hyphens)
    user_id = Column(String(32), ForeignKey("users.id"), nullable=False, index=True)
    app_id = Column(String(32), ForeignKey("apps.id"), nullable=False, index=True)
    content = Column(Text, nullable=False)  # Modified: use Text type
    vector = Column(Text)  # Modified: use Text type
    metadata_ = Column("metadata", JSON, default=dict)
    state = Column(Enum(MemoryState), default=MemoryState.active, index=True)
    created_at = Column(DateTime, default=get_current_utc_time, index=True)
    updated_at = Column(
        DateTime, default=get_current_utc_time, onupdate=get_current_utc_time
    )
    archived_at = Column(DateTime, nullable=True, index=True)
    deleted_at = Column(DateTime, nullable=True, index=True)
    
    # 记忆衰退相关字段
    decay_score = Column(sa.Float, default=1.0, index=True, nullable=False)  # 衰退分数 0-1
    last_accessed_at = Column(DateTime, nullable=True, index=True)  # 最后访问时间
    access_count = Column(Integer, default=0, nullable=False)  # 访问次数
    importance_score = Column(sa.Float, default=0.5, nullable=False)  # 重要性分数 0-1

    user = relationship("User", back_populates="memories")
    app = relationship("App", back_populates="memories")
    categories = relationship(
        "Category", secondary="memory_categories", back_populates="memories"
    )

    __table_args__ = (
        Index("idx_memory_user_state", "user_id", "state"),
        Index("idx_memory_app_state", "app_id", "state"),
        Index("idx_memory_user_app", "user_id", "app_id"),
    )


class RawMemoryInput(Base):
    __tablename__ = "raw_memory_inputs"

    id = Column(String(32), primary_key=True, default=generate_uuid_without_hyphens)
    user_id = Column(String(32), ForeignKey("users.id"), nullable=False, index=True)
    app_id = Column(String(32), ForeignKey("apps.id"), nullable=False, index=True)
    original_text = Column(Text, nullable=False)
    summary = Column(Text, nullable=True)
    extracted_facts = Column(JSON, default=list)
    metadata_ = Column("metadata", JSON, default=dict)
    infer = Column(Boolean, default=False, nullable=False)
    processing_status = Column(String(50), default="pending", nullable=False, index=True)
    error_reason = Column(Text, nullable=True)
    processed_at = Column(DateTime, nullable=True, index=True)
    created_at = Column(DateTime, default=get_current_utc_time, index=True)
    updated_at = Column(
        DateTime, default=get_current_utc_time, onupdate=get_current_utc_time
    )

    user = relationship("User", foreign_keys=[user_id])
    app = relationship("App", foreign_keys=[app_id])

    __table_args__ = (
        Index("idx_raw_memory_user_time", "user_id", "created_at"),
        Index("idx_raw_memory_app_time", "app_id", "created_at"),
    )

class ArchivedMemory(Base):
    """
    归档记忆表 - 独立存储衰退后的记忆
    
    当记忆的衰退分数低于阈值时，记忆会从 memories 表移动到此表
    归档的记忆可以随时恢复到 memories 表
    """
    __tablename__ = "archived_memories"
    
    # 基本字段（与 Memory 表保持一致）
    id = Column(String(32), primary_key=True)  # 保持原记忆ID
    user_id = Column(String(32), ForeignKey("users.id"), nullable=False, index=True)
    app_id = Column(String(32), ForeignKey("apps.id"), nullable=False, index=True)
    content = Column(Text, nullable=False)
    vector = Column(Text)
    metadata_ = Column("metadata", JSON, default=dict)
    
    # 原始时间信息
    created_at = Column(DateTime, nullable=False, index=True)  # 原始创建时间
    updated_at = Column(DateTime, nullable=False)  # 原始更新时间
    
    # 归档相关信息
    archived_at = Column(DateTime, default=get_current_utc_time, nullable=False, index=True)  # 归档时间
    archived_from_state = Column(Enum(MemoryState), nullable=False)  # 归档前的状态
    
    # 衰退信息（归档时的快照）
    decay_score_at_archive = Column(sa.Float, nullable=False)  # 归档时的衰退分数
    last_accessed_at = Column(DateTime, nullable=True)  # 最后访问时间
    access_count = Column(Integer, default=0, nullable=False)  # 访问次数
    importance_score = Column(sa.Float, default=0.5, nullable=False)  # 重要性分数
    
    # 分类信息（JSON存储，因为不需要关联查询）
    categories_snapshot = Column(JSON, default=list)  # 归档时的分类快照
    
    # 关系
    user = relationship("User", foreign_keys=[user_id])
    app = relationship("App", foreign_keys=[app_id])
    
    __table_args__ = (
        Index("idx_archived_user_time", "user_id", "archived_at"),
        Index("idx_archived_app_time", "app_id", "archived_at"),
        Index("idx_archived_decay", "decay_score_at_archive"),
        Index("idx_archived_created", "created_at"),
    )



class Category(Base):
    __tablename__ = "categories"
    id = Column(
        String(32), primary_key=True, default=generate_uuid_without_hyphens
    )  # Modified
    name = Column(
        String(255), unique=True, nullable=False, index=True
    )  # Modified: specify length
    description = Column(Text)  # Changed to Text instead of String(1000)
    created_at = Column(
        DateTime, default=datetime.datetime.now(datetime.UTC), index=True
    )
    updated_at = Column(
        DateTime, default=get_current_utc_time, onupdate=get_current_utc_time
    )

    memories = relationship(
        "Memory", secondary="memory_categories", back_populates="categories"
    )


memory_categories = Table(
    "memory_categories",
    Base.metadata,
    Column(
        "memory_id", String(32), ForeignKey("memories.id"), primary_key=True, index=True
    ),  # Modified
    Column(
        "category_id",
        String(32),
        ForeignKey("categories.id"),
        primary_key=True,
        index=True,
    ),  # Modified
    Index("idx_memory_category", "memory_id", "category_id"),
)


class AccessControl(Base):
    __tablename__ = "access_controls"
    id = Column(
        String(32), primary_key=True, default=generate_uuid_without_hyphens
    )  # Modified
    subject_type = Column(
        String(100), nullable=False, index=True
    )  # Modified: specify length
    subject_id = Column(String(32), nullable=True, index=True)  # Modified
    object_type = Column(
        String(100), nullable=False, index=True
    )  # Modified: specify length
    object_id = Column(String(32), nullable=True, index=True)  # Modified
    effect = Column(String(50), nullable=False, index=True)  # Modified: specify length
    created_at = Column(DateTime, default=get_current_utc_time, index=True)

    __table_args__ = (
        Index("idx_access_subject", "subject_type", "subject_id"),
        Index("idx_access_object", "object_type", "object_id"),
    )


class ArchivePolicy(Base):
    __tablename__ = "archive_policies"
    id = Column(
        String(32), primary_key=True, default=generate_uuid_without_hyphens
    )  # Modified
    criteria_type = Column(
        String(100), nullable=False, index=True
    )  # Modified: specify length
    criteria_id = Column(String(32), nullable=True, index=True)  # Modified
    days_to_archive = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=get_current_utc_time, index=True)

    __table_args__ = (Index("idx_policy_criteria", "criteria_type", "criteria_id"),)


class MemoryStatusHistory(Base):
    __tablename__ = "memory_status_history"
    id = Column(
        String(32), primary_key=True, default=generate_uuid_without_hyphens
    )  # Modified
    memory_id = Column(
        String(32), ForeignKey("memories.id"), nullable=False, index=True
    )  # Modified
    changed_by = Column(
        String(32), ForeignKey("users.id"), nullable=False, index=True
    )  # Modified
    old_state = Column(Enum(MemoryState), nullable=False, index=True)
    new_state = Column(Enum(MemoryState), nullable=False, index=True)
    changed_at = Column(DateTime, default=get_current_utc_time, index=True)

    __table_args__ = (
        Index("idx_history_memory_state", "memory_id", "new_state"),
        Index("idx_history_user_time", "changed_by", "changed_at"),
    )


class MemoryAccessLog(Base):
    __tablename__ = "memory_access_logs"
    id = Column(
        String(32), primary_key=True, default=generate_uuid_without_hyphens
    )  # Modified
    memory_id = Column(
        String(32), ForeignKey("memories.id"), nullable=False, index=True
    )  # Modified
    app_id = Column(
        String(32), ForeignKey("apps.id"), nullable=False, index=True
    )  # Modified
    accessed_at = Column(DateTime, default=get_current_utc_time, index=True)
    access_type = Column(
        String(100), nullable=False, index=True
    )  # Modified: specify length
    metadata_ = Column("metadata", JSON, default=dict)

    __table_args__ = (
        Index("idx_access_memory_time", "memory_id", "accessed_at"),
        Index("idx_access_app_time", "app_id", "accessed_at"),
    )


def categorize_memory(memory: Memory, db: Session) -> None:
    """Categorize a memory using OpenAI and store the categories in the database."""
    try:
        # Get categories from OpenAI
        categories = get_categories_for_memory(memory.content)

        # Get or create categories in the database
        for category_name in categories:
            category = db.query(Category).filter(Category.name == category_name).first()
            if not category:
                category = Category(
                    name=category_name,
                    description=f"Automatically created category for {category_name}",
                )
                db.add(category)
                db.flush()  # Flush to get the category ID

            # Check if the memory-category association already exists
            existing = db.execute(
                memory_categories.select().where(
                    (memory_categories.c.memory_id == memory.id)
                    & (memory_categories.c.category_id == category.id)
                )
            ).first()

            if not existing:
                # Create the association
                db.execute(
                    memory_categories.insert().values(
                        memory_id=memory.id, category_id=category.id
                    )
                )

        db.commit()
    except Exception as e:
        db.rollback()
        print(f"Error categorizing memory: {e}")


@event.listens_for(Memory, "after_insert")
def after_memory_insert(mapper, connection, target):
    """Trigger categorization after a memory is inserted."""
    db = Session(bind=connection)
    categorize_memory(target, db)
    db.close()


@event.listens_for(Memory, "after_update")
def after_memory_update(mapper, connection, target):
    """Trigger categorization after a memory is updated."""
    db = Session(bind=connection)
    categorize_memory(target, db)
    db.close()
# UUID automatic conversion - unified handler
def _convert_uuid_field(value):
    """Convert UUID to string without hyphens"""
    if hasattr(value, "hex"):
        return value.hex
    elif isinstance(value, str) and "-" in value:
        return value.replace("-", "")
    return value


def _convert_model_uuids(target, *fields):
    """Convert UUID fields for a model"""
    for field in fields:
        value = getattr(target, field, None)
        if value:
            setattr(target, field, _convert_uuid_field(value))


# Register UUID conversion for all models
for model_class, fields in [
    (User, ["id"]),
    (App, ["id", "owner_id"]),
    (Memory, ["id", "user_id", "app_id"]),
    (RawMemoryInput, ["id", "user_id", "app_id"]),
    (ArchivedMemory, ["id", "user_id", "app_id"]),
    (Category, ["id"]),
    (Config, ["id"]),
    (AccessControl, ["id", "subject_id", "object_id"]),
    (ArchivePolicy, ["id", "criteria_id"]),
    (MemoryStatusHistory, ["id", "memory_id", "changed_by"]),
    (MemoryAccessLog, ["id", "memory_id", "app_id"]),
]:
    event.listen(
        model_class,
        "before_insert",
        lambda mapper, connection, target, f=fields: _convert_model_uuids(target, *f)
    )
