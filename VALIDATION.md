# Asteria Local MVP Validation

验证日期：2026-10-08。基线 `5847209`；开发分支 `dev/asteria-v2-m0-audit`。验证过程中未部署、未连接原配置数据库。开始时已有 auth.py、backend CI 和 auth tests 的未提交修改，已保留并在其基础上扩展。

## 现有实现与 Git 恢复检查

读取 architecture-audit.md、implementation-plan.md、m1-validation.md；检查当前代码、分支、完整可达历史、相关路径历史、reflog，以及 git fsck 报告的不可达树。未找到 BGE Embedding、ChromaDB、Reranker、RAG Pipeline 的历史实现。可复用部分为账户、JWT/bcrypt、加密个人模型设置、供应商服务、会话/消息表和 Next.js 页面骨架。

本轮按 Product-first 覆盖旧计划中的云部署、Celery/Redis 和基础设施前置要求，没有引入这些组件。

## 环境与独立数据

- Python 3.12.10，Next.js 16.2.4，本机 CPU FastEmbed 0.9.0，pypdf 6.19.0。
- 新增独立容器 asteria-local-mvp-db，PostgreSQL 18-alpine；只绑定 127.0.0.1:55432；库 asteria_mvp_test。未修改已有其他数据库容器。
- 合成 .env.mvp-test 使用新 JWT/Fernet secret；不改原 backend/.env 或 frontend/.env.local。原全局 NVIDIA_API_KEY/NVIDIA_CHAT_MODEL 均未配置，只检查有无，没有打印凭据值。
- 前端 http://localhost:3001，后端 127.0.0.1:18001。系统拒绝绑定 8000，故选择新本地端口；使用同源 /api 代理修复了浏览器直接跨域连接失败。
- 独立 browser-mvp@example.com 测试账户；自行生成两页 independent-course.pdf，不使用用户课程资料。第 1 页包含叶绿素/光合作用；第 2 页包含 35% 成绩权重、1200 字报告和虚构截止日期。

## 浏览器真实交互

使用 Codex 浏览器实际操作页面，不用直接写浏览器状态或 API 返回成功代替 UI 结果。

| 步骤 | 观察结果 |
| --- | --- |
| 注册独立用户、登录 | 页面成功进入 dashboard |
| Learn → 创建 COMP101 Workspace | 课程下拉列表出现新空间并选中 |
| 上传 independent-course.pdf | 页面文件列表显示 ready、2 chunks |
| 询问项目成绩占比 | 显示模拟 LLM 摘录，包含 35 percent |
| 展开 Sources | independent-course.pdf、PDF page 2、对应原文显示 |
| 刷新浏览器 | 原 Workspace、会话、问答及引用恢复 |
| New Chat → 重新选择历史会话 | 已有消息重新出现 |
| 继续询问报告字数 | 同一会话追加问题和包含 1200-word 的来源回答 |
| 切换真实 BGE，Reindex | 页面标注 local BGE + simulated LLM，原 PDF 再次 ready |
| 询问吸收太阳光的色素 | 返回 chlorophyll 原文，来源 PDF page 1 |
| API 重启、浏览器刷新 | 原会话及全部历史仍存在 |
| 无关世界杯问题 | 显示资料不足，不展示 Sources |
| Download original | 来源原 PDF 通过鉴权后成功下载 |

完整注册到继续讨论的工程流程先用模拟 Embedding/模拟 LLM 完成。随后在同一页面验证真实 BGE 的重索引和检索；**真实 LLM 没有运行**。答案的措辞/理解质量和真实模型对引文的支持关系尚未验收。

浏览器上传自动化曾出现工具端长等待；结果最终在页面显示就绪。该等待不能被当成索引耗时测量。没有作性能 SLA 声明。

## 真实 BGE 独立检索小样本

BAAI/bge-small-en-v1.5，CPU、384 维，真实 ONNX Embedding；按页分块的上述 PDF。脚本直接测试提问向量和段落向量的余弦相似度。

| 独立问题 | 首位 PDF 页 | 相似度 | 阈值 0.45 |
| --- | --- | --- | --- |
| What percentage of the grade is assigned to the final project? | 2 | 0.8129 | 保留，符合预期页 |
| Which pigment absorbs sunlight in photosynthesis? | 1 | 0.7916 | 保留，符合预期页 |
| What is the report word count? | 2 | 0.6528 | 保留，符合预期页 |
| Who won the 2022 football world cup? | 2 | 0.4112 | 过滤，无来源 |

原始独立结果保存在本地 data/mvp-test/bge-validation.json，不随 Git 提交。3 个相关问题定位页正确，1 个无关问题被过滤，仅限这个小样本，不是模型质量基准，也不能证明中文检索或任意问题的拒答可靠。

## 自动化及构建

最终 PostgreSQL 命令为在 backend 设置独立 ASTERIA_TEST_DATABASE_URL 后执行 unittest discover。LearningAPITests 的 14 个测试各自使用随机 schema，结束仅删除自己创建的测试 schema；已有 3 个认证回归使用 SQLite；4 个 SDK contract 检查使用内存 HTTP 模拟。

- **21/21 tests 通过**，21.019 秒。此前 13 项 SQLite 基础流程也通过；新增后的最终数据库集成结果以上述 PostgreSQL 运行计。
- 后端 ruff check 通过。
- 前端 TypeScript、ESLint 和最终生产 build 通过。
- git diff --check 通过；只有 Windows 行尾转换提示。
- 没有触发 GitHub 远程 CI，不能把本地结果称为 CI 已通过。

覆盖账户与 Workspace/会话/文件跨用户隔离、跨 Workspace 不检索、页码、来源持久化、刷新续聊的数据契约、同 ID 幂等、删除文件及向量、保存历史引用快照、失败轮次不落库、无证据/假引用拒答、坏文件/扫描件/大小限制/超时后重试、忙时拒绝额外 worker、Key 保留/URL allowlist/损坏密文、非法登录与 Token、bcrypt 字节边界、错误响应不回显凭据、上传暂存前体积限制，以及模型重定向不转发 Key、429 不自动重试、输出 token 上限和 45 秒子进程总期限。

模型超时测试对 subprocess.TimeoutExpired 注入可控替身，证明错误映射与设置的 hard deadline；没有等待真实供应商 45 秒。SDK contract 用真实 SDK 解析合成 HTTP 响应，不调用真实模型。另外实际运行供应商子进程，连接一次临时 loopback HTTP 模拟服务并成功返回合成答案；证明子进程隔离与 SDK 调用路径可运行，仍不是真实 LLM 验证。用户隔离与数据契约是工程验证，不等于 RAG 答案质量。

## 尚存边界

- 缺少真实 LLM 凭据，因此完整真实 AI RAG 验收仍待完成；默认演示继续明确标注模拟 LLM。
- 默认模型为英文 BGE，中文/跨语言未验证；复杂 PDF 表格/公式和 OCR 未覆盖。
- 单 API worker、受限空间规模、应用级本地并发；不是多进程或生产并发方案。
- 向量保存 PostgreSQL JSON，限额内全量余弦扫描，无 Chroma、pgvector 或 Reranker。
- 来源编号从实际片段校验，但不能算法保证模型每一句事实都有来源支持；需真实课程题集和模型质量评估。
- 超时/进程中断文档手动 Reindex；没有后台任务队列、租约、自动重启恢复。
- 保存最近会话列表及消息有明确上限；旧未绑定 Workspace 会话保留 API 读取入口，尚未增加页面迁移/绑定功能。没有会话删除/导出功能。
- 单个成功 turn 有幂等，但进程在供应商回答后、数据库提交前崩溃仍可能导致再次调用；没有宣称模型调用 exactly-once。
- 文件和数据库不是单一事务，突然崩溃可能留下孤立本地文件；未实现一致性清理工具。
- 本地截图、原始实验数据和早期审计报告沿用忽略规则；公开运行说明和验证记录分别在根目录 LOCAL_MVP.md 与 VALIDATION.md。

后续先完成真实凭据与课程数据验收，再以规模需求推进版本化迁移、数据库级并发控制、分页、会话生命周期、token 预算与恢复。大型基础设施继续暂缓。
