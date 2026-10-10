# 开发与验证

## 仓库结构

| 路径 | 职责 |
| --- | --- |
| `backend/app/api` | 接口、认证与请求边界 |
| `backend/app/services` | 检索、生成、引用校验与学习状态 |
| `backend/app/models` / `schemas` | 数据模型与接口协议 |
| `backend/app/cli` | 本地运行、状态检查和独立真实模型验收 |
| `backend/tests` | Mock 模型、独立数据库的自动回归 |
| `frontend/app` / `components` | 页面与交互组件 |
| `frontend/lib` / `tests` | 前端状态逻辑与回归测试 |
| `.github/workflows` | 前后端持续检查 |

`data/`、`docs/`、`.local/` 是忽略提交的本地数据与工作记录，不属于安装依赖。上传文件、数据库、模型缓存和凭据保留在各自原位置。详细历史验收报告通过本地 `docs/README.md` 查找；一次性报告工具在 `data/tools/`，不参与应用启动。

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

后端验证用户归属，并拒绝生成或上传过程中的冲突删除。文件先暂存，数据库提交失败时恢复；提交后清理失败的文件保留在数据目录的 deleted-files。当前本地互斥只适合单 API worker，多 worker 需要数据库级协调，进程在暂存与提交之间退出仍需人工恢复。

使用临时空间检查取消删除、删除对话后资料保留、删除空间后返回列表，以及上传或生成时的冲突提示。自动回归位于 test_deletion.py，使用独立数据库与临时文件。
