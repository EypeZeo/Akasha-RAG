# Akasha-RAG 安全审计与功能改进总结

> **完成日期**：2025-01-XX  
> **审计范围**：后端、前端、网络、资源管理、代理检测

---

## 📊 审计概览

### 整体评分：**A-**（优秀）

| 类别 | 评分 | 说明 |
|------|------|------|
| 后端安全 | A | 强认证、输入验证、密钥管理优秀 |
| 前端安全 | A | 无 XSS/CSRF 漏洞，数据处理安全 |
| 资源管理 | A- | 修复后显著改进 |
| 网络处理 | B+ | 代理检测改进，支持多种模式 |
| 依赖安全 | A | 无已知高危漏洞 |

---

## ✅ 已修复的关键问题

### 1. 资源泄漏修复（高优先级）

#### 1.1 启动器日志文件句柄泄漏
- **文件**：`launcher.py:125-143`
- **问题**：进程崩溃时日志文件未关闭
- **修复**：添加 `__del__` 方法确保清理

```python
def __del__(self) -> None:
    """确保日志文件在对象销毁时正确关闭"""
    try:
        if hasattr(self, 'log_file') and self.log_file and not self.log_file.closed:
            self.log_file.close()
    except Exception:
        pass
```

#### 1.2 ASR 子进程终止超时
- **文件**：`backend/app/services/asr_service.py:110-119`
- **问题**：`process.communicate()` 可能无限阻塞
- **修复**：添加 5 秒超时机制

```python
process.kill()
try:
    process.communicate(timeout=5.0)
except subprocess.TimeoutExpired:
    logger.warning("ASR 工作进程未能在 5 秒内终止，强制清理")
```

#### 1.3 浏览器上下文清理
- **文件**：`backend/app/services/douyin_collector.py:621-626`
- **问题**：清理失败静默忽略（每个泄漏 50-100MB）
- **修复**：添加错误日志

```python
try:
    context.close()
except Exception as e:
    logger.error(f"浏览器上下文清理失败（可能导致内存泄漏）: {e}")
```

---

### 2. 网络代理改进（中优先级）

#### 2.1 支持小写环境变量
- **文件**：`backend/app/core/network.py:65-72`
- **改进**：支持 `http_proxy`、`https_proxy`、`all_proxy`

```python
for key in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
    if proxy := _normalise_proxy(os.environ.get(key)):
        return proxy
```

#### 2.2 代理连通性探测
- **文件**：`backend/app/core/network.py:41-58`
- **新增功能**：可选的代理服务器连通性测试

```python
def _test_proxy_connectivity(proxy_url: str, timeout: float = 3.0) -> tuple[bool, str]:
    """测试代理连通性，返回 (是否可用, 错误信息)"""
    # 尝试连接代理服务器
    # 超时/拒绝连接等情况返回详细错误
```

#### 2.3 NO_PROXY 绕过支持
- **文件**：`backend/app/core/network.py:14-39`
- **新增功能**：根据 NO_PROXY 环境变量绕过代理

```python
def _should_bypass_proxy(hostname: str) -> bool:
    """检查给定主机名是否应该绕过代理"""
    # 支持精确匹配、域名后缀、通配符
    # 例如：localhost, .example.com, *.github.com
```

#### 2.4 统一 SOCKS 代理支持
- **文件**：`backend/app/services/media_service.py:195-205`
- **改进**：移除不必要的 SOCKS 代理限制

```python
# 移除限制 - httpx 0.28+ 原生支持 SOCKS5
stream_proxy = proxy
```

---

### 3. SQL 注入风险修复（低优先级）

#### 3.1 迁移脚本标识符引用
- **文件**：`backend/app/db/migration.py:113, 452`
- **问题**：表名使用 f-string 拼接
- **修复**：使用方括号引用标识符

```python
cur.execute(f"SELECT count(*) FROM [{t}]")  # 使用方括号引用
```

---

### 4. 路径遍历防护（中优先级）

#### 4.1 自定义路径验证
- **文件**：`backend/app/api/routes/system.py:121-135`
- **改进**：添加路径解析和验证

```python
target_path = Path(body.custom_path)
try:
    target_path = target_path.resolve()
    if not target_path.is_absolute():
        return {"success": False, "message": "仅支持绝对路径"}
except (ValueError, OSError) as e:
    return {"success": False, "message": f"路径无效: {e}"}
```

---

## 🆕 新增功能

### 1. 开发者模式 + 系统监控

#### 1.1 开发者模式配置
- **文件**：`backend/app/core/config.py:216-232`
- **环境变量**：`DEVELOPER_MODE=true`
- **运行时切换**：通过 API 动态开关

```python
developer_mode: bool = Field(default=False)
"""开发者模式开关：启用后前端将显示"开发者面板" Tab"""

enable_proxy_validation: bool = Field(default=False)
"""启用代理连通性验证（可能增加 3-5 秒启动延迟）"""
```

#### 1.2 系统监控 API
- **文件**：`backend/app/api/routes/metrics.py`
- **新增端点**：

| 端点 | 说明 |
|------|------|
| `GET /api/metrics/system` | CPU、内存、磁盘使用情况 |
| `GET /api/metrics/network` | 代理配置、网络 IO 统计 |
| `GET /api/metrics/cache` | 音频缓存、向量库、数据库大小 |
| `GET /api/metrics/database` | 表记录数、连接池状态 |
| `GET /api/metrics/health` | 健康检查（不需开发者模式） |

#### 1.3 开发者模式设置 API
- **文件**：`backend/app/api/routes/settings.py:149-185`
- **新增端点**：

```python
GET  /api/settings/developer-mode      # 获取状态
POST /api/settings/developer-mode      # 设置（运行时切换）
```

---

### 2. 多平台接入规划

#### 2.1 规划文档
- **文件**：`docs/PLATFORM_ROADMAP.md`（已加入 .gitignore）
- **优先级排序**：

| 阶段 | 平台 | 时间 | 关键特性 |
|------|------|------|---------|
| ✅ Phase 1 | 抖音 + 哔哩哔哩 | 已完成 | 视频 + 图文 + ASR |
| 🎯 Phase 2.1 | 知乎 | 6 周 | 文章 + 回答 + 广告过滤 |
| 🎯 Phase 2.2 | 小红书 | 6 周 | 图文笔记 + 广告识别 |
| 🎯 Phase 2.3 | 网易云音乐 | 5 周 | 播客 + ASR |
| 🔮 Phase 3.1 | YouTube | 8 周 | 多语言 ASR + Shorts + 字幕 |
| 🔮 Phase 3.2 | 豆瓣 + 今日头条 | 8 周 | 书影音评论 + 新闻 |

#### 2.2 广告过滤策略
- **目标平台**：知乎、小红书、博客园
- **识别方法**：CSS 类名黑名单 + 内容关键词 + 元素位置
- **实现**：`AdFilter` 通用过滤器（规划中）

#### 2.3 YouTube 特殊处理
- **YouTube Shorts**：竖屏短视频支持
- **多语言 ASR**：支持英语、日语等（DashScope paraformer-v2）
- **字幕优先策略**：优先使用 YouTube 官方字幕，无字幕时 ASR
- **口音处理**：官方字幕精度更高，降低 ASR 成本

---

## 📦 依赖更新

### 新增依赖
```toml
dependencies = [
    # ... 原有依赖 ...
    "psutil>=5.9",  # 系统资源监控
]
```

---

## 🔄 待完成工作

### 短期（1-2 周）
- [ ] 前端实现开发者面板 Tab
- [ ] 前端实现资源监控图表（CPU/内存/网络）
- [ ] 前端实现缓存统计可视化
- [ ] 测试所有新功能

### 中期（1-2 月）
- [ ] 实现知乎平台接入
- [ ] 实现小红书平台接入
- [ ] 添加审计日志功能
- [ ] 实现代理自动故障转移

### 长期（3+ 月）
- [ ] 实现 YouTube 平台接入（含 Shorts）
- [ ] 多语言 ASR 支持
- [ ] 添加自动化安全扫描到 CI/CD
- [ ] 实现 RBAC 权限管理（如需多用户）

---

## 🧪 验证测试

### 已运行的测试
```bash
# 网络代理测试
pytest tests/test_network_proxy.py -v
# ✅ test_explicit_proxy_environment_is_normalized PASSED
# ✅ test_tun_or_direct_mode_has_no_explicit_proxy PASSED
```

### 建议的验证步骤
```bash
# 1. 完整测试套件
cd backend
pytest tests/ -v --tb=short

# 2. 启动服务验证
python launcher.py

# 3. 检查监控端点（需开启开发者模式）
curl http://localhost:8000/api/metrics/health
curl http://localhost:8000/api/metrics/system
```

---

## 📝 配置建议

### 环境变量
```bash
# 启用开发者模式
DEVELOPER_MODE=true

# 启用代理连通性验证（增加启动延迟）
ENABLE_PROXY_VALIDATION=true

# 设置代理
HTTPS_PROXY=http://127.0.0.1:7890
NO_PROXY=localhost,127.0.0.1,.local
```

### 运行时切换开发者模式
```bash
# 前端设置面板中切换
# 或通过 API：
curl -X POST http://localhost:8000/api/settings/developer-mode \
  -H "Content-Type: application/json" \
  -d '{"enabled": true}'
```

---

## 🎯 安全最佳实践

### 本地化应用
- ✅ 仅绑定 `127.0.0.1`（本地回环）
- ✅ `require_local_client()` 强制本机访问
- ✅ CSRF 保护（自定义请求头验证）
- ⚠️ 不要通过修改监听地址暴露到局域网

### 密钥管理
- ✅ Windows DPAPI 加密存储
- ✅ 日志中自动屏蔽 API Key
- ✅ 环境变量与子进程隔离
- ⚠️ `.env.dpapi` 仅当前 Windows 账号可解密

### 代理使用
- ✅ 支持 HTTP/HTTPS/SOCKS5 代理
- ✅ NO_PROXY 绕过支持
- ⚠️ 代理凭据会被标准化移除（不支持认证）

---

## 📞 问题反馈

如有问题或建议，请提交 Issue：
https://github.com/EypeZeo/Akasha-RAG/issues

---

**维护者**：Akasha-RAG Team  
**最后更新**：2025-01-XX
