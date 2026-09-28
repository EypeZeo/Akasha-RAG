# Changelog

## [1.1.1] - 2025-01-XX

### 🎉 新增功能

#### 开发者模式与系统监控
- **开发者面板**：新增完整的开发者面板 UI，支持实时监控
  - 系统资源监控（CPU、内存、磁盘使用率）
  - 进程信息展示（PID、线程数、运行时长）
  - 网络状态监控（代理模式、流量统计、错误率）
  - 缓存统计可视化（音频缓存、向量库、数据库大小）
  - 数据库表统计（各表记录数）
  - 自动刷新（每 5 秒更新一次）
  - 支持深色模式
  - 国际化支持（8 种语言）

- **监控 API**：新增 5 个系统监控端点
  - `GET /api/metrics/system` - 系统资源指标
  - `GET /api/metrics/network` - 网络状态
  - `GET /api/metrics/cache` - 缓存统计
  - `GET /api/metrics/database` - 数据库统计
  - `GET /api/metrics/health` - 健康检查
  - `GET /api/metrics/audit/recent` - 审计日志查询
  - `GET /api/metrics/performance` - 性能指标

- **审计日志系统**：完整的操作审计功能
  - 12 种审计事件类型（登录、数据操作、设置变更、安全事件等）
  - JSON 结构化日志存储
  - 自动日志轮转（10MB）+ ZIP 压缩
  - 90 天日志保留策略
  - 支持通过 API 查询最近 500 条日志

- **开发者模式配置**
  - 新增 `DEVELOPER_MODE` 环境变量
  - 支持运行时动态切换（通过前端或 API）
  - `GET /api/settings/developer-mode` - 查询状态
  - `POST /api/settings/developer-mode` - 切换模式

### 🔒 安全改进

#### 资源泄漏修复
- **启动器日志文件泄漏**：添加 `__del__` 方法确保异常时正确关闭日志文件
- **ASR 子进程超时**：为 `process.communicate()` 添加 5 秒超时，避免无限阻塞
- **浏览器上下文清理**：改进错误日志，避免内存泄漏（每个泄漏 50-100MB）

#### 网络代理增强
- **支持小写环境变量**：现在同时检查 `http_proxy`、`https_proxy`、`all_proxy`
- **NO_PROXY 绕过支持**：新增 `_should_bypass_proxy()` 函数，支持：
  - 精确匹配（localhost）
  - 域名后缀匹配（.example.com）
  - 通配符匹配（*.example.com）
- **代理连通性探测**：可选的代理服务器连通性测试（`ENABLE_PROXY_VALIDATION`）
- **统一 SOCKS 支持**：移除不必要的 SOCKS 代理限制

#### 其他安全修复
- **SQL 注入防护**：迁移脚本中使用方括号引用标识符
- **路径遍历防护**：系统路由添加路径验证和绝对路径检查

### 🚀 性能优化

- **自动依赖同步**：启动时自动检测并更新 `uv.lock`，避免手动干预
- **依赖管理**：新增 `psutil>=5.9` 用于系统监控

### 📝 文档

- **安全审计报告**：`SECURITY_AND_IMPROVEMENTS.md` - 详细的安全审计和改进总结
- **实施总结**：`IMPLEMENTATION_SUMMARY.md` - 完整的功能实现文档
- **平台接入规划**：`docs/PLATFORM_ROADMAP.md` - 知乎、小红书等平台的详细技术方案（2800+ 行，内部文档）

### 🐛 Bug 修复

- 修复启动时 `uv.lock` 过时导致的失败问题
- 修复开发者面板 Tab 切换时的显示问题
- 修复审计日志中文编码问题

### 🔧 内部改进

- 统一错误处理机制
- 改进日志记录（屏蔽敏感信息）
- 代码质量提升（添加类型注解、文档字符串）

---

## [1.0.1] - 2025-01-XX

（之前的版本记录）

