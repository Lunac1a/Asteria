# Phase 4 / Unit 3 / Sprint 1 — Database Migration, Backup & Restore

日期：2026-10-23。验收：**CONDITIONAL PASS**。

在明确停止全部写入者、同版本代码、同 PostgreSQL 主版本、独立空目标的范围内，已验证 PostgreSQL 与必需文档文件成对恢复，并恢复登录、文档访问、幂等重放及新 Mock Chat。工具不能自行隔离所有外部文件写入者；无法确认停写时不得运行备份。本结论不包括在线备份或任意版本迁移。

## Git 与改动

起始及结束 HEAD：`1e669a55b3cce245328a7393539ac9882554991e`。未提交或推送。

新增：`backend/app/cli/backup.py`、`backend/app/services/schema_compatibility.py`、`backend/tests/test_backup.py`、`backend/docs/backup-restore.md` 和本报告。

修改：`backend/app/cli/local.py`（启动前兼容性检查）、`backend/app/db/init_local.py`（事务内创建及检查）、`backend/app/main.py`（未完成恢复阻止业务启动）。没有新增数据库迁移、依赖、并发策略或 Worker 改动。

开始时已有未跟踪实验记录，全部保留。对开始时记录的文件重新计算 SHA-256，仅上述三个有意修改的原有源文件不同；见 `.local/backup-restore-20261023/files-before.json`、`preservation-final.json`。没有操作真实部署数据库、文档或密钥。实验仅销毁本次创建的数据库容器和合成源文档。

## 存储架构与一致性边界

PostgreSQL 保存用户及密码哈希、加密 Provider 配置、Workspace、文档元数据、分块及向量、会话/消息/学习数据、证据引用、Chat 幂等结果和 Workspace 删除恢复记录。模型位于 `backend/app/models/`。

文档 `storage_key` 指向 `LOCAL_DATA_DIR/uploads/` 内的相对路径。删除恢复记录包含不可变绝对根目录和文件清单，必须保留。`PREPARED`/`COMMITTED` 操作仍可能需要文件恢复，本工具拒绝在其存在时备份；先按现有维护流程完成恢复，再停止所有写入者。终态记录保留原历史根目录，不改写、不重放。

必需文件为数据库引用的上传文件及终态恢复清单中仍存在的暂存文件。模型缓存可重新准备；日志、锁、临时文件、未引用孤立文件不进入此备份。孤立文件不会被删除。数据库本身含私有文本、密码哈希和加密 Provider 密钥，因此备份仍需受限 ACL 和加密存储。原始 `ENCRYPTION_KEY`、数据库凭据、JWT 密钥须独立安全保管，不能放进普通归档。

停 HTTP 不够：API、Persistent Worker、索引/Provider 子进程、CLI、定时任务、维护任务及外部数据库/文件写入者都必须停止，并防止重新连接。`--confirm-stopped` 仅为操作者确认。工具另持有现有 storage lease、拒绝其他数据库连接、对模型表持 SHARE 锁；这些措施不能阻止不合作的外部文件写入者。隔离实验中写入者均由夹具拥有，备份时已停止。

## 实现与迁移政策

| 位置 | 行为 |
|---|---|
| `backend/app/cli/backup.py:52` | 同时核对 SQLAlchemy 和容器内 PostgreSQL 的数据库名、PGDATA、system identifier；只接受显式本地目标及完整容器 ID |
| `backend/app/cli/backup.py:132` | 离线文件复制、SHA-256/大小、`pg_dump -Fc --no-owner --no-privileges`；清单最后写入，拒绝未完成恢复源 |
| `backend/app/cli/backup.py:169` | 校验版本标识、文件和数据库归档完整性；限制路径、重定向、链接 |
| `backend/app/cli/backup.py:183` | 仅新目录及空数据库，拒绝原 cluster/database；先校验，再标记、复制、事务恢复、检查并最后清除标记 |
| `backend/app/services/schema_compatibility.py:14` | 当前模型 PostgreSQL DDL 的稳定哈希，不是假称 Alembic revision |
| `backend/app/services/schema_compatibility.py:23` | 检查模型表/列存在、类型、nullable；并非完整任意 schema 审计 |
| `backend/app/cli/local.py:81` | 支持的 serve 入口启动前检查 schema 和恢复标记 |
| `backend/app/main.py:35` | 自定义 ASGI 启动也受未完成恢复标记保护，保持现有 readiness/业务闸门 |

Manifest 包括 PostgreSQL 版本、源身份/根目录、模型哈希、反射 schema 和各组件大小/SHA-256。恢复比较反射的列、主外键、唯一约束、CHECK 和索引；不声称验证所有默认值、触发器、权限等。PostgreSQL dump 可等价改写文本枚举数组的 cast，仅对此有限表达式规范化，枚举值仍严格比较。

没有现成 Alembic 或版本表。现有 `create_all` 只建缺失表，不能迁移已有列；现在在 PostgreSQL 事务中创建并检查。未来变更必须先成对备份、隔离演练、审核明确 SQL，再维护执行。此次没有演示性 schema 迁移；未故意注入部分 DDL 失败。回退是恢复匹配代码的数据库与文件备份，不能声称任意 downgrade 安全。自定义 ASGI 启动不具备完整 CLI schema 预检，运行手册明确此边界。

## 验证结果与证据

| 验证类别 | 实际结果 | 证据 |
|---|---|---|
| Windows 全后端 unittest | 359 项，351 通过、8 跳过，113.525 秒 | `.local/backup-restore-20261023/backend-regression-final.log` |
| 独立真实 PostgreSQL 恢复/启动回归 | 38 项，37 通过、1 跳过，48.217 秒；跳过的是 SQLite 专用拒绝测试 | `.local/backup-restore-run3-20261023/postgres-regression.log` |
| 最后新增未完成恢复源保护后的定向测试 | 10 项全部通过，0.072 秒 | `.local/backup-restore-20261023/backup-guards-unsandboxed.log` |
| Ruff | 全部通过 | `.local/backup-restore-20261023/ruff-final.log` |
| `git diff --check` | 通过 | 完成时执行 |
| 独立恢复实验 | PASS | `.local/backup-restore-run3-20261023/result.json`、`verify_roundtrip.py` |

这些测试有重叠，不累加。全套执行时尚未添加最后一个源标记测试；最终小改动另由上述 10 项覆盖。全套的 8 个 PostgreSQL 条件跳过由独立真实 PostgreSQL 模块覆盖。最后定向测试首次在 Windows sandbox 临时目录创建/清理遇到 WinError 5，未进入断言；原日志 `backup-guards-final.log` 保留，正常权限重跑通过，不将其归为产品失败。

实验使用 Windows Python、已有 Docker PostgreSQL 18.6、独立 PGDATA 和回环随机端口；应用通过 FastAPI TestClient/ASGI 调用，**不是 TCP HTTP 或 Next.js 端到端**。Embedding 为测试替身，**未验证 Persistent Worker**；真实 Windows Provider 子进程调用本地 HTTP Mock LLM，无付费调用。

实验先注册登录、设置合成加密配置、创建 Workspace、上传文档、执行带证据的 Chat，插入一条终态 CLEANED 恢复记录。备份后移除合成源容器/卷和源文档，再向独立空环境恢复。15 张表的逐行规范化哈希与计数一致（包含空学习表），文档字节一致；启动恢复 ready=200；登录、用户信息、Workspace、下载、旧 request_id 重放和新 Mock Chat 通过，原外部 Fernet key 可解密恢复配置。未验证 pending 删除操作跨根迁移——工具明确拒绝该情况。

错误目标 PGDATA、缺失文件、损坏 dump 均拒绝；校验失败前不写目标。另以重新计算 checksum 的无效 pg archive 验证 pg_restore 失败留下 `.restore-incomplete`，启动预检拒绝。真实 PostgreSQL 临时改名 `users.email` 可被 schema 检查发现，随后还原。恢复失败没有改变合成源记录。全部实验容器及卷已清理；备份、结果和失败记录保留。

前两轮实验因 dump/restore 的等价枚举 cast 表示差异被严格 schema 比较拒绝；证据保留于 `.local/backup-restore-20261023/` 和 `.local/backup-restore-run2-20261023/`。修正有限规范化后第三轮通过，没有取消 schema 比较。

## 可复现操作

正式操作及目标验证、密钥保管、失败处理详见 [运行手册](backend/docs/backup-restore.md)。从仓库根目录运行独立实验（需要本地已有 `postgres:18-alpine` 和 Docker 权限；每次使用新的证据目录，避免覆盖旧记录）：

```powershell
New-Item -ItemType Directory .local/backup-restore-NEW-RUN
Copy-Item .local/backup-restore-run3-20261023/verify_roundtrip.py .local/backup-restore-NEW-RUN/verify_roundtrip.py
backend/.venv/Scripts/python.exe -B .local/backup-restore-NEW-RUN/verify_roundtrip.py
```

从 `backend` 运行回归与校验：

```powershell
.venv/Scripts/python.exe -B -m unittest discover -s tests -v
.venv/Scripts/python.exe -B -m unittest discover -s tests -p test_backup.py -v
.venv/Scripts/python.exe -m ruff check app tests
.venv/Scripts/python.exe -B -m app.cli.backup verify --bundle D:/Lunacia/Asteria/.local/backup-restore-run3-20261023/backup
git diff --check
```

真实 PostgreSQL 回归由实验脚本在其拥有的恢复目标中设置 `ASTERIA_RECOVERY_TEST_URL`、`ASTERIA_RECOVERY_TEST_PGDATA=/tmp/backup-test/pgdata`，运行 `test_workspace_delete_recovery`、`test_startup_recovery` 后清理；不要将这些测试变量指向用户数据库。

## 已知限制与验收条件

1. 一致性依赖受控离线维护，工具并非通用外部写入隔离系统；停写无法确认即 BLOCKED。已验证本地 Docker PostgreSQL，不声称远程或原生 Windows PostgreSQL 工具路径也已支持。
2. 同代码/同 PostgreSQL 主版本恢复；无在线/WAL/PITR、跨版本迁移、备份 fsync/断电一致性、自动保留策略、roles/ACL 恢复或恶意归档真实性保证。Docker 客户端超时不代表服务器取消，须保持目标离线并人工核查，不能删标记强启。
3. 当前 schema guard 是有限兼容性检查，不是迁移管理系统。此次没有重新验证前端、真实模型或 Persistent Worker；相关先前证据不计为本次结果。

可用于后续提交的摘要：新增显式目标、离线成对备份恢复 CLI；加入模型 schema 和未完成恢复启动保护；保留事务/幂等/恢复元数据；补充完整性保护测试及运维流程。当前改动保持未提交。到此停止，不进入 Sprint 2。
