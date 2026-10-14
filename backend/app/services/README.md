# Services：业务逻辑按职责归组

接口文件负责接收请求、鉴权和编排操作；这里负责执行具体业务逻辑。部分服务也查询数据库，因此它们并非全部是纯函数，也不是独立部署的服务。

## 从哪里开始读

- 想看“系统怎样回答”：`chat/answers.py`，入口 `grounded_answer`。
- 想看“资料怎样被找到”：`knowledge/retrieval.py`，入口 `retrieve`。
- 想看“学习时为什么选这些资料”：`learning/materials.py`，入口 `acquire`。
- 想看“学习笔记怎样更新”：`learning/state.py`，入口 `apply_observation`。
- 想看“怎样请求用户配置的模型”：`llm/completion.py`，入口 `provider_completion`。

完整请求入口仍在 `app/api/routes/chat.py`；文档上传、入库和失败恢复的编排在 `app/api/routes/knowledge.py`。目录归组没有把这些流程改成异步队列或新 Agent 框架。

## 文件职责

| 文件 | 负责什么 |
| --- | --- |
| `chat/answers.py` | 组织上下文、生成回答、协调引用校验，保留可验证的内容 |
| `chat/routing.py` | 判断普通问答是否需要资料，读取上轮资料策略 |
| `chat/language.py` | 决定回答使用中文还是英文，处理明确语言偏好 |
| `chat/citations.py` | 检查引用和原文对应、纠正唯一可确定的编号、解析事实审计结果 |
| `knowledge/retrieval.py` | 验证空间归属、提供数据与模型路径、调用索引子进程、检索就绪片段 |
| `knowledge/scoring.py` | 对候选片段评分与排序，不调用模型生成答案 |
| `knowledge/index_worker.py` | 在独立进程解析文档、切块、生成向量并限制资源；不持有业务数据库或模型服务凭据 |
| `learning/materials.py` | 识别学习规划、讲解或文档问答，选择文档、抽样概览和限定范围的检索 |
| `learning/state.py` | 学习目标、观察、笔记快照与回顾生成 |
| `learning/response.py` | 将模型生成的讲解与教学动作组成正文，并同步笔记下一步 |
| `llm/completion.py` | 读取用户模型配置、解密凭据、应用结构化输出协议，统一调用和错误处理 |
| `llm/provider.py` | 调用 OpenAI-compatible 服务，约束超时、重试与子进程，解析模型响应 |
| `llm/protocol.py` | 定义 JSON 格式、模型输出解析与错误类型 |
| `runtime_metrics.py` | 记录耗时、调用阶段与错误类别，不记录对话正文和密钥 |
| `deletion_service.py` | 清理对话相关数据库记录，被对话和空间删除共同使用 |

各目录的 `__init__.py` 只声明包，不执行业务操作，也不批量导入其他服务。

模型下载准备属于运行工具，已移到 `app/cli/prepare_embeddings.py`；现有本地 `prepare` 命令继续使用它。原空文件 `auth_service.py` 已移除；认证实现仍在 `api/routes/auth.py` 与 `core/deps.py`、`core/security.py`。

## 为什么保留这些拆分

检索找到相关片段，不代表回答得到支持，所以引用校验独立。模型调用处理网络、凭据与协议，回答生成处理内容，两者也独立。学习笔记是已保存状态，教学正文是生成内容，通过 `learning/response.py` 对齐，不各自生成下一步。

`runtime_metrics.py` 和 `deletion_service.py` 被多个业务使用，留在顶层。无需为两个共享模块再增加一层目录。

## 主要调用关系

```text
api/routes/chat.py
  ├─ chat/routing.py         普通问答的资料使用决策
  ├─ learning/materials.py   学习任务的资料选择 → knowledge/retrieval.py
  ├─ chat/answers.py         回答生成 → llm/completion.py → llm/provider.py
  │   ├─ chat/citations.py   引用与事实校验
  │   └─ learning/response.py 正文与笔记共用教学动作
  └─ learning/state.py       保存学习观察与笔记

api/routes/knowledge.py
  └─ knowledge/retrieval.py → knowledge/index_worker.py（等待子进程结果）
```

这表示职责和依赖，不代表每个请求都会走所有分支。学习概念讲解可以不检索资料，文档问答仍要求证据。回顾生成由 `learning/state.py` 使用相同 `llm/provider.py`，保留独立的回顾格式。

## 本次迁移对照

原平铺模块按上表归组：`rag_service.py` → `chat/answers.py`、`citation_service.py` → `chat/citations.py`、`smart_routing.py` → `chat/routing.py`、`conversation_language.py` → `chat/language.py`；三个资料模块移入 `knowledge/`，三个学习模块移入 `learning/`。

`nvidia_nim_api_service.py` → `llm/provider.py`，因为实现已支持通用兼容接口；`generation_protocol.py` → `llm/protocol.py`。原 `rag_service.py` 内的模型配置和调用函数移到 `llm/completion.py`，资料选择和意图识别直接依赖该入口，无需依赖回答生成模块。

应用、测试、CLI 和本地报告工具的导入路径同时更新，没有保留旧路径的转发文件。新增调用请直接依赖负责该职责的模块，避免把逻辑重新聚集到一个“大 service”中。
