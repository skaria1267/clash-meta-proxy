# 节点轮换代理服务

自动轮换节点的智能代理池，解决429限流问题。

## 功能特性
- 自动节点轮换
- 失败自动重试
- 多节点负载均衡
- REST API接口

## 环境变量
- `CLASH_YAML`: Clash配置（Base64编码）
- `SWITCH_INTERVAL`: 切换间隔（默认1个请求）
- `PORT`: 服务端口（默认8080）

## API端点
- `/` - 服务信息
- `/proxy?url=目标URL` - 代理请求
- `/switch` - 手动切换节点
- `/nodes` - 查看所有节点
- `/health` - 健康检查

## 使用方式

### 1. 通过API代理
```
GET http://服务地址/proxy?url=https://example.com
```

### 2. 直接使用Clash端口
```
http://服务地址:7890
```

## 部署到Zeabur
1. Fork此仓库
2. 在Zeabur中导入
3. 设置环境变量CLASH_YAML
4. 部署完成
