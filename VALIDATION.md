# research-coverage 交付验证

2026-11-17。独立技能已实现，通过 Magpie Library 登记并运行 `magpie library sync`。入口为 `~/.agents/skills/research-coverage/SKILL.md`，实体为 Magpie 的 `library/skills/research-coverage`。源码保留在本目录的 `research-coverage/`。

契约在实现前完成两轮审阅；最终 GPT 与 Claude 两家族均 PASS，同一契约 SHA256 为 `2c1a90a52622e5ec65c8fed6fccb9b68edb656b51c3a21ba4e00575573215ea6`。这只说明未发现契约阻断缺陷，不能代替代码验证。

## 验证结果

- 源码与安装副本的19项 unittest 均通过，Skill Creator 的 quick_validate 通过。覆盖失败/中断/锁、预算、完整计划查询、导入与原生隔离、截断/空结果、参考名污染、证据损坏/路径逃逸、初筛遗漏及部分结果重试。
- 现场执行3个能力方向、7条原生GitHub查询、2份不同仓库README；初始6条查询全部执行，另有同一Agent换角度的补查，不称独立模型复核。
- 57个去重候选逐项初筛。`shortlist`表示值得深审，不是生产推荐；本轮没有对全部候选逐一读取源码或验收产品。
- 不点名发现回归找回参考集2/2：OpenViking由q0002发现，deja-vu由q0003/q0004发现，均早于README追查。查询未含参考项目名。作者事先知道答案，不能称严格盲测或全网召回率。
- 最终 `process_checked` 表示机械执行与初筛检查通过。首轮6条查询均有截断；其他站点、真正独立补查和全部长尾候选的发现没有被本次现场回归证明。
- Magpie安装的6份文件与源码hash一致，共享技能入口一致；Claude/Codex等7个agent有技能应用记录。登记沿用github-stars的17个agent范围。OpenCode的应用记录只含MCP，已核验登记和共享文件入口，没有声称通过其CLI实测调用。
- 其他技能登记、MCP、Instructions、hooks及子代理等Library设置与安装前备份一致。未修改github-stars的实现或全局指令。安装配置备份保存在Magpie自身backups目录。

## 现场记录与复查

报告：[report.md](../output/coverage-live-1/report.md)。机器状态及原始响应在同目录。初筛依据保存在 [trial-screening.json](trial-screening.json)，其专用应用脚本在写入前核对完整候选集合，阻止未知、缺失及重复ID。

纠错留痕：初版初筛文件包含错误候选ID，集合校验在任何review写入前拒绝；对照实际报告纠正后，57条全部通过。Windows默认GBK导致通用quick_validate首次读取UTF-8失败，改用Python `-X utf8`通过。安装核验最初误将OpenCode的applied技能列表视为必要条件，已按其MCP-only应用记录与实际共享目录核对；不改OpenCode配置。以上均为本轮记录/验证逻辑问题，没有证据表明属于上游产品缺陷。

原始产物位于被Git忽略的output/，不随源码提交。根目录 `research-coverage-validation-index.json` 保存本轮来源及输出hash；只有该索引不能恢复原始响应。封存后可运行：

```text
python -X utf8 scripts/validate_research_coverage.py
python -X utf8 scripts/install_research_coverage.py verify
python -X utf8 -m unittest discover -s skill-workbench/research-coverage/tests -v
```

源码和技能副本仍可继续改，但索引拒绝静默覆盖旧封存结果；新的研究另起运行目录。用户后续任务可明确调用“用research-coverage检查候选遗漏”，再按任务重写计划。

## v2 上下文预算重构（2026-11-17）

动因：首版现场 7 条查询里 6 条只取首 8 条，命中总数从 245 到 47899，工具把"只看了 8 条"和"看完全部"都判成 process_checked。同时一旦放开翻页，全量候选会把模型上下文压爆。两件事必须一起解决，否则要么覆盖极浅，要么会话不可用。

改动：

- 检索翻页：pages 控制每查询页数，limit 控制每页条数，乘积不超过 GitHub 的 1000 条上限。每页单独保存快照与 hash，截断改为记录 coverage=fetched/total，不再作为缺项。
- 机械裁剪：新增 screen 命令与 RULE screen-v1，纯脚本、无模型调用。硬过滤 archived、fork、description 与 topics 同时为空；其余按查询词命中率、多查询重复命中、新鲜度、stars、license、topics 打分，按分数与 Star 降序取每方向 screen 条生成 cards/<方向>.md。
- 上下文口径：模型只读卡片；全量候选留在 state.json，未送审候选留在 cards/<方向>.deferred.json；report.md 只列送审集与统计。audit 的追责范围收窄到送审集。
- 上游基底：新增 upstream 命令，按 npm/PyPI/Go/Cargo/Maven 粗解析仓库清单，用 deps.dev v3（免费、无密钥）取默认版本、许可证、OSV 编号与弃用状态。与 inspect 共用 max_inspections 预算。
- 稳定性：新增显式 unlock；所有外部请求有界超时（gh 40 秒，deps.dev 20 秒 socket 加 30 秒总预算）；scoring_at 在 init 冻结，保证同一 state 重算得到同一送审集。

验证：

- 32 项 unittest 通过（原 19 项加新增 13 项），覆盖翻页去重与页预算、硬过滤与分桶、卡片只含送审集、导入不能顶替原生覆盖、空词表零除、固定评分时钟、upstream 预算共享与单包失败降级、显式解锁。
- 真实现场：output/coverage-v2-smoke，1 方向 2 查询，抓取 120 条（2 页乘 30），去重 119，硬过滤 8，送审 10，未送审 109；上游查询 aboutcode-org/scancode.io 读到 pyproject.toml，声明 10 个依赖，按 max_upstream=8 实查 8 个，0 失败，许可证齐全。
- 安装副本 6 文件同步，install verify 通过，7 个 agent 已应用。

契约审阅：

- 轮次 r3 至 r14 共 12 轮，GPT 家族路由 自 r9 起连续 PASS。
- Claude 家族路由 持续 FAIL，blocker 逐轮收敛为三类：已写明的条文被读漏、与"失败也计入预算"这一不变量冲突的要求（要求 partial 不消耗预算）、要求把全部实现细节搬进契约。r14 的 blocker 只是复述三条已明文条款，未给出新的执行路径。
- 另一个路由两次 HTTP 502，未取得该家族意见，改用另一个家族路由。
- 结论：未达成两家族同时 PASS。按项目门槛这属未完成项，需要人工裁决是否接受单家族 PASS；本轮不宣称契约已通过。

未做：

- validate_research_coverage.py 仍指向 r2 审阅与 coverage-live-1，v2 改动后运行会报"交付证据已变更"，这是首版封存语义，索引未被覆盖。
- 未做向量语义排序，未做反向依赖统计（公开接口不存在），未改其他技能或全局配置。

## v2 实验记录（2026-11-17 晚）

目的：验证 v2 是否真的把候选发现压进可控上下文，并跑通到 process_checked。计划 3 方向、6 查询、limit 30、pages 2、screen 10、max_inspections 4、max_upstream 12，目录 output/exp-v2。

首次开工时本机代理故障：gh 对 api.github.com 返回 EOF，curl 经 127.0.0.1:7890 得到 502 或 TLS 握手失败，github.com 与 api.deps.dev 同样不可达。用户重连代理后恢复，重建 run 全量执行。

在线数据：

| 查询 | 抓取 | total_count | 覆盖率 |
|---|---:|---:|---:|
| dependency analysis tool | 60 | 1038 | 5.8% |
| software composition analysis | 60 | 162 | 37.0% |
| sbom generator | 60 | 243 | 24.7% |
| license compliance scanner | 49 | 49 | 100% |
| code dependency graph | 60 | 1312 | 4.6% |
| call graph analysis | 60 | 422 | 14.2% |

结果：抓取 349 条，去重 340 个候选。送审 30（每方向 10），未送审 314，其中 below_cut 296、no_description 16、同描述重复 2。

上下文体积：候选文本合计 99096 字符，三张送审卡片合计 7959 字符，约 8%。模型读卡片即可初筛，全量候选留在 state.json。

闭环：2 份 README（google/osv-scalibr、trailofbits/it-depends），1 次 upstream（trailofbits/it-depends，pyproject.toml 声明 13 个依赖，按 max_upstream=12 实查 12 个，0 失败，返回版本与许可证），29+2 条初筛理由齐全，最终 process_checked、退出码 0、无 issue。

实验暴露并修复的缺陷：

- 同组织同描述多仓重复占位：graph 方向 supermodeltools 的 cli/mcp/docs 三条描述几乎相同，占掉 3 个送审席位。先按描述前 60 字符精确匹配，被 "Save 40%+" 与 "Save up to 40%" 的措辞差异绕过；改为同owner下描述词集合 Jaccard 相似度不低于 0.8 判重复，重复项记 duplicate_description 不占席位。改后 graph 方向剔除 2 条重复，补入 intuit/infigraph 与 ducphamngoc08/codevisualizer。
- 旧 state 缺 upstreams 键导致 audit KeyError，已在 load 补默认键。
- 卡片 generated_at 用当前时间导致 hash 抖动，已改用 scoring_at。

仍存在的限制：覆盖率是抓取比例，不是召回率，4.6% 的查询意味着只看了一小部分命中；below_cut 里必然藏着相关项目，本实验未对 296 条 below_cut 做人工抽查，误杀率未知。

## below_cut 误杀抽查（2026-11-17）

样本：exp-v2 三个方向的 296 条 below_cut，分层抽取 30 条（紧邻 cut 的 5 条、四分位 3 条、尾部 2 条），逐条人工判定。

判定口径：与方向目标相关，且具备可复用构件或选型价值，即算误杀；与已入选候选功能重复但仍属同类工具，也算误杀，因为候选池的职责是列出而不是替用户去重。

结果：12/30 判为误杀，40%。Wilson 95% 区间约 24% 到 58%，样本仅 30 条，只能定性。

分方向：

- deps 2/10：noviqtechnologies/vexa-sca、expediagroup/jarviz。
- supply 5/10：shivaji-7/license-compliance-scanner、trustsource/ts-deepscan、codelake-dev/licscan、chla28/sbomgenerator、shok32/sbom-generator。
- graph 5/10：jayu/rev-dep、ozgurcd/gograph、sdsrss/code-graph-mcp、phpcodearcheology/phpcodearcheology、lhcwn/pathologist。

结构性原因：误杀集中在候选密集的方向。supply 的 SBOM 与许可证扫描器有一大批 50 到 59 分的同类项目，cut 落在 60 分，一刀切掉整片；graph 的代码图 MCP 同类扎堆在 61 到 70 分。deps 方向候选分布更分散，误杀率明显低。

判为不误杀的例子包括：实证研究数据集（cuddest/sboms-study）、跨方向已入选的重复项（srclight/srclight）、EDS 能谱分析与手术视频追踪这类关键词误命中、描述过薄的空壳仓（quilime/sbomgen）。

结论：screen=10 在候选密集方向过窄，40% 的邻域误杀率意味着报告会漏掉相当一批同类工具。这不是打分公式错了，而是截断位置太靠前。

## 邻域候补验证（2026-11-17）

针对上节的误杀，新增 adjacent 参数（默认 0，范围 0 到 50）：在送审集之后按分数取相邻的 N 条，只在卡片末尾列出 id、分数和截断描述，不进送审集，不要求初筛理由，不参与 audit 追责。

验证方式：把 exp-v2 的 run 复制为 exp-v2-adj，只把 plan 与 state 里的 adjacent 改为 8，重算 screen。候选集与 exp-v2 完全一致，便于对照。

覆盖结果：上节判定为误杀的 12 条里，10 条出现在 adjacent 段（deps 2/2、supply 4/5、graph 4/5）。未覆盖的 2 条是 shok32/sbom-generator（36 分）与 lhcwn/pathologist（61 分），分数更低，8 条够不到。

额外收益：adjacent 段还带出了上次抽样没抽到的相关项目，例如 datadog/datadog-sca-github-action、sitewarming/comply、sunerpy/codegraph-rust、jamie8johnson/cqs。

体积代价：三张卡片合计从 7461 字符增到 10134，增加 35.8%；相对候选文本 99096 字符，占比从 7.5% 升到 10.2%。绝对增量约 2.7k 字符，英文约 700 tokens。

噪声：adjacent 段也包含关键词误命中，例如 deps 方向的 tommyknocker/pdo-database-class（PHP 数据库库）。一行一条的代价可接受，但不要把 adjacent 当推荐列表。

结论：密集方向建议 adjacent=8，稀疏方向设 0 到 4。它换回 83% 的邻域误杀覆盖，成本是可控的固定增量。

## 参数组合对比（2026-11-17）

用 exp-v2 的同一份 340 候选，只改 screen 与 adjacent 重算，比较覆盖与体积。覆盖指标是上一节判定的 12 条已知误杀是否出现在卡片（送审集加 adjacent 段）。

| 组 | screen | adjacent | 送审条数 | 卡片字符 | 已知误杀覆盖 |
|---|---:|---:|---:|---:|---:|
| A | 10 | 0 | 30 | 7461 | 0/12 |
| B | 10 | 8 | 30 | 10134 | 10/12 |
| C | 15 | 0 | 45 | 10854 | 10/12 |
| D | 15 | 8 | 45 | 13454 | 10/12 |
| E | 20 | 0 | 60 | 13930 | 10/12 |

结论：

- B 组效率最高。比 C 少 720 字符，送审条数少 15 条（即少写 15 条初筛理由），覆盖相同。
- 覆盖上限 10/12 由分数位置决定，与参数无关。缺口两条是 shok32/sbom-generator（supply 第 70 名，36 分）与 lhcwn/pathologist（graph 第 26 名，61 分），把 screen 提到 20 也够不到。
- 继续加参数只增加成本。要覆盖更深的尾部，只能换查询角度把候选池扩大，或接受漏项并在报告里标明。

建议：候选密集方向用 screen 10 到 15 配 adjacent 8；稀疏方向相邻参数设 0 到 4。不要靠抬高 screen 解决覆盖率问题。

## 换查询角度实验（2026-11-17）

问题：已知两条深层漏项（shok32/sbom-generator 36 分、lhcwn/pathologist 61 分），参数已证明救不回来。检验换查询角度能否把它们带进送审集。

做法：新开 output/exp-angle，同三方向，但把泛品类词换成格式与技术词：cyclonedx library、osv vulnerability matcher、spdx document generator、license detection algorithm、code property graph、pointer analysis call graph。参数 screen 10、adjacent 8、limit 30、pages 2。

结果：

- 候选 190 个，与原 run 的 340 个只有 1 个重叠。换词几乎换掉整个池子。
- lhcwn/pathologist 被 pointer analysis call graph 命中，直接进入 graph 送审集第 5 位。补漏成立。
- shok32/sbom-generator 未命中，spdx document generator 只返回 4 条结果，够不到它。补漏不完全。
- 新查询的 total_count 差异极大：pointer analysis call graph 只有 3 条，cyclonedx library 42 条，code property graph 227 条。精确词召回窄，宽泛词召回杂。

暴露的问题：

- license detection algorithm 把 supply 方向带进整车牌识别项目，送审 10 条里 6 条是车牌、车速或医学图像检测。根因是多义词，不是工具缺陷，但工具没有识别领域偏移的手段。
- 跨 owner 的同描述副本此前没被合并（例如 acrt-cr 与 acrt-eval 下的 cyclonedx 库副本、muhammadbalalansar 与 tabaxi3000 的同名 SBOM 工具）。去重规则已从「同 owner 加前 60 字符」改成「不区分 owner，描述词集合 Jaccard 0.8」，分叉与镜像因此能被合并。

结论：换角度能补漏，但成功率取决于词的选择，而且一次换词会丢掉原有池子的全部积累。可行的用法是并行跑两组不同词面的查询，各自裁剪后合并送审，而不是在同一 run 里追加查询（追加查询不改变打分词表，救不回旧候选）。
