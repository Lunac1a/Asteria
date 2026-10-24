# RAG Chat C=1 Baseline — PASS

2026-10-23。仅 **Independent Sessions / S0 / C=1 / Windows 单 API**。完整真实 RAG 预检和三轮计时、全量请求核验均完成。这是固定 Mock 条件下的基线，不是容量上限或真实商用 LLM 性能结论。

新证据目录：`.local/benchmark-c1-complete-20261023-01/`，以下简称 E。旧失败和修正证据仍在 `.local/benchmark-c1-20261023-{preflight,rep1}/`、`.local/benchmark-c1-retry-20261023-01/`；原报告完整保存为 `E/previous-report.md`，没有覆盖旧运行结果。

## 1. 最终工具身份与集成检查

main / HEAD：`8b1b0d9b605d25e7e6d6101bf21a60708aa33135`。已跟踪生产文件无变化，无提交、推送、付费 Provider 调用。本轮仅修改 benchmark 的 `rag_c1_measure.py`、`rag_c1_preflight.py`、`test_rag_c1_timing.py` 和本报告；运行/分析脚本位于 E。此前未跟踪工具、文档和证据均保留。

`E/freeze.json`、`E/tools/` 冻结全部工具/测试；`final-integrity.json` 确认四次运行源码、依赖、模型缓存一致，最终工具仍与冻结哈希一致。

| 文件 | SHA256 |
|---|---|
| rag_c1_preflight.py | `a08cd46d5c247de02402c75b1889dc387eb67974fbfe10092fcc1e7a1b530544` |
| rag_c1_measure.py | `d86663e8ca91cc005a21d84a0247cbd7c8873f199143f4fa01254bee4d94aea7` |
| rag_c1_dataset.py | `96ec5ab95a9a65a14570262c0d66767a1e489953993d6bd7471ea085fd883d2b` |
| 工作负载设计（未修改） | `803e699c1427fa3c26965643a3fd5f9fa847131512cfcc425e1e16759626831f` |

模型身份比较 backend-qualified ID，并在断言前保存实际 SQL 行。HTTP 完成时间在非 streaming HTTPX 收完整 body 后立即记录；JSON/oracle/hash 校验移到窗口后的离线处理。合成响应 body 完整保存于 `requests.jsonl`，没有为了吞吐删除正确性断言。Mock 服务端仍验证实际 evidence，失败不无条件返回成功。

集成检查覆盖：真实 loopback TCP 响应头已发但 body 未发时不能计完成；确定性时钟下客户端验证不改变 HTTP latency 或窗口 goodput；离线验证不阻塞下一次发送；429/503/timeout/transport 分类保留；模型 ID、D1、百分位门槛。**最终9项测试通过，全 backend Ruff、git diff --check通过。** 初次 sandbox TCP 检查未连到测试服务；相同代码在获准 loopback 环境通过，没有修改阈值。证据：`final-tests.log`、`ruff.log`。

## 2. 实际环境、配置与完整预检

Windows 11 Pro build26300；i9-13980HX（24核/32逻辑CPU），RAM31.62GiB，平衡电源模式；开始可用RAM约14.28GiB、磁盘约1296GiB。Docker Desktop29.8.2：32CPU、内存16,562,987,008bytes；PG容器无独立CPU/内存quota，受Desktop VM上限约束，Privileged=false。见 `hardware.json`、`container-resource-limits.json`、各轮 `resources-before.json`。

Python3.12.10，实际全部依赖在各轮 `source.json`，四次相同。真实 PostgreSQL **18.6**，镜像：
`postgres@sha256:77f585114c32fbca283dc835b0596f4e52b51b4c6662d7810b2f4084f60a1873`。
每轮新专属容器、动态loopback端口、新 `benchmark_d1` DB、`/tmp/d1/pgdata`；max_connections=100、shared_buffers=128MB、work_mem=4MB、fsync/synchronous_commit=on、read committed。未使用SQLite。

独立Windows API进程，真实TCP Uvicorn，workers=1、limit_concurrency=16、keepalive=5。固定离线FastEmbed/Persistent Worker、LLM_BACKEND=provider、Runtime Metrics=true、Session Concurrency=false；citation audit/startup recovery/maintenance=false。query/index timeout=30/90s；恢复3次/600s/backoff1s/failed-cycle limit3。客户端timeout=90s、trust_env=false、连接复用、无自动重试。完整非敏感实际配置见各轮 `configuration.json`。

真实Windows Provider子进程调用唯一allowlist本地Mock HTTP，固定请求等待200ms，输出grounded JSON。PROVIDER_IS_SIMULATED=false表示这里使用真实Provider执行路径，**不是外部商用模型**。benchmark wrapper仅以model_name标签携带HTTP identity；没有替换生成函数、修改prompt或Provider业务逻辑。

D1-v1：20个合成文档，各规范化8000字符、10chunks，总200；向量384维；60个固定问题/事实/oracle，seed=20261023。内容JSON hash：
`178365eac1181925ffaed23b2a919c3b5f99f2c8a385f58ed5823216fdc9aa77`。

独立完整预检 **60/60** TCP RAG、答案/证据、ChatTurn/消息检查通过，真实Mock调用60次。实际DB模型行：
`fastembed:sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 / ready / 10`。
每轮重新索引，逐chunk内容/维数/向量哈希与预检完全相同；各查询Mock答案字节哈希跨预热及三轮一致。

各轮预建360个独立会话（60warmup+300measured候选），相同历史user/assistant对、questioning profile和Workspace绑定；无活动LearningContext。索引、建会话、预检、预热、离线SQL均不计入measured吞吐。证据为各目录的 `dataset.json`、`database-model-identity.json`、`indexed-manifest.json`、`model-manifest.json` 和 `result.json`。

## 3. 三轮计时结果

每轮120秒窗口，300请求cap均未触发，未延长预热或测量。总运行墙钟 **1013.984秒（16.90分钟）**，包含预检、三轮准备/预热/测量/清理，小于25分钟预算。

| 指标 | Rep1 | Rep2 | Rep3 |
|---|---:|---:|---:|
| warmup（各60请求，秒） | 104.750 | 103.843 | 105.235 |
| offered / sent | 69 / 69 | 69 / 69 | 74 / 74 |
| admitted / HTTP completed / verified success | 69 / 69 / 69 | 69 / 69 / 69 | 74 / 74 / 74 |
| 窗口内verified success | 68 | 68 | 73 |
| 排空阶段完成 | 1 | 1 | 1 |
| 排空时间（秒） | 0.437 | 0.141 | 1.375 |
| **窗口内successful throughput（req/s）** | **0.5667** | **0.5667** | **0.6083** |
| offered/sent throughput（req/s） | 0.5750 | 0.5750 | 0.6167 |
| 409 / 429 / 503 / 其他失败HTTP | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 |
| timeout / transport error / not_sent | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| client p50（ms） | 1734 | 1734 | 1609 |
| client mean（ms） | 1740.3 | 1735.5 | 1637.6 |
| client min–max（ms） | 1687–1875 | 1656–1843 | 1484–1844 |
| server p50（ms） | 1725.61 | 1725.38 | 1602.21 |
| p95 / p99 | 不报告 | 不报告 | 不报告 |
| measured Provider calls | 69 | 69 | 74 |
| HTTP telemetry accepted/written | 69/69 | 69/69 | 74/74 |
| HTTP/lifecycle drops、failures | 全部0 | 全部0 | 全部0 |

Goodput分母均120s，排空完成者不计入分子。结果率分母为69/69/74 sent（本闭环offered=sent）；全部成功，失败类别仍保留。Latency描述窗口内发送cohort的全部最终响应（包括drain），本次与success-only分布相同，**不与窗口内goodput分子混淆**。每轮n<200，按门槛不报告p95，更不将最大值命名p99。

客户端monotonic为GetTickCount64，分辨率 **15.625ms**；表中整数毫秒是记录值，不代表1ms精度。应用stage/Provider使用QPC perf_counter（报告分辨率1e-7s）；client与server结束边界不同，差值不直接解释为网络。实际信息在 `clocks.json`。Mock约200ms观测也有量化误差，不据此断言精确睡眠时长。

下一次start−前一次body completion平均 **4.985 / 5.735 / 2.603ms**，最大32/32/16ms；包含调度、账本、资源门槛检查和请求构造，也受时钟量化。JSON/oracle/hash已不在闭环发送路径上；残余生成器间隙没有从wall-window吞吐中扣除。

## 4. 阶段与嵌套关系

每请求平均inclusive ms；同名多个interval汇总。离线独立重算interval union，与stage_covered一致；本次首层区间无实质重叠。**Provider已经包含于Generation，不再次加到覆盖时长。**

| 阶段/指标 | Rep1 | Rep2 | Rep3 |
|---|---:|---:|---:|
| Coordination/preparation | 14.049 | 14.827 | 11.956 |
| Routing | 0.002 | 0.002 | 0.001 |
| Retrieval/material acquisition | 99.411 | 102.862 | 66.995 |
| Generation | 1595.902 | 1585.786 | 1535.569 |
| Persistence（含commit） | 21.059 | 22.393 | 16.099 |
| Cleanup | 0.035 | 0.033 | 0.022 |
| stage_covered | 1730.458 | 1725.903 | 1630.641 |
| unexplained | 8.737 | 9.435 | 6.512 |
| 嵌套Provider duration（不加总） | 1591.787 | 1582.480 | 1532.831 |
| Mock HTTP服务观测（约值，不加总） | 206.0 | 206.5 | 207.7 |

直接观察：Generation占server平均时长 **91.76% / 91.38% / 93.80%**；unexplained占 **0.50% / 0.54% / 0.40%**。主要耗时边界已定位，但不能仅凭父阶段断言数据库或Worker内部瓶颈。第三轮多个阶段更快，配置未变且无因果对照，不能宣称优化收益。

## 5. 资源观测

约1秒采样；窗口内API/Worker/Provider组分别119/119/120条观测，各组缺失和错误计数0。有些时点确无活跃Provider Job，空组可为0；缺失不填0。短命进程可能在采样间隙出现/结束，**RSS只表示采样峰值，不保证真实峰值**。启动阶段尚无Worker的错误不混入测量。

| 指标 | Rep1 | Rep2 | Rep3 |
|---|---:|---:|---:|
| API RSS mean/max（MiB） | 124.73/128.34 | 123.94/128.51 | 125.02/128.59 |
| Worker Job RSS mean/max（MiB） | 625.18/625.19 | 624.99/625.02 | 624.96/624.98 |
| Provider Job RSS mean/sampled max（MiB） | 57.27/90.18 | 60.89/89.95 | 58.99/90.18 |
| API CPU（平均占用核数） | 0.0528 | 0.0498 | 0.0394 |
| Worker CPU（平均占用核数） | 0.3052 | 0.3012 | 0.3024 |
| Provider Job CPU seconds/completed request | 1.229 | 1.215 | 1.200 |
| Generator+Mock CPU（平均占用核数） | 0.0137 | 0.0121 | 0.0106 |
| Generator+Mock sampled max RSS（MiB） | 114.16 | 114.62 | 114.71 |
| PG Docker CPU mean（100%=1核） | 0.706% | 0.706% | 0.260% |
| PG Docker memory sampled range（MiB） | 57.43–59.23 | 55.09–56.43 | 54.64–55.98 |

长生命周期CPU为同PID累计CPU差值/实际采样跨度；host百分比需除以32再×100。Provider使用全部69/69/74个关闭Job累计CPU（含launchers/子进程、包含drain cohort），不相加短命PID瞬时百分比，也不冒充精确120秒窗口CPU。RSS含共享页，不能简单相加为独占总内存。private bytes见 `analysis.json`。PG每轮20个Docker stats样本，非逐秒连续监测；VM/CLI和系统其他进程没有完整归因。

## 6. 正确性、遥测、安全

全部 **212** 个measured请求逐个离线核验：唯一request_id对应匹配session/question/完整响应的ChatTurn；使用会话Message从2条到4条，一条MessageEvidence且sources与响应一致；未使用会话保持2条。总ChatTurn/MessageEvidence为129/129/134，总Message为978/978/988（含预热及预建会话）。非Chat数据表哈希不变，包括Workspace、文档/向量、用户配置、profile/binding及Learning表。本questioning基准不覆盖活动Learning更新。

HTTP attempt、business request、fresh leader Flight、Mock model tag及Provider调用均可对账；无follower/replay。每请求恰好一次Provider，oracle全部通过。HTTP遥测69/69/74条，accepted=written、drops/failed=0；区间覆盖独立复算通过。未注入故障，不以零失败证明任意失败路径。

四次Worker正常READY→STOPPED、0重启；各 `api-cleanup.json` 确认持有的进程句柄全部exited、Job/gate释放、storage lease可重新取得。每个measured Provider Job关闭时active=0。四个专属PG容器全部stop/rm，最终按label查询无残留。合成文件、遥测和原始响应保留；JWT、Fernet/Provider key不进入报告或manifest。

## 7. 复现、局限与决定

原始证据：各 `repN/{source,configuration,indexed-manifest,model-manifest,result,measurement,verified-requests,measured-telemetry,api-before,api-after,api-final,api-cleanup,external-resources,mock-calls}.json`，以及 `requests.jsonl`、`storage/runtime/metrics.jsonl`。汇总为 `suite-result.json`、`analysis.json`、`final-integrity.json`。这是本地ignored证据，不能声称fresh clone自带。

仓库根目录PowerShell执行命令（旧输出目录不可覆盖；复现须新目录和相同冻结工具）：

```powershell
backend/.venv/Scripts/python.exe -B .local/benchmark-c1-complete-20261023-01/run.py
backend/.venv/Scripts/python.exe -B .local/benchmark-c1-complete-20261023-01/analyze.py
backend/.venv/Scripts/python.exe -m unittest discover -s backend/tests -p 'test_rag_c1*.py' -v
backend/.venv/Scripts/ruff.exe check backend
git diff --check
```

`run.py`仅顺序执行一次preflight与rep1/2/3，开始前校验哈希，共享25分钟预算；结果及清理通过才继续。单次入口 `rag_c1_preflight.py --output NEW_DIR`；measured rep另加 `--measure-gate NEW_PREFLIGHT_DIR`。旧失败版未用于本次。未重跑完整Backend/Frontend回归，也不把历史计数加到本次9项测试。

局限：桌面后台负载、动态频率、OS/PG缓存未隔离或清空；三轮差异不构成因果证据。客户端15.625ms量化、短命进程峰值与best-effort遥测限制如上。重复事实的200-chunk D1、warm Worker、Mock200ms及闭环C1不能代表真实知识库、外部LLM或固定到达率容量；p95/p99和其他并发/场景未建立。

**PASS：完成批准的I/S0/C=1基线，以及本次检查范围内的正确性、归因和清理验证。** 若下一步获批准，可只针对Provider调用内部耗时边界取证，区分初始化/IPC/HTTP等待；目前没有把其中某一步认定为已验证根因。停止于报告，未运行C≥2、S1、开环或性能优化。
