# Asteria Phase 4 — Final Operations Acceptance

2026-10-23（Australia/Sydney）。结论：**CONDITIONAL PASS，限已验证 Windows 单 API 进程范围**。

本次代表性 Worker 事件可由应用日志识别故障实例、清理判断、重启预算及恢复结果；恢复后的真实 HTTP 功能与数据检查通过。原始进程终止的原因仍不可从现有应用遥测确认。Phase 4 可按下述边界结束，不能视为生产发布、远程 CI 或任意故障恢复认证。

## 已完成能力及证据复核

| 范围 | 已接受能力与证据 | 保留的边界 |
|---|---|---|
| Unit 1 | `PHASE4_UNIT1_SUMMARY.md`：独立 HTTP/业务/Flight 身份、Chat 首层区间及覆盖计算、Worker 生命周期、共享有界异步写入；`.local/unit1-step9-20261022/` | 单进程写入；遥测可丢失，无持久审计保证，无 Worker→HTTP 直接关联 |
| Unit 2 | `backend/docs/windows-deployment.md`、`.local/windows-deployment-smoke2-20261023/smoke-result.json`、`.local/windows-cli-smoke2-20261023/cli-result.json` | Windows Job/FastEmbed、PostgreSQL、Next.js 代理曾验证；本次不重复整套实验，不推广 Linux/多 API 进程 |
| Unit 3 Sprint 1 | `PHASE4_UNIT3_SPRINT1_REPORT.md`、`.local/backup-restore-run3-20261023/result.json` | 离线停全部写入者、同模型/同 PG 主版本、新目标成对恢复；有限 schema 检查，不是通用迁移引擎 |
| Unit 3 Sprint 2 | `PHASE4_UNIT3_SPRINT2_REPORT.md`、`.local/release-resume2-20261023/result.json` | 两类回滚在本地隔离环境通过；无已提交候选、无远程 Actions，真实 Worker 的版本切换未验证 |
| Unit 3 Sprint 3 | `.local/incident-20261023/`，本报告及 `backend/docs/incident-response.md` | 一个空闲 Worker 崩溃，自动恢复；不证明在途请求故障、反复崩溃或手动恢复成功 |

本次检查真实本地代码；关键实现为 `runtime_metrics.py`、`telemetry_writer.py`、`knowledge/lifecycle_events.py`、`query_supervisor.py`、`persistent_query.py`、`windows_job.py` 和 `app/main.py`。没有改变策略或功能。Readiness 检查数据库/恢复门禁，不是持续 Worker 存活探针；Operator 必须结合生命周期事件和功能请求判断恢复。

## 演练环境与安全边界

复用既有 Windows 部署夹具，单 Uvicorn API（测试线程承载 lifespan），直接回环 TCP；独立 PostgreSQL 18.6 容器与文件根，离线 FastEmbed 缓存、真实 Persistent Worker/Windows Job Object、真实 Windows Provider 子进程连接本地 HTTP Mock LLM。没有启动 Next.js，没有生产 DB 或付费调用。

开启 Runtime Metrics 和 Persistent Worker；正常非维护模式，STARTUP_RECOVERY_ENABLED=false。重启配置未放宽：limit=3、window=600 秒、failed-cycle limit=3、backoff=1 秒。通过配置/数据库依赖覆盖选择隔离目标，未修改产品实现。

先注册/登录、上传并索引合成文档，基线带引用 Chat 成功。然后只终止一个空闲 Worker 根进程。注入信息单独保存在 `injection-ground-truth.json`；运维诊断脚本只读取应用产生的 `runtime/metrics.jsonl`，未用注入原因或退出码推断事件。事件以 supervisor_run_id/sequence 排序，身份以 worker_instance_id 区分。

`smoke-result.json` 沿用原夹具的 proxy 字段名和文字；它们实际均为直接 API 检查，不是前端代理。`scope-clarification.json` 明确纠正该标签，完整脚本 `exercise.py` 可核查。

## 应用证据时间线

同一 Supervisor run：`a08e5b04-d700-497e-890b-71da9dbc9f5a`。
G1：`00142b1d-e50d-4dc8-b554-1e0f50e5c81e`；G2：`c0eeab60-81c7-4b8f-9e49-4b1013e3f0d4`。
以下时间为 UTC（Sydney 当日 +11 小时）。来源：`application-lifecycle.json` / `operator-diagnosis.json`。

| 序号 | 时间 UTC | 应用直接记录 | 可得结论 |
|---|---|---|---|
| 1–2 | 05:51:42–44 | initialization_started/completed，G1 | 初始化完成且 READY |
| 3 | 05:51:49.852190 | failure_detected，process_not_alive，READY→FAILED | G1 存活检查失败；不能据此确定终止源 |
| 4–5 | 05:51:49.852190 | cleanup_started / cleanup_confirmed，checks_passed | Supervisor 确认既有 Job/slot 清理条件满足 |
| 6 | 同一故障阶段 | restart_scheduled，budget_available | 按现有策略允许自动恢复 |
| 7–8 | 05:51:50.857117 | G2 initialization_started / restart_attempted | replaces=G1，attempt=1，window_restarts=1 |
| 9–10 | 05:51:52.280071 | initialization_completed / restart_completed | G2 READY；从检测到恢复约 2.428 秒 |
| 11–13 | 05:51:53–54 | cleanup_started/confirmed / shutdown_completed | 演练结束正常停机 |

13 个生命周期事件连续，未发现中间序号缺口；不能因此保证其他场景不丢尾部事件。收集时使用有界测试 flush，生产没有保证退出 flush。

**直接观察**：故障 generation、清理确认、一次预算扣减、新旧 generation 关联和恢复成功。
**可重建**：自动恢复期间 Query Embedding 能力短暂不可用，重启前已有清理确认；此空闲事件没有已知失败的 HTTP 请求。
**不可观察**：原始退出码、谁终止进程、是否 OOM/系统/模型缺陷；详细 OS 清理子检查、精确受影响请求映射也未写入遥测。注入真值能解释实验原因，但不作为生产诊断结论。

Operator 仅观察并等待受控自动恢复，没有手动重启、重置预算、删除锁或强制替换。若发生 cleanup_unconfirmed/DEGRADED，应停止并升级处理；本次未触发这些分支，也不声称验证了手动干预。

## 恢复验收与数据

- 恢复后 health/ready/me 均成功，文档下载字节一致。
- 新业务 request_id 的真实 TCP Chat 返回 200、带引用答案；只新增一次 Mock Provider 调用。
- HTTP identity：`9fd85e47-b1a3-4bc8-8222-94aaeddb2b89`，trace：`b76c8835-dc08-4f25-810b-c1b48216cfa9`，业务 request：`130df7b3-f986-4f3f-9422-fa9c9f7b58e7`。
- 该请求 server_elapsed_ms=1455.440，stage_covered_ms=1444.979，unexplained_ms=10.461。区间证据在 `http-metrics.json`；此为单次验收数据，不是性能 SLO 或基准结论。该 HTTP 事件没有 Worker instance ID。
- 从注入前到恢复完成、尚未发新 Chat 时，15 张表的规范化行哈希均不变。新 Chat 后，用户/加密配置/Workspace/文档/Chunk/删除恢复表不变；会话/消息允许正常新增。
- 独立 OS handle 核对旧进程树已退出；最终两个 generation 所持 6 个进程 handle 均退出，Job/slot 释放、Supervisor STOPPED、storage lease 可重新获取。
- API、Mock 服务停止，实验 PostgreSQL 容器及卷删除；合成数据、缓存和证据保留，不清理历史记录。

应用清理事件是运维证据；OS handles 是夹具的独立安全核对，两者不混为相同可观察能力。

## 回归与复现

本次仅运行相关回归：`test_query_supervisor`、`test_worker_lifecycle_events`、`test_telemetry_writer`、`test_runtime_metrics`，**38 项全部通过，1.449 秒**。Ruff 和 git diff --check 通过。没有重复完整 Phase 4 实验计划。

沿用而不重复计数的最近证据：Sprint 2 Windows 后端 352 通过/9 条件跳过；独立 PostgreSQL 38 通过/1 SQLite 专用跳过；前端 15 项测试、Lint、16.4.1 build 通过。测试集合重叠，不相加。Next.js 修复后的生产审计零告警；完整审计仍有 braces 开发依赖链 5 个 high，处置边界见 Sprint 2 报告，未宣称所有依赖安全。

从仓库根目录，将夹具复制到新同层目录以免覆盖证据：

```powershell
New-Item -ItemType Directory .local/incident-NEW-RUN
Copy-Item .local/incident-20261023/exercise.py .local/incident-NEW-RUN/exercise.py
backend/.venv/Scripts/python.exe -X utf8 -B .local/incident-NEW-RUN/exercise.py
```

要求 Windows、Docker Desktop、既有 postgres:18-alpine 和本地模型缓存 `data/mvp-test/models/models--Qdrant--paraphrase-multilingual-MiniLM-L12-v2-onnx-Q`；无需下载或真实 Provider。创建并清理的资源由夹具持有。可复现的范围限定为此单一故障，不扩大注入场景。

运维步骤见 [Incident Response Runbook](backend/docs/incident-response.md)：发现→分类→最小化证据→恢复权限判断→恢复/升级→功能与数据验收→记录已知根因及不确定性。

## Git 与操作边界

HEAD 保持 `1e669a55b3cce245328a7393539ac9882554991e`。本次仅新增本报告、Incident runbook、`.local/incident-20261023/` 实验与证据；无产品代码变更，无 commit/push。

现有未提交内容：Sprint 1 backup/schema/startup 保护与测试文档；Sprint 2 CI/测试 runner/schema 测试及发布文档；前端 16.4.1 package/lock。历史未跟踪证据全部保留。`before.json` / `preservation.json` 对本轮开始时所有已跟踪及未跟踪文件做哈希核对。旧验收报告中“当时未提交/旧 HEAD”描述是历史快照，不改写为当前状态。

发布仍须最终授权 SHA、匹配依赖、停全部写入者、清理确认、兼容备份及 smoke。Supervisor 状态/预算不跨 API 进程持久化，不可反复重启绕过 budget。恢复只支持明确离线、兼容模型/PG 主版本及独立新目标；禁止删除恢复标记使服务强行可用。

## 剩余限制与最多三项 backlog

1. 根因诊断深度：原始退出信息、Worker→HTTP 关联及详细清理核验仍缺失；仅在后续具体诊断需要时补充，当前不实施新设施。
2. 发布证据：没有远程 GitHub Actions 或正式 release SHA 的部署验证；CI OS/Node 差异、真实 Worker 的跨版本切换仍待最终发布验证。
3. 运维风险：遥测非持久、预算进程内、停写依赖操作规程，braces 开发链风险保留；维持单进程和隔离 CI，不将本次结论外推至 Linux、多进程、反复崩溃、断电或在线恢复。

## Backend Benchmark 交接（尚未启动）

稳定范围：Windows Python 3.12、单 API、PostgreSQL 18.6、已有离线 FastEmbed/Persistent Worker 和 Job Object、Mock LLM、Runtime Metrics。保存精确 source/dependency/image/model/config 身份；当前工作区未提交，不能只引用 HEAD 作为测量版本。

先固定 Persistent Worker、session concurrency、startup/maintenance、模型缓存冷暖和 telemetry flags，再定义负载。保持 restart 默认 (3,600s,3,1s)，不得为吞吐放宽清理/限流/幂等语义。延迟按 stage 区间并集，Provider 嵌套时间不重复加；记录失败率、预算及 dropped/failed telemetry，不能把失败/缺失样本过滤成成功。

可用证据入口：Unit 1 summary、Windows deployment docs、Sprint 1/2 reports、本次 application-lifecycle/http-metrics/operator-diagnosis/smoke-result。不要把单次故障恢复或历史混合测试计数当作 Benchmark 成绩。Phase 4 到此关闭在条件验收范围内，未开始 Backend Benchmark。
