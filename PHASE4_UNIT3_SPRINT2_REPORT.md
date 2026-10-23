# Phase 4 / Unit 3 / Sprint 2 — Release & Rollback Acceptance

2026-10-23 — **CONDITIONAL PASS**。Windows 本地隔离范围内，发布检查及两类回滚通过；不是远程 Actions 或正式生产发布认证。

## Git 与变更范围

HEAD 仍为 `1e669a55b3cce245328a7393539ac9882554991e`。未 commit/push/deploy。保留所有 Sprint 1/2 未提交改动和历史证据。

累计 Sprint 2 改动：两个 `.github/workflows/*-ci.yml`、`backend/scripts/ci_tests.py`、`backend/scripts/check_release_files.py`、`backend/tests/test_schema_release.py`、发布手册和本报告。安全修复仅修改 `frontend/package.json`/`package-lock.json` 中 Next.js 配套依赖。本次恢复验收没有再改产品源码、依赖或 CI 策略，只更新本报告、`backend/docs/release-rollback.md`，并在新的 `.local` 目录中修正/运行实验夹具。

旧报告保存在 `.local/release-resume-20261023/report-before.md`；原 GBK 失败夹具和 Next.js 失败日志不覆盖。开始文件哈希清单为该目录的 `before.json`，结束差异为 `preservation.json`。当前未提交内容不属于 HEAD；正式发布仍需经授权提交，记录该 SHA 并对其执行门禁。

## 安全状态

已安装、package.json 精确版本和 lockfile 一致：`next=16.4.1`、`eslint-config-next=16.4.1`，`react/react-dom=19.2.4` 不变。官方 [2026-10-14 公告](https://nextjs.org/blog/nextjs-security-update-october-14-2026) 指定 16.4.1 修复本次三项 Next.js 漏洞。先前 `npm ci` 和生产页面冒烟记录保留在 `.local/next-security-20261023/`；本次重新 lint/test/build，全程无安全绕过。

本次生产依赖审计为 **0**；完整审计仍非零：5 个高危包条目来自同一链 `eslint-config-next → @next/eslint-plugin-next → fast-glob → micromatch → braces@3.0.3`，不是五个独立根因。[GHSA-vfj7-8cjw-p6xm / CVE-2026-93687](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm) 描述深度嵌套模式造成栈耗尽，公告当前无 patched version。

暴露边界是开发/lint 工具链处理模式输入；当前锁依赖不在生产审计集合内，但不能据此称开发环境无风险。限制不可信 glob/config 输入，隔离 CI、不提供生产密钥，保留任务超时，跟进上游修复。没有执行 audit fix --force 或降级 Next.js lint 配置。本次没有证明该漏洞在本项目可利用，也没有隐藏告警。

## CI 与实际检查

保留既有 CI：Windows Python 3.12 完整后端/Ruff/私有路径检查；Ubuntu + PostgreSQL 18.6 service 的恢复/schema lane；Ubuntu Node 22 前端 npm ci/lint/test/build。两份 YAML 可解析，人工检查工作目录、触发器、隔离变量和服务健康检查；这不是 GitHub workflow 远程执行验证。

`ci_tests.py` 明确输出发现/通过/失败/跳过；PG lane 缺环境直接失败，只允许 SQLite 专用负向测试跳过。路径检查不是内容级 secret scanner。正式 release 必须手动触发两条 workflow 于相同 SHA，不能因 path filters 没跑就放行。

| 本次检查 | 结果 | 证据 |
|---|---|---|
| Windows 后端完整 lane | 361 项：352 通过、9 PG 条件跳过，176.633 秒 | `.local/release-resume-20261023/backend.log` |
| 真实 PostgreSQL lane | 39 项：38 通过、1 SQLite 专属负向测试跳过，68.360 秒 | `.local/release-resume2-20261023/postgres-regression.log` |
| Frontend | Lint 通过，15 测试通过、0 跳过，build 通过 | `.local/release-resume-20261023/frontend-*.log` |
| Ruff、配置路径检查、diff check | 通过 | 同目录 `ruff.log`、`private-paths.log`；结束执行 diff check |
| npm audit --omit=dev | 退出 0、0 告警 | 同目录 `audit-production.json` |
| npm audit 全依赖 | 退出非零、5 high、0 critical | 同目录 `audit-all.json` |
| GitHub Actions | 未执行，无 push | 不声称远程 PASS |

两组后端测试重叠，不累加。PG 测试使用 Windows Python 连接 Docker PostgreSQL，验证的是新 lane 的命令和 fixture；没有在 Ubuntu runner 重现完整环境。本机 Node 24.14.1/npm 11.11.0 与 CI Node 22 存在环境差异。本次复用已有安装；上一安全修复已运行 npm ci。

## 发布实验、配置与身份

成功证据：`.local/release-resume2-20261023/result.json`、`verify_release.py`、各 `case-*.log`。源是当前实际代码的独立副本，良好副本逐文件 SHA-256 位于 `release-code-manifest.json`，聚合身份：

`6f5ddcba006978181fbc7de5c15cfb9f545e8733f8b6fa904335c473c1214031`

结果记录基线 HEAD 及 requirements/package/lock 哈希，`python-packages.json` 记录实际 Python 包版本；结束逐文件复核良好副本未变。该内容身份是实验快照，不是假称已提交的正式 release。故障只注入副本 `app/main.py`，不修改产品源码。

使用两个自建 PostgreSQL 18.6 容器、回环随机端口、专用 `recovery_protocol`/`/tmp/backup-test/pgdata`、新文件目录和完整合成配置。镜像：`postgres@sha256:77f585114c32fbca283dc835b0596f4e52b51b4c6662d7810b2f4084f60a1873`。临时环境文件在每次启动结束删除，配置不来自真实部署，密钥不进入报告。测试 Embedding，Persistent Worker 关闭；真实 Windows Provider 子进程调用本地 HTTP Mock LLM，无付费调用。

数据创建后停止写入者，在应用故障实验前制作并验证 Sprint 1 成对备份。数据库身份、清单模型版本、文件哈希和原加密密钥均匹配；备份含终态删除恢复元数据，无 pending 操作。良好 CLI `serve` 的 schema preflight、lifespan、health/readiness、真实 TCP 登录/me、文档内容及 Chat 幂等重放通过。Windows CTRL_BREAK 后有 Application shutdown complete 且进程退出；退出码为 3，不伪称零退出。

## 两类回滚结果

**Case A — 应用启动失败：通过。**

1. 备份已存在且验证通过，良好代码通过 smoke 后关闭。
2. 故障副本抛出明确合成启动异常，退出 1，记录为 REJECTED。
3. 改回同一良好副本/配置/数据库和文件对，启动和 TCP smoke 再次通过。
4. 15 张表的逐行规范化哈希/计数在前后相同，文档字节一致。没有通过清空数据库来恢复功能。

**Case B — 数据库结构不兼容：通过。**

1. 仅在实验源数据库改名 users.email，模拟不兼容状态；不是生产迁移。
2. 良好旧代码 CLI 在监听前以 Missing schema column 拒绝启动，退出 1，code-only rollback 不被接受。
3. 使用既有 backup.restore 将兼容备份恢复到第二个独立空数据库和新目录，保持旧失败目标不覆盖；模型/文件/schema 验证通过。
4. 15 张表恢复哈希/计数与备份前一致，文档字节一致；CLI TCP 登录/文档/幂等重放通过。
5. 原外部合成 Fernet key 解密成功；另通过 TestClient 检查启动恢复 ready=200 和新的 Mock Chat。新 Chat 为 ASGI 验证，不能称它是 TCP 路径。
6. 额外合成 `.restore-incomplete` 标记使 CLI 拒绝启动（退出 1）。夹具随后移除自己加在已成功恢复目标上的测试标记；这不是绕过失败恢复的操作步骤。

同轮验证错误 PGDATA、损坏归档、缺失文件拒绝。结果 PASS 后两个自建数据库容器/卷均停止删除，独立 docker 查询确认不存在；应用和 Mock 服务退出，数据/归档/日志作为证据保留。Docker Desktop 保持运行，没有停止用户服务。

历史失败：`.local/release-20261023/` 保留 GBK 问题；本次 `.local/release-resume-20261023/experiment.log` 记录 Docker Engine 未启动，未创建容器。启动 Docker Desktop 后在新目录重跑成功，不把环境失败计入产品回归。

## 可复现命令与操作流程

仓库根目录，复制到全新同层目录，保留旧结果：

```powershell
New-Item -ItemType Directory .local/release-NEW-RUN
Copy-Item .local/release-resume2-20261023/verify_release.py .local/release-NEW-RUN/verify_release.py
backend/.venv/Scripts/python.exe -X utf8 -B .local/release-NEW-RUN/verify_release.py
```

要求 Windows、Docker Desktop 和已有 postgres:18-alpine（记录/核对上述 digest）。夹具复用当前代码及既有实验工具；新运行必须重新记录代码哈希。自动建立/校验独立数据库、执行 `scripts/ci_tests.py postgres` 并清理自己的容器，不要手工将测试 URL 改为实际数据库。

从 backend：`python scripts/ci_tests.py all`、`python scripts/check_release_files.py`、`ruff check .`。PG lane 用显式隔离 URL/精确 PGDATA 后执行 `python scripts/ci_tests.py postgres`，不回退 SQLite。从 frontend：`npm run lint`、`npm test`、`npm run build`、`npm audit --json`、`npm audit --omit=dev --json`。实际用 backend/.venv 的 Python，最后 `git diff --check`。

正式人工流程见 [release-rollback.md](backend/docs/release-rollback.md)：选取授权不可变 SHA；独立 checkout/匹配依赖及绝对 storage root；验证配置与备份；阻断流量、停止全部写入者、确认进程清理；最终成对备份；切换代码；原 CLI 预检与 smoke；记录 ACCEPTED/REJECTED/ROLLED_BACK。兼容时仅回退代码，不兼容/未知时保持停写并成对恢复到新目标，明确恢复点后写入可能丢失。不得自动迁移、删除恢复标记或运行双 API。

## 验收范围与剩余条件

1. **本地隔离验收通过，正式发布仍有门禁**：尚无包含未提交工作的新 release SHA，也未执行远程 Actions；CI Node/OS 差异尚需远程验证。必须对最终授权版本重新验收。
2. **离线恢复边界不变**：操作者必须停止所有文件/数据库写入者，同模型/同 PG 主版本，显式新目标；不保证在线备份、任意降级、跨版本或断电一致性。本次未重验真实模型/Persistent Worker 的版本切换，也不是前后端整体 TCP 联动测试。
3. **开发依赖风险保留**：braces 链仍有 high 告警，按上述隔离和输入控制管理并跟进补丁；生产审计零不等于所有软件无漏洞。

没有其他已识别的本次阻断；不扩建发布基础设施。到此停止，不开始 Sprint 3。
