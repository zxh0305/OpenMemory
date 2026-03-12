# OpenMemory 详细使用指南

## 📖 目录

1. [项目简介](#项目简介)
2. [快速开始](#快速开始)
3. [详细配置](#详细配置)
4. [MCP工具使用](#mcp工具使用)
5. [API接口说明](#api接口说明)
6. [存储与性能](#存储与性能)
7. [常见问题](#常见问题)
8. [优化建议](#优化建议)

---

## 项目简介

OpenMemory 是一个为 LLM 提供持久化记忆能力的开源解决方案，支持：

- ✅ **多用户隔离**: 每个用户拥有独立的记忆空间
- ✅ **MCP协议**: 支持 Claude、Cursor、Cline 等多种AI客户端
- ✅ **语义搜索**: 基于向量数据库的智能检索
- ✅ **自动分类**: AI自动为记忆分配分类标签
- ✅ **Web管理**: 可视化的记忆管理界面

---

## 快速开始

### 1. 环境准备

**必需软件**:
- Docker Desktop (Windows/Mac) 或 Docker Engine (Linux)
- Docker Compose

**系统要求**:
- 内存: 最少 2GB，推荐 4GB+
- 磁盘: 最少 5GB 可用空间
- 网络: 需要访问 OpenAI API 或兼容的API服务

### 2. 一键启动

```bash
# 1. 克隆项目
git clone <repository-url>
cd openmemory

# 2. 配置环境变量
cp api/.env.example api/.env
cp ui/.env.example ui/.env

# 3. 编辑 api/.env，设置你的 API 密钥
# 必需配置:
OPENAI_API_KEY=sk-your-api-key-here

# 4. 启动所有服务
docker compose up -d

# 5. 查看日志确认启动成功
docker compose logs -f
```

**服务地址**:
- 前端界面: http://localhost:3000
- API文档: http://localhost:8765/docs
- Qdrant管理: http://localhost:6333/dashboard

### 3. 验证安装

访问 http://localhost:4000，你应该能看到 OpenMemory 的主界面。

---

## 详细配置

### API 配置 (api/.env)

#### 基础配置

```env
# ========== 必需配置 ==========

# OpenAI API 密钥 (必需)
OPENAI_API_KEY=sk-your-api-key-here

# ========== 可选配置 ==========

# 默认用户ID (可选，默认: user)
USER=my_user_id

# 是否在启动时创建默认用户 (可选，默认: false)
CREATE_DEFAULT_USER=true

# 数据库配置 (可选，默认使用 SQLite)
# SQLite (默认):
DATABASE_URL=sqlite:///./openmemory.db

# PostgreSQL (生产环境推荐):
# DATABASE_URL=postgresql://username:password@localhost:5432/openmemory

# MySQL:
# DATABASE_URL=mysql+pymysql://username:password@localhost:3306/openmemory
```

#### LLM 配置

```env
# LLM 提供商 (可选，默认: openai)
# 可选值: openai, openai_structured, azure, ollama
OPENAI_PROVIDER=openai

# LLM API 端点 (可选，默认: https://api.openai.com/v1)
OPENAI_BASE_URL=https://api.openai.com/v1

# LLM 模型 (可选，默认: gpt-4o-mini)
OPENAI_MODEL=gpt-4o-mini
```

#### 嵌入模型配置

```env
# 嵌入模型 API 端点 (可选)
OPENAI_EMBEDDING_MODEL_BASE_URL=https://api.openai.com/v1

# 嵌入模型 API 密钥 (可选，默认使用 OPENAI_API_KEY)
OPENAI_EMBEDDING_MODEL_API_KEY=sk-your-embedding-key

# 嵌入模型名称 (可选，默认: text-embedding-3-small)
OPENAI_EMBEDDING_MODEL=text-embedding-3-small

# 嵌入向量维度 (可选，默认: 1536)
# text-embedding-3-small: 1536
# text-embedding-3-large: 3072
# text-embedding-ada-002: 1536
OPENAI_EMBEDDING_MODEL_DIMS=1536
```

#### 分类功能配置

```env
# 分类功能 API 端点 (可选)
CATEGORIZATION_OPENAI_BASE_URL=https://api.openai.com/v1

# 分类功能 API 密钥 (可选)
CATEGORIZATION_OPENAI_API_KEY=sk-your-categorization-key

# 分类功能模型 (可选，默认: gpt-4o-mini)
CATEGORIZATION_OPENAI_MODEL=gpt-4o-mini
```

### UI 配置 (ui/.env)

```env
# API 服务地址 (必需)
NEXT_PUBLIC_API_URL=http://localhost:8765

# 默认用户 ID (可选)
NEXT_PUBLIC_USER_ID=user
```

### 使用国内API服务

如果你使用国内的 API 服务(如硅基流动、DeepSeek等)，可以这样配置:

```env
# 硅基流动示例
OPENAI_API_KEY=sk-your-siliconflow-key
OPENAI_PROVIDER=openai_structured
OPENAI_BASE_URL=https://api.siliconflow.cn/v1
OPENAI_MODEL=deepseek-ai/DeepSeek-R1

# 嵌入模型也使用硅基流动
OPENAI_EMBEDDING_MODEL_API_KEY=sk-your-siliconflow-key
OPENAI_EMBEDDING_MODEL_BASE_URL=https://api.siliconflow.cn/v1
OPENAI_EMBEDDING_MODEL=BAAI/bge-large-zh-v1.5
OPENAI_EMBEDDING_MODEL_DIMS=1024
```

---

## MCP工具使用

### 什么是 MCP?

MCP (Model Context Protocol) 是一个标准协议，允许 AI 客户端访问外部工具和资源。OpenMemory 实现了 MCP 服务器，让 AI 可以直接读写记忆。

### 支持的客户端

- **Claude Desktop** - Anthropic 官方桌面应用
- **Cursor** - AI 代码编辑器
- **Cline** - VS Code 扩展
- **Roo Cline** - Cline 的增强版
- **Windsurf** - AI 编程助手
- **Witsy** - AI 助手工具
- **Enconvo** - macOS AI 助手

### 配置步骤

#### 方法一: 自动安装 (推荐)

1. 启动 OpenMemory 服务
2. 访问 http://localhost:3000
3. 在首页找到 "Install OpenMemory" 区域
4. 选择你的客户端标签页
5. 复制显示的安装命令
6. 在终端中运行该命令

**示例 - Claude Desktop**:
```bash
npx install-mcp i http://localhost:8765/mcp/claude/sse/user --client claude
```

#### 方法二: 手动配置

如果你的客户端支持直接配置 MCP 服务器 URL:

```
http://localhost:8765/mcp/openmemory/sse/{your_user_id}
```

将 `{your_user_id}` 替换为你的用户 ID (默认是 `user`)

### MCP 提供的工具

#### 1. add_memories - 添加记忆

**功能**: 保存新的记忆内容

**使用场景**:
```
用户: "记住我喜欢喝咖啡，不喜欢茶"
AI: [自动调用 add_memories 保存]
```

**参数**:
- `messages`: 要保存的记忆内容(字符串或列表)
- `user_id`: 用户ID
- `app_id`: 应用ID (可选)

#### 2. search_memory - 搜索记忆

**功能**: 根据查询内容搜索相关记忆

**使用场景**:
```
用户: "我喜欢喝什么饮料?"
AI: [调用 search_memory 查找] → "根据我的记忆，你喜欢喝咖啡"
```

**参数**:
- `query`: 搜索查询
- `user_id`: 用户ID
- `app_id`: 应用ID (可选)
- `limit`: 返回结果数量 (默认: 10)

#### 3. list_memories - 列出所有记忆

**功能**: 获取用户的所有记忆列表

**使用场景**:
```
用户: "显示我所有的记忆"
AI: [调用 list_memories 获取列表]
```

**参数**:
- `user_id`: 用户ID
- `app_id`: 应用ID (可选)

#### 4. delete_all_memories - 删除所有记忆

**功能**: 清空用户的所有记忆

**使用场景**:
```
用户: "清除我的所有记忆"
AI: [调用 delete_all_memories 清空]
```

**参数**:
- `user_id`: 用户ID
- `app_id`: 应用ID (可选)

### 多用户使用

每个用户有独立的 MCP 端点:

```bash
# 用户 alice
http://localhost:8765/mcp/openmemory/sse/alice

# 用户 bob  
http://localhost:8765/mcp/openmemory/sse/bob
```

在 UI 界面右上角可以切换用户，MCP 链接会自动更新。

---

## API接口说明

### 认证

所有 API 请求都需要提供 `user_id` 参数来标识用户。

### 核心接口

#### 1. 创建记忆

```http
POST /api/v1/memories
Content-Type: application/json

{
  "user_id": "user",
  "app_id": "app_id",
  "content": "记忆内容"
}
```

#### 2. 搜索记忆

```http
GET /api/v1/memories/search?user_id=user&query=搜索内容&limit=10
```

#### 3. 列出记忆

```http
GET /api/v1/memories?user_id=user&page=1&page_size=20
```

**过滤参数**:
- `state`: 记忆状态 (active/paused/archived/deleted)
- `app_id`: 应用ID
- `category`: 分类名称
- `date_from`: 开始日期 (YYYY-MM-DD)
- `date_to`: 结束日期 (YYYY-MM-DD)

#### 4. 获取记忆详情

```http
GET /api/v1/memories/{memory_id}?user_id=user
```

#### 5. 更新记忆

```http
PUT /api/v1/memories/{memory_id}
Content-Type: application/json

{
  "user_id": "user",
  "content": "更新后的内容",
  "state": "active"
}
```

#### 6. 删除记忆

```http
DELETE /api/v1/memories/{memory_id}?user_id=user
```

### 应用管理

#### 列出应用

```http
GET /api/v1/apps?user_id=user
```

#### 获取应用详情

```http
GET /api/v1/apps/{app_id}?user_id=user
```

### 统计信息

```http
GET /api/v1/stats?user_id=user
```

返回:
- 总记忆数
- 总应用数
- 应用列表

---

## 存储与性能

### 存储容量

#### 记忆数量限制

**无硬性上限** - 实际限制取决于:
- 磁盘空间
- 数据库类型 (SQLite/PostgreSQL/MySQL)
- 向量数据库容量

#### 单条记忆大小

- **推荐**: 每条记忆 < 10,000 字符
- **最大**: 理论上无限制 (使用 Text 类型)

### 内存占用

#### 最小配置 (个人使用)

```
总计: ~2 GB RAM
├── Qdrant: ~500 MB
├── API: ~300 MB
└── UI: ~200 MB

适用于: < 10,000 条记忆
```

#### 推荐配置 (团队使用)

```
总计: ~4 GB RAM
├── Qdrant: ~1 GB
├── API: ~1 GB
└── UI: ~500 MB

适用于: 10,000 - 100,000 条记忆
```

#### 生产配置 (企业使用)

```
总计: ~8+ GB RAM
├── Qdrant: ~4-6 GB
├── API: ~2 GB
└── UI: ~1 GB

适用于: > 100,000 条记忆
```

### 向量存储计算

**公式**: 内存 ≈ 记忆数 × 向量维度 × 4字节 × 1.5

**示例** (text-embedding-3-small, 1536维):
- 1,000 条: ~9 MB
- 10,000 条: ~92 MB
- 100,000 条: ~920 MB
- 1,000,000 条: ~9.2 GB

### 性能优化

#### 1. 使用 PostgreSQL

```env
# 替代默认的 SQLite
DATABASE_URL=postgresql://user:pass@localhost:5432/openmemory
```

**优势**:
- 更好的并发性能
- 支持更大数据量
- 更快的查询速度

#### 2. 降低向量维度

```env
# 使用更小的向量维度
OPENAI_EMBEDDING_MODEL_DIMS=512  # 默认 1536
```

**效果**: 内存占用减少 66%，搜索速度提升

#### 3. 限制 Qdrant 内存

在 `docker-compose.yml` 中:

```yaml
mem0_store:
  deploy:
    resources:
      limits:
        memory: 4G
      reservations:
        memory: 1G
```

#### 4. 调整工作进程数

```yaml
# docker-compose.yml
command: >
  uvicorn main:app --workers 4
  # workers = CPU核心数 × 2
```

---

## 常见问题

### Q1: 服务启动失败

**检查步骤**:
```bash
# 1. 查看日志
docker compose logs -f

# 2. 检查端口占用
netstat -ano | findstr "8765"  # Windows
lsof -i :8765                   # Linux/Mac

# 3. 重新构建
docker compose down -v
docker compose build --no-cache
docker compose up -d
```

### Q2: MCP 连接失败

**解决方案**:
1. 确认服务已启动: `docker compose ps`
2. 检查 API 可访问: 访问 http://localhost:8765/docs
3. 验证用户ID正确
4. 查看客户端日志

### Q3: 搜索结果不准确

**优化方法**:
1. 使用更好的嵌入模型:
   ```env
   OPENAI_EMBEDDING_MODEL=text-embedding-3-large
   OPENAI_EMBEDDING_MODEL_DIMS=3072
   ```

2. 增加搜索结果数量:
   ```python
   search_memory(query="...", limit=20)
   ```

3. 使用更精确的查询词

### Q4: 记忆无法保存

**检查项**:
1. API 密钥是否正确
2. 查看 API 日志: `docker compose logs -f openmemory-api`
3. 确认 Qdrant 运行正常
4. 检查磁盘空间

### Q5: 如何备份数据

```bash
# 备份 SQLite 数据库
cp api/openmemory.db api/openmemory.db.backup

# 备份 Qdrant 数据
tar -czf mem0_storage_backup.tar.gz mem0_storage/

# 备份 PostgreSQL (如果使用)
pg_dump openmemory > openmemory_backup.sql
```

### Q6: 如何迁移到生产环境

1. **使用 PostgreSQL**:
   ```env
   DATABASE_URL=postgresql://user:pass@prod-db:5432/openmemory
   ```

2. **配置反向代理** (Nginx):
   ```nginx
   server {
       listen 80;
       server_name your-domain.com;
       
       location / {
           proxy_pass http://localhost:3000;
       }
       
       location /api {
           proxy_pass http://localhost:8765;
       }
   }
   ```

3. **启用 HTTPS**:
   使用 Let's Encrypt 或其他 SSL 证书

4. **设置环境变量**:
   不要在 `.env` 文件中存储生产密钥，使用环境变量或密钥管理服务

---

## 优化建议

### 代码优化

#### 1. 已完成的优化

- ✅ **UUID转换逻辑**: 将重复的 150+ 行代码精简为 30 行
- ✅ **统一事件处理**: 使用循环注册事件监听器

#### 2. 可选的优化

**移除未使用的表** (如果不需要):
- `AccessControl` - 访问控制表
- `ArchivePolicy` - 归档策略表  
- `MemoryStatusHistory` - 状态历史表

**简化分类功能** (如果不需要自动分类):
- 可以禁用自动分类以节省 API 调用

### 配置优化

#### 最小化配置 (仅核心功能)

```env
# api/.env - 最小配置
OPENAI_API_KEY=sk-xxx
USER=user
```

```env
# ui/.env - 最小配置
NEXT_PUBLIC_API_URL=http://localhost:8765
NEXT_PUBLIC_USER_ID=user
```

#### 性能优化配置

```env
# 使用更快的模型
OPENAI_MODEL=gpt-4o-mini
CATEGORIZATION_OPENAI_MODEL=gpt-4o-mini

# 使用更小的嵌入维度
OPENAI_EMBEDDING_MODEL_DIMS=512

# 使用 PostgreSQL
DATABASE_URL=postgresql://user:pass@localhost:5432/openmemory
```

### 部署优化

#### Docker Compose 优化

```yaml
# 限制资源使用
services:
  openmemory-api:
    deploy:
      resources:
        limits:
          cpus: '2'
          memory: 2G
        reservations:
          cpus: '1'
          memory: 1G
```

#### 监控建议

```bash
# 实时监控资源使用
docker stats

# 查看磁盘使用
du -sh mem0_storage/
ls -lh api/openmemory.db

# 查看日志
docker compose logs -f --tail=100
```

---

## 总结

OpenMemory 是一个功能强大且灵活的记忆管理系统:

- 🚀 **快速开始**: 5分钟内完成部署
- 🔧 **灵活配置**: 支持多种 LLM 和数据库
- 📊 **可扩展**: 从个人使用到企业级部署
- 🛠️ **易维护**: Docker 容器化，一键部署

**推荐使用场景**:
- AI 助手的长期记忆
- 个人知识管理
- 团队知识库
- 客户关系管理

**获取帮助**:
- 查看 API 文档: http://localhost:8765/docs
- 查看项目 README: README.md
- 提交 Issue: GitHub Issues

---

**最后更新**: 2025-01-12
**版本**: 1.0.0