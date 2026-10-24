# Backend Benchmark — RAG Chat Workload Design

2026-10-23。状态：**设计提交 Chat 审阅；未执行负载、未启动服务、未修改测试工具**。
本地 HEAD：`8b1b0d9b605d25e7e6d6101bf21a60708aa33135`，branch `main`，与 `BENCHMARK_BASELINE.md` 一致。开始时已跟踪文件干净，历史未跟踪 evidence 保留。本设计中的数值是预先指定的实验参数，不是已证明的容量或性能目标。

## 1. 研究问题与边界

在 Windows 单 API、真实 TCP FastAPI、独立 PostgreSQL、离线 FastEmbed、Persistent Query Worker、固定本地 Mock Provider 的条件下，增加 RAG Chat 到达负载时，成功完成速率何时停止增长？延迟/拒绝主要位于协调、材料获取、生成还是持久化？可选 session concurrency 改变了什么，是否保留正确性？

容量定义为**该固定模型、数据和 Mock 条件下，满足预先约定错误/延迟约束的负载范围**；若尚未约定业务 SLO，只报告吞吐—延迟—错误曲线及饱和迹象，不宣布“支持 N 并发”。不是实际外部 LLM SLO、索引吞吐、学习模式性能、Linux 或多 API 进程实验。

源码依据：`knowledge/retrieval.py:85` 会读取 workspace 的 ready Chunk/Document、真实调用 query_worker.embed、内存评分并最多返回 5 条；这不是数据库向量索引检索。`query_supervisor.py:247` 在已有 embedding in-flight 时返回 429，在不可用时返回 503。`api/routes/chat.py:125` 之后依次包括 admission/preparation、routing、material acquisition、generation、persistence/commit 和 cleanup。因此 Worker 限流、全局 Chat admission 和 session 协调可能先于 CPU 饱和，需要分类观察，不能只解释为数据库慢。

## 2. 固定主数据集 D1

拟创建一个合成用户、一个 workspace、20 个 UTF-8 TXT 文档，主题为 20 个虚构设施的维护说明。每个文档含独特设施代号、维护周期、容量及校验规则，所有数据人工合成。每个文档在应用空白规范化后**恰好 8,000 字符**；生成器用固定模板/词表、seed=20261023 生成，无随机时间戳。尾部填充同主题自然句，不使用真实隐私文本。

`index_worker.py:96` 按步长 800 切出最多 1,000 字符：D1 应为每文档 10 chunks（前 9 个 1,000 字符，末个 800），总计 **200 chunks**，200 字符相邻重叠。实际应用索引后核对这个分布、向量维数/model_id、内容及向量哈希；禁止直接写随机向量。上传/索引顺序固定，建库和索引不进入 Chat 测量。文本文件/hash、规范化文本/hash、chunk ordinal/长度/hash、文档/向量集合 manifest 一起冻结。当前未生成数据文件，因此这些哈希是开始测量的前置条件，不能凭本报告伪称已固定。

60 个固定查询：每个文档 3 个单文档可回答问题（维护周期、容量、操作约束），每次只要求一个事实；答案/预期文档/允许的 chunk 集写进 oracle。查询按固定种子打乱的 60 项循环均匀分布，所有并发等级和 A/B 使用相同预生成序列；不得根据上次成功/失败选择问题。`document_ids` 不限定，检索扫描整个 200-chunk workspace。主请求 `answer_mode=document`、`session_type=questioning`、`learning_mode=direct`，强制走知识路径，排除 smart-routing 分支变化。

预检要求 60 个查询都能在 min_score=.35、lexical_rescue=true 下返回正确设施证据并完成 grounded generation；若失败，停止修订数据/oracle 为新版本后重新冻结，**不在测量时调低阈值或过滤难题**。这测量 RAG 执行链，不是开放域质量评测。

**会话控制**：主场景预建会话，每个只有固定 1 个 user/assistant 历史对、固定 questioning profile 和 SessionWorkspace 绑定。每会话只接受一个 measured 新逻辑 turn，防止上下文随负载增长。预建 300 个 measured 会话/rep，加独立 warmup 会话；创建与校验不计入发送窗口。每个逻辑 turn 使用唯一 request_id，HTTP attempt 另有唯一实验 ID；重复场景除外。没有活动 LearningContext；所有学习表保持基线不变。活动 Learning 的正确性继续由既有回归证明，不用 questioning 基准冒充覆盖。

**Mock**：本地 ThreadingHTTPServer，真实 Provider 子进程/HTTP/JSON 协议，固定 **200ms 最小服务等待**，无随机延迟、无重试、无流式输出。按查询 oracle 检查收到的实际 evidence，返回确定性 grounded JSON、正确 citation 编号及固定近似长度答案（每查询冻结字节/hash；不伪造语义证据）。记录实际 Mock 等待及并发；调度造成的额外等待不抹掉。若缺正确 evidence，作为正确性失败，不无条件返回成功。审计调用次数及 attempt ID，不记录凭据/原始 prompt；生产 provider_completion/generate_response 不被替换成即时返回。200ms 是实验控制参数，不代表商业 LLM 延迟。

## 3. 三类场景与矩阵

所有场景 Persistent Worker=true；同一场景分两种独立启动的 flag 配置：S0 `SESSION_CHAT_CONCURRENCY_ENABLED=false`（默认），S1=true（可选）。不在测量窗口中热改开关，不混合统计。

| 场景 | 逻辑负载与候选等级 | 解释 |
|---|---|---|
| I：独立会话主场景 | C=1/2/4/8/16；每请求不同 session_id/request_id | 每 client lane 同时最多一请求，完成即补充；不是同步 barrier 波次 |
| S：同会话竞争 | cohort 大小 1/2/4/8/16；同 cohort 一个新会话，各请求不同 request_id；10 个 cohort/level/flag | barrier 只用于该竞争诊断；等待 cohort 清理后换会话，结果单列，不能合并进 I 的 req/s |
| F：Singleflight | cohort 大小 1/2/4/8/16；同用户/session/payload/request_id，只 HTTP identity 不同；10 cohort/level/flag | 区分实际 leader/follower/rejected/persisted_replay；不能将多个 HTTP 成功算多次生成 |

F 保持 per-request follower limit=4、global=8，因此 8/16 是明确超限探测，不能要求全部成功。仅一 cohort 同时活动，不顺便改变全局竞争变量。使用 barrier 和既定 Mock 200ms，不在 measured 请求上加同步门；若实际未形成 follower，则如实标为 persisted replay/未命中共享执行，不冒充 Singleflight 证据。每 cohort 收敛后再发送一次离线 replay 检查，排除于性能统计。

S 的 409 是必须统计的协调冲突，可能还有 admission/embedding 429。不假设所有 cohort 恰好一个成功：错开的请求可能顺序成功；验收的是串行化后的历史/持久化一致性，而不是固定 HTTP 分布。出现 409 不自动视为错误实现。

### 测量阶段和边界

I 每 cell 3 次独立 repetition，新的 DB/存储基线、API/Worker 进程，但同一冻结模型缓存。每 rep 先 60 个顺序 warmup（覆盖全部查询一次），最长 180 秒，任一非 200/oracle 失败即停；排除 warmup 的指标/状态，重置客户端计数并取遥测计数快照。

Measured 目标 120 秒，或 300 次已发送请求先到即停止发新请求；实际窗口 T=最后停止发送时刻−开始时刻，明确标记是否 request cap 触发。最晚再 drain 90 秒，不因等结果延长发送窗口。请求 timeout=90 秒；最终超时仍须在离线 DB 检查中分类 committed/absent/unresolved。90 秒不是撤销服务端执行证明，未确认清理不开始下一 rep。资源/硬停止见第 7 节。

先运行 C=1 S0，审阅后才批准 C=2→4→8→16；之后 S1 使用相同场景/数据。正式 A/B 成对复测按预定 AB/BA/AB 顺序，等级顺序相同，禁止根据“最好结果”重跑。S/F 为后续独立诊断，不与 I 同时执行。没有一个命令自动批准整个矩阵。

### 到达率与闭环偏差

闭环 I 随延迟自动降低 offered load，只能用于有限并发探索；不能据此证明固定到达率下的可持续容量。后续必须另做开环确认：以已审阅 C=1 的三次 wall-window goodput 中位数 R1 为参考，冻结 λ={0.5,1,1.5,2}×R1 req/s，周期到达、固定小抖动种子（±5% 间隔且正值），每 rep 同样 120 秒/最多 300 scheduled，3 rep。

开环发射计划预先生成，不等待前一个响应；客户端最多 32 outstanding，不增设无界队列。超过上限记录 client_not_sent(overload)，计划点仍计 offered；不得拖延成闭环。记录 scheduled_at、actual_send_at 和 dispatch lag，丢发率/客户端资源饱和时标为“生成器受限”，不能归咎服务端。到达至完成的 schedule latency 与实际发送至完成 HTTP latency 同时报。开环与闭环不拼接；更高 λ 只有清理和正确性检查通过后另行批准。

## 4. 指标、分母与失败处理

统一 monotonic 时钟：客户端保存 scheduled/send/headers/body-complete/error 时点；应用区间使用自身时钟，不直接将两个进程的绝对 monotonic 值相减。UTC 仅辅助关联。TCP body 完成是成功响应边界，不计后验 SQL 验证。

| 指标 | 精确定义 |
|---|---|
| N_offered | 窗口内 scheduled HTTP attempts；闭环等于已发数，未另虚构独立到达流 |
| N_sent | 实际交给 HTTP 客户端发送的 attempts；连接失败仍在此集合 |
| N_observed | 应用中间件可关联的 HTTP records；不假设等于 N_sent |
| N_admitted | 明确定义为获准进入 execute_chat 的 admission 成功数；Follower 和 persisted replay 单列，不能用 N_sent−429 推导 |
| successful throughput / goodput | 窗口内完成且离线验证为有效 200 的数 / T；后续 drain 完成不塞回此数 |
| offered / dispatched throughput | N_offered/T、N_sent/T；F 另报逻辑 turns/s 与 Provider executions/s |
| cohort outcome rate | 对窗口内 offered cohort 最终 success、429、409、503、其他 HTTP、transport error、timeout、not_sent 分别计数/N_offered；另给 sent 分母，并注明重叠的最终 DB outcome 不是第二次 HTTP 结果 |
| drain-inclusive rate | 最终 verified successes/(T+实际 drain)，仅辅助；不能与上面的窗口 goodput 混称 |
| latency | all received HTTP outcomes 和每 status/path 单独分布；success-only 必须明确标签；transport error 给 time-to-error，timeout 给 deadline/右删失计数，不填成有限真实完成延迟 |
| CPU | 每进程组 CPU seconds 增量/采样秒数=占用核数，同时给除以逻辑 CPU 数后的 host 百分比；明确分母 |
| Memory | API、Worker Job、Provider Job、Mock/load generator 分组 RSS 与 private bytes 的均值/最大值；PG 容器 CPU/memory 另列，RSS 共享页不能当可相加的独占总内存 |

延迟最近秩百分位：n≥20 报 p50，n≥200 报 p95，n≥1000 报 p99；低于门槛报 n/median（仅描述）/min/max，不把最大值命名为 p99。每 rep 独立；只有同 cell、配置和数据一致时可附 pooled 分布，注明相关性及三个 rep 的范围，不把 pooled 样本当独立置信区间。本设计的 300/rep 上限意味着初轮不承诺 p99；需另批准更长运行。

所有请求台账对账：N_offered=N_sent+N_not_sent；所有 sent 均有最终客户端 outcome。服务器可能在 Uvicorn 限流层返回 503（limit_concurrency=16，连接也影响门限），没有中间件记录；归为“pre-app/unknown”并保留证据，不伪造 stage。状态码相同也区分 session/global admission、embedding busy、恢复不可用等已证实来源，无法确认时为 unknown。

**正确性不变量**：每成功 fresh 逻辑 request_id 恰好一个已提交 ChatTurn，user/session/workspace binding、response 和请求语义一致；恰好一对新增 Message、正确 sources/MessageEvidence；无重复持久化。拒绝/失败不能留下部分消息或错误完成的 turn；客户端超时可出现合法已提交结果，单独标为 committed-after-timeout 并禁止盲目重试。F 每逻辑 turn 至多一个有效生成执行及一份持久结果，接受的 follower/replay 与 leader 响应一致，不能要求 follower 各调用 Provider。唯一 request_id 的不同语义冲突预检应拒绝，不计主负载。

S 验证所有完成 turn 对应某个合法串行历史：无丢失/交叉消息，后续生成只见已提交前缀；questioning profile/SessionWorkspace 保持正确，学习表无变化。每次 rep 完成后检查全部 measured request IDs，不采样检查、不在 timed loop 内逐请求 SQL 验证。Mock 调用须按 logical request/HTTP attempt 归因：复用既有 context label + benchmark-only model-name tag 透传方案，不改变 prompt/embedding/provider 执行；记录该观测适配源码哈希和常数开销，不用并发共享全局 current 字典。任何额外调用、无法归因或幻觉式无 evidence 成功均使 correctness gate 失败。

**应用遥测**：按 HTTP identity 收集各首层 stage、follower_wait、provider durations、execution_source/role、stage_covered/unexplained；仅对实际执行路径计 stage，失败路径已有区间保留。阶段区间 inclusive，Provider 嵌套时间不叠加。输出每路径覆盖比例、unexplained 分布和记录缺失率；不拿父阶段时长直接推断数据库/模型内部原因。保留 worker run/instance/restart/budget、writer accepted/dropped/written/failed/queued 前后差值。无尾事件不代表没有错误。

## 5. 资源与测量适配

API 独立 Windows 进程，load generator/Mock 在另一进程或各自进程；不继续将 API+harness 混为同一个 CPU/RSS 数。复用 Windows Job/process handle 采样，1 秒间隔；短 Provider 进程可能漏采，需起止 CPU/peak accounting 或明确下界，不能以缺样记 0。Docker stats/PG settings 只用于已验证属于本实验的容器；每次实际查询的观测成本固定，设置有界 timeout，禁止采样阻塞主请求。

最小 benchmark-only 适配：独立 API fixture；可重用 HTTP client/连接池（每 lane 最大一 keepalive 连接，开环上限32），关闭自动重试、固定 trust_env=false；逐请求并发安全台账；进程内只读 admission 事件和 writer snapshot 导出；Mock attribution；离线核验。所有适配不改变 lock 顺序/容量、Provider 调用实现或生产路由。获取 N_admitted 的 observer 只在 acquire 成功返回时记一条事件，不能把等待/失败算入；若不能安全取得，该指标标 unavailable，先解决观测门槛再执行主基线。

metrics.jsonl 有旋转/队列上限。使用持续只读收集器保留所有轮转代际，或在短测预检证明预算内不会覆盖；记录收集器开销、丢失和 writer 快照。任何 drop/failure/不可解释记录缺失使阶段归因结果不完整，不把不完整记录当快路径；HTTP 总体 outcome 仍保留。进程内 observer 不是生产 debug endpoint，也不能启动新 Python 进程去假称读取活服务计数。

## 6. 重现清单和现有工具复用

每 run manifest 必须包含：精确 Git SHA、工作区 diff/工具哈希、全部依赖锁及实际版本、Windows build、CPU 型号/逻辑核数/电源模式、物理内存、磁盘、后台负载、Docker/WSL 版本及 CPU/RAM limits、PG18.6 image digest/PGDATA/端口、server settings（max_connections/shared_buffers/work_mem/synchronous_commit/fsync/事务隔离）、SQLAlchemy pool size/overflow/timeout、API limit=16/keepalive=5/worker=1、所有 benchmark 相关 flags、Job 限制、模型/tokenizer/缓存哈希、Mock 协议/响应/延迟、D1/oracle、seed、时钟及生成器连接配置。敏感值只记录“已供应且验证”，不输出实际 JWT/Fernet/Provider key。

主条件：Persistent Worker=true，FastEmbed 离线，Runtime Metrics=true，citation audit=false，startup recovery=false、maintenance=false，query timeout=30s、index timeout=90s、session wait=1s、singleflight wait=30s、limits=4/8；重启默认3/600s/failed-cycles3/backoff1s。仅 S0/S1 的 session concurrency 是开关变量。不要修改生产 .env；fixture 显式生成隔离配置。

warm 定义为模型已缓存且 Worker READY，60-query warmup 后测量；OS/PG缓存仅称“经过预热”，不声称受控清空。每 rep 恢复同一 dataset/session 初态，单独记录 DB增长和 restart。cold startup 是另一个将来的实验，不能混入主延迟。A/B 除被研究变量外，dataset、sessions、Mock、pool、连接复用、资源配额、计量均相同；配置不符即 invalid，而非用结果事后修正。

| 已有文件（backend/tests/experiments/） | 可复用 | 必须适配 / 不可直接比较 |
|---|---|---|
| chat_baseline.py:63,98,137,195 | DB身份校验、真实离线索引、确定性历史、逐 turn 验证 | 5 warmup+50顺序、1 chunk；替换 generate_response，不是真实本地 HTTP Provider；函数 wrapper 计时与现有 stage 不同 |
| chat_worker_ab.py:35–84 / chat_worker_ab_block.py | 顺序ABBA、源hash、Worker/Job收尾、向量一致性 | 25/块、50/arm，warm顺序、单查询单chunk；生成函数Mock；p99=最大值；历史 HTTP-normalized rate 不是真实容量 |
| run_final_load.py:35,67 | 管理实验 Job、DB拥有权、finally清理 | 固定WSL Ubuntu PG16和端口；主方案改复用 Phase4 DockerPG18隔离夹具，不自动再跑整套回归 |
| final_load_workload.py:8,51 | 固定计划、A/B顺序、资源采样思想 | 1/2/4、10次 barrier burst、Mock2s；线程池4；与持续 closed/open loop不同；API+harness资源混合 |
| analyze_final_load.py:25–73 | 全状态计数、拒绝延迟、后验DB核验 | 固定levels、假设每200各一次生成、从字符串判原因；需支持409/503/timeout/unknown、F共享生成、真实时间窗口及样本门槛 |

复用 Phase4 Windows/PG/Mock 管理及 startup/cleanup 验证，不新建基础设施。上述旧结果仅为夹具行为和正确性参考；PG版本、代码SHA、Mock延迟、数据规模、生成方式、开关、缓存或客户端生命周期不同，不能直接做改善百分比。

## 7. 时间与资源预算 / 停止规则

下一步仅 I/S0/C1：3 rep，测量最多6分钟、最多900 measured attempts；warmup共180请求最多9分钟；总墙钟预算25分钟（含一次准备/索引/校验/cleanup）。到时停发并保留失败证据，不延长去追漂亮百分位。

完整候选 I 矩阵10 cells×3 rep，测量上限60分钟、9000 attempts；加warmup/drain/准备总墙钟上限3小时，分批批准。S/F 每cell最多10 cohorts（16档最多160 HTTP）；各场景墙钟上限20分钟，未完成标截断。开环另批，最多4 rates×3 rep；不在第一轮自动执行。

运行前空闲 RAM≥6GiB、可用磁盘≥10GiB；为本次数据/日志设5GiB目录预算，不改模型已有Job内存限制。系统可用 RAM持续10秒低于max(2GiB,物理内存10%)、本目录达到5GiB、磁盘不足5GiB、客户端dispatch lag p95>100ms连续两个10秒窗口：停止发新请求并标资源/生成器受限，不删除证据。

任一数据不变量失败、cleanup_unconfirmed/DEGRADED/MANUAL_REQUIRED、意外Worker重启、未知在途状态无法收敛：立即停止升级并 fail closed。非预期5xx/transport timeout 超过5%（最近50 sent，至少20才判断）或连续3次timeout：停止该cell。I 的429超过20%连续两个10秒窗口且每窗≥20 offered，结束cell并不自动提高C；保留它作为过载结果而非抹除。S/F预期冲突/超限不套用I拒绝规则，但安全/资源规则相同。没有自动调预算、降低安全检查或重试失败请求。

## 8. 启动基线的精确门槛与下一步建议

**建议下一步只执行 I/S0/C1 的三次 warm repetitions**，回答路径是否真实、计量是否对齐、串行成本分布如何；不要把它称为容量测量完成。

执行前必须全部满足：

1. 用户审阅本设计并授权下一步；无性能负载已提前运行。
2. 冻结 D1/query/oracle/session/generator manifests 与工具源码hash；所有数据/密钥合成，生产路径拒绝；确认PG身份和cleanup所有权。
3. 60-query 非计时路径预检全部有正确检索 evidence、真实 query embedding、真实 Mock HTTP generation、一次成功commit；没有test embedding、生成短路或 persisted replay 混入 fresh 主场景。
4. 独立API、配置矩阵、Mock200ms、客户端无重试/连接上限、计数observer和OS/Docker采样已验证；不复用并发不安全的全局 current。
5. 能对账 HTTP attempt→business turn→Mock call→DB outcome；预检包含一次成功、拒绝及超时分类的工具自检，不能给主实验注入故障或依赖隐藏harness信息解释产品错误。
6. stage覆盖、writer快照/旋转收集和失败计数可用；相关正确性回归通过，实际schema兼容/Worker READY，既有证据适用于冻结代码。
7. 资源预算、停止条件、运行记录和安全清理已就绪；没有生产服务/paid Provider被选择。

任何门槛未满足应报告 NOT READY 并只修最小 benchmark 工具；不能以 SQLite/ASGI/test模型替代来“通过”。完成第一 cell 后先审阅样本数、全部失败/拒绝、归因覆盖和正确性，才决定是否批准 C2/后续S1及开环。此设计不包含优化方案、代码提交或性能结论。
