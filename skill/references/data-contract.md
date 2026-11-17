# 数据与检查口径

计划包含topic、axes（id、label、queries），及七个上限：max_queries 1–40、max_inspections 2–20、limit 1–50（每页条数）、pages 1–10（每查询翻页数）、screen 1–200（每方向送审条数）、adjacent 0–50（紧邻候补条数）、max_upstream 1–200（每次上游查询的包数）。limit乘pages不得超过GitHub的1000条可翻页上限。旧计划缺字段时用默认值 12/6/10/3/25/0/40，不改写已封存的plan.json。每方向至少两条规范化后不同的完整查询。

原生接口为公开GitHub仓库检索，默认best-match，不设Star下限。一条查询按pages翻页，每页单独保存快照和SHA256；抓取数小于total_count标truncated并记录coverage=fetched/total，不能从未返回推断不存在，也不能用覆盖率当召回率。

研究目录保存plan.json、state.json、snapshots/、cards/、upstream/ 和 report.json/report.md。查询、追查、决定和候选来源均保留。run续跑不重复已成功查询；脚本不做记录内自动重试，任一页失败即结束该次检索，重试只能显式search新建记录。预算按query记录条数计，一条查询的多页只算一次；失败、partial与导入同样各算一条。

## 机械裁剪 screen-v1

裁剪全部在脚本内完成，不调用模型，可复现可解释。输入是该方向已发现的全部候选。

硬过滤（直接进被判出文件，原因记录为archived、fork或no_description）：归档仓库、fork、既无描述也无topics。

同描述去重：不区分owner，描述词集合（NFKC后长度不小于3的非停用词）Jaccard相似度不低于0.8视为同一产品，只保留分数最高的一条进入送审集，其余记duplicate_description进被判出文件，不占送审额度。分叉、镜像与拆分仓常共享同一描述，因此不按owner限定；描述差异明显的不同产品正常并存。

其余候选打分，满分100：

| 分量 | 取值 |
|---|---|
| 查询词命中 | 40 × min(命中词数 / max(1, min(词表大小,10)), 1)，词表为空记0 |
| 多查询重复命中 | min(origins条数, 3) × 6 |
| 最近提交 | 以state的scoring_at为固定基准：90天内15，1年内8，2年内3，更早或缺失0 |
| Star | min(12, log10(stars+1) × 3) |
| 许可证 | 有且非NOASSERTION得5 |
| topics交集 | 有topics且命中词得5 |

词表来自该方向全部查询的英文token（≥3字符、去停用词）和中文2-gram。排序按分数降序、Star降序、候选id升序，取前screen条生成卡片。

产物：

- `cards/<方向>.md`：送审卡片，表格形式，含分数、Star、语言、最近提交、许可证、描述（截200字符）、命中来源。
- `cards/<方向>.json`：同内容的机器可读版，附规则名、k、总数、生成时间。
- `cards/<方向>.deferred.json`：未送审候选，含分数、理由和filtered_by（below_cut或硬过滤原因）。候选一条都不删除。
- `cards/<方向>.adjacent.json`：紧邻 cut 的 adjacent 条候补，只有 id、分数和截断描述，同样出现在卡片末尾。不要求初筛理由，不计入追责。

裁剪只决定"哪些先看"，不构成相关性判断。长尾冷门项目天然排在后面，需要时从被判出文件抽查并追加search换角度。

## 上下文口径

模型只读 `cards/<方向>.md`。report.md只列送审卡片与统计，未送审候选只报数量与文件路径，全量候选留在state.json。audit只对送审集追责初筛理由，未送审不要求逐条给理由。

## 上游基底 upstream-v1

读该仓库的公开清单文件，按存在顺序尝试package.json、pyproject.toml、requirements.txt、go.mod、Cargo.toml、pom.xml，提取直接依赖名，去重后取前max_upstream个查询deps.dev v3（免费、无密钥）：先取默认版本，再取该版本的许可证、OSV编号、弃用标记；PyPI包额外带projectStatus。每次upstream调用与inspect共用max_inspections预算，预算耗尽即拒绝，不发起外部请求。

限制必须如实说明：解析是正则级粗解析，不解析workspace、extras、optional依赖、Maven属性继承与父POM；查询的是默认版本，不是项目实际锁定版本；deps.dev公开接口没有反向依赖计数，不能声称统计了"被谁依赖"。单包失败记错误继续，不中止整次查询。结果写入 `upstream/`，state只留摘要。

## 导入协议

```json
{
  "provider": "实际使用的搜索工具名称",
  "results": [
    {"url": "https://github.com/example/project", "title": "项目标题", "description": "原始结果描述"}
  ]
}
```

通过任意工具得到的材料应保存原始结果，再整理成此格式；不要制造搜索记录。脚本保存导入字节、时间和hash，但不能证明指定工具真的执行过查询。导入覆盖单独计数，报告不称其为原生执行。

## 候选与状态

候选id：GitHub仓库为小写owner/repo，去.git；普通URL保留query、去fragment。相同候选保留多个发现来源。README外链合并到同一候选池。review的三类决定为shortlist（待深审）、exclude（初筛排除）、defer（暂未处理），必须有理由。

audit检查：每方向至少两次不同的有效查询、全部初始计划查询均有成功记录、至少两份不同候选README成功读取、送审候选均有理由且无defer/待审、无未解决失败/running记录、证据完整性（含每页快照）与预算。追责只在送审集内；未送审与硬过滤候选不阻塞通过，只需在报告给出数量、规则与可追查路径。GitHub返回incomplete_results记为partial，显式同查询完整重试可解决。没有候选的方向会标缺口。截断显示为覆盖率，表示有限覆盖，不能以增加limit替代其他方向查询。报告包含生成时的state.json哈希；变更研究后需重新audit。

输出状态：

- needs_attention：有机械缺项、损坏、失败、送审候选未筛或参考集合漏项/污染。
- process_checked：上述机械项满足，只表示记录充分；不能证明候选全集或语义判断正确。
- documented_with_imports：机械项满足且使用了导入；必须同时说明哪些方向只有外部提供记录。

参考集合是JSON字符串数组，例如["owner/project"]，只交给audit。检查查询是否包含全名或项目名，若出现则标contaminated。它不是盲测证明。

退出码：正常命令0；audit有缺项2；输入/运行错误1。CLI只读公开远端API与deps.dev，写入研究目录。哈希检测意外变化，不能防伪。正在运行的记录不能被audit当成功；强杀导致遗留锁时，需核对进程后处理，脚本不自动清理未知文件。
