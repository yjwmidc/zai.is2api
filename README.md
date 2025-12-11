# Zai.is API Gateway

这是一个将 zai.is 封装为 OpenAI 兼容 API 的私有网关。

## 功能特性

*   **OpenAI 兼容接口**: 支持流式 (stream=True) 和非流式响应。
*   **多账号轮询**: 支持添加多个 Discord Token，自动维护 Zai Access Token 池。
*   **自动刷新**: 后台任务自动刷新即将过期的 Token。
*   **严格限流**: 内置 Redis 分布式锁，严格控制每个 Token 1 RPM (请求/分钟)。
*   **Docker 部署**: 一键 Docker Compose 启动。

## 快速开始

### 1. 启动服务

```bash
docker-compose up --build -d
```

服务将在 `http://localhost:8000` 启动。

### 2. 添加账号

使用 Discord Token 注册账号到网关：

```bash
curl -X POST "http://localhost:8000/v1/accounts" \
     -H "Content-Type: application/json" \
     -d '{"discord_token": "YOUR_DISCORD_TOKEN"}'
```

### 3. 调用对话接口

```bash
curl -X POST "http://localhost:8000/v1/chat/completions" \
     -H "Content-Type: application/json" \
     -d '{
       "model": "gemini-3-pro-image-preview",
       "messages": [{"role": "user", "content": "Hello!"}],
       "stream": true
     }'
```

## 目录结构

*   `app/`: 核心代码
    *   `api/`: 路由定义
    *   `core/`: 配置
    *   `services/`: 业务逻辑 (Token 管理, Zai API 客户端)
    *   `workers/`: 后台任务 (Token 刷新)
    *   `models/`: 数据库模型
*   `scripts/`: 原始 `zai_token.py` 脚本
*   `data/`: SQLite 数据库存储

## 注意事项

*   请确保 `scripts/zai_token.py` 与项目同步。
*   默认数据库为 SQLite，存储在 `data/zai_gateway.db`。
*   Redis 用于 Token 缓存和限流。
