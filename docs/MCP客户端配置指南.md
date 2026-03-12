# MCP客户端配置指南

## ✅ 服务器状态确认

根据测试结果，你的OpenMemory MCP服务器**已经正常运行**：

- ✅ MCP服务器运行正常 (http://localhost:8765)
- ✅ SSE端点可访问
- ✅ API连接成功
- ✅ 所有MCP工具可用

## 🔧 配置MCP客户端

### 方法1: Cursor编辑器配置

1. **打开Cursor设置**
   - 按 `Ctrl+Shift+P` (Windows/Linux) 或 `Cmd+Shift+P` (Mac)
   - 输入 "Preferences: Open User Settings (JSON)"
   - 或直接打开 `~/.cursor/config.json`

2. **添加MCP服务器配置**

```json
{
  "mcpServers": {
    "openmemory": {
      "url": "http://localhost:8765/mcp/cursor/sse/your_user_id",
      "transport": "sse"
    }
  }
}
```

3. **替换用户ID**
   - 将 `your_user_id` 替换为你的实际用户ID
   - 例如: `user123`, `admin`, `your_email@example.com` 等
   - 用户ID可以是任意字符串，用于区分不同用户的记忆

4. **重启Cursor**
   - 完全关闭Cursor
   - 重新打开Cursor
   - MCP连接会自动建立

### 方法2: Windsurf编辑器配置

在Windsurf设置中添加：

```json
{
  "mcp": {
    "servers": {
      "openmemory": {
        "url": "http://localhost:8765/mcp/windsurf/sse/your_user_id",
        "transport": "sse"
      }
    }
  }
}
```

**注意**: 将URL中的 `windsurf` 和 `your_user_id` 替换为实际值

### 方法3: 其他MCP客户端

对于其他支持MCP协议的客户端，使用以下通用配置：

```json
{
  "url": "http://localhost:8765/mcp/{client_name}/sse/{user_id}",
  "transport": "sse"
}
```

**参数说明**:
- `{client_name}`: 客户端名称 (如: cursor, windsurf, witsy)
- `{user_id}`: 你的用户ID

## 📝 URL格式说明

完整的MCP端点URL格式：

```
http://localhost:8765/mcp/{client_name}/sse/{user_id}
```

**示例**:
- Cursor用户: `http://localhost:8765/mcp/cursor/sse/john_doe`
- Windsurf用户: `http://localhost:8765/mcp/windsurf/sse/jane_smith`
- 自定义客户端: `http://localhost:8765/mcp/my_app/sse/user123`

## 🛠️ 可用的MCP工具

配置完成后，你可以使用以下工具：

### 1. add_memories
**功能**: 添加新记忆  
**触发时机**: 当你告诉AI关于自己的信息时  
**示例**:
- "记住我喜欢喝咖啡"
- "我的生日是1990年1月1日"
- "我住在北京"

### 2. search_memory
**功能**: 搜索记忆  
**触发时机**: 每次你提问时AI会自动调用  
**示例**:
- "我喜欢什么饮料？"
- "我的生日是什么时候？"
- "我住在哪里？"

### 3. list_memories
**功能**: 列出所有记忆  
**触发时机**: 当你想查看所有存储的信息时  
**示例**:
- "列出我的所有记忆"
- "显示你记住的关于我的信息"

### 4. delete_all_memories
**功能**: 删除所有记忆  
**触发时机**: 当你想清空所有记忆时  
**示例**:
- "删除我的所有记忆"
- "清空记忆"

## ✅ 验证配置

### 1. 检查连接状态

在MCP客户端中，你应该能看到：
- OpenMemory服务器已连接
- 4个可用工具 (add_memories, search_memory, list_memories, delete_all_memories)

### 2. 测试工具

尝试以下命令测试：

```
你: 记住我喜欢喝咖啡
AI: [调用 add_memories 工具] 已记住你喜欢喝咖啡

你: 我喜欢什么饮料？
AI: [调用 search_memory 工具] 根据记忆，你喜欢喝咖啡
```

## 🔍 故障排除

### 问题1: 无法连接到MCP服务器

**检查**:
```bash
# 确认服务器正在运行
curl http://localhost:8765/docs
```

**解决**:
```bash
# 启动服务器
cd api
python main.py
```

### 问题2: 工具调用失败

**可能原因**:
1. 用户ID格式错误
2. 客户端名称不匹配
3. 环境变量未设置

**检查环境变量**:
```bash
cd api
python test_mcp_connection.py
```

### 问题3: 记忆无法保存

**检查**:
1. 数据库是否正常 (api/openmemory.db)
2. Qdrant是否运行 (docker-compose up -d mem0_store)
3. API密钥是否有效

## 📊 配置示例

### 完整的Cursor配置示例

```json
{
  "mcpServers": {
    "openmemory": {
      "url": "http://localhost:8765/mcp/cursor/sse/john_doe",
      "transport": "sse"
    }
  },
  "editor.fontSize": 14,
  "editor.tabSize": 2
}
```

### 多用户配置示例

如果你有多个用户需要使用不同的记忆空间：

```json
{
  "mcpServers": {
    "openmemory_work": {
      "url": "http://localhost:8765/mcp/cursor/sse/work_user",
      "transport": "sse"
    },
    "openmemory_personal": {
      "url": "http://localhost:8765/mcp/cursor/sse/personal_user",
      "transport": "sse"
    }
  }
}
```

## 🎯 最佳实践

1. **用户ID命名**
   - 使用有意义的ID: `john_work`, `jane_personal`
   - 避免特殊字符: 使用字母、数字、下划线
   - 保持一致性: 不要频繁更改用户ID

2. **记忆管理**
   - 定期查看记忆: 使用 `list_memories`
   - 及时更新信息: 当信息变化时告诉AI
   - 清理无用记忆: 必要时使用 `delete_all_memories`

3. **隐私保护**
   - 不要在记忆中存储敏感信息 (密码、信用卡等)
   - 定期检查存储的内容
   - 使用独立的用户ID区分工作和个人

## 📚 相关文档

- [MCP连接问题排查指南.md](MCP连接问题排查指南.md) - 详细的故障排除
- [api/test_mcp_tools.py](api/test_mcp_tools.py) - 测试脚本
- [mcp_client_config_example.json](mcp_client_config_example.json) - 配置模板

## 🆘 获取帮助

如果遇到问题：

1. 运行诊断脚本:
   ```bash
   cd api
   python test_mcp_tools.py
   ```

2. 查看服务器日志:
   ```bash
   cd api
   python main.py
   ```

3. 检查API文档:
   打开 http://localhost:8765/docs

4. 查看详细排查指南:
   参考 [MCP连接问题排查指南.md](MCP连接问题排查指南.md)