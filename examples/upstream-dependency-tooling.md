# 依赖与上游基底工具调研

2026-11-17。用 research-coverage v2 的两个 run（`output/exp-v2`、`output/exp-angle`）产出的候选池整理。结论只覆盖"依赖分析、许可证合规、依赖图与调用图"三条线，不含产品级验收。

## 结论

1. 做依赖与漏洞事实采集，基底应是 deps.dev（在线传递依赖）加 osv-scalibr（本地扫描），两者都不需要常驻服务，许可证干净，生态同源。
2. 做许可证门禁，成熟组合是 syft 出 SBOM、grant 或 provenant 做策略判定；fossology 与 ScanCode.io 适合合规体系，不适合嵌进流水线。
3. 给自己 agent 提供代码结构，infigraph 与 callix 是当前最合适的两块底座，前者 62 语言、本地无 LLM，后者用统一图 IR 抹平语言差异。MCP 形态的 srclight、socraticode 拿来即用，但把检索策略锁死在它们的实现里。

## 方法

两个 run 用同一组方向、不同词面：

| run | 查询 | 抓取 | 候选 | 送审 | 备注 |
|---|---|---:|---:|---:|---|
| exp-v2 | dependency analysis tool / software composition analysis / sbom generator / license compliance scanner / code dependency graph / call graph analysis | 349 | 340 | 30 | 完整闭环，31 条初筛理由 |
| exp-angle | cyclonedx library / osv vulnerability matcher / spdx document generator / license detection algorithm / code property graph / pointer analysis call graph | 190 | 189 | 30 | 换词面补漏，未做完整初筛 |

两轮候选只有 1 个重叠，说明词面决定池子。覆盖率从 4.6% 到 100% 不等，抓的是 best-match 前 60 条，不是全集。

参数：limit 30、pages 2、screen 10；exp-v2 未开 adjacent，exp-angle 开 adjacent 8。裁剪后 340 个候选的描述文本 99096 字符，exp-v2 卡片 7461 字符，加 adjacent 的副本为 10134 字符，约 10%。

局限：初筛依据是仓库描述与元数据，只有 osv-scalibr 与 it-depends 两份 README 做过实际阅读。没有编译、没有跑基准、没有验证许可证真伪。

## 推荐组合

按用途拆，不要试图找一个全能工具。

| 用途 | 主选 | 备选 | 形态 | 主要代价 |
|---|---|---|---|---|
| 依赖与漏洞事实 | deps.dev + google/osv-scalibr | anchore/syft | HTTP API + Go 库 / CLI | deps.dev 无 SLA，osv-scalibr 需要自己接数据源 |
| 许可证门禁 | anchore/grant | getprovenant/provenant、samsung/lpvs | CLI / Rust 库 / 服务 | grant 只做判定，取证要配 syft；lpvs 绑定 Gerrit 式提交流程 |
| 合规体系 | fossology | aboutcode-org/scancode.io | 系统 / 服务 | 重，要数据库与运维；不适合嵌进 CI |
| 代码结构底座 | intuit/infigraph | callix-tools/callix | Rust 库 / Rust 图 IR | infigraph 许可证标 NOASSERTION，采用前必须核验 |
| agent 直接可用 | srclight/srclight | giancarloerra/socraticode | MCP 服务 | 检索策略被封装，换实现成本高 |

it-depends 值得单独提：它把依赖图与 SBOM 合在一条管线里，`upstream` 实测显示它自己依赖 cyclonedx-python-lib、networkx、johnnydep，说明解析与图计算是分离的，可以直接借它的解析层而不用整体部署。

## 分方向清单

### 依赖分析与软件成分

| 候选 | 形态 | 判断 | 注意 |
|---|---|---|---|
| google/osv-scalibr | Go 库 | 首选。与 OSV 数据同源，Google 维护，可嵌入扫描器 | 需要自己接 OSV 数据与调度 |
| aboutcode-org/scancode.io | Python 服务 | 管线化 SCA，输出组件与许可证，可当事实层 | 要部署服务，偏重 |
| llnl/surfactant | Python 框架 | 构建期采集，模块化，适合打进 CI | 文档偏少，生态小 |
| dependency-check/dependencycheck | Java | 漏洞检测基线，NVD 数据成熟 | Java 生态绑定，误报需要调 |
| optave/ops-codegraph-tool | TS CLI | 函数级依赖图加影响分析，覆盖 34 语言 | 商业化倾向，版本策略要看清 |
| intuit/infigraph | Rust 引擎 | AST 图加语义检索，62 语言，无 LLM 依赖 | 许可证 NOASSERTION |
| anchore/syft | Go CLI/库 | 第二轮补入。容器与文件系统 SBOM 生成，事实标准 | 只出清单，不做判定 |

### 许可证与清单合规

| 候选 | 形态 | 判断 | 注意 |
|---|---|---|---|
| anchore/grant | Go CLI | 首选门禁。判定简单直接，输出可进策略 | 取证依赖 syft |
| getprovenant/provenant | Rust | 许可证、版权、SBOM 一次出，性能取向 | 新，生态和规则库还在长 |
| samsung/lpvs | Java 服务 | patch 级预检，把合规前移到提交 | 绑定 Gerrit 流程 |
| fossology | 系统 | 规则库最完整，适合做合规体系 | 运维成本高 |
| sktelecom/bomlens | Shell + 工具链 | 本地 SBOM 与风险报告，企业侧对照 | 文档以韩文场景为主 |
| trailofbits/it-depends | Python | 依赖图与 SBOM 合在一条管线 | 用 pip 解析，非标准构建会漏 |

### 依赖图与调用图

| 候选 | 形态 | 判断 | 注意 |
|---|---|---|---|
| callix-tools/callix | Rust 图 IR | 多语言统一 IR，免语言服务器，结构最干净 | 5 star，成熟度未知 |
| srclight/srclight | Python MCP | 代码索引加调用图，直接给 agent 用 | 封装程度高，扩展受限 |
| giancarloerra/socraticode | TS | 3.4k star，多语言依赖图加符号影响分析 | AGPL，商业使用要评估 |
| sidhant0707/codeautopsy | TS | 静态识别架构模式与技术栈，适合做上游摸底 | 偏报告生成，不做图存储 |
| supermodeltools/cli | Go | 调用图与依赖图 CLI，主打省 agent token | 同组织有 mcp/docs 两个重复仓 |
| joernio/joern | Scala/Java | 第二轮补入。CPG 领域事实标准，查询语言成熟 | 重，学习曲线陡 |
| fraunhofer-aisec/cpg | Kotlin 库 | 库化的 CPG 提取，适合嵌入 | 主要覆盖 C/C++/Java |
| lhcwn/pathologist | 未知 | 第二轮补入，C/C++ 调用图与指针分析 | 61 分长尾，描述之外的实现未核 |

## 排除的类型

| 类型 | 例子 | 理由 |
|---|---|---|
| 集成层 | dependency-check-gradle | 能力来自本体，自己不做分析 |
| 平台绑定 | masahiroid/sbom-vulnerability-scanner、azure-devops-license-sbom-scanner | 绑定编辑器或平台，不可复用 |
| 空壳新仓 | nplmshjtr/ai-sbom-gate、sanobarshaikhbsc2025-cyber/licenselens | 0 star，无使用痕迹，描述覆盖过广 |
| 停更 | richlamdev/dependabot-scraper | 2023 年后无提交 |
| 资料清单 | magnologan/awesome-sca | 只有列表，无实现 |
| 词面误命中 | 车牌识别、医学图像检测类项目 | license detection algorithm 的多义噪声 |

## 未验证与风险

- infigraph 许可证为 NOASSERTION，采用前必须核验授权，否则有合规风险。
- 除两份 README 外，其余候选没有读过源码，也没有跑过任何基准。判断依据是描述、语言、活跃度、许可证四项。
- 覆盖率不是召回率。dependency analysis tool 命中 1038 条只抓 60 条，长尾没有看。
- 第二轮（exp-angle）的 supply 方向被车牌项目污染，该方向的补充发现不可信，未纳入本清单。
- 没有评估任何商业 SaaS（vexa-sca 类），它们的描述无法验证。

## 复现

```text
python -X utf8 skill-workbench/research-coverage/scripts/coverage.py screen --run output/exp-v2/run
python -X utf8 skill-workbench/research-coverage/scripts/coverage.py audit --run output/exp-v2/run
```

初筛理由在 `output/exp-v2/reviews.json`，原始响应在 `output/exp-v2/run/snapshots/`。
