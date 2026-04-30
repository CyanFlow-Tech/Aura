# Aura Gateway 重构分析报告

## 概览

本轮重构完成了三类核心治理：

1. 将运行时代码整体迁移到 `aura/` 包目录。
2. 将 `SessionManager` 从 FastAPI 异常中解耦，恢复领域层边界。
3. 将 `LLM` 从“`api_url` 同时承担 provider 标识与真实地址”的隐式协议切换，重构为显式 provider 策略。

同时保留了根目录薄兼容入口：

- [server.py](/root/folkspace/Aura/gateway/server.py:1)
- [aura_client_test.py](/root/folkspace/Aura/gateway/aura_client_test.py:1)

这样现有本地命令不会立即失效，但后续推荐统一使用包入口：

- `python -m aura.server`
- `python -m aura.aura_client_test`

## 当前结构评价

迁移后，仓库的 Python 运行时代码已集中在：

- [aura/server.py](/root/folkspace/Aura/gateway/aura/server.py:1)
- [aura/session.py](/root/folkspace/Aura/gateway/aura/session.py:1)
- [aura/pipeline.py](/root/folkspace/Aura/gateway/aura/pipeline.py:1)
- [aura/stages.py](/root/folkspace/Aura/gateway/aura/stages.py:1)
- [aura/channels.py](/root/folkspace/Aura/gateway/aura/channels.py:1)
- [aura/llm.py](/root/folkspace/Aura/gateway/aura/llm.py:1)
- [aura/tools/](/root/folkspace/Aura/gateway/aura/tools)
- [aura/utils/](/root/folkspace/Aura/gateway/aura/utils)

这是比原先“根目录横铺模块”的组织方式更合理的结构，优点是：

- 命名空间清晰，避免顶层模块污染。
- 相对导入明确表达包内依赖。
- 更符合 Python 项目打包、测试和发布习惯。

## 本轮发现并处理的问题

### 1. 顶层平铺结构不利于维护和打包

旧问题：
- 原来 `server.py`、`session.py`、`llm.py`、`stages.py` 等全部位于仓库根目录。
- `tools/`、`utils/` 与顶层模块混合存在，运行依赖依赖 `cwd` 与 `sys.path` 的偶然性。

影响：
- 模块边界不清晰。
- 测试与运行入口耦合工作目录。
- 打包和复用成本偏高。

本次处理：
- 代码迁移到 [aura/](/root/folkspace/Aura/gateway/aura)。
- 所有内部引用调整为包内相对导入。
- 新增包初始化文件：
  - [aura/__init__.py](/root/folkspace/Aura/gateway/aura/__init__.py:1)
  - [aura/tools/__init__.py](/root/folkspace/Aura/gateway/aura/tools/__init__.py:1)
  - [aura/utils/__init__.py](/root/folkspace/Aura/gateway/aura/utils/__init__.py:1)

### 2. `SessionManager` 直接抛 `HTTPException`

位置：
- [aura/session.py](/root/folkspace/Aura/gateway/aura/session.py:38)

旧问题：
- 会话层直接依赖 FastAPI。
- `get_session()` 和 `stream()` 的失败语义被绑定成 HTTP 404/409。

本次处理：
- 引入领域异常：
  - `SessionError`
  - `SessionNotFoundError`
  - `NoActiveTurnError`
- `SessionManager.get_session()` / `subscribe()` 改抛领域异常。
- 由 [aura/server.py](/root/folkspace/Aura/gateway/aura/server.py:53) 完成 API 异常映射。

收益：
- 会话层恢复为纯领域/运行时模块。
- API 层负责 HTTP 语义，职责边界更清晰。

### 3. `LLM` provider 配置和响应解析策略混杂

位置：
- [aura/config.py](/root/folkspace/Aura/gateway/aura/config.py:38)
- [aura/llm.py](/root/folkspace/Aura/gateway/aura/llm.py:28)

旧问题：
- `api_url` 既可能是真实 URL，也可能是 `HUNYUAN` / `ZHIYUAN` 这种 provider 标识。
- provider 协议切换依赖字符串隐式约定。
- `parse_hunyuan()` 和 `parse_zhiyuan()` 基本重复。

本次处理：
- 新增显式字段 `provider`。
- 引入 `LLMProviderSpec` 和 `PROVIDER_SPECS`。
- 将 SSE delta 解析合并为 `_parse_sse_choice_delta()`。
- 保留对旧配置形式的兼容回退。

收益：
- 配置语义更清楚。
- 扩展新 provider 时不再依赖堆条件判断。

### 4. 会话流订阅语义更明确

位置：
- [aura/session.py](/root/folkspace/Aura/gateway/aura/session.py:233)

本次处理：
- 将“获取订阅通道”和“异步流式遍历”拆开为：
  - `subscribe()`
  - `stream()`

收益：
- `server.py` 可以先完成会话状态校验，再消费流。
- 409 语义不再埋在异步生成器内部。

### 5. 包迁移期间的导出错误已修正

迁移过程中出现过一次 `aura/tools/searching/__init__.py` 误写入顶层 `tools` 导出的情况，现已修正为：

- [aura/tools/searching/__init__.py](/root/folkspace/Aura/gateway/aura/tools/searching/__init__.py:1)

这类问题说明批量移动文件时仍应分层执行并立即校验包出口。

## 上一轮已完成且仍有效的治理

### 1. 抽取 `BaseConversationStage`

位置：
- [aura/stages.py](/root/folkspace/Aura/gateway/aura/stages.py:70)

### 2. 为 `QueueChannel` 增加 `replay()`

位置：
- [aura/channels.py](/root/folkspace/Aura/gateway/aura/channels.py:74)

### 3. 让 `FactoryMixin.build()` 变为无副作用

位置：
- [aura/utils/polymorphic.py](/root/folkspace/Aura/gateway/aura/utils/polymorphic.py:27)

## 仍然存在的工程问题

### 1. `pipeline.py` 仍是手工场景装配

位置：
- [aura/pipeline.py](/root/folkspace/Aura/gateway/aura/pipeline.py:44)
- [aura/pipeline.py](/root/folkspace/Aura/gateway/aura/pipeline.py:81)

### 2. `PipelineBundle` 仍使用弱类型集合

位置：
- [aura/pipeline.py](/root/folkspace/Aura/gateway/aura/pipeline.py:30)

### 3. `audio_stream()` 入口函数仍较复杂

位置：
- [aura/server.py](/root/folkspace/Aura/gateway/aura/server.py:97)

### 4. `SearchIntentStage` 的 planner 输出仍缺少结构化约束

位置：
- [aura/stages.py](/root/folkspace/Aura/gateway/aura/stages.py:184)

## 建议的下一步

1. 为 `aura` 包补充基础单元测试，先覆盖 `session.py`、`channels.py`、`llm.py`。
2. 将 pipeline 场景注册化，减少 `server.py` 的场景判断。
3. 将 `audio_stream()` 的 keepalive 状态机抽出独立组件。
4. 在 `pyproject.toml` 中声明脚本入口，例如 `aura-server`、`aura-client`。

## 验证说明

本轮需要重点验证三类内容：

1. `aura` 包内导入是否全部成立。
2. `python -m aura.server` 是否可加载应用。
3. 根目录兼容入口是否仍可工作。
