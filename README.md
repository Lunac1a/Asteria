# Asteria

**你的 Personal Learning Copilot。**

把学习资料、问题和思考放在同一个空间里。从一个问题开始，在解释、练习与反馈中逐步理解，再用学习记录与回顾接续下一次探索。

## 你可以用它做什么

- **整理学习空间**：按课程或主题组织 PDF、Markdown 和 TXT，管理资料与历史对话。
- **随时提问**：解释概念、讨论想法，或结合自己的资料寻找答案。
- **深入学习**：带着目标展开对话，获得解释、提示、练习与反馈；也可以直接要答案、跳过问题或改变重点。
- **保留学习脉络**：编辑学习笔记，结束时生成并调整回顾，下次继续学习。

## 亮点

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

## 技术栈

Next.js · React · FastAPI · PostgreSQL · FastEmbed · OpenAI-compatible API

## License

[MIT](LICENSE)
