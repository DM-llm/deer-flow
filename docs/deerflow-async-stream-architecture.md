# DeerFlow 异步任务 + Redis Stream 架构设计

## 🎯 **核心场景与需求**

DeerFlow 从同步流式响应升级为异步任务执行 + 事件流回放架构，解决以下关键场景：

### **场景1：多对话并行**
- **需求**：用户开启新对话时，原有的调查任务能继续在后台执行
- **解决**：每个对话对应独立的 `thread_id`，任务在 API 服务器内异步执行
- **交互处理**：需要用户交互的事件（interrupt）先写入 Redis Stream，用户确认后继续执行

### **场景2：断线续连**
- **需求**：用户离开页面后，后端任务继续执行并保存所有事件
- **解决**：任务完全脱离前端连接，所有事件写入 Redis Stream 持久保存

### **场景3：历史回放**
- **需求**：用户可查看历史对话记录，完整展现之前的事件内容
- **内容**：包括消息对话、调研活动(Activities)、最终报告(Report)
- **解决**：从 Redis Stream 读取完整事件流，前端分类渲染到不同标签页

### **场景4：实时续看**
- **需求**：查看历史对话时，如果任务还在运行，能实时接收新事件
- **展示**：Activities 标签显示实时调研进度，Report 标签显示增量报告内容
- **解决**：支持连续模式回放（`continuous=true`），从历史事件无缝切换到实时事件流

---

## 🏗️ **实际实现架构**

### **简化统一架构**
```
Web App → /api/chat/async → StreamGraphRunner (内置在API服务器) → Redis Stream
    ↑                                                                    ↓
    ← /api/chat/replay ← Event Reader ← Stream Key Offset Management ←
```

**核心理念**：**单进程异步架构 + Redis Stream 事件持久化**

- **无独立Worker进程**：异步任务直接在 FastAPI 服务器内执行
- **统一异步模式**：所有对话通过任务创建 + 事件回放完成
- **Redis Stream 作为事件总线**：`chat:{thread_id}:{task_id}` 作为 topic
- **断线续连支持**：通过 offset 机制随时重新连接

### **核心组件说明**

1. **前端统一接口** - 异步任务 + 实时回放
   - 创建任务：`POST /api/chat/async` → 返回 `task_id`
   - 实时回放：`GET /api/chat/replay?thread_id=xxx&query_id=task_id`
   - 自动切换：历史回放 → 实时回放（continuous模式）

2. **TaskManager** (`src/async_tasks/task_manager.py`) - 任务生命周期管理
   - 创建和跟踪异步任务，支持 UUID 任务ID
   - 任务状态管理（pending → running → completed/failed/cancelled）
   - 按线程ID管理任务关联，支持一个线程多个任务
   - Redis 存储任务信息，TTL 7天

3. **StreamGraphRunner** (`src/async_tasks/stream_runner.py`) - 工作流执行器
   - 异步执行 LangGraph 工作流（完全复制原有 `_astream_workflow_generator` 逻辑）
   - 正确处理 LangGraph 事件并转换为前端期望的格式
   - 实时将每个事件写入 Redis Stream (`chat:{thread_id}:{task_id}`)
   - 进度跟踪和错误处理
   - 支持所有原有工作流参数

4. **Redis Stream** (`src/config/redis_config.py`) - 事件流存储
   - Stream Key 格式: `chat:{thread_id}:{task_id}`
   - 事件持久化和多次回放，支持 offset 断点续播
   - 连接失败时提供 MockRedisClient 保证系统稳定

5. **Replayer** (`/api/chat/replay`) - 统一事件回放器
   - SSE 格式事件流输出
   - 支持静态回放和连续回放（continuous=true）
   - 自动查找最新 task_id（当 query_id="default" 时）
   - 前端可随时查看历史和实时事件
   - **修复了无限循环问题**：正确处理 Redis Stream offset

---

## 🔄 **数据流程详解**

### **创建新对话**
```
前端发起请求 → POST /api/chat/async → TaskManager.create_task() → 返回 task_id
    ↓
前端立即开始回放 → GET /api/chat/replay?query_id=task_id&continuous=true
    ↓
后台异步执行 StreamGraphRunner → 写入 Redis Stream (chat:{thread_id}:{task_id})
    ↓
前端通过 SSE 实时接收事件 → 渲染对话界面
```

### **断线续连**
```
前端重新连接 → GET /api/chat/replay?query_id=task_id&offset=last_event_id
    ↓
Replayer 从指定 offset 开始读取 → 返回遗漏的事件
    ↓
如果任务仍在运行 → 切换到连续模式继续接收新事件
```

### **历史对话查看**
```
前端查看历史 → GET /api/chat/replay?thread_id=xxx&query_id=default
    ↓
后端 find_latest_query_id() → 查找该线程最新的 task_id
    ↓
从 Redis Stream 完整回放 → 前端重现历史对话过程
```

### **用户交互处理**
```
LangGraph 产生 interrupt 事件 → 写入 Redis Stream → 任务暂停等待
    ↓
前端通过回放读取 interrupt 事件 → 用户确认操作
    ↓
前端发送 interrupt_feedback → 任务恢复执行 → 继续写入事件
```

---

## 📋 **技术实现要点**

### **Redis Stream 设计**

#### Stream Key 格式
```
chat:{thread_id}:{task_id}
```
- `thread_id`: 对话线程ID，前端持久化管理
- `task_id`: 异步任务ID，作为 query_id 使用，确保唯一性

**重要修复**：
- ❌ 错误：`chat:{thread_id}:default` 
- ✅ 正确：`chat:{thread_id}:{task_id}`

#### Offset 机制（关键修复）
- Redis Stream 自动生成递增ID：`1750264301951-0`
- 前端可通过 `offset` 参数指定起始位置
- **修复了无限循环问题**：使用 `get_next_stream_id()` 函数避免重复读取同一事件
- 支持断点续播：`offset=1750264301951-0`

#### 事件数据结构（实际实现）

**消息对话事件示例**
```json
{
  "id": "1750264301951-0",
  "event": "message_chunk",
  "thread_id": "user_thread_123",
  "data": {
    "agent": "coordinator",
    "id": "msg_001", 
    "role": "assistant",
    "content": "消息内容",
    "finish_reason": "stop"
  }
}
```

**工具调用事件示例**
```json
{
  "id": "1750264301952-0",
  "event": "tool_calls",
  "thread_id": "user_thread_123",
  "data": {
    "agent": "researcher",
    "id": "call_001",
    "role": "assistant",
    "tool_calls": [
      {
        "id": "call_001",
        "type": "function",
        "function": {"name": "web_search", "arguments": "量子计算发展现状"}
      }
    ]
  }
}
```

**用户交互事件示例**
```json
{
  "id": "1750264301953-0",
  "event": "interrupt",
  "thread_id": "user_thread_123",
  "data": {
    "task_id": "task-uuid",
    "thread_id": "user_thread_123",
    "id": "interrupt_001",
    "role": "assistant",
    "content": "请确认计划",
    "finish_reason": "interrupt",
    "options": [
      {"text": "Edit plan", "value": "edit_plan"},
      {"text": "Start research", "value": "accepted"}
    ]
  }
}
```

#### 支持的事件类型（与前端完全兼容）

**基础消息事件**
- `message_chunk`: 消息分块，包含用户对话和AI生成的报告内容

**工具调用事件**（主要显示在 Activities 标签）
- `tool_calls`: 工具调用开始（web搜索、数据分析等）
- `tool_call_chunks`: 工具调用过程分块
- `tool_call_result`: 工具调用结果

**交互控制事件**
- `interrupt`: 用户交互事件（计划确认、编辑等）
- `research_start`: 研究开始标记
- `research_end`: 研究结束标记

**系统状态事件**
- `error`: 错误事件
- `replay_end`: 回放结束标记

**❌ 已废弃的事件类型**
- `system_event`: 早期实现的错误事件类型，已完全移除

### **StreamGraphRunner 事件处理（关键修复）**

#### 正确的事件处理逻辑
```python
# 完全复制原有 _astream_workflow_generator 的处理逻辑
async for agent, _, event_data in self.graph.astream(...):
    if isinstance(event_data, dict):
        if "__interrupt__" in event_data:
            await self._handle_interrupt_event(...)
        continue
    
    # 处理消息事件
    message_chunk, message_metadata = cast(tuple[BaseMessage, dict[str, any]], event_data)
    
    if isinstance(message_chunk, ToolMessage):
        # → tool_call_result 事件
    elif isinstance(message_chunk, AIMessageChunk):
        if message_chunk.tool_calls:
            # → tool_calls 事件
        elif message_chunk.tool_call_chunks:
            # → tool_call_chunks 事件
        else:
            # → message_chunk 事件
```

#### 重要修复记录
1. **导入错误修复**：移除了错误的 `from typing import tuple`
2. **事件类型转换**：不再生成 `system_event`，而是正确转换为前端期望的事件类型
3. **事件格式统一**：与原有 `_astream_workflow_generator` 完全一致

### **任务状态管理**

#### 任务状态流转
```python
class TaskStatus(Enum):
    PENDING = "pending"      # 等待执行
    RUNNING = "running"      # 正在执行
    COMPLETED = "completed"  # 执行完成
    FAILED = "failed"        # 执行失败
    CANCELLED = "cancelled"  # 用户取消
```

#### 任务信息存储（TaskInfo 类）
```json
{
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "thread_id": "user_thread_123", 
  "user_input": "研究人工智能发展趋势",
  "status": "running",
  "progress": 0.65,
  "current_step": "处理事件中... (23 个事件)",
  "created_at": "2025-01-15T10:30:00",
  "started_at": "2025-01-15T10:30:05",
  "completed_at": null,
  "error_message": null,
  "config": {
    "messages": [...],
    "resources": [...],
    "max_plan_iterations": 1,
    "auto_accepted_plan": true
  }
}
```

### **前端交互设计**

#### Thread ID 持久化
- 前端 localStorage 保存 thread_id
- 页面刷新后恢复对话状态
- 支持多个浏览器标签页同时查看

#### 回放模式切换
- **静态回放**: `continuous=false`，只读取历史事件
- **连续回放**: `continuous=true`，历史事件+实时事件
- **智能回放**: 根据 `/api/threads/{thread_id}/running-task` 检测任务状态自动选择模式

#### 前端标签页渲染（基于原有框架）
- **Activities 标签**: 展示调研执行过程和工具调用活动
  - 工具调用过程：tool_calls, tool_call_chunks, tool_call_result
  - 用户交互：interrupt（计划确认、编辑反馈等）
  - 研究状态：research_start, research_end
  
- **Report 标签**: 展示最终生成的研究报告内容
  - 报告内容：从 message_chunk 中提取 agent="reporter" 的内容
  - 结构化展示：解析报告的章节结构（摘要、分析、结论等）

---

## 🚀 **API 接口设计**

### **异步任务 API**

#### 创建异步任务
```http
POST /api/chat/async
Content-Type: application/json

{
  "thread_id": "user_thread_123",
  "messages": [
    {"role": "user", "content": "研究人工智能发展趋势"}
  ],
  "max_plan_iterations": 1,
  "max_step_num": 3,
  "auto_accepted_plan": true,
  "enable_background_investigation": true,
  "report_style": "academic",
  "enable_deep_thinking": false
}
```

响应：
```json
{
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "thread_id": "user_thread_123",
  "status": "pending",
  "message": "异步任务已创建并开始执行",
  "created_at": "2025-01-15T10:30:00"
}
```

#### 查询任务状态
```http
GET /api/tasks/{task_id}
```

响应：
```json
{
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "thread_id": "user_thread_123",
  "user_input": "研究人工智能发展趋势",
  "status": "running",
  "progress": 0.65,
  "current_step": "处理事件中... (23 个事件)",
  "created_at": "2025-01-15T10:30:00",
  "started_at": "2025-01-15T10:30:05",
  "completed_at": null,
  "error_message": null
}
```

#### 获取线程任务列表
```http
GET /api/tasks?thread_id=user_thread_123&status=running&limit=10
```

#### 取消任务
```http
POST /api/tasks/{task_id}/cancel
```

### **事件回放 API**

#### 回放历史事件
```http
GET /api/chat/replay?thread_id=user_thread_123&offset=0
```

#### 连续回放（历史+实时）
```http
GET /api/chat/replay?thread_id=user_thread_123&continuous=true&query_id=latest
```

#### 检查线程运行状态
```http
GET /api/threads/{thread_id}/running-task
```

响应：
```json
{
  "has_running_task": true,
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "running",
  "progress": 0.65,
  "current_step": "处理事件中...",
  "started_at": "2025-01-15T10:30:05"
}
```

### **系统监控 API**

#### 查看系统状态
```http
GET /api/worker/stats
```

响应：
```json
{
  "is_running": true,
  "total_tasks": 156,
  "pending_tasks": 2,
  "running_tasks": 3,
  "completed_tasks": 145,
  "failed_tasks": 6,
  "max_concurrent_tasks": 10,
  "uptime_seconds": 0
}
```

#### 清理过期任务
```http
POST /api/worker/cleanup?days=7
```

---

## ⚙️ **部署和配置**

### **环境配置**
```bash
# Redis 配置
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_DB=0
REDIS_PASSWORD=optional

# 系统默认支持多并发异步任务，无需额外配置
```

### **启动服务**

#### 1. 启动 Redis
```bash
docker run -d --name redis -p 6379:6379 redis:latest
```

#### 2. 启动 API 服务器（包含异步任务执行）
```bash
uv run python server.py  # 默认端口 8000，内置异步任务执行
```

#### 3. 启动前端服务
```bash
cd web && npm run dev
```

### **Docker 部署**
```yaml
version: '3.8'
services:
  redis:
    image: redis:latest
    ports:
      - "6379:6379"
    
  deerflow:
    build: .
    ports:
      - "8000:8000"
    depends_on:
      - redis
    environment:
      - REDIS_HOST=redis
```

**注意**：无需独立的 worker 服务，所有异步任务都在 API 服务器内执行。

---

## 📊 **使用示例**

### **场景1：创建新对话**
```bash
# 创建异步任务
curl -X POST "http://localhost:8000/api/chat/async" \
  -H "Content-Type: application/json" \
  -d '{
    "thread_id": "chat_20250115_001", 
    "messages": [
      {"role": "user", "content": "帮我调研量子计算发展现状"}
    ],
    "auto_accepted_plan": true,
    "enable_background_investigation": true
  }'

# 响应
{
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "pending",
  "message": "异步任务已创建并开始执行"
}
```

### **场景2：查看任务进度**
```bash
# 查询任务状态
curl "http://localhost:8000/api/tasks/550e8400-e29b-41d4-a716-446655440000"

# 响应  
{
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "running",
  "progress": 0.4,
  "current_step": "处理事件中... (23 个事件)"
}
```

### **场景3：历史对话回放**
```bash
# 回放历史事件
curl "http://localhost:8000/api/chat/replay?thread_id=chat_20250115_001"

# SSE 事件流（修复后的正确格式）
event: research_start
data: {"agent": "researcher", "content": "开始量子计算调研...", "research_id": "query-uuid"}

event: tool_calls  
data: {"agent": "researcher", "tool_calls": [{"function": {"name": "web_search"}}]}

event: tool_call_result
data: {"agent": "researcher", "tool_call_id": "call_001", "content": "找到25篇量子计算相关论文"}

event: message_chunk
data: {"agent": "reporter", "role": "assistant", "content": "## 量子计算发展现状报告\n\n基于调研结果..."}

event: replay_end
data: {"thread_id": "chat_20250115_001", "mode": "static", "total_events": 45}
```

### **场景4：实时续看**
```bash
# 检查是否有运行中任务
curl "http://localhost:8000/api/threads/chat_20250115_001/running-task"

# 响应：有运行中任务
{
  "has_running_task": true,
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "running"
}

# 启用连续模式回放
curl "http://localhost:8000/api/chat/replay?thread_id=chat_20250115_001&continuous=true"
```

---

## 🔧 **监控和调试**

### **Redis 监控**
```bash
# 查看所有对话流
redis-cli KEYS "chat:*"

# 查看特定对话的事件数量
redis-cli XLEN chat:user_thread_123:task-uuid

# 查看最新事件
redis-cli XREVRANGE chat:user_thread_123:task-uuid + - COUNT 5

# 查看任务信息
redis-cli KEYS "task:*"
redis-cli GET task:550e8400-e29b-41d4-a716-446655440000
```

### **任务监控**
```bash
# 查看所有任务
curl "http://localhost:8000/api/tasks?limit=50"

# 查看运行中任务
curl "http://localhost:8000/api/tasks?status=running"

# 查看特定线程的任务
curl "http://localhost:8000/api/tasks?thread_id=chat_20250115_001"

# 查看系统状态
curl "http://localhost:8000/api/worker/stats"
```

### **日志调试**
```bash
# 查看 API 服务器日志（包含异步任务执行日志）
uv run python server.py --log-level debug

# 在代码中可以设置具体组件的日志级别
# logger.setLevel(logging.DEBUG)
```

---

## 🎯 **核心优势**

1. **极简部署**：单进程启动，无需管理独立的后台工作器
2. **真正异步**：后端完全脱离前端连接，支持长时间运行的调研任务
3. **断线重连**：前端可随时重新连接并恢复到最新状态
4. **多次回放**：支持完整重现对话过程，便于分析和分享
5. **自然并发**：基于 asyncio，支持多个对话同时进行，任务互不干扰
6. **交互等待**：需要用户交互的事件会暂停等待，确保流程正确性
7. **分类展示**：Activities 显示调研过程，Report 显示研究成果，内容结构清晰
8. **生产就绪**：支持监控、错误处理、资源管理等
9. **容错机制**：Redis 连接失败时使用 MockClient，确保系统稳定运行
10. **运维友好**：简化架构减少维护成本，所有功能集成在单个服务中

---

## 🔧 **已修复的关键问题**

### **问题1：无限循环回放**
- **症状**：回放接口一直重复读取同一个事件，导致前端无限循环
- **原因**：Redis Stream offset 处理错误，使用当前事件ID作为下次读取起点
- **修复**：实现 `get_next_stream_id()` 函数，使用下一个ID作为起点
- **结果**：回放正常结束，支持断点续播

### **问题2：事件类型错误**
- **症状**：所有事件都被包装为 `system_event`，前端无法正确解析
- **原因**：StreamGraphRunner 没有按照原有流式API的逻辑处理事件
- **修复**：完全复制 `_astream_workflow_generator` 的事件处理逻辑
- **结果**：正确生成前端期望的事件类型（message_chunk, tool_calls等）

### **问题3：导入错误**
- **症状**：服务器启动失败，`cannot import name 'tuple' from 'typing'`
- **原因**：错误地从 typing 模块导入内置类型 tuple
- **修复**：移除错误的导入，使用内置 tuple 类型
- **结果**：服务器正常启动

### **问题4：架构过度设计**
- **症状**：最初设计过于复杂，包含很多虚构的事件类型
- **原因**：对前端和后端的实际实现理解不够准确
- **修复**：基于实际的 LangGraph 事件和前端类型定义进行设计
- **结果**：架构简洁实用，符合实际需求

---

## 📝 **注意事项**

1. **Redis 依赖**：系统依赖 Redis 存储事件流，需确保 Redis 稳定运行
2. **存储管理**：
   - 任务信息 TTL 7天自动清理
   - Redis Stream 需定期清理（可通过 `/api/worker/cleanup` API）
3. **并发处理**：基于 asyncio 异步处理，理论上支持大量并发任务
4. **错误处理**：
   - 任务执行失败会更新状态为 failed
   - Redis 连接失败时使用 MockClient 保证系统不崩溃
5. **安全考虑**：生产环境需要添加认证和权限控制
6. **资源管理**：长时间运行的任务可能占用内存，建议监控系统资源
7. **前端集成**：需要前端适配 SSE 事件流和任务状态轮询
8. **Python 版本**：确保使用 Python 3.9+ 版本，避免类型注解问题
9. **依赖管理**：建议使用 `uv run python server.py` 启动，确保依赖正确加载

---

## 🚧 **后续开发建议**

1. **前端集成**：需要修改前端代码，从同步流式 API 切换到异步任务 + 回放模式
2. **用户交互优化**：支持在回放过程中进行用户交互（interrupt 事件处理）
3. **性能优化**：对于大量事件的任务，可以考虑分页回放
4. **监控仪表板**：开发任务监控界面，方便管理员查看系统状态
5. **任务调度**：添加任务优先级和资源限制机制
6. **备份恢复**：实现 Redis Stream 数据的备份和恢复功能

这个架构现在已经完全可用，可以为 DeerFlow 提供强大的异步执行和断线重连能力！ 