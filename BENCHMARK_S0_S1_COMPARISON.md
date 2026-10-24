# Backend Benchmark — Controlled Arrival Rate: S0 vs S1

2026-10-23。**PASS：六轮固定到达率实验、数据正确性、遥测和清理验证通过。**

**在本次1 req/s、最多2个在途请求的固定条件下，S1提高了成功RAG吞吐。** S0三轮窗口goodput均0.5000 req/s，S1为0.9833/0.9917/0.9917 req/s；三轮均值0.9889，相比S0约+97.8%。这是该单一负载点的观测，不是最大可持续容量、生产SLO或一般性能提升的证明。

## 1. 冻结身份与实验控制

- HEAD/main：`8b1b0d9b605d25e7e6d6101bf21a60708aa33135`。生产tracked diff开始/结束均为空，无commit/push。保留全部历史untracked实现、报告及`.local`证据。
- 新增仅benchmark文件：`backend/tests/experiments/rag_arrival_scheduler.py`、`rag_arrival_measure.py`、`rag_arrival_run.py`、`backend/tests/test_rag_arrival.py`及本报告。S1通过隔离进程env启用既有flag，不修改生产默认值、锁、容量或恢复策略。
- 证据根 **E=`.local/benchmark-arrival-s0-s1-20261023-01/`**。`freeze.json`及`tools/`记录完整工具SHA256和副本；`final-integrity.json`重新核对C1/C2冻结工具、设计、依赖、模型、向量及本次工具未变。
- Windows11 build26300、i9-13980HX（24核/32逻辑CPU）、32GiB级物理RAM、Balanced power；Docker Desktop29.8.2，VM32CPU/15.43GiB。非privileged，PG容器未另设CPU/RAM配额。详见`hardware.json`、`container-resource-limits.json`。
- 单Windows API进程、真实TCP、每轮独立PostgreSQL18.6（fsync/synchronous_commit on、read committed）、离线FastEmbed、Persistent Query Worker及真实Provider子进程→本地确定性Mock HTTP。Mock最小等待200ms；无付费调用。
- 每轮`source.json`含Python及全部实际package版本；`configuration.json`、`postgres.json`含应用和PG设置。六轮除S0/S1 flag及隔离端口/路径外，配置与接受的C1一致。Worker restart3/600s、query30s、index90s、HTTP90s、Runtime Metrics=true均保持。
- D1 seed20261023、20文档/200真实chunks/384维；dataset SHA256 `178365eac1181925ffaed23b2a919c3b5f99f2c8a385f58ed5823216fdc9aa77`。backend-qualified model=`fastembed:sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`；chunk内容、向量和离线模型文件hash逐轮与C1相同。

## 2. 调度、预检与计量边界

固定顺序：S0-r1 → S1-r1 → S1-r2 → S0-r2 → S0-r3 → S1-r3（AB/BA/AB）。每轮新DB/storage/API/Worker，360个预建独立会话；前60用于完整RAG warmup，剩余会话按计划index分配，同一index对应同一query，唯一session/request/HTTP identity，无重试。六轮均60/60真实检索、generation、oracle/DB预检通过；warmup均小于180s。

每轮计划时点为t0+[0..119]秒，与响应完成无关；发送窗口固定[t0,t0+120s)，120次offered。两客户端在窗口前构建，各最多1个keepalive连接；两个future都未完成时立即记not-sent，不排队、不补发。迟到>100ms不补发且使计量验收失败；本次无迟到丢发，实际send最大lag为47ms。HTTP完成边界来自完整body接收，昂贵oracle/hash/SQL校验在窗口和drain后。

goodput=窗口内已完成且最终验证为正确200的数量/120；drain成功不回填。服务端拒绝率同时给出offered与sent分母；客户端not-sent独立记录，不伪造HTTP结果。保留原RAM/磁盘/证据大小与Worker安全门槛，单轮既有25min上限；六轮suite上限40min，实际1716.203s（28.60min）。未触发安全停止。

## 3. 三次独立重复结果

| 配置/轮次 | scheduled / sent / completed HTTP | 200 | 429 | not-sent | 窗口内200 / drain200 | goodput req/s | 429/offered（429/sent） | drain等待 s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| S0-r1 | 120 / 120 / 120 | 60 | 60 | 0 | 60 / 0 | 0.5000 | 50.00% (50.00%) | 0.015 |
| S0-r2 | 120 / 120 / 120 | 60 | 60 | 0 | 60 / 0 | 0.5000 | 50.00% (50.00%) | 0.015 |
| S0-r3 | 120 / 120 / 120 | 60 | 60 | 0 | 60 / 0 | 0.5000 | 50.00% (50.00%) | 0.016 |
| S1-r1 | 120 / 119 / 119 | 119 | 0 | 1 | 118 / 1 | 0.9833 | 0.00% (0.00%) | 0.782 |
| S1-r2 | 120 / 120 / 120 | 120 | 0 | 0 | 119 / 1 | 0.9917 | 0.00% (0.00%) | 0.656 |
| S1-r3 | 120 / 120 / 120 | 120 | 0 | 0 | 119 / 1 | 0.9917 | 0.00% (0.00%) | 0.766 |

所有轮次409/503/其他HTTP错误、timeout、transport failure均0。总计720个scheduled、719个sent/HTTP completed、539个成功、180个429、1个not-sent；536个成功在窗口内，3个在drain。not-sent为S1-r1的index43（t0+43s），原因`client_outstanding_limit`：index41/42仍在途，其最终client时长2032/2016ms且均成功；该到达没有后来补发。原始记录见各轮`arrival-plan.json`、`requests.jsonl`、`verified-requests.json`。

## 4. 延迟、阶段与Provider

以下为逐轮分布。成功n=60/119/120，满足p50门槛20；所有单轮HTTP样本均<200，不报告单轮p95；也不报告p99。完整min/max/mean/median、all-HTTP与各status分布、scheduled-to-complete延迟保存在`analysis.json`/`measurement.json`。

| 轮次 | 成功n | client成功min / p50 / max ms | server成功p50 ms | scheduled-to-complete成功p50 ms | all-HTTP client p50 ms | 429 client/server p50 ms |
|---|---:|---:|---:|---:|---:|---:|
| S0-r1 | 60 | 1515.0 / 1719.0 / 1922.0 | 1724.22 | 1734.0 | 32.0 | 15.0 / 9.75 |
| S0-r2 | 60 | 1609.0 / 1859.0 / 1969.0 | 1847.86 | 1859.0 | 16.0 | 15.0 / 10.53 |
| S0-r3 | 60 | 1703.0 / 1765.0 / 1860.0 | 1759.93 | 1766.0 | 31.0 | 15.0 / 9.92 |
| S1-r1 | 119 | 1735.0 / 1813.0 / 2032.0 | 1814.53 | 1829.0 | 1813.0 | 未发生 |
| S1-r2 | 120 | 1578.0 / 1765.0 / 1875.0 | 1766.12 | 1781.0 | 1765.0 | 未发生 |
| S1-r3 | 120 | 1703.0 / 1750.0 / 1969.0 | 1755.09 | 1766.0 | 1750.0 | 未发生 |

S0的all-HTTP p50由快速拒绝主导，不能当作RAG成功延迟。Windows monotonic约15.625ms量化，短请求可能记录0ms；server阶段使用更细时钟，不跨进程直接相减绝对时点。

下面是**成功请求**mean ms；Provider嵌套于Generation，不能再加到首层覆盖中。

| 轮次 | Coordination | Retrieval/material | Generation | nested Provider | Persistence/commit | Unexplained |
|---|---:|---:|---:|---:|---:|---:|
| S0-r1 | 14.83 | 91.45 | 1583.62 | 1580.42 | 20.75 | 8.54 |
| S0-r2 | 16.91 | 107.31 | 1687.28 | 1683.69 | 22.78 | 9.38 |
| S0-r3 | 15.94 | 103.35 | 1610.18 | 1606.74 | 21.72 | 9.69 |
| S1-r1 | 14.77 | 93.96 | 1690.12 | 1686.68 | 18.15 | 8.44 |
| S1-r2 | 13.92 | 86.77 | 1611.64 | 1608.43 | 18.82 | 8.12 |
| S1-r3 | 14.69 | 95.14 | 1629.24 | 1625.80 | 17.30 | 8.36 |

Routing和cleanup的原始区间/分布同样保留。719条HTTP重新计算interval-union覆盖通过，首层未发现重叠；covered+unexplained与server总时长一致。429只有coordination/cleanup，无Provider及未执行的后续阶段；未以0填充未执行阶段。Mock实际均值约207–209ms，保持既定200ms最小等待。

为减少成功查询组成差异，另对每组双方都成功的相同计划index/query比较（每组60对，独立session/DB，不是同一次请求）：

| Pair | S1−S0 client mean ms | Generation mean差 ms | Provider mean差 ms |
|---|---:|---:|---:|
| 1 | +107.32 | +106.60 | +106.37 |
| 2 | -103.17 | -74.62 | -74.24 |
| 3 | +7.17 | +17.62 | +17.63 |

差异方向不一致；本证据不支持“开启S1必然让Provider变慢”。配对仅控制query，不消除温度、调度或背景负载变化，也不提供内部Provider根因。详见`paired-successes.json`。

## 5. CPU、内存与进程记账

| 轮次 | API占用核数 / RSS均值-最大 MiB | Worker核数 / RSS最大 MiB | Provider Job CPU总s / 每成功s | Provider采样RSS最大 MiB | Generator+Mock核数 / RSS最大 MiB | PG CPU%均值 / 观测数 |
|---|---:|---:|---:|---:|---:|---:|
| S0-r1 | 0.044 / 125.12–129.05 | 0.259 / 625.02 | 74.156 / 1.236 | 89.84 | 0.012 / 114.95 | 0.77 / 19 |
| S0-r2 | 0.049 / 124.88–128.50 | 0.266 / 624.57 | 77.766 / 1.296 | 89.99 | 0.013 / 114.71 | 0.80 / 19 |
| S0-r3 | 0.046 / 124.20–127.62 | 0.264 / 625.43 | 73.703 / 1.228 | 89.77 | 0.014 / 115.02 | 0.66 / 19 |
| S1-r1 | 0.090 / 126.18–130.88 | 0.525 / 625.18 | 156.250 / 1.313 | 156.66 | 0.016 / 114.50 | 1.48 / 19 |
| S1-r2 | 0.078 / 126.75–130.97 | 0.522 / 625.14 | 152.297 / 1.269 | 154.21 | 0.012 / 115.31 | 1.48 / 19 |
| S1-r3 | 0.092 / 125.36–130.56 | 0.532 / 625.18 | 152.266 / 1.269 | 156.39 | 0.016 / 114.84 | 1.44 / 19 |

占用核数=CPU秒增量/采样时间；除以32×100才是host百分比。每轮API/Worker/Provider有119个窗口内采样，缺group=0、采样error=0；generator与PG原始样本/间隔、RSS/private-bytes分布见`analysis.json`和`external-resources.json`。PG为docker stats离散观测，非连续时间加权平均。RSS共享页不可当独占内存相加；Provider采样峰值是下界，空活动集合不是漏样填0。

539次成功各对应1个Provider Job、1条Provider duration与1次有效Mock HTTP；每Job累计3个OS进程（与C1/C2一致），关闭时active=0。CPU取完整Job accounting，不把短PID瞬时速率相加，也不把3个OS进程称为3次LLM调用。资源用量随成功数增长；未出现资源安全门槛触发，但不能排除短暂峰值或单线程调度竞争。

## 6. 正确性、归因与安全结论

- 六轮360个warmup查询全通过；全部719个submitted请求逐一DB核验。每个200恰好1个ChatTurn，session/question/完整response hash一致，恰好新增1对Message，1份MessageEvidence与响应sources一致，oracle通过。
- 每个429无ChatTurn，原始2条Message不增加、无MessageEvidence；not-sent及所有其他未使用会话也保持2条Message。总数核验及非Chat表全行hash一致，未见重复、部分写入或跨会话污染。此核验不冒充对每一个Message列都逐列hash。
- 719个HTTP记录完整且身份唯一，与请求台账一一对应；not-sent没有伪造HTTP记录。每轮writer accepted/written增量=sent，dropped/failed=0；测量期间lifecycle无新事件、Worker前后READY/restarts0。成功均fresh leader，Provider归因一一对应，无自动重试/额外调用。
- 所有180个429均没有成功admission observer、无Provider，并在coordination阶段失败，定位为Chat admission。源码：`backend/app/services/chat/capacity.py:21`为S0非阻塞单槽限制；`backend/app/api/routes/chat.py:130`附近S1采用已有shared分支。
- S1所有已发送请求均成功，没有观测到限制转移为Embedding Worker拒绝。`backend/app/services/knowledge/query_supervisor.py:247`仍保留single-inflight、429/503与恢复限制；未改容量。不能据“未触发”证明Worker并发容量增加。唯一未发送由客户端双在途上限直接记录，不能算成Worker拒绝。
- 六轮Worker最终STOPPED，持有的进程handles均确认退出、Job/gate与storage lease释放、API exit0；Provider Job active=0。六个本次PG容器已独立复查不存在。见`final-integrity.json`、`final-docker-cleanup.json`、各轮`api-cleanup.json`。未操作其他容器或用户数据。

## 7. 复现命令、证据与限制

仓库根使用现有Windows venv；需要本机Docker和本地TCP/Job权限，无新增依赖：

```powershell
backend/.venv/Scripts/python.exe -m unittest discover -s backend/tests -p "test_rag_*.py" -v
backend/.venv/Scripts/ruff.exe check backend/tests/experiments/rag_*.py backend/tests/test_rag_*.py
git diff --check
```

17个工具测试在执行前及执行后均通过，含固定到达、满槽丢发、不追赶、90s在途不产生无界队列、安全停止、拒绝/timeout/drain分母，以及已有真实TCP body完成边界测试。Ruff/diff-check通过。未重复无关Backend suite，也没有给真实业务注入故障。日志：`tool-tests.log`、`final-tests.log`、`final-ruff.log`、`final-checks.json`。

实际suite命令：`backend/.venv/Scripts/python.exe -B .local/benchmark-arrival-s0-s1-20261023-01/run.py`。固定六轮、逐轮hash及cleanup门槛。未来另获授权复现时须使用**新的证据根**，复制freeze/run并保持工具hash，禁止覆盖本次E；单轮入口`rag_arrival_run.py --configuration S0或S1 --output <新目录> --measure-gate .local/benchmark-c1-complete-20261023-01/preflight`。

离线可重复运行E内`analyze.py`、`integrity.py`、`paired.py`（不启动负载）。关键证据：`freeze.json`/`tools/`（来源），`suite-result.json`（六轮完成），`analysis.json`（所有分布/资源/覆盖），各轮`arrival-plan.json`/`verified-requests.json`/`measured-telemetry.json`/`mock-calls.json`（逐请求事实），`paired-successes.json`，最终integrity/cleanup/checks。仅合成数据，不记录真实prompt、凭据或私人文档。

限制：

1. 只测一个到达率和最多2个在途，持续120s×3/config；S1一次not-sent表明该有限客户端上限有影响。不能推断最大可持续容量、无界到达下队列行为、真实付费模型性能或业务SLO。历史C1/C2仅用于环境/data合同，未拿不同offered load结果计算本次提升。
2. 单轮成功样本不足200，p95/p99未建立；S0与S1成功query组成不同。配对比较控制部分组成差异，但AB/BA/AB不是背景负载/温度完全控制的因果实验。
3. 资源是离散采样，无法排除短峰值；Generation/Provider是inclusive边界，内部CPU/导入/调度/HTTP子阶段未拆分。无Worker失败/503/timeout实测，不扩展声称故障恢复覆盖。

**验收：PASS（本次受控负载点的吞吐/正确性/遥测/清理）。** S1在相同offered load下成功吞吐更高；本次未发现Worker容量拒绝转移或持久化回归。生产代码与历史证据未改，未提交/推送，未运行其他等级/工作负载。到此停止，返回Chat审阅。
