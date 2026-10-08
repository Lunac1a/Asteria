# Asteria v2 — Local MVP

Asteria 是可在本机运行的 Personal Learning Copilot。保留 Next.js + FastAPI + PostgreSQL、bcrypt/JWT、Fernet 加密个人 LLM 设置及现有会话表，新增课程 Workspace、资料索引与可核对的来源片段。

## 快速运行（Windows / PowerShell）

要求：Python 3.12、Node.js 20+、本地 PostgreSQL。可以用 Docker 仅运行数据库，无 Redis、Celery 或云服务。

1. 在仓库根目录启动一个**独立本地开发数据库**。下面的密码仅用于 loopback 开发，不要用于生产：

```powershell
docker run -d --name asteria-local-db -p 127.0.0.1:55432:5432 -e POSTGRES_USER=asteria -e POSTGRES_PASSWORD=local-development-only -e POSTGRES_DB=asteria_local -v asteria-local-pg:/var/lib/postgresql postgres:18-alpine
```

已有本地 PostgreSQL 也可使用，只需单独建立空数据库并填写 DATABASE_URL。不要指向现有生产库。已存在同名容器时用 `docker start asteria-local-db`，不要重复创建。

2. 准备后端。已有 backend/.venv 可跳过创建步骤；根目录遗留 .venv 不使用。

```powershell
cd backend
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe scripts/local.py configure
.venv/Scripts/python.exe scripts/local.py init
.venv/Scripts/python.exe scripts/local.py prepare
.venv/Scripts/python.exe scripts/local.py serve
```

`configure` 写入新 `.env.local-mvp`，生成 JWT/Fernet secrets，不打印它们，不覆盖已有文件。`init` 只在 loopback PostgreSQL 显式创建缺失表，不更改已有表、不删除数据。应用导入及启动不再自动执行 DDL。`prepare` 首次联网下载公开 BGE 模型，无需 Embedding Key；之后请求中的解析和 Embedding 均离线运行。下载失败可重试 prepare，不要设置 Hugging Face token 来掩盖网络问题。

`serve` 只监听 `127.0.0.1:18001`，固定一个 API worker。**不要增加 workers 或启动多个 API 实例**：MVP 的文档和聊天并发限制是进程内实现。服务重启不会删除 PostgreSQL 或本地文件。

3. 在另一个终端启动前端：

```powershell
cd frontend
npm ci
npm run dev
```

打开 [http://localhost:3001](http://localhost:3001)。浏览器统一请求同源 `/api`，Next.js 转发到 `ASTERIA_API_URL`，默认 `http://127.0.0.1:18001`；不再使用 NEXT_PUBLIC_API_BASE_URL。可参照 `frontend/.env.local.example` 设置代理目标。不要把前端或 API 暴露到公网。

4. 注册、登录 → Learn → Create Workspace → 上传文本型课程 PDF → 等待 `ready` → Settings 保存已有 NVIDIA/OpenAI-compatible Key、模型及服务地址 → 回到 Learn 提问 → 展开 Sources 核对文档名、页码与原文 → 刷新或重新选择会话 → 继续提问。

个人 Key 保存在 PostgreSQL 的 Fernet 密文中。省略 Key 更新模型时保留原 Key；空 Key 被拒绝。默认仅允许 `https://integrate.api.nvidia.com/v1`。使用其他**已有**服务时，管理员可在后端环境变量 LLM_ALLOWED_BASE_URLS 中显式添加完整 API 根地址，逗号分隔；页面不能自行添加允许目标。不会自动购买、开通或切换付费服务。真实调用可能消耗已有供应商额度。

## 无凭据时的工程演示

显式创建独立模拟配置，不能把它当成真实 AI 验证：

```powershell
cd backend
.venv/Scripts/python.exe scripts/local.py configure --env-file .env.mvp-test --test-ai
```

先将该文件的 DATABASE_URL 改成独立的本地测试库，例如 `asteria_mvp_test`，LOCAL_DATA_DIR 改为 `../data/mvp-test`；可在上面的 Docker 命令中使用相应数据库名。

```powershell
.venv/Scripts/python.exe scripts/local.py init --env-file .env.mvp-test
.venv/Scripts/python.exe scripts/make_test_pdf.py
.venv/Scripts/python.exe scripts/local.py serve --env-file .env.mvp-test
```

模拟 Embedding 是确定性词项哈希，模拟 LLM 只摘录检索片段；两者没有语义回答能力。页面会显示 Test mode，回答会标注“模拟 LLM”。要单独验证真实 BGE，可将 EMBEDDING_BACKEND 改为 fastembed、RETRIEVAL_MIN_SCORE 改为 0.45，执行 prepare 后重启 API，并对原文档 Reindex。LLM_BACKEND 保持 test 时仍不属于真实 RAG 全链路验收。

## 环境变量

完整样例见 `backend/.env.example` 和 `frontend/.env.local.example`。不要提交 `.env`、生成的 secrets、用户上传或模型缓存。

| 配置 | 默认或用途 |
| --- | --- |
| DATABASE_URL | 独立本地 PostgreSQL，驱动 postgresql+psycopg |
| JWT_SECRET_KEY / ENCRYPTION_KEY | configure 自动生成；不要更换已有库的加密密钥，否则需重新保存个人 Key |
| LOCAL_DATA_DIR | ../data/local-mvp，相对 backend；文件 uploads/ 与模型 models/ |
| EMBEDDING_BACKEND | fastembed；test 是显式测试替身 |
| EMBEDDING_MODEL | BAAI/bge-small-en-v1.5（英文检索）；换模型后 prepare + Reindex |
| LLM_BACKEND | provider；test 是显式模拟 |
| LLM_ALLOWED_BASE_URLS | 可信管理员允许的精确 API 根地址，默认 NVIDIA |
| RETRIEVAL_MIN_SCORE | 0.45；仅为 MVP 初始阈值，没有大规模校准 |
| MAX_UPLOAD_BYTES | 最大 10 MiB，可降低 |
| INDEX_TIMEOUT_SECONDS / QUERY_TIMEOUT_SECONDS | 最大 90 / 30 秒，可降低 |
| MAX_WORKSPACE_CHUNKS | 最大 2000，每次检索加载上限 |
| ASTERIA_API_URL（前端服务端） | 默认 http://127.0.0.1:18001，同源代理目标 |

APP_NAME、APP_HOST、APP_PORT、FRONTEND_URL、JWT_ALGORITHM、JWT_EXPIRE_MINUTES 保留原配置。全局 NVIDIA_API_KEY / NVIDIA_CHAT_MODEL 不再是无关启动条件；真实问答使用登录用户已有的个人设置。

## MVP 功能及架构

- 创建、选择个人课程 Workspace，最多 100 个；所有资料与新学习会话绑定空间。
- 文本 PDF、UTF-8 Markdown/TXT 上传到私有本地目录；列表、processing/ready/failed、Reindex、Delete、鉴权下载。
- PDF 用 pypdf 按真实物理页编号提取；Markdown/TXT 不伪造 PDF 页码。1000 字符分块、200 字符重叠，不跨 PDF 页。
- FastEmbed 在 CPU 上运行 BGE ONNX Embedding，向量与页码、文本存入 PostgreSQL JSON。按授权 Workspace 过滤后做余弦检索，返回最多 5 个来源。无需 ChromaDB 服务或 pgvector 扩展，没有引入 Reranker。
- 复用已有供应商调用和加密个人设置。仅发送有限历史和当前检索片段，要求模型返回 JSON 答案及来源编号。后端校验来源编号，引用卡片只来自真实检索结果；无证据、不支持或无效引用时拒答。
- 复用 chat_sessions/messages；通过增量关联表绑定 Workspace，通过 message_evidence 保存引用快照。成功的一轮用户/助手消息和来源原子保存；失败不遗留半轮消息。同 request_id 可安全重放成功结果；会话 updated_at 正确更新。
- 浏览器保存选中的空间和会话，刷新后从 PostgreSQL 读取历史；支持重新打开与继续。最多展示最近 100 会话、每会话 200 条消息，模型最多使用最近 12 条且总计 12000 字符。

```text
Browser → Next.js /api proxy → FastAPI (JWT + account + Workspace owner checks)
  Upload → local private file → bounded parser/embedding subprocess
         → PostgreSQL documents + page-aware chunks + vectors
  Question → authorized Workspace vector search → existing personal LLM provider
           → validated citations → atomic messages + source snapshots → browser
```

表结构均为新增：workspaces、documents、document_chunks、session_workspaces、message_evidence、chat_turns。旧 users、user_llm_settings、chat_sessions、messages 结构不变。历史未绑定空间的旧会话保留，仍能从原会话 API 读取；当前学习页面展示绑定 Workspace 的会话，不会把旧聊天自动混入课程资料。

## 资源与访问边界

文件上限 10 MiB、100 页、300000 个提取字符、400 chunks/文档、20 文档/Workspace、2000 chunks/Workspace。请求在 multipart 暂存前限制总大小，并限制上传等待时间；解析/Embedding 子进程限制 1.5 GiB 进程内存和 2 个推理线程，到时直接终止。单个模型推理进程、单个文档修改及单个聊天并发，忙时返回可重试的 429，不无限排队。真实 LLM 请求显式 45 秒超时，无自动重试、最多 2048 输出 tokens。

本地单进程实现无需持久化队列。重启中断的 processing 文档在时间窗口过后可手动 Reindex；不会宣称具备后台任务恢复。删除文件同时删除检索向量；历史会话中已有的来源快照保留并标记已删除，不能下载被删除原文。要彻底清除历史引用，需要后续会话删除功能。

认证保留 bcrypt/JWT，账户及 owner 校验覆盖资料、原文件和会话。修复非法 Token sub、畸形登录输入、bcrypt 字节边界、密码日志、验证错误回显凭据、空 Key 覆盖、损坏 Key 和任意 provider URL。默认去掉页面外部遥测及远程字体请求，适合本地运行。

## 验证

详细实际结果见 [VALIDATION.md](VALIDATION.md)。

```powershell
cd backend
.venv/Scripts/python.exe -m unittest discover -s tests -v
# 可选：用独立本地 *_test 数据库运行 PostgreSQL API 集成测试
$env:ASTERIA_TEST_DATABASE_URL='postgresql+psycopg://asteria:local-development-only@127.0.0.1:55432/asteria_mvp_test'
.venv/Scripts/python.exe -m unittest discover -s tests -v
.venv/Scripts/ruff.exe check .
cd ../frontend
npm run lint
npx tsc --noEmit
npm run build
```

API 测试使用合成账户与 AI 替身。PostgreSQL 模式每项测试创建和删除独立随机 schema，拒绝非 loopback 或非 `_test` 结尾的数据库；既有 3 项注册测试仍使用 SQLite。独立 PDF 由 make_test_pdf.py 生成，包含已知评分比例、字数及页码，非用户资料。

## 当前限制与后续 Backend Engineering

目前真实 LLM 凭据未配置，已验证工程闭环与真实 BGE 检索，**尚未通过真实 LLM 的完整 RAG 验收**。默认 BGE-small-en 面向英文，中文和中英跨语言检索尚未验证；需要以真实课程题集评估，再选择多语言模型。复杂表格/公式的 PDF 提取、OCR、文件内恶意指令、事实幻觉都不能由 4 个检索样本消除；引用可核对不等于答案事实必然正确。

下一阶段按产品证据推进：先补真实 LLM/课程评测及无证据、跟进问题、中文资料测试；再做显式版本迁移、数据库级并发与幂等、分页和删除会话、文件/数据库一致性恢复、provider 上下文 token 预算和真实超时测试。资料规模或并发超过本地限额时再评估 pgvector/Chroma、Reranker、持久化任务及 Worker。云部署、Redis/Celery、复杂 Memory/Multi-Agent、Socratic Mode 和高级观测均未实现。
