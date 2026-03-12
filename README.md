# OpenMemory

OpenMemory 是一个为 LLM 提供记忆能力的开源本地化解决方案，支持个性化记忆存储、查询和管理。

## 🚀 快速开始

### ✅ 前提条件

- [Docker](https://www.docker.com/)
- [Docker Compose](https://docs.docker.com/compose/)
- Windows（WSL）或 Unix/Linux
- 设置好 API 密钥

---

### 🔧 初始化环境变量

1. **创建 API 环境变量文件**

在 `api/` 目录下创建 `.env` 文件（参考 `api/.env.example`）：

```bash
# 复制示例文件（如果存在）
cp api/.env.example api/.env
```

编辑 `api/.env`，设置你的配置：

```env
# 数据库配置
DATABASE_URL=sqlite:///./openmemory.db

# 用户配置（可选，系统支持多用户，用户会在首次使用时自动创建）
USER=default_user
# 是否在启动时创建默认用户（可选，默认为 false）
# 设置为 true 会在启动时自动创建一个默认用户，方便首次使用
CREATE_DEFAULT_USER=false

# OpenAI API 配置
OPENAI_API_KEY=your_openai_api_key_here
OPENAI_PROVIDER=openai
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o-mini

# OpenAI Embedding 配置
OPENAI_EMBEDDING_MODEL_API_KEY=${OPENAI_API_KEY}
OPENAI_EMBEDDING_MODEL_BASE_URL=https://api.openai.com/v1
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
```

2. **创建 UI 环境变量文件**

在 `ui/` 目录下创建 `.env` 文件（参考 `ui/.env.example`）：

```bash
# 复制示例文件（如果存在）
cp ui/.env.example ui/.env
```

编辑 `ui/.env`：

```env
# API 地址
NEXT_PUBLIC_API_URL=http://localhost:8765

# 默认用户 ID（可选）
NEXT_PUBLIC_USER_ID=default_user
```

---

### ▶️ 启动服务

使用 Docker Compose 一键启动所有服务：

```bash
docker compose up -d
```

这将启动以下组件：
- **Qdrant Vector Store**: `http://localhost:6333`
- **MCP Server (API)**: `http://localhost:8765`
- **前端 UI**: `http://localhost:4000`

> ✅ 你可以在 `http://localhost:8765/docs` 查看 API 文档。

---

### 👥 多用户支持

OpenMemory 完全支持多用户，每个用户拥有独立的记忆数据：

- **自动创建用户**：当创建记忆或使用 API 时，如果用户不存在会自动创建
- **切换用户**：在 UI 界面可以通过右上角的用户切换器切换不同的用户
- **用户隔离**：每个用户的记忆数据完全隔离，互不干扰
- **默认用户**：如果设置了 `CREATE_DEFAULT_USER=true`，系统会在启动时创建一个默认用户（可选）

> 💡 提示：即使不创建默认用户，系统也能正常工作。用户会在首次使用时自动创建。

---

### ✅ MCP 配置说明

- MCP 的 SSE 接口：`http://localhost:8765/mcp/openmemory/sse/{user_id}`
- MCP 前端界面：在 `http://localhost:4000` 可以访问 MCP 的前端界面
  - 在那里，你可以查看各个 MCP 客户端的配置命令
  - 并且可以查看 Memories 存储的数据。

---

### ⏹️ 停止并清理服务

**停止服务（保留数据）**：
```bash
docker compose down
```

**停止服务并清理数据**：
```bash
docker compose down -v
```

> 💡 `-v` 参数会删除所有卷，包括数据库和 Qdrant 存储数据，请谨慎使用。

---

## 📝 其他常用命令

| 命令 | 描述 |
|------|------|
| `docker compose build` | 重新构建 Docker 镜像 |
| `docker compose up -d` | 后台启动所有服务 |
| `docker compose logs -f` | 查看所有服务日志（实时） |
| `docker compose logs -f openmemory-api` | 查看 API 服务日志 |
| `docker compose logs -f openmemory-ui` | 查看 UI 服务日志 |
| `docker compose exec openmemory-api bash` | 进入 API 容器进行调试 |
| `docker compose exec openmemory-api alembic upgrade head` | 手动运行数据库迁移 |

---

## 🧠 小贴士

- 如果修改了代码，可以使用 `docker compose restart` 重启服务
- 如果需要完全重新构建，使用 `docker compose build --no-cache && docker compose up -d`
- 数据库文件存储在 `api/openmemory.db`，Qdrant 数据存储在 `mem0_storage/` 目录
- 所有服务都配置了健康检查，确保服务正常启动后才启动依赖服务

---

## ❤️ 欢迎贡献

我们欢迎任何形式的贡献：文档优化、功能改进、测试反馈等。只需 Fork 并提交 PR！

- Fork 项目
- 创建新分支：`git checkout -b feature/your-feature-name`
- 提交更改：`git commit -m '描述你的改动'`
- 推送到远程：`git push origin feature/your-feature-name`
- 提交 Pull Request"# OpenMemory_change" 
