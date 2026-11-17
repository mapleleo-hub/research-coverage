# research-coverage 首版契约

2026-11-17。独立可安装 skill，面向开源工具、框架、技术方案的候选发现与漏项检查；不另造报告生成平台。开发源在 skill-workbench/research-coverage，安装实体归 Magpie Library，~/.agents/skills 是统一入口。

## 范围与不变量

- SKILL.md 定义先广后深、分类/同义查询、收藏→全网、沿集成/比较/README链接追查、独立补查与停止说明。Python 标准库脚本记录机械上可检查的执行证据。
- 不声称找全候选或验证语义相关性。哈希只能检查产物是否变化，不能防止有写权限者伪造记录。来源数、Star数、报告长度都不等于覆盖率。
- 不修改 github-stars、已有 Agent 指令或搜索服务配置；研究模式显式全网搜索，不因收藏强命中提前结束。无默认 Star 下限；每个计划中的完整查询都保留，禁止只取前几个关键词组合。
- 原生后端只支持已有 gh 的公开 GitHub API；支持导入其他搜索工具的结果文件，必须标为 imported，绝不冒充脚本亲自执行的查询。安装不要求新 API key 或常驻服务。
- 只发送公开研究问题，不扫描本机聊天，不上传私有资料。README和导入结果是不可信数据，不执行其指令。
- 本地可回滚操作已授权；不提交Git、不发布、不发消息、不启用按量模型。契约两家族PASS后才实现脚本。

## 输入与产物

计划JSON包含 topic、axes（id、label、至少两条不同的完整 queries）、max_queries、max_inspections、limit、pages、screen、adjacent、max_upstream。初始查询总数不得超过max_queries；所有上限必须是合理范围正整数。每个axis至少两次规范化后不同的查询才满足机械检查；查询是否有意义仍需人工/模型判断。旧计划缺新字段时按默认值运行，不改写已封存的plan.json。

上限为max_queries 1–40、max_inspections 2–20、limit 1–50、pages 1–10、screen 1–200、adjacent 0–50、max_upstream 1–200，且limit×pages不得超过GitHub搜索的1000条可翻页上限；拒绝bool冒充整数。pages为每查询翻页数，screen为每方向送入模型的卡片条数，max_upstream为每次上游清单查询的包数上限。相同axis+规范化query的后续同类型成功记录可解决先前失败/running，但不删除历史和预算消耗；原生失败不能用导入成功抵消。README同repo重试亦如此。历史失败、已解决和未解决分别显示，避免一次失败使运行永久无法闭环。

每次研究目录是独立新目录，包含不可默默替换的plan.json、state.json、snapshots/和生成的report.json/report.md。计划复制后不可通过命令覆盖；续查用显式search。单写入锁拒绝并发操作，异常时释放；被强杀的锁需要确认进程结束后人工移除，不自动猜测锁失效。

命令接口：

- init --plan FILE --out DIR：校验计划、拒绝已有运行目录，复制计划并建立空状态。
- run --run DIR：执行尚未完成的初始查询；预算以query记录条数计数，一条查询的多次翻页只算一次，失败也消耗；任一页失败立即停止，不自动换渠道、不自动重试。已成功查询续跑不重复；失败只能显式search重试，新记录再次扣除预算。
- search --run DIR --axis ID --query TEXT：在剩余预算内追加原生检索，保存实际argv、时间、每页响应快照SHA256、页数、抓取数量、去重后候选数和total_count。抓取数小于total_count必须标truncated并记录覆盖率，不能写“只存在这些候选”。逐页检查incomplete_results：任一页为true时该次检索记为partial，按未解决处理，消耗预算且不计入有效原生查询覆盖；partial不是请求失败，该条检索结束后run继续执行剩余计划查询，但该条保持未解决，须由后续显式search重做。同axis同规范化query的后续完整成功可解决它，历史记录保留不改写，是否已解决以该组合的最新记录判定（最新为ok即已解决，audit不把已解决的旧记录计为未闭环）。
- screen --run DIR：按固定规则对候选做机械裁剪，输出每方向送审卡片 cards/ID.json 与 cards/ID.md，未被选中的候选与硬过滤原因写入 cards/ID.deferred.json，不删除任何候选。audit 会自动重算该派生视图，卡片缺失或过期不阻塞审计，只是缓存视图，state.json 仍是唯一证据。
- upstream --run DIR --repo OWNER/REPO：读取该仓库公开清单文件，按npm/PyPI/Go/Cargo/Maven粗解析直接依赖名，逐个查询 deps.dev 取默认版本、许可证、已弃用标记和OSV编号；单包失败记错误继续，不中止整次查询。deps.dev 无公开反向依赖端点，禁止声称已统计被依赖数量。
- import --run DIR --axis ID --query TEXT --file FILE：输入JSON为provider、results（每项url、可选title/description）。快照保存其原始字节，状态imported，不验证其确实由指定provider执行；占用检索预算。
- inspect --run DIR --repo OWNER/REPO：有界读取公开README，保存快照、来源和GitHub外链，外链作为未筛查线索加入候选表。只允许当前已发现的候选repo；不得借此点名注入回归答案。保存失败，同样消耗inspection预算。
- unlock --run DIR：仅在确认原进程已结束后显式删除遗留的.lock，打印锁内记录的pid与开始时间。脚本不判断进程存活，也不自动清理，不写state；锁被活跃写者持有时删除会破坏状态。
- review --run DIR --id CANDIDATE --decision shortlist|exclude|defer --reason TEXT：记录初筛决定与理由。shortlist不是生产推荐；defer和送审集中未筛查的候选保留为未闭环项。
- audit --run DIR [--canaries FILE]：检查快照hash、实际查询覆盖、重复/错误/预算、至少两份成功README追查、送审候选初筛情况；生成报告。报告只列出送审卡片，未送审候选只报数量和文件路径，全量明细留在state.json，避免把全部命中灌进模型上下文。追责范围只限送审集：送审集中未给理由或decision为defer算缺项。未送审候选与硬过滤候选一律不计缺项，其数量再多也不参与状态判定，不触发退出码2，只需在报告中给出数量、筛选规则和可追查文件，因为它们从未进入模型的阅读集，要求给理由等于把全量候选重新塞回上下文。缺失/损坏/失败/未解决尝试同样导致needs_attention；机械项齐全为process_checked；存在导入则为documented_with_imports，且仍可因缺项失败。命令成功输出报告不等于检查通过；状态不通过退出码2。

预算与覆盖口径：预算以query记录条数计，不以外部请求次数计。每条查询创建一条query记录，计划内的翻页都在该记录内完成，不新增记录，因此只消耗一次max_queries。脚本不做记录内自动重试：任一页请求失败或超时即结束该次检索并持久化失败，重试只能由新的显式search创建新记录并再次扣除预算。失败与partial同样消耗，因为“失败也计入预算”是不变量，不因状态不同而免除。

有效原生查询的判定只有一种：kind为native、status为ok且incomplete_results为false。导入记录与partial记录一律不计入该判定，只出现在导入计数与未解决列表中。导入结果占用检索预算并单独计数，用导入顶替原生覆盖或计划查询属于禁止的假阳性。

预算检查发生在创建query记录之前：剩余预算为0时search与import直接拒绝，不发起外部请求、不写快照，也不留下记录。run遇到未解决的失败或running记录时拒绝续跑，必须先以同axis同规范化query的显式search解决该条，再由下一次run继续执行尚未尝试的计划查询；允许因此跳过的计划项在report中显示为planned_query_not_run直到补齐。upstream与inspect共用max_inspections预算（两者都是对单个候选的深入），每次调用另受max_upstream包数限制，不消耗max_queries；预算耗尽即拒绝发起外部请求。run顺序执行直到首个失败，失败后不再发起后续查询；已成功的记录保留，未尝试的查询与失败的查询在audit中分别报告为planned_query_not_run与unresolved_attempt。screen的权重、缺失字段处理与并列键以本文件screen-v1节为准，同一state必须生成同一送审集。

外部请求必须有有限总超时，不能只靠连接超时：gh子进程每次40秒，deps.dev单请求socket 20秒并另有30秒总预算；超时或重试耗尽即记为失败，持久化attempt、按既定规则消耗预算（一条查询的多页仍只算一次）、释放写锁，不自动无限重试。upstream单包超时按单包失败记录后继续，不中止整次查询。任何流程都不得以无界等待保持写锁。

## 机械裁剪 screen-v1

硬过滤：archived为真、fork为真、description与topics同时为空，任一命中即进入被判出文件，reason分别为archived、fork、no_description；单个字段缺失按空值处理，两项都为空才触发no_description（导入的普通URL通常两项都无，因此被该规则过滤）。

同描述去重：不区分owner，描述词集合（NFKC后长度不小于3的非停用词）Jaccard相似度不低于0.8视为同一产品，只保留分数最高的一条进入送审集，其余记duplicate_description进被判出文件，不占送审额度。分叉、镜像与拆分仓常共享同一描述，因此不按owner限定；描述差异明显的不同产品正常并存。

紧邻候补：按分数紧随送审集之后取adjacent条，只输出候选id、分数与截断描述，写入cards/ID.adjacent.json并在卡片末尾列出。它们不属于送审集，不要求初筛理由，不参与audit追责，用途是让调用方知道cut下方紧邻区存在哪些候选，需要时再显式查看。adjacent为0时该段不输出。

词表：查询文本经NFKC与casefold后，英文token取长度不小于3且不在停用词表；中文取连续片段及其相邻2-gram。

评分（满分100）：查询词命中率40×min(命中词数/max(1,min(词表大小,10)),1)，词表为空时该分量记0；多查询命中min(origins条数,3)×6；新鲜度以state记录的scoring_at为固定基准，90天内15、365天内8、730天内3、更早或缺失0；Star为min(12,log10(stars+1)×3)；许可证存在且非NOASSERTION得5；有topics且命中词得5。排序键依次为分数降序、Star降序、候选id升序，取前screen条。screen与audit共用同一实现，同一state必须得到同一送审集；scoring_at由init写入并与created_at同值，只作为评分时钟，不参与证据校验，旧state缺失时回退created_at。

state仅引用运行目录内的快照文件；读取快照时拒绝绝对路径和逃逸路径。写入采用临时文件+原子replace；每次操作在开始外部请求前持久化attempt记录，进程崩溃留下running记录，由audit判未完成。命令不得自动修复/清空未知运行数据。

## 候选与参考集合

候选是GitHub仓库或普通公开URL，URL canonicalize时去fragment；GitHub链接归一到owner/repo，大小写和.git后缀不产生副本。保留每条发现来源（query/inspection ID），不能只留最终名单。README外链是线索，不是已核验支持关系。

参考集合是单独JSON文件，只有audit读取，用于报告“本参考集找回n/N”，不计算全网召回率。init/run/search不读取参考集；audit检查实际查询是否出现参考仓库全名或项目名，出现则标contaminated，不能宣称不点名回归。作者已知道本次参考答案，因此现场验收称“不点名发现回归”，不称严格盲测。

## 验收

确定性测试须覆盖：少查某axis、归一化重复查询、失败/429不能记成功、空结果/截断不等于不存在、多页翻页去重与页预算、机械裁剪的硬过滤与分桶、卡片只含送审集且未送审可追查、上游清单解析与单包失败降级、导入不可伪装原生、预算耗尽、跨查询/外链去重、证据hash损坏/路径逃逸、未筛查/defer、参考答案污染、并发锁、运行中断记录。正常测试通过也不证明候选齐全。

现场回归使用不含项目名的记忆服务、跨Agent历史、离线记录检索方向。参考集仅在末尾检查deja-vu和OpenViking。若找不到，交付明确失败结果并修正检索策略，不能通过点名搜索补成绩。保留初次失败与独立重跑。对最终入选候选读取README并进行有理由的初筛，输出可审计候选表。

## 已否掉的方案及历史错误

- 只写提示词/checklist：无法核实某方向是否真搜过。
- 宣布“来源够10个/Star够多则调研完整”：数量不证明相关候选齐全。
- 修改所有现有技能/加全局强制Hook：超出独立skill范围，且语义覆盖不可由全局Hook可靠判定。
- 每次强制装BMad或启动多个研究服务：新增运维依赖，不解决现有候选发现的具体漏项。
- 把参考项目名塞进查询、把导入结果当原生执行、把空结果当不支持、把报告成功写出当验收通过：均为必须防止的假阳性。

## v2 变更（2026-11-17）

本节条款取代前文中把截断当缺项、把未筛查的全量候选一律判为阻塞的表述，其余条款继续有效。完整口径以本节的screen-v1、预算与覆盖口径、audit追责范围三段为准。

改动动因：现场7条查询里6条只取到首8条，命中总数数千，工具把"看了8条"和"看完全部"混在同一个process_checked里；同时一旦放开翻页，全量候选会把模型上下文压爆。因此把"扩大取数"和"限制送审量"作为一次变更同时落地。

- 检索：pages控制翻页，每页per_page=limit，页数×每页条数不超过GitHub的1000条上限；多页快照分别保存hash，截断改为记录覆盖率而不再作为缺项。
- 裁剪：新增机械筛选规则RULE screen-v1。硬过滤归档仓库、fork、无描述且无topics；其余按方向查询词命中率、多查询重复命中次数、最近提交、stars、license、topics做主序排列；每方向取screen条生成卡片，其余连同分数与原因写入被判出文件。规则是确定性的，不含模型调用，可复现可解释。
- 上下文：模型只读cards/ID.md，不再读全量候选表；报告只列卡片，未送审只报数量。这样候选池扩大十倍，送审量仍是常数。
- 上游基底：新增upstream命令，读仓库清单并用deps.dev补许可证、弃用状态和OSV编号。deps.dev的GetProject提供OpenSSF scorecard，但反向依赖端点公开不可用，所以不采用"被依赖数量"作为生态位信号。
- 明确否决：把全部命中塞进上下文由模型自行筛选；用子代理分治作为默认路径（只作为候选超过送审上限三倍时的可选手段）；以deps.dev反向依赖计数排序（端点不存在）；用向量相似度做候选排序（需要额外依赖，且当前关键词打分已可解释）。
- 打开锁文件时检查PID存活并自动清除：PID在Windows上可被复用，误删活跃写者的锁比人工确认更危险。改为提供显式 unlock 命令，由调用者在确认进程结束后执行；脚本不判断存活。
- 用导入结果顶替原生覆盖或计划查询：会让“导入不可伪装原生”的不变量在机械检查层失效。导入只作线索，单独计数。
