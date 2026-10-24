# Backend Benchmark — I / S0 / C=2

2026-10-23。**PASS：三轮有界实验及正确性/清理验证通过；不代表稳定容量通过。**

结果：S0 的单 Chat admission 槽位使每轮 300 次请求中只有 2 次成功，298 次返回 429（99.333%）。窗口内成功吞吐未增加；快速拒绝使 300-request cap 在约 3.4 秒触发，因此不能把与 C1 的数值差异解释为稳定容量下降。未执行 C≥4、S1、优化、付费调用、提交或推送。

## 1. Source、环境与实验身份

- HEAD/main：`8b1b0d9b605d25e7e6d6101bf21a60708aa33135`；开始/结束 tracked diff 均为空。历史 untracked evidence 与 C1 文件保留。
- 本步新增：`backend/tests/experiments/rag_c2_measure.py`、`rag_c2_run.py`、`backend/tests/test_rag_c2_measure.py` 和本报告。只扩展 benchmark 双 lane/混合 outcome 核验；生产源码未改。
- 新证据根 E：`.local/benchmark-c2-20261023-01/`；C1 对照：`.local/benchmark-c1-complete-20261023-01/`。所有工具 SHA256 见 E/`freeze.json`、原样副本 E/`tools/`。
- `final-integrity.json` 确认 C1 全部冻结工具、设计、生产 HEAD、requirements/实际 Python packages、模型文件、200 chunk 内容和向量哈希与 C1 相同；semantic configuration 相同，仅隔离路径/端口不同。
- Windows 11 build 26300，i9-13980HX，24 cores/32 logical CPUs，Balanced power；Docker Desktop29.8.2、32 CPUs/15.43GiB VM RAM，非 privileged、无另加容器 CPU/内存配额。见 `hardware.json`、`container-resource-limits.json`。
- 单 Windows Uvicorn/API，TCP HTTP；三份独立 PG18.6（固定 image digest，fsync/synchronous_commit on）、离线 FastEmbed、Persistent Worker=true、Runtime Metrics=true、S0=false session concurrency；query30s、index90s、HTTP90s。配置/PG settings/完整依赖版本见各 rep 的 `configuration.json`、`postgres.json`、`source.json`。
- D1 SHA256 `178365eac1181925ffaed23b2a919c3b5f99f2c8a385f58ed5823216fdc9aa77`；20 documents/200 real indexed chunks，384 dimensions；backend-qualified model ID 和真实向量对照通过。模型、oracle、seed20261023、Mock200ms不变。

## 2. 执行边界与结果

每轮新 DB/storage/API/Worker，60 个独立 warmup 查询全部通过真实检索、Provider 子进程→本地 Mock HTTP、答案/evidence 与数据库验证。复用已接受 C1 preflight gate，并逐轮重新索引和比较真实向量。两客户端各自单连接、无自动重试、不同 session/request IDs，闭环完成后即补充。

120s 或300次发送先到即停；三轮均 request cap。请求上限包括429，没有延长窗口、退避、过滤拒绝或重跑挑选。两连续10s高拒绝窗口停止规则尚未形成窗口，300 cap先触发。HTTP完成时钟不包含后验oracle/SQL；离线核验和writer flush在drain之后。

| Rep | warmup s | T发送窗口 s | offered/sent/completed HTTP | admitted / 200 | 429 | 窗口内200 / drain200 | goodput req/s | offered req/s | drain s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| rep1 | 107.453 | 3.453 | 300/300/300 | 2/2 | 298 | 1/1 | 0.2896 | 86.881 | 0.515 |
| rep2 | 105.860 | 3.453 | 300/300/300 | 2/2 | 298 | 1/1 | 0.2896 | 86.881 | 0.594 |
| rep3 | 106.875 | 3.344 | 300/300/300 | 2/2 | 298 | 1/1 | 0.2990 | 89.713 | 0.719 |

每轮409/503/其他HTTP错误/timeout/transport error均0。总900次HTTP、6个有效成功、894个429，只有3个成功在发送窗口内；不得用6/T冒充goodput。suite总墙钟515.250s < 25min。准备/索引、180次warmup、drain和离线验证均不混入吞吐。

## 3. 延迟与首层阶段

成功每轮n=2，只有描述性min/median/max，不满足p50≥20或p95≥200；全部/429 n=300/298可报告p50/p95。所有样本都保留；无p99。

| Rep | 200 client min / median / max ms | 200 server median ms | 429 client p50 / p95 ms | 429 server p50 / p95 ms |
|---|---:|---:|---:|---:|
| rep1 | 1765.0 / 1781.0 / 1797.0 | 1781.03 | 15.0 / 16.0 | 8.65 / 10.06 |
| rep2 | 1812.0 / 1812.5 / 1813.0 | 1812.28 | 15.0 / 16.0 | 8.70 / 10.09 |
| rep3 | 1813.0 / 1836.5 / 1860.0 | 1828.24 | 15.0 / 16.0 | 8.40 / 9.60 |

三轮 all-HTTP client p50/p95也均15/16ms，完全由快速429主导，不能当作RAG成功延迟。Windows monotonic分辨率约15.625ms，0ms拒绝值是时钟量化，不是零执行成本。server使用更细计时，跨进程绝对时点不直接相减。

下表仅成功请求的mean ms（各n=2）；Provider已包含在Generation，绝不再加入coverage。

| Stage | Rep1 | Rep2 | Rep3 |
|---|---:|---:|---:|
| coordination_preparation | 15.895 | 13.535 | 13.809 |
| routing | 0.002 | 0.003 | 0.002 |
| material_acquisition | 94.373 | 92.254 | 87.851 |
| generation | 1645.755 | 1673.425 | 1693.217 |
| persistence | 14.752 | 22.256 | 20.913 |
| cleanup | 0.026 | 0.028 | 0.030 |
| Nested Provider | 1641.730 | 1669.465 | 1689.465 |
| Mock HTTP elapsed | 203.500 | 203.000 | 211.000 |
| unexplained | 10.229 | 10.779 | 12.418 |

429仅执行coordination/preparation及cleanup，没有Routing/Retrieval/Generation/Persistence/Provider；未执行阶段不填0进均值。429平均server约9.08/9.23/8.86ms，其中unexplained约7.02/6.99/6.74ms；不能擅自归于DB或Worker。完整按status分布、原始interval在`analysis.json`与`rep*/measured-telemetry.json`。900条重算interval union通过，首层区间未见重叠，coverage+unexplained与server total一致。

## 4. 资源与Provider进程

| Rep | API cores / RSS mean,max MiB | Worker cores / RSS max MiB | Provider RSS sampled max MiB | Provider Job CPU s (2 calls) | Generator+Mock cores / RSS max MiB | PG CPU% / RSS MiB |
|---|---:|---:|---:|---:|---:|---:|
| rep1 | 0.691 / 122.02,122.80 | 0.356 / 625.21 | 88.74 | 2.640625 | 0.312 / 114.12 | 6.28% / 58.19MiB |
| rep2 | 0.713 / 121.79,122.88 | 0.349 / 625.00 | 77.60 | 2.531250 | 0.330 / 113.76 | 6.09% / 56.77MiB |
| rep3 | 0.706 / 119.70,121.14 | 0.330 / 625.05 | 88.97 | 2.609375 | 0.397 / 113.47 | 6.60% / 57.26MiB |

CPU cores=同PID CPU seconds增量/采样时间，host%=cores/32×100；不是把32核百分比当单核。每轮API/Worker/Provider仅4次采样，generator为3/3/2次，PG每轮1次。没有检测到采样错误或缺失group，但不能据此排除短暂峰值/资源饱和；PG一次观测不是窗口均值。Provider空Job集合的0 RSS表示采样时无活动Provider，非漏样填0；RSS峰值只是采样下界、RSS不能当独占可加总内存。完整private-bytes分布在`analysis.json`。

每个成功对应1个Provider Job、1条Provider metric、1次Mock HTTP；每Job累计3个OS进程（与C1一致），关闭时active=0。CPU包括该Job进程树，不等于3次LLM调用。短Provider进程不以PID瞬时CPU速率相加；累计CPU取Job accounting。6次调用全部可归因，没有额外/重试调用。

## 5. 正确性、遥测与清理

- 每个200恰好1个ChatTurn：session/question/完整response hash匹配，恰好新增1对Message，1份MessageEvidence与响应sources一致；所有oracle通过。
- 每个429的request_id无ChatTurn，会话仍只有原始2条Message且无MessageEvidence。每轮总ChatTurn62、Message844、MessageEvidence62（包含60warmup），不存在重复/部分持久化。
- 非Chat表前后全行hash一致（含workspace/profile/bindings/learning等）；不宣称每个Message列都单独hash，Chat tables按上述结构/内容约束核验。
- 各轮HTTP遥测300/300，writer accepted/written各+300、dropped/failed各0，lifecycle测量期间无新增事件；所有HTTP身份对应唯一请求，成功fresh leader，拒绝在admission前后路径可定位。
- Worker测前/后READY、restarts=0；每轮结束STOPPED，保留的process handles均确认退出、Job released、API exit0，三份PG容器均已移除。见`final-cleanup.json`、`rep*/api-cleanup.json`、`result.json`。模型缓存/原始证据保留。

## 6. 与C1对照及诊断

| 配置 | 每轮T s | 每轮200 | 每轮429 | goodput req/s | client成功median ms | nested Provider mean ms |
|---|---|---|---|---|---|---|
| C1 | 120.000 / 120.000 / 120.000 | 69 / 69 / 74 | 0 / 0 / 0 | 0.5667 / 0.5667 / 0.6083 | 1734.0 / 1734.0 / 1609.0 | 1591.8 / 1582.5 / 1532.8 |
| C2 | 3.453 / 3.453 / 3.344 | 2 / 2 / 2 | 298 / 298 / 298 | 0.2896 / 0.2896 / 0.2990 | 1781.0 / 1812.5 / 1836.5 | 1641.7 / 1669.5 / 1689.5 |

**直接证据**：C2没有观测到成功goodput增加；大量admission拒绝，单次成功Generation占主导，Provider mean比C1历史三轮更高。S0的`backend/app/api/routes/chat.py:32`使用BoundedSemaphore(1)，`backend/app/services/chat/capacity.py:21`非阻塞获取失败返回429；成功admission observer仅2条/rep，与成功/Provider对应，其他请求没有进入Provider。独立sessions排除了同session争用作为这些拒绝的解释。

**推断边界**：S0 admission是本实验无法获得两路同时生成的已验证限制；没有证据将其称为Provider内部或数据库容量饱和。短窗口、每轮2个成功、不同成功query组成以及非交错历史对照，都不支持“延迟恶化X%”或“容量下降X%”因果结论。CPU采样与大量拒绝相符，但不能把Provider较长总时长归因于进程启动、导入、调度或网络中的某一项。

## 7. 复现、验证与限制

仓库根、现有venv执行（需要本机Docker/Windows Job及本地TCP权限）：

```powershell
backend/.venv/Scripts/python.exe -m unittest discover -s backend/tests -p "test_rag_c*.py" -v
backend/.venv/Scripts/ruff.exe check backend/tests/experiments/rag_c*.py backend/tests/test_rag_c*.py
git diff --check
```

11个focused unittest通过，Ruff通过，diff-check通过；日志E/`tool-tests.log`、`ruff.log`、`check-results.json`。最初误用pytest发现venv未安装pytest，随后使用项目已有unittest，无新增依赖；该失败不是业务回归。未重复无关全Backend suite。

实际三轮命令：`backend/.venv/Scripts/python.exe -B .local/benchmark-c2-20261023-01/run.py`。coordinator固定只运行rep1–3并逐轮核对freeze/cleanup。**不可覆盖现有E重跑**；若以后另获批准，复制run.py/freeze到新证据根，使用同冻结源码，再运行。单轮入口为`rag_c2_run.py --output <新的目录> --measure-gate .local/benchmark-c1-complete-20261023-01/preflight`。离线汇总：`backend/.venv/Scripts/python.exe -B .local/benchmark-c2-20261023-01/analyze.py`。

限制：

1. cap使测量只有约3.4s；每轮成功n=2。不可外推稳定容量、尾延迟或付费Provider性能；没有开环或S1证据。
2. 窗口包括两个lane的HTTP client构建/首次连接时间，C1复用warmup连接；这一启动开销在极短窗口中不可忽略。`requests.jsonl`提供实际send时点；没有去掉该开销改善结果。measurement.json使用最后实际request-start作为cap边界，window.json是更早的分配时点诊断记录，统计以measurement.json为准。逐lane日志写入成本也保留。
3. 1秒资源采样仅捕获少数点，PG docker-stats只一次；Provider Job CPU是完整累计，但CPU峰值/真实RSS峰值未建立。时钟量化与历史非交错对照进一步限制性能因果推断。

结论：**PASS（已批准I/S0/C2的有界执行、证据完整性与正确性），不是“C2容量达标”。** 全部历史证据保留，未改变生产/并发/恢复策略。到此停止，后续实验须另行审阅批准。
