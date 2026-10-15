# 开发与验证

## 人工恢复遗留索引

Recovery 默认禁止执行：管理员白名单 `RECOVERY_ADMIN_USER_IDS=[]`，单进程确认 `RECOVERY_SINGLE_PROCESS_CONFIRMED=false`。白名单填写已有用户的 UUID JSON 数组，不授予普通用户恢复权限。

操作者必须先确认旧 API 已退出、当前仅有一个 API 进程（单 worker），且所有索引入口都遵守 `document_slot`。无法确认时保持写入口关闭。`RECOVERY_SINGLE_PROCESS_CONFIRMED=true` 是人工运维确认，不是自动进程探测；不要用于多实例、滚动重启或多 worker 部署。

管理员使用现有登录 Bearer token，POST `/api/admin/document-recovery/preview`，请求体 `{"document_ids":["目标文档ID"]}`（最多 20 个），查看状态和实际 Chunk 数量。预览不修改数据库，返回有效期五分钟、绑定管理员和当前进程的 `preview_token`。

核对目标后 POST `/api/admin/document-recovery/execute`，请求体 `{"preview_token":"预览凭证","confirm":true}`。服务在持有文档操作锁期间开启新的读事务并重新查询，仅把仍为 `processing` 的文档改为 `failed`，保留 Chunk、核对计数；`ready`、已失败或不存在的文档跳过。正常索引运行期间拒绝恢复。进程重启或凭证过期后必须重新预览。

恢复提交失败时回滚并释放锁；数据库恢复后重新预览，不盲目假定提交失败。恢复后用户通过已有重新索引接口重试。本机制不负责终止卡住的任务、发现孤儿子进程或扫描所有遗留文档，也不能证明跨进程互斥。测试：在 `backend` 运行 `.venv/Scripts/python.exe -B -m unittest discover -s tests -p test_document_recovery.py -v`。

## 仓库结构

| 路径 | 职责 |
| --- | --- |
| `backend/app/api` | 接口、认证与请求边界 |
| `backend/app/services` | 按 chat、knowledge、learning、llm 归组的业务逻辑，详见 [职责说明](backend/app/services/README.md) |
| `backend/app/models` / `schemas` | 数据模型与接口协议 |
| `backend/app/cli` | 本地运行、状态检查和独立真实模型验收 |
| `backend/tests` | Mock 模型、独立数据库的自动回归 |
| `backend/docs` | 随代码维护的恢复操作说明 |
| `frontend/app` / `components` | 页面与交互组件 |
| `frontend/lib` / `tests` | 前端状态逻辑与回归测试 |
| `.github/workflows` | 前后端持续检查 |

`data/`、`docs/`、`.local/` 是忽略提交的本地数据与工作记录，不属于安装依赖。上传文件、数据库、模型缓存和凭据保留在各自原位置。详细历史验收报告通过本地 `docs/README.md` 查找；一次性报告工具在 `data/tools/`，不参与应用启动。

Workspace 安全、崩溃与恢复实验按阶段收录在本地 [.local/INDEX.txt](.local/INDEX.txt)。该索引和原始证据不会随 Git 发布；新克隆仓库使用 [恢复操作说明](backend/docs/workspace-recovery.txt)，不要依赖本机实验路径。

## 日常检查

后端使用 Python 3.12；依赖与 Ruff 版本来自 `backend/requirements.txt`：

```powershell
Push-Location backend
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m unittest discover -s tests -q
Pop-Location
```

前端使用 Node.js 22.13 或更高版本：

```powershell
Push-Location frontend
npm run lint
npm test
npm run typecheck
npm run build
Pop-Location
```

`npm test` 运行已有 Node 测试，并启用 TypeScript 类型擦除。前端构建会准备 PDF worker。后端 CI 与本地使用相同 Python 版本，前端 CI 使用 Node 22，并运行 lint、测试和构建。构建与单元测试不会验证真实模型的教学质量。

真实模型验收应单独创建测试空间，通过真实上传、索引、提问、查看来源和刷新历史验证；从 `backend` 目录运行 `python -m app.cli.learning_trial`（使用后端虚拟环境），用 `--help` 查看参数。该工具会调用已配置的真实模型，最多 12 轮；生成失败不会自动重复付费请求。使用 `start --source-workspace <已授权空间ID>` 复制资料到独立试验账户，`upload-existing` 上传，`turn --message '问题'` 提问，`finish` 清理该工具创建的账户和空间。未结束的试验不会被新试验覆盖。记录和资料副本保留在忽略提交的 `data/learning-trial-*`，不得当作公开测试 fixture。

## 学习与资料使用

学习模式先识别任务，再决定是否使用学习空间资料。教学规划与概念讲解允许使用通用知识，文档事实和摘要必须有资料支持。复用现有 Retriever 与检索组件，每轮最多一次语义检索，最终证据最多 8 个片段、12,000 字符；首部、中部、末部采样不代表完整文档摘要。

资料范围与稳定文档顺序保存在 MessageEvidence 策略元数据中，学习焦点保存在 LearningContext。只接受当前用户空间内真实文档 ID；检索继续限定该范围。未就绪、被删除或模型过期的索引不能充当文档证据。

模型生成一份 lesson，正文与常驻学习笔记共用其中的下一步。无动作或动作被校验省略时清除旧建议，不从笔记机械追加问题。语义审计同时检查教学内容是否冒充文档结论。部分段落不能确认时，只保留独立通过校验的段落和安全的教学内容。

### 手动验收

在临时学习空间上传两份不同主题资料，请求学习路线、指定文件问答、概念讲解和“继续上一节”。核对引用支持的真实内容、资料与通用知识区分、下一步与笔记同步；刷新后检查历史与来源仍一致。要求“只解释”或暂停时应清除旧动作。对空空间、未就绪索引与文档未覆盖的数字，不得虚构文件或来源。

Mock 测试验证边界与执行契约；真实模型应逐项检查事实、限定条件和教学价值，不把 HTTP 200 当作语义成功。任务识别仍依赖模型，抽样概览可能漏掉章节；每轮一次检索可能漏召回。未开启语义审计时仅保留原引用格式检查。


### 引用编号与校验

引用编号发生错配时，以本轮已授权片段的文档 ID 为范围，用原文唯一匹配纠正编号，同步正文、claims 与返回来源。纠正后继续执行完整映射检查与逐段语义审计。跨文档、缺失身份、歧义匹配、伪造引文和越界编号不会被自动修复。`Citation binding` 与保存策略中的 `citation_binding` 区分编号纠正和事实校验结果；编号按每条消息定义，允许不同轮次随检索排序变化。

## 删除对话与空间

删除入口位于「···」菜单，操作前显示确认。删除对话清理消息、证据快照、学习笔记、回顾与重试记录，保留空间和资料。删除空间额外清理其文档、片段和上传文件。

后端验证用户归属，并拒绝生成或上传过程中的冲突删除。空间删除仅在显式单实例维护模式下执行：先独立提交文件恢复清单，再暂存文件；业务删除与 `COMMITTED` 状态一起提交，最后清理文件并标记 `CLEANED`。提交结果通过新的数据库事务和行锁判断，不能只根据进程内异常推断。

`STARTUP_RECOVERY_ENABLED` 默认关闭。启用时要求维护模式和单实例人工确认，并在整个 API 生命周期持有本机存储目录锁。Lifespan 完成数据库与文件检查、恢复 `PREPARED → RESTORED` 或清理 `COMMITTED → CLEANED` 后，才打开启动门禁；冲突或数据库不可用时保持 Not Ready。`/api/health` 表示存活，`/api/ready` 检查恢复就绪和未完成状态。Ready 不会解除维护模式限制；请求处理中不会自动恢复正在执行的删除操作。

本机锁只约束遵守同一协议、使用相同存储目录的进程。旧版本、外部写入者及多实例运行仍不受支持；无法确认旧进程退出时不得启用恢复。人工兜底必须停机、保留证据、调用同一恢复校验，再重启复查，详见 [操作步骤与边界](backend/docs/workspace-recovery.txt)。

使用临时空间检查取消删除、删除对话后资料保留、删除空间后返回列表，以及上传或生成时的冲突提示。自动回归位于 test_deletion.py，使用独立数据库与临时文件。

协议测试位于 `test_workspace_delete_recovery.py`，启动与门禁测试位于 `test_startup_recovery.py`；PostgreSQL 专项必须使用显式指定的隔离数据库，配置要求见测试文件。不要用正常开发数据库运行故障注入脚本。

2026-10-16 保存的最近一轮结果：全量 245 项（237 通过、8 项 PostgreSQL 分支跳过）；独立 PostgreSQL 协议 26 项通过、启动专项 11 项通过及 1 项 SQLite 专用测试跳过；独立进程场景 9 项通过，其中两项在恢复中强杀。证据见本地索引。这是已保存的验证记录，不代表每次文档整理都重新执行了测试，也不证明断电或多实例安全。
