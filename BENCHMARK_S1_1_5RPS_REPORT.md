# Backend Benchmark — S1 at 1.5 req/s

2026-10-23。**PASS：三轮受控实验、正确性、计量和清理验收通过；不是“无拒绝承载1.5 req/s”的容量通过。**

**主要发现：提高offered load后，成功吞吐仍约0.99 req/s；三轮各有33.33%请求在Chat Admission被拒绝。** 客户端未饱和；没有观察到Embedding Worker或Uvicorn拒绝。最强限制候选是现有S1最多两个已获admission请求的策略边界，且获准请求的占用时间主要在Generation/Provider。不能由父阶段总时长确定Provider内部根因，也不能从该单一新负载点宣布最大可持续容量。

## 1. 来源、配置与控制

- HEAD/main=`8b1b0d9b605d25e7e6d6101bf21a60708aa33135`；tracked diff开始/结束均为空。无生产修改、调优、提交、推送。历史实现、报告和实验数据原样保留。
- 本步新增仅benchmark工具：`backend/tests/experiments/rag_s1_15_scheduler.py`、`rag_s1_15_measure.py`、`rag_s1_15_run.py`、`backend/tests/test_rag_s1_15.py`及本报告。旧C1/C2/1req/s工具未改；三个已接受freeze manifest及已记录artifact hashes均重新验证。
- 证据根 **E=`.local/benchmark-s1-15rps-20261023-01/`**。`freeze.json`记录全部工具SHA256、D1及限制，`tools/`保存源码副本，`final-integrity.json`核对实际依赖/配置/模型/数据及每个请求。
- 保持Windows11 build26300、i9-13980HX（24核/32逻辑CPU）、Balanced power、单API进程、真实TCP、每轮独立PostgreSQL18.6、Persistent FastEmbed Worker、真实Provider子进程→本地确定性Mock HTTP（最小等待200ms）。无付费调用。Docker Desktop29.8.2、32CPU/15.43GiB VM RAM，无额外容器CPU/RAM配额、无privileged。
- D1：seed20261023，20合成文档/200真实chunks/384维；SHA256 `178365eac1181925ffaed23b2a919c3b5f99f2c8a385f58ed5823216fdc9aa77`。backend-qualified model=`fastembed:sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`；内容/向量/离线模型hash逐轮与C1相同。
- S1=true、Runtime Metrics=true；HTTP90s、query30s、index90s、Worker restart3/600s和生产admission/recovery策略不变。除隔离路径/端口和此前S0→S1既有flag外，冻结配置未变。完整Python/package版本、PG settings见各轮`source.json`、`configuration.json`、`postgres.json`。
- **本步明确变化：offered rate 1→1.5 req/s，最大客户端在途2→4。** 因两项同时变化，与上次1 req/s不是只改变到达率的完美A/B；不将结果差异全部归因于一个参数。

## 2. 调度、预检与安全门槛

每轮新DB/storage/API/Worker，60个warmup查询均完成真实RAG oracle/数据库检查，随后120s固定窗口。计划t0+i/1.5（i=0..179），180次到达独立于响应完成，无重试、无限队列或事后补发。四个客户端各最多一个连接；有空槽才提交。成功goodput只计窗口内完成且离线验证正确的200；drain成功单列。

每轮预建360个独立会话（60 warmup，300可用measured），每个计划index使用独立session/request identity，固定query序列循环。JSON/oracle/hash/SQL验证不进入HTTP完成时长，完整核验在窗口/drain后。每轮warmup<180s；总墙钟843.922s<既有三轮25min预算。

RAM/磁盘/证据目录、HTTP/drain、Worker状态、正确性及遥测安全门槛保持。原I场景高拒绝率停止规则仍为连续两个完整10s窗口各≥20 offered且429>20%；本次每窗15 offered，故不满足样本门槛，**没有降低该门槛或绕过已满足的停止条件**。非预期HTTP/transport结果保留原保守停止。

## 3. 三轮结果

| 轮次 | warmup s | scheduled/sent/completed HTTP | admitted/200 | 429 | not-sent | 窗口内200/drain200 | goodput req/s | 429率 offered=sent | drain s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| S1-r1 | 108.360 | 180/180/180 | 120/120 | 60 | 0 | 119/1 | 0.9917 | 33.33% | 0.297 |
| S1-r2 | 98.907 | 180/180/180 | 120/120 | 60 | 0 | 119/1 | 0.9917 | 33.33% | 0.297 |
| S1-r3 | 103.531 | 180/180/180 | 120/120 | 60 | 0 | 119/1 | 0.9917 | 33.33% | 0.562 |

三轮均实际窗口120s、offered/sent rate=1.5 req/s；409/503/其他HTTP失败、timeout、transport error全部0。总540计划/提交/HTTP完成，360个正确成功、180个429、0 not-sent；357个成功在窗口内、3个在drain。不能把360/360s=1.0冒充窗口goodput。所有结果保留。

每20秒均30 offered、20最终成功、10拒绝（三轮全部六个区间相同）；这里按计划时点分组，区间success不等于该区间内完成goodput。`arrival-plan.json`、`requests.jsonl`、`verified-requests.json`保留逐attempt原始证据。

## 4. 延迟与阶段

每轮all-HTTP n=180、success n=120、429 n=60；均未达到批准的p95≥200或p99≥1000门槛，因此**不报告p95/p99**。p50采用既有最近秩定义，n≥20。完整min/max/mean/median及schedule latency在`analysis.json`/`measurement.json`。

| 轮次 | 成功client min/p50/max ms | 成功server p50 ms | 429 client/server p50 ms | all-HTTP client p50 ms | scheduled-to-complete成功p50 ms |
|---|---:|---:|---:|---:|---:|
| S1-r1 | 1546.0/1641.0/1812.0 | 1646.92 | 15.0/9.00 | 1609.0 | 1656.0 |
| S1-r2 | 1531.0/1610.0/1953.0 | 1610.45 | 16.0/6.43 | 1578.0 | 1625.0 |
| S1-r3 | 1687.0/1735.0/1875.0 | 1744.26 | 0.0/7.65 | 1719.0 | 1755.3 |

all-HTTP包含快速拒绝，不能冒充成功RAG延迟。成功请求阶段mean ms如下（每轮n=120）：

| 轮次 | Coordination | Retrieval/material | Generation | nested Provider | Persistence/commit | Unexplained |
|---|---:|---:|---:|---:|---:|---:|
| S1-r1 | 13.26 | 87.82 | 1525.51 | 1522.23 | 16.96 | 7.94 |
| S1-r2 | 11.04 | 60.04 | 1549.11 | 1546.07 | 17.05 | 5.76 |
| S1-r3 | 14.29 | 84.78 | 1623.76 | 1620.94 | 17.39 | 7.06 |

Routing、cleanup和全部原始区间亦保留。Provider已嵌套在Generation内，不重复相加；540条HTTP重新计算interval-union覆盖通过，未见首层重叠，covered+unexplained与server总时长一致。429只执行coordination/cleanup，没有进入Provider；未执行阶段不填0混入均值。

## 5. 首个拒绝与计量精度

三轮首个429均为index2（第三个计划请求），实际记录约t0+1.344s，边界为Chat Admission；此前没有成功HTTP响应完成。index0/1已获admission且仍在途。不能从这里建立“成功延迟先升高才开始拒绝”的先后链条：尚无拒绝前的完成样本，本次也不是缓慢爬升负载实验。

20秒分组的成功client均值见`timeline.json`：前两轮没有单调上涨；第三轮首/末区间约1734.5/1778.15ms，后段略高，但拒绝早已从第3次到达开始，不能将该变化视为拒绝的前兆或推断内部根因。

**时钟限制显式核查**：沿用已冻结的Windows `GetTickCount64()` monotonic（15.625ms分辨率），没有中途切换时钟或重新挑选数据。2/3秒计划点并不总落在时钟tick上，记录的dispatch lag可能略负；它不能精确区分该量级的提前发送与时钟量化。原始值保留，不归零掩盖：

| 轮次 | recorded lag min/max ms | 相邻发送间隔min/mean/max s | 成功响应距120s截止最近距离 ms | 最大占用客户端槽数 |
|---|---:|---:|---:|---:|
| S1-r1 | -10.667/36.333 | 0.640000/0.666637/0.703000 | 297.0 | 3 / 4 |
| S1-r2 | -9.667/36.333 | 0.625000/0.666637/0.703000 | 282.0 | 3 / 4 |
| S1-r3 | -5.333/36.333 | 0.625000/0.666726/0.703000 | 125.0 | 3 / 4 |

负值均在一个tick内；没有集中补发、scheduler-late丢发或窗口外发送。成功响应距截止至少125ms（>2 ticks），goodput归属不依赖这个量级的时钟误差。client短拒绝0ms也不表示零执行成本；server使用更细计时。`timeline.py`的离线门槛检查通过。

## 6. 资源、Provider及正确性

| 轮次 | API核数 / RSS mean-max MiB | Worker核数 / RSS max MiB | Provider Job CPU总s/每次s | Provider采样RSS max MiB | Generator+Mock核数/RSS max MiB | PG CPU%均值/观测数 |
|---|---:|---:|---:|---:|---:|---:|
| S1-r1 | 0.081/127.21–132.34 | 0.517/625.15 | 145.266/1.211 | 164.33 | 0.014/116.34 | 1.73/19 |
| S1-r2 | 0.062/126.01–130.13 | 0.495/624.90 | 147.516/1.229 | 164.10 | 0.012/116.55 | 1.70/19 |
| S1-r3 | 0.078/125.53–130.76 | 0.514/625.12 | 150.672/1.256 | 158.25 | 0.016/116.19 | 1.56/19 |

CPU核数=CPU秒增量/采样时间；host%=核数/32×100。API/Worker/Provider窗口内采样数119/120/119，缺group和采样error均0；PG、generator完整离散样本与RSS/private bytes在`analysis.json`/`external-resources.json`。PG均值非时间加权积分；RSS共享页不能当独占内存相加，Provider采样峰值只是下界。没有资源安全门槛触发，不能据离散采样排除短峰值或调度竞争。

- 360个成功每个恰好1个ChatTurn，session/question/完整response hash正确，恰好一对新增Message及匹配的MessageEvidence，response/evidence oracle通过。180个429无ChatTurn或新增消息/evidence；所有未使用会话仍为原始2条Message。
- 每轮总ChatTurn180、Message1080、MessageEvidence180（含60 warmup）；全部submitted逐一核验，不采样。非Chat表全行hash保持。Chat表按上述结构与response/source约束验证，不冒充逐列hash全部Message字段。
- 360个成功对应360个Provider Job/duration/有效Mock HTTP调用；拒绝无Provider，无额外调用。每Job累计3个OS进程（与历史一致），close时active=0；CPU使用完整Job accounting，未把短PID速率相加或把3个OS进程算3次LLM调用。
- HTTP遥测540/540、每轮writer accepted/written各+180、dropped/failed=0；身份唯一、成功fresh leader。测量期间Worker READY、restarts0、无新增lifecycle事件。
- 三轮API exit0、Worker最终STOPPED，持有的进程handles全部确认退出，Job/gate与storage lease释放。三个本次PG容器独立复查不存在；未处理其他容器/用户数据。见`final-integrity.json`、`final-docker-cleanup.json`与每轮`api-cleanup.json`。

## 7. 与上次S1及瓶颈判断

| 实验 | offered req/s | 客户端上限 | 三轮窗口goodput req/s | 三轮429 | 三轮not-sent |
|---|---:|---:|---|---|---|
| 上次S1 | 1.0 | 2 | 0.9833 / 0.9917 / 0.9917 | 0 / 0 / 0 | 1 / 0 / 0 |
| 本次S1 | 1.5 | 4 | 0.9917 / 0.9917 / 0.9917 | 60 / 60 / 60 | 0 / 0 / 0 |

没有超过上次约0.99 req/s的已观察水平；本次与上次最佳值相同。相较上次三轮均值的微小数字差异不能证明容量提升（上次一次client not-sent，且客户端上限与负载同时变化）。上次成功client p50为1813/1765/1750ms，本次1641/1610/1735ms；成功query组成及运行时段不同，不把较低值解释成负载增加改善性能。

**直接观察与源码共同支持**：所有180个429都有应用HTTP telemetry，未获admission、在coordination阶段失败、无Provider；不是已验证的Uvicorn前置拒绝或Embedding拒绝。`backend/app/api/routes/chat.py:130–131`启用shared admission，`backend/app/services/chat/capacity.py:20`的`_shared >= 2`立即返回429。首拒绝时两个已获准请求仍在途。

**最强候选**：现有两请求admission限制是本次最先显现的边界；Generation/Provider占据大部分成功请求时间，延长槽位持有时间。此证据不能证明底层CPU、数据库、Embedding或Provider内部已达到最大能力，也不构成调高上限的安全依据。

**负载生成器**：180/180发送，0 not-sent，最大占用3/4，无发送延迟门槛触发或追赶突发；没有观察到它限制本次goodput。时钟精度仍限定毫秒级解释。

**是否出现延迟先增后拒绝**：未建立；三个固定负载窗口都从第三次到达就拒绝，之前没有成功完成样本。没有观察到限制迁移到Embedding Worker；该组件现有single-inflight与恢复保护未改，也未在本实验验证更高embedding容量。

## 8. 复现、验证与结论

执行前/后22个工具unittest、Ruff、diff-check全部通过，覆盖180个无累计漂移计划点、四槽有界、无追赶、not-sent、原拒绝停止阈值，以及既有TCP完成/失败/drain计数。停止规则的高到达样本测试使用合成时钟，没有运行额外真实负载等级。日志E/`tool-tests.log`、`final-tests.log`、`final-ruff.log`、`final-checks.json`。

```powershell
backend/.venv/Scripts/python.exe -m unittest discover -s backend/tests -p "test_rag_*.py" -v
backend/.venv/Scripts/ruff.exe check backend/tests/experiments/rag_*.py backend/tests/test_rag_*.py
git diff --check
```

实际协调命令：`backend/.venv/Scripts/python.exe -B .local/benchmark-s1-15rps-20261023-01/run.py`。固定只运行三轮S1/1.5，逐轮检查冻结源码与cleanup；不要向已有证据目录重跑。未来另获授权复现须使用新目录及相同freeze/run，单轮入口`rag_s1_15_run.py --output <新目录> --measure-gate .local/benchmark-c1-complete-20261023-01/preflight`。

离线汇总可重复执行E内`analyze.py`、`integrity.py`、`timeline.py`；不启动负载。关键证据：`freeze.json`、`suite-result.json`、`analysis.json`、`timeline.json`、每轮原始请求/arrival/telemetry/Mock台账、configuration/source/model/index manifests及cleanup文件。仅合成文档/查询与测试凭据；不输出真实秘密或私人内容。

边界：仅这一新增负载点、Windows单API、固定D1/Mock，最多四客户端在途。没有更高等级、开关或工作负载；未测试真实付费模型、故障恢复、生产SLO或最大可持续容量。没有优化或生产行为修改。

**验收：PASS（实验与正确性）；不支持“全部1.5 req/s均能成功”的结论。到此停止，返回Chat审阅。**
