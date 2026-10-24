# Asteria

[English](README.md) | **简体中文**

**你的 Personal Learning Copilot。**

把学习资料、问题和思考放在同一个空间里。从一个问题开始，在解释、练习与反馈中逐步理解，再用学习记录与回顾接续下一次探索。

## 你可以用它做什么

- **整理学习空间**：按课程或主题组织 PDF、Markdown 和 TXT，管理资料与历史对话。
- **随时提问**：解释概念、讨论想法，或结合自己的资料寻找答案。
- **深入学习**：带着目标展开对话，获得解释、提示、练习与反馈；也可以直接要答案、跳过问题或改变重点。
- **保留学习脉络**：学习对话常驻显示下一步、当前重点和笔记（桌面右栏、窄屏顶部卡片），支持编辑；结束时生成并调整回顾，下次继续学习。

## 功能

- **自然使用资料**：无需选择检索模式，系统根据问题决定是否使用学习空间资料。
- **可核对的来源**：资料回答可展开来源面板，查看引用片段与 PDF 原始页码。
- **适应你的学习节奏**：问答与学习两种对话方式，学习过程不强制固定阶段。
- **中英文交流**：支持简体中文与英文界面；回答语言遵循交流上下文与明确要求，历史内容保持原样。
- **连接自己的模型**：填写兼容 OpenAI 的 API 地址、API Key 和模型名称即可配置服务。
- **本地保存**：资料在本机解析与索引，学习记录保存在本地数据库。回答所需的上下文会发送给你配置的模型服务。

## 本地使用

当前提供 Windows / PowerShell 本地运行方式，需要 **Python 3.12、Node.js 22.13+、Docker Desktop**，以及可用的 OpenAI-compatible 模型服务。

### 首次安装

克隆仓库并进入目录：

```powershell
git clone https://github.com/Lunac1a/Asteria.git
cd Asteria
```

为新环境创建本地 PostgreSQL：

```powershell
docker run -d --name asteria-local-mvp-db -p 127.0.0.1:55432:5432 -e POSTGRES_USER=asteria -e POSTGRES_PASSWORD=local-development-only -e POSTGRES_DB=asteria_local -v asteria-local-pg:/var/lib/postgresql postgres:18-alpine
```

安装后端、生成本地配置并准备数据库与 Embedding 模型：

```powershell
Push-Location backend
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe app/cli/local.py configure
.venv/Scripts/python.exe app/cli/local.py init
.venv/Scripts/python.exe app/cli/local.py prepare
Pop-Location
```

安装并构建前端：

```powershell
Push-Location frontend
npm ci
npm run build
Pop-Location
```

以上步骤用于全新环境；已有配置与数据库的用户直接使用下方启动方式。首次准备 Embedding 模型需要联网下载。

### 启动与停止

需要启用 Persistent Worker、核对 Readiness 或进行可控优雅关闭时，使用
[Windows 部署基线](backend/docs/windows-deployment.md)中的前台启动流程。
下方快捷脚本的停止方式为强制终止，不能替代优雅关闭验证。

打开 Docker Desktop，在项目根目录执行：

```powershell
.\Start-Asteria.ps1
```

打开 **[http://localhost:3001](http://localhost:3001)**，注册或登录，在 **Settings / 设置** 中填写模型 API 地址、API Key 和模型名称。接口地址填写 API 根路径，例如 `https://api.example.com/v1`。

服务地址需要加入 `backend/.env` 的 `LLM_ALLOWED_BASE_URLS`，修改后重启后端。密钥只在设置页填写，不提交到 Git。

停止应用：

```powershell
.\Stop-Asteria.ps1
```

默认保留数据库；若也需要暂停数据库，可使用 `Stop-Asteria.ps1 -Database`，数据卷不会删除。

### 开始你的第一次学习

1. 创建一个学习空间，例如一门课程或一个主题。
2. 上传可读取的 PDF、UTF-8 Markdown 或 TXT，等待处理完成。
3. 选择 **问答**，直接提问；或选择 **学习**，说明你想理解的目标。
4. 需要时展开 Sources 核对资料，编辑 Learning Notes 记录自己的理解。
5. 结束时编辑并保存 Recap，下次从首页或历史对话继续。

## 架构与运行边界

```text
Next.js → 同源 /api → FastAPI（单进程）→ PostgreSQL
                         ├─ FastEmbed 检索与 Persistent Query Worker
                         └─ 每次调用独立的 Provider 子进程 → OpenAI-compatible API
```

当前验证范围为 **Windows 单 API 进程 + PostgreSQL**。Worker Supervisor 使用 Windows Job Object 管理进程树，提供有界恢复与清理失败时停止恢复的保护。Linux、多 API 实例和生产容量不在已验证范围内。Persistent Worker 和可选 Session 并发需要按部署文档显式配置，不应把 Benchmark 配置当作默认配置。

后端保留请求幂等与 Singleflight、Session/Workspace 协调、事务一致性、HTTP 分阶段耗时和 Worker 生命周期事件。部署、备份、回滚与故障处置使用下方运行指南。

## Benchmark 工程结果

使用真实 TCP HTTP、隔离 PostgreSQL 18.6、离线 FastEmbed、固定合成资料和本地确定性 Mock LLM，在保留 Provider 子进程隔离的条件下验证了一项导入优化：

| 同条件 A/B 指标 | 结果 |
| --- | --- |
| Provider 平均耗时 | 1623.98 → 1492.16 ms（降低 8.12%） |
| 成功 Chat 服务端平均耗时 | 1766.80 → 1635.38 ms（降低 7.44%） |
| Provider 每次调用 CPU 时间 | 降低 10.30% |
| 1.5 req/s 输入下成功吞吐 | 两组均为 0.9917 req/s |
| HTTP 429 拒绝率 | 两组均为 33.33% |

结论是延迟和 CPU 成本改善，**没有证明该负载下容量提高**。这些数据来自本地 Mock 的固定负载实验，不代表真实模型或生产环境性能。实验方法、正确性检查和限制见 [Benchmark 摘要](backend/docs/benchmark-summary.md)。

## 管理对话与学习空间

在列表或当前对话顶部的「···」菜单中选择删除。删除对话会保留空间与资料；删除学习空间会同时删除所属资料和对话，目前仅允许在管理员确认的单实例维护模式下执行，普通运行默认拒绝。当前版本没有回收站，操作前请确认删除范围。维护与恢复步骤见 [Workspace 恢复操作说明](backend/docs/workspace-recovery.txt)。

## 开发与验证

- [开发与验证](backend/docs/development.md)：项目结构、测试命令与资料使用策略。
- [Windows 部署](backend/docs/windows-deployment.md)：配置、启动、Readiness 与优雅关闭。
- [备份与恢复](backend/docs/backup-restore.md)、[发布与回滚](backend/docs/release-rollback.md)。
- [故障响应](backend/docs/incident-response.md)、[Worker 生命周期事件](backend/docs/worker-lifecycle-events.md)。
- [测试与证据管理](backend/docs/verification.md)、[Benchmark 工作负载设计](backend/docs/benchmark-workload-design.md)。

`backend/tests/` 和 `frontend/tests/` 保留可执行回归测试；`backend/tests/experiments/` 保留可复用实验工具。完整历史报告归档到本地 `docs/`，原始实验数据保留在被忽略的 `backend/tests/experiments/evidence/` 与 `.local/`，不随仓库发布。公开文档只保留经过整理的方法、结果与限制。

## License

[MIT](LICENSE)
