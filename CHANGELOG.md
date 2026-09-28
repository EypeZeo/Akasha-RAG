# Changelog

## [1.2.0](https://github.com/EypeZeo/Akasha-RAG/compare/v1.1.1...v1.2.0) (2026-09-28)


### Features

* **chat:** export and print the full session history, not just what's loaded ([#39](https://github.com/EypeZeo/Akasha-RAG/issues/39)) ([9831ff2](https://github.com/EypeZeo/Akasha-RAG/commit/9831ff299e10277a2c596b00be3d939639908476)), closes [#29](https://github.com/EypeZeo/Akasha-RAG/issues/29)
* Developer Mode, System Monitoring & Security Improvements (v1.1.1) ([#69](https://github.com/EypeZeo/Akasha-RAG/issues/69)) ([b460f95](https://github.com/EypeZeo/Akasha-RAG/commit/b460f95b97fefc9bda9f0d6a00b2c0e591a77f25))
* process-wide model-call admission gate for LLM/embedding/vision ([#12](https://github.com/EypeZeo/Akasha-RAG/issues/12)) ([f2c5418](https://github.com/EypeZeo/Akasha-RAG/commit/f2c5418a20bb1e28a50e7fce356e8162ad022c6b))
* **rag:** add answer and citation metrics ([#54](https://github.com/EypeZeo/Akasha-RAG/issues/54)) ([0f648cd](https://github.com/EypeZeo/Akasha-RAG/commit/0f648cd8505b32289b2b02d38218ace6ea3a6fef))
* **rag:** add deterministic evaluation core ([#51](https://github.com/EypeZeo/Akasha-RAG/issues/51)) ([09ee798](https://github.com/EypeZeo/Akasha-RAG/commit/09ee7981f76cf22244199b0b7012098a7a533d17))
* **rag:** add opt-in live retrieval collector ([#62](https://github.com/EypeZeo/Akasha-RAG/issues/62)) ([8bbf3b6](https://github.com/EypeZeo/Akasha-RAG/commit/8bbf3b6929a661987a38c3ca7e6401ac8268c212))
* **rag:** add replay runner with frozen index manifest ([#53](https://github.com/EypeZeo/Akasha-RAG/issues/53)) ([ee71b00](https://github.com/EypeZeo/Akasha-RAG/commit/ee71b009cb368bf2f63b1188cafd14896b2eeaa6))
* **rag:** add sanitized evaluation traces ([#52](https://github.com/EypeZeo/Akasha-RAG/issues/52)) ([ea0f83c](https://github.com/EypeZeo/Akasha-RAG/commit/ea0f83cf9ddac6e1e566fe88b767a8a8bf6a7be5))
* shared Dialog component with nested-dialog support, aria-modal and focus management ([#15](https://github.com/EypeZeo/Akasha-RAG/issues/15)) ([515f0c8](https://github.com/EypeZeo/Akasha-RAG/commit/515f0c8bfbba2635665a3f104dd00c2dcd7f40d7))
* **sources:** add ingestion status filters and retry controls ([#66](https://github.com/EypeZeo/Akasha-RAG/issues/66)) ([b3abe12](https://github.com/EypeZeo/Akasha-RAG/commit/b3abe12173eeb0f4b639a42a7f26e493f8a54f8a))


### Bug Fixes

* **app:** polish setup UI and adapt provider networking ([#64](https://github.com/EypeZeo/Akasha-RAG/issues/64)) ([32c4fcb](https://github.com/EypeZeo/Akasha-RAG/commit/32c4fcb6e69c8ae5a73b2ade46c85e4df0aace2c))
* Bilibili sync freshness, part reconciliation, and cancellation correctness ([#10](https://github.com/EypeZeo/Akasha-RAG/issues/10)) ([b8bd2d1](https://github.com/EypeZeo/Akasha-RAG/commit/b8bd2d12a438e4a8f2e0d445c486cf6f99deb854))
* **build-flow:** keep the ingest and export polls alive across remounts and re-renders ([#38](https://github.com/EypeZeo/Akasha-RAG/issues/38)) ([7965b5f](https://github.com/EypeZeo/Akasha-RAG/commit/7965b5ffafa904f04f05814b703f04ad6349b576))
* cancel /ask/stream on client disconnect via a thread bridge instead of running to completion ([#13](https://github.com/EypeZeo/Akasha-RAG/issues/13)) ([613e5e3](https://github.com/EypeZeo/Akasha-RAG/commit/613e5e3e27c9aefdf888bb1cd5dfaf738cb88f71))
* **chat:** enforce client key parity across ask endpoints ([#61](https://github.com/EypeZeo/Akasha-RAG/issues/61)) ([7695ec5](https://github.com/EypeZeo/Akasha-RAG/commit/7695ec515cd7990ffb3dc53e8c30a4aad9feed34))
* **chat:** invalidate every in-flight ChatPanel request on any session transition ([#30](https://github.com/EypeZeo/Akasha-RAG/issues/30)) ([c2e3be2](https://github.com/EypeZeo/Akasha-RAG/commit/c2e3be20dcfe2d9097191c45f7bb77ebf593f418))
* **chat:** notice a client disconnect while the SSE bridge queue is full ([#33](https://github.com/EypeZeo/Akasha-RAG/issues/33)) ([e9903ad](https://github.com/EypeZeo/Akasha-RAG/commit/e9903ad9fb125cc0f49d9412227e9848b728b451)), closes [#28](https://github.com/EypeZeo/Akasha-RAG/issues/28)
* **chat:** show effective platform in collection scope ([#48](https://github.com/EypeZeo/Akasha-RAG/issues/48)) ([c0c979a](https://github.com/EypeZeo/Akasha-RAG/commit/c0c979ad5d2d3e69cb81ba9af2360625e6d53638))
* **collections:** resolve collections by (platform, remote id) and reject ambiguous requests ([#37](https://github.com/EypeZeo/Akasha-RAG/issues/37)) ([ce9c8b1](https://github.com/EypeZeo/Akasha-RAG/commit/ce9c8b14f05936fa2c045268457c52c99b39dcf0)), closes [#34](https://github.com/EypeZeo/Akasha-RAG/issues/34)
* correct export author/link fields and PDF content escaping ([#14](https://github.com/EypeZeo/Akasha-RAG/issues/14)) ([da80193](https://github.com/EypeZeo/Akasha-RAG/commit/da8019374b845f374d12a504c1fdeea6178018c2))
* **docs:** fix Mermaid subgraph syntax and complete the v1.0.0 changelog ([0aa5691](https://github.com/EypeZeo/Akasha-RAG/commit/0aa5691ea649a7ab7bdbcf307152508a160aa4a7))
* **douyin:** preserve snapshot through provider sync drift ([#65](https://github.com/EypeZeo/Akasha-RAG/issues/65)) ([837adb9](https://github.com/EypeZeo/Akasha-RAG/commit/837adb94bbf9be8d1966a2bb3f71ca1a4f6755f4))
* **evaluation:** harden Douyin diagnostics and R0 preflight ([#67](https://github.com/EypeZeo/Akasha-RAG/issues/67)) ([1108c35](https://github.com/EypeZeo/Akasha-RAG/commit/1108c35df039ab5ca85d7928ad75f1e05180cfa8))
* **ingestion:** unify state reset and cache invalidation ([#68](https://github.com/EypeZeo/Akasha-RAG/issues/68)) ([149f401](https://github.com/EypeZeo/Akasha-RAG/commit/149f401ee8ca76dc8f1a9b374124ef3fe0db5e84))
* **modals:** ignore stale and late responses in the ingest-confirm and export modals ([#45](https://github.com/EypeZeo/Akasha-RAG/issues/45)) ([d8e584a](https://github.com/EypeZeo/Akasha-RAG/commit/d8e584aab1f91ee28ea015730e0f3dee8c50a2d2)), closes [#43](https://github.com/EypeZeo/Akasha-RAG/issues/43)
* move maintenance-op blocking IO off the event loop, guard clear-all against active ingestion ([#11](https://github.com/EypeZeo/Akasha-RAG/issues/11)) ([39f048a](https://github.com/EypeZeo/Akasha-RAG/commit/39f048ab31d4823624725e706edf9a434fb3ecdb))
* PR1 — security, data-integrity, and i18n audit fixes ([#2](https://github.com/EypeZeo/Akasha-RAG/issues/2)) ([03407eb](https://github.com/EypeZeo/Akasha-RAG/commit/03407ebd09b14a2ef504243e9fb54be096d01517))
* **security:** harden logs and remote media handling ([#5](https://github.com/EypeZeo/Akasha-RAG/issues/5)) ([907ae69](https://github.com/EypeZeo/Akasha-RAG/commit/907ae69afdff8bca8dc8d118683bc9e5be18ba9f))
* **security:** protect local credentials with DPAPI ([#6](https://github.com/EypeZeo/Akasha-RAG/issues/6)) ([8d48609](https://github.com/EypeZeo/Akasha-RAG/commit/8d4860979ccfbaf23b5d92318c14d926687c60f2))
* **security:** reject non-loopback API clients ([#7](https://github.com/EypeZeo/Akasha-RAG/issues/7)) ([6fa1a2a](https://github.com/EypeZeo/Akasha-RAG/commit/6fa1a2a69676ec59e3170f7e80d6745465a1755a))
* **sources-panel:** newest stats request wins, no follow-ups after unmount, newest ingest click wins ([#46](https://github.com/EypeZeo/Akasha-RAG/issues/46)) ([8de5480](https://github.com/EypeZeo/Akasha-RAG/commit/8de5480d43c660dc8fbb541107613ba62a5cbac4)), closes [#43](https://github.com/EypeZeo/Akasha-RAG/issues/43)
* **sources:** eliminate two intermittent full-suite test failures in SourcesPanel.test.tsx ([#22](https://github.com/EypeZeo/Akasha-RAG/issues/22)) ([a7c9cae](https://github.com/EypeZeo/Akasha-RAG/commit/a7c9cae873359c911e12842609756dd4fcf0d32b))
* **sources:** give the pagination test headroom over Vitest's default timeout ([#23](https://github.com/EypeZeo/Akasha-RAG/issues/23)) ([4a052e9](https://github.com/EypeZeo/Akasha-RAG/commit/4a052e9a0879e2437c8707acd7cbf9539fdd7722))
* **sources:** scope the panel by (platform, remote id) and ignore stale collection and video responses ([#42](https://github.com/EypeZeo/Akasha-RAG/issues/42)) ([3dc4ce7](https://github.com/EypeZeo/Akasha-RAG/commit/3dc4ce739538e5bdfbde1e46d7bb58078ef71c1f))
* **sources:** use userEvent.click() to fix the pagination test's real flake ([#24](https://github.com/EypeZeo/Akasha-RAG/issues/24)) ([432a20d](https://github.com/EypeZeo/Akasha-RAG/commit/432a20d01bd783c3aefd684d35cf93dc45ee3bbf))


### Performance Improvements

* **frontend:** avoid preloading markdown on landing ([#56](https://github.com/EypeZeo/Akasha-RAG/issues/56)) ([1b0ffb9](https://github.com/EypeZeo/Akasha-RAG/commit/1b0ffb97b4857db7cbff65df908ffdf7d190a7cc))
* **frontend:** lazy load login modal ([#8](https://github.com/EypeZeo/Akasha-RAG/issues/8)) ([924485d](https://github.com/EypeZeo/Akasha-RAG/commit/924485d833b80020c2948ddac7cb3f7318e99e1d))

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

## [1.1.0](https://github.com/EypeZeo/Akasha-RAG/compare/v1.0.1...v1.1.0) (2026-09-28)


### Features

* **chat:** export and print the full session history, not just what's loaded ([#39](https://github.com/EypeZeo/Akasha-RAG/issues/39)) ([9831ff2](https://github.com/EypeZeo/Akasha-RAG/commit/9831ff299e10277a2c596b00be3d939639908476)), closes [#29](https://github.com/EypeZeo/Akasha-RAG/issues/29)
* process-wide model-call admission gate for LLM/embedding/vision ([#12](https://github.com/EypeZeo/Akasha-RAG/issues/12)) ([f2c5418](https://github.com/EypeZeo/Akasha-RAG/commit/f2c5418a20bb1e28a50e7fce356e8162ad022c6b))
* **rag:** add answer and citation metrics ([#54](https://github.com/EypeZeo/Akasha-RAG/issues/54)) ([0f648cd](https://github.com/EypeZeo/Akasha-RAG/commit/0f648cd8505b32289b2b02d38218ace6ea3a6fef))
* **rag:** add deterministic evaluation core ([#51](https://github.com/EypeZeo/Akasha-RAG/issues/51)) ([09ee798](https://github.com/EypeZeo/Akasha-RAG/commit/09ee7981f76cf22244199b0b7012098a7a533d17))
* **rag:** add opt-in live retrieval collector ([#62](https://github.com/EypeZeo/Akasha-RAG/issues/62)) ([8bbf3b6](https://github.com/EypeZeo/Akasha-RAG/commit/8bbf3b6929a661987a38c3ca7e6401ac8268c212))
* **rag:** add replay runner with frozen index manifest ([#53](https://github.com/EypeZeo/Akasha-RAG/issues/53)) ([ee71b00](https://github.com/EypeZeo/Akasha-RAG/commit/ee71b009cb368bf2f63b1188cafd14896b2eeaa6))
* **rag:** add sanitized evaluation traces ([#52](https://github.com/EypeZeo/Akasha-RAG/issues/52)) ([ea0f83c](https://github.com/EypeZeo/Akasha-RAG/commit/ea0f83cf9ddac6e1e566fe88b767a8a8bf6a7be5))
* shared Dialog component with nested-dialog support, aria-modal and focus management ([#15](https://github.com/EypeZeo/Akasha-RAG/issues/15)) ([515f0c8](https://github.com/EypeZeo/Akasha-RAG/commit/515f0c8bfbba2635665a3f104dd00c2dcd7f40d7))
* **sources:** add ingestion status filters and retry controls ([#66](https://github.com/EypeZeo/Akasha-RAG/issues/66)) ([b3abe12](https://github.com/EypeZeo/Akasha-RAG/commit/b3abe12173eeb0f4b639a42a7f26e493f8a54f8a))


### Bug Fixes

* **app:** polish setup UI and adapt provider networking ([#64](https://github.com/EypeZeo/Akasha-RAG/issues/64)) ([32c4fcb](https://github.com/EypeZeo/Akasha-RAG/commit/32c4fcb6e69c8ae5a73b2ade46c85e4df0aace2c))
* Bilibili sync freshness, part reconciliation, and cancellation correctness ([#10](https://github.com/EypeZeo/Akasha-RAG/issues/10)) ([b8bd2d1](https://github.com/EypeZeo/Akasha-RAG/commit/b8bd2d12a438e4a8f2e0d445c486cf6f99deb854))
* **build-flow:** keep the ingest and export polls alive across remounts and re-renders ([#38](https://github.com/EypeZeo/Akasha-RAG/issues/38)) ([7965b5f](https://github.com/EypeZeo/Akasha-RAG/commit/7965b5ffafa904f04f05814b703f04ad6349b576))
* cancel /ask/stream on client disconnect via a thread bridge instead of running to completion ([#13](https://github.com/EypeZeo/Akasha-RAG/issues/13)) ([613e5e3](https://github.com/EypeZeo/Akasha-RAG/commit/613e5e3e27c9aefdf888bb1cd5dfaf738cb88f71))
* **chat:** enforce client key parity across ask endpoints ([#61](https://github.com/EypeZeo/Akasha-RAG/issues/61)) ([7695ec5](https://github.com/EypeZeo/Akasha-RAG/commit/7695ec515cd7990ffb3dc53e8c30a4aad9feed34))
* **chat:** invalidate every in-flight ChatPanel request on any session transition ([#30](https://github.com/EypeZeo/Akasha-RAG/issues/30)) ([c2e3be2](https://github.com/EypeZeo/Akasha-RAG/commit/c2e3be20dcfe2d9097191c45f7bb77ebf593f418))
* **chat:** notice a client disconnect while the SSE bridge queue is full ([#33](https://github.com/EypeZeo/Akasha-RAG/issues/33)) ([e9903ad](https://github.com/EypeZeo/Akasha-RAG/commit/e9903ad9fb125cc0f49d9412227e9848b728b451)), closes [#28](https://github.com/EypeZeo/Akasha-RAG/issues/28)
* **chat:** show effective platform in collection scope ([#48](https://github.com/EypeZeo/Akasha-RAG/issues/48)) ([c0c979a](https://github.com/EypeZeo/Akasha-RAG/commit/c0c979ad5d2d3e69cb81ba9af2360625e6d53638))
* **collections:** resolve collections by (platform, remote id) and reject ambiguous requests ([#37](https://github.com/EypeZeo/Akasha-RAG/issues/37)) ([ce9c8b1](https://github.com/EypeZeo/Akasha-RAG/commit/ce9c8b14f05936fa2c045268457c52c99b39dcf0)), closes [#34](https://github.com/EypeZeo/Akasha-RAG/issues/34)
* correct export author/link fields and PDF content escaping ([#14](https://github.com/EypeZeo/Akasha-RAG/issues/14)) ([da80193](https://github.com/EypeZeo/Akasha-RAG/commit/da8019374b845f374d12a504c1fdeea6178018c2))
* **douyin:** preserve snapshot through provider sync drift ([#65](https://github.com/EypeZeo/Akasha-RAG/issues/65)) ([837adb9](https://github.com/EypeZeo/Akasha-RAG/commit/837adb94bbf9be8d1966a2bb3f71ca1a4f6755f4))
* **evaluation:** harden Douyin diagnostics and R0 preflight ([#67](https://github.com/EypeZeo/Akasha-RAG/issues/67)) ([1108c35](https://github.com/EypeZeo/Akasha-RAG/commit/1108c35df039ab5ca85d7928ad75f1e05180cfa8))
* **ingestion:** unify state reset and cache invalidation ([#68](https://github.com/EypeZeo/Akasha-RAG/issues/68)) ([149f401](https://github.com/EypeZeo/Akasha-RAG/commit/149f401ee8ca76dc8f1a9b374124ef3fe0db5e84))
* **modals:** ignore stale and late responses in the ingest-confirm and export modals ([#45](https://github.com/EypeZeo/Akasha-RAG/issues/45)) ([d8e584a](https://github.com/EypeZeo/Akasha-RAG/commit/d8e584aab1f91ee28ea015730e0f3dee8c50a2d2)), closes [#43](https://github.com/EypeZeo/Akasha-RAG/issues/43)
* move maintenance-op blocking IO off the event loop, guard clear-all against active ingestion ([#11](https://github.com/EypeZeo/Akasha-RAG/issues/11)) ([39f048a](https://github.com/EypeZeo/Akasha-RAG/commit/39f048ab31d4823624725e706edf9a434fb3ecdb))
* PR1 — security, data-integrity, and i18n audit fixes ([#2](https://github.com/EypeZeo/Akasha-RAG/issues/2)) ([03407eb](https://github.com/EypeZeo/Akasha-RAG/commit/03407ebd09b14a2ef504243e9fb54be096d01517))
* **security:** harden logs and remote media handling ([#5](https://github.com/EypeZeo/Akasha-RAG/issues/5)) ([907ae69](https://github.com/EypeZeo/Akasha-RAG/commit/907ae69afdff8bca8dc8d118683bc9e5be18ba9f))
* **security:** protect local credentials with DPAPI ([#6](https://github.com/EypeZeo/Akasha-RAG/issues/6)) ([8d48609](https://github.com/EypeZeo/Akasha-RAG/commit/8d4860979ccfbaf23b5d92318c14d926687c60f2))
* **security:** reject non-loopback API clients ([#7](https://github.com/EypeZeo/Akasha-RAG/issues/7)) ([6fa1a2a](https://github.com/EypeZeo/Akasha-RAG/commit/6fa1a2a69676ec59e3170f7e80d6745465a1755a))
* **sources-panel:** newest stats request wins, no follow-ups after unmount, newest ingest click wins ([#46](https://github.com/EypeZeo/Akasha-RAG/issues/46)) ([8de5480](https://github.com/EypeZeo/Akasha-RAG/commit/8de5480d43c660dc8fbb541107613ba62a5cbac4)), closes [#43](https://github.com/EypeZeo/Akasha-RAG/issues/43)
* **sources:** eliminate two intermittent full-suite test failures in SourcesPanel.test.tsx ([#22](https://github.com/EypeZeo/Akasha-RAG/issues/22)) ([a7c9cae](https://github.com/EypeZeo/Akasha-RAG/commit/a7c9cae873359c911e12842609756dd4fcf0d32b))
* **sources:** give the pagination test headroom over Vitest's default timeout ([#23](https://github.com/EypeZeo/Akasha-RAG/issues/23)) ([4a052e9](https://github.com/EypeZeo/Akasha-RAG/commit/4a052e9a0879e2437c8707acd7cbf9539fdd7722))
* **sources:** scope the panel by (platform, remote id) and ignore stale collection and video responses ([#42](https://github.com/EypeZeo/Akasha-RAG/issues/42)) ([3dc4ce7](https://github.com/EypeZeo/Akasha-RAG/commit/3dc4ce739538e5bdfbde1e46d7bb58078ef71c1f))
* **sources:** use userEvent.click() to fix the pagination test's real flake ([#24](https://github.com/EypeZeo/Akasha-RAG/issues/24)) ([432a20d](https://github.com/EypeZeo/Akasha-RAG/commit/432a20d01bd783c3aefd684d35cf93dc45ee3bbf))


### Performance Improvements

* **frontend:** avoid preloading markdown on landing ([#56](https://github.com/EypeZeo/Akasha-RAG/issues/56)) ([1b0ffb9](https://github.com/EypeZeo/Akasha-RAG/commit/1b0ffb97b4857db7cbff65df908ffdf7d190a7cc))
* **frontend:** lazy load login modal ([#8](https://github.com/EypeZeo/Akasha-RAG/issues/8)) ([924485d](https://github.com/EypeZeo/Akasha-RAG/commit/924485d833b80020c2948ddac7cb3f7318e99e1d))

## [1.0.1](https://github.com/EypeZeo/Akasha-RAG/compare/v1.0.0...v1.0.1) (2026-09-12)

（之前的版本记录）
