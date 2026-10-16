# 随机种族工具实战续测

目标：让两方 OpenCode Go `opencode-go/deepseek-v4.1-flash` 持续实战，以原始观测、
模型指令、执行反馈和原生效果检查操作工具；发现有证据的缺陷先复现、修复、验收再续局。
战术选择归模型。有限对局不能证明所有游戏操作都没有缺陷，需要另外记录尚未覆盖的接口。

## 已准备的运行方式

```sh
./run_sc2.sh .venv/bin/python go_duel_series.py --random-races --games 4 --seed 20261014
./run_sc2.sh .venv/bin/python go_duel_series.py --resume logs/go-duels/<本轮目录>
```

每次调用只打一局，局后检查完再resume，便于加载修复并保存每局精确源码。
种族为双方每局独立抽取，允许同族；赛程保存到series.json，resume不重抽。
按P1/P2各自的种族分别保留复盘策略，同族局不共享另一方复盘。
保持Slower realtime、正常迷雾、独立会话、每方24战斗请求及1800游戏秒/1860墙钟秒。

种子20261014的新四局抽签为：PvP、TvP、TvZ、PvP。这是待运行赛程，不是已完成比赛。
随机选择可能没有覆盖某些组合；实际覆盖矩阵按完成比赛记录，不能把随机当成全覆盖。

101项随机赛程、Go传输、超时恢复、正常任务执行、迟到计划及预算保护检查通过：
`logs/random-tools-preflight-regressions.log`。未为这些检查发送模型请求。

## 额度口径与运行授权

用户最新明确“不管余额了，继续对战，把已知问题都修复下”，已取消原Codex最后10%停止条件。
Go额度充足；继续按有限赛程运行并审计，不再等待余额答复。
项目request budget是本地尝试上限，不能替代服务商剩余额度，也不能清零旧计数。
当前Go公开说明将额度定义为金额，并同时设置5小时、周、月窗口；具体窗口和当前用量
必须核实，不能仅从token数或成功回复次数推断余额。

官方成员预算只读查询成功，但返回limit=null、spent=0，含义是成员月度预算无上限；
它不是Go订阅剩余100%的证据，不用这个结果启动无限消耗。
不将成员月度预算冒充Go套餐余额；Go实战继续按原每局上限及持久账本计数，
复盘和失败尝试也照常纳入记录。

官方参考：
- https://opencode.ai/docs/go/
- https://opencode.ai/v2/docs/console/api/budgets/

## 开始本轮时的工具覆盖缺口

`arena/technology.py`已说明多项主动能力依赖经过验收的Python cast技能。
开始本轮时Go duel runner没有加载这些技能，不能把研究完成或攻击移动当成真正施法。
尚需按种族验收的例子：神族闪烁/风暴/力场/守护者之盾/时空加速/运输装卸，
虫族胆汁/菌毯/治疗/真菌/神经寄生/飞蛇技能，以及人族EMP/渡鸦/隐形/大和炮/运输。
这些是能力覆盖缺口，不是已经证明某个原生动作失败。

执行证据分开记录：模型发出意图、整批校验、原生命令发送、引擎接受和实际效果。
区分真实工具故障、缺少接口、错误参数、资源/前置不足和模型战术选择。
补能力时必须公开可用性、成本、冷却、目标及失败原因；不能自动替模型施法或选战略。

## 本轮实现：直接原生能力接口

新模块`arena/native_abilities.py`提供`use_ability`：明确指定己方caster tag、原生ability name、
目标null/tag/[x,y]及1..120游戏秒期限。模型请求时查询己方当前原生能力，给出施法者位置、
能量、buff、装载乘客、任务分配及可用能力；装载后才出现的卸载命令也按实时查询发布，
不依赖不完整的静态技能列表。查询失败时标记unknown，不伪造ready，不因报表查询失败停止整局。

每caster每轮只接受一条能力指令；相同request_id重送不重复施放。只对仍由己方拥有的
施法者和当前观察到的单位目标发单；使用实际引擎能力查询复核资源、能量、冷却与科技。
分配给其他任务的单位需要模型先释放；Nexus时空加速可与其生产并存。迟到能力计划需新观测。
发单后不自动重施，观察原生命令/能量/buff/装载/形态/可用性变化以报告acknowledgement，
不把acknowledgement冒充完整战术效果。原生动作拒绝、失去施法者和确认超时显式反馈。

购买、研究继续使用现有宏。**原生建造/生产队列取消不由该单次施法接口暴露**：必须同时结束原宏、
处理原生取消及观察退款/产品状态，不能把它当普通施法让宏随后重新购买。
既有cancel_task/cancel_expansion仍只撤销未发单宏及其支持的控兵任务。

256项相关回归曾通过；随后报表中立目标范围和查询失败标记的改动单独补验。
`verify_native_abilities_live.py`准备了零模型调用的原生效果检查，明确标记debug布场，
依次验证闪烁位移、守护者之盾能量和buff、时空加速能量与目标buff、运输装卸真实乘客状态。
此处不将未运行的原生检查算作通过；完成结果会追加实际artifact路径。

第一局`logs/go-duels/random-qs_5mo05/match-01`在接口开发前已经启动，使用其原始源码快照；
新接口不归因于这局结果。P1 request14的ReadTimeout后request15成功返回，实际验证了退避恢复。

## 已完成实战与验收

随机本轮`logs/go-duels/random-qs_5mo05`：

- 第一局PvP：647.7游戏秒、1539.27墙钟秒，P1 Victory/P2 Defeat，
  `engine_player_result`、`model_surrender`，实际返回模型均DeepSeek V4.1 Flash。
  P1请求14、P2请求22超时均恢复；本局之后复盘共计47/200。
  原报告动作错误0仅覆盖旧观察错误通道，不能证明全部动作被接受。
- 第二局TvP：402游戏秒，`context_budget`、双方Unknown。本轮累计76/200，
  P1战斗37、P2战斗35、各2次复盘；P1最后一次超限输入未发送，不计API尝试。
  能力报表导致完整输入超出45,000上限，是宿主缺陷。模型复盘把它主要归于战略不准确。

两局原始结果、源码快照、回放、账本不重写。`inspect_go_duel.py`生成的`tool-audit.json`
是衍生观察审计，原始请求结果优先于模型复盘。

### 原生能力与即时动作响应

`logs/native-abilities-random-tools-clean-response-capture/result.json`五项实际效果全部通过，
每项只发一次，当前两通道动作拒绝0。此前`...-response-capture`也观察到五项效果，但夹具
尝试在已自动转成折跃门的建筑上训练，新增反馈揭示1次NotSupported；原记录保留。
旧夹具passed只检查五项效果，不代表这次额外训练有效。已改为实际观察到的Nexus训练来验时空加速。

`logs/action-response-random-tools-01/result.json`原生负对照：故意让Nexus训练SCV，
即时响应NotSupported，观察错误数组为空。新适配器准确关联原指令并记录一次拒绝。
这是旧Linux SC2Env漏收即时动作响应的直接证据；非对局缺陷或模型调用。

PvP回放P2正常迷雾证据：`match-01/p2-warp-terrain-evidence.json`。
撤退坐标[170,170]距最近静态可行走地面34.30格，不能归因于工具拒绝一个合法地面目标；
新报表增加粗略静态可行走格，最终动态路线仍由原生查询决定。
551秒的追猎折跃在回放中未见对应动作或产出，旧即时响应未保存，不能断言确切拒绝原因。

### 建造取消

新增`cancel_native_building args={task_id:己方建造宏ID}`：停止原宏并取消其关联的未完成
工人建造建筑，或停止已观察到的建造者赶路。已完成建筑明确no-op，不摧毁、不假装退款。
混合非法计划保持原子性；单次原生命令，不自动重发，失败后宏也不重新购买。
先请求停止建造者但建筑抢先出现时，报告需要新取消决策，不误报完成。
原生队列、科研、基地升级取消尚未由该接口覆盖。

三族零模型调用原生验收全部通过：
- `logs/native-building-cancel-random-tools-protoss/result.json`水晶塔取消，扣除采矿后的实际退款75矿。
- `logs/native-building-cancel-random-tools-terran/result.json`补给站取消，实际退款75矿。
- `logs/native-building-cancel-random-tools-zerg/result.json`孵化场取消，实际退款225矿，工蜂恢复12个。

以上每例只发一次取消、原宏停止、当前两通道拒绝0；明确给资源，没有fast build。
生产确认另排除幻象，不把未发单时出现的单位冒充购买；Gateway购买识别自动变成WarpGate的产出，
但反向形态变换仍须真正成为Gateway。动作被控制权阻止时不再记录购买或占用资金。

### 45,000输入边界修复

用TvP真实失败上下文离线复现，原生actor/target表按共同值分组，浮点坐标按精确二进制
分母缩放为整数，再使用base36字段名索引。所有坐标、类型、顺序、当前事实和反馈可完全还原，
没有提高上限。复现完整输入44,739字符，原生报表及当前反馈逐项相等；见
`match-02/context-repair-evidence.json`。复现用最后记录的策略/策略政策及当前世界时间，非新实战。
真实失败样本作为压缩测试夹具保存在`testdata/duel-native-context.json.gz`。

当前320项相关回归通过：`logs/random-tools-complete-regressions.log`。
覆盖结构取消、三族生产、完整反馈、时限、所有输入编码的组合还原及真实超限案例。
普通原生Gateway购买验收：`logs/gateway-alias-random-tools-01/result.json`实际新建1座传送门、
已有折跃门不计入产出、仅一次购买、拒绝0。该次在Gateway完成后即确认，未观察到随后转换；
跳过Gateway完成瞬间而只收到WarpGate的确认场景由离线测试覆盖，不把这次原生结果冒充该场景。

第三局TvZ在347游戏秒仍因context_budget终止，双方Unknown，累计106/200（战斗P1=49、
P2=51，复盘各3）。虫族真实样本补为`testdata/duel-zerg-native-context.json.gz`。
共同值组的行索引进一步改为可选平铺索引，两个实际失败样本分别为44,339和44,812完整字符，
原生能力报表、当前反馈、世界数值逐项还原相等，上限仍为45,000。

另外修正编码后决策时钟：object_pool/key_table封装没有顶层world，原代码从封装读取时间
得到None，使自适应请求间隔失效。现在使用解码后的原始world.game_s，编码与请求节奏独立；
两份真实上下文回归检查保存时间及下一秒不重复请求。第三局原始尝试次数不回退。
第四局PvP沿用106/200，开始后加载上述两项修复；第三局结果不据此更改。

### 第四局的新证据与下一批

第四局在381.5游戏秒因P2的`context_budget`中断，双方Unknown；复盘后本批累计
135/200（战斗P1=63、P2=64，复盘各4）。保存原始源码、回放、请求和计数。
`match-04/tool-audit.json`记录90次即时原生攻击拒绝，全部来自P1的`defend_base`。
`match-04/p1-incomplete-defense-evidence.json`按本方正常迷雾读取回放，确认两个被控单位
尚在折跃，进度0.0125至0.925。防守选择遗漏了完成状态检查，现排除未完成及幻象单位，
完成后原防守任务可继续接管；原始失败测试见`logs/random-tools-match04-before.log`。

完整输入的复现保留原生游戏时钟精度；截为一位小数会掩盖这个临界失败。
新增可选`review_integer_pool_v1`共享格式：负整数引用池中对象，原有负整数显式转义，
池条目本身为字面值、仅递归解码子值。旧格式继续支持。当前事实不裁剪，45,000上限保持。
真实神族交战夹具`testdata/duel-protoss-native-context.json.gz`修复后完整输入43,854字符，
world、feedback、native_abilities还原相等，见`match-04/context-repair-evidence.json`。
负整数、保留字段字面字典、循环/越界引用和各编码组合均有检查。
相关324项回归通过：`logs/random-tools-match04-complete-regressions.log`。

用户已明确10%停止线指Codex额度、Go额度充足；本环境不能读取Codex剩余额度，
不能用Go预算或本地请求数冒充。下一批`logs/go-duels/random-i9702q2l`种子20261015，
随机抽取TvT、ZvZ、TvT、TvP，各局仍双方Slower、正常迷雾，每次启动一局再审计。
新批账本同时校验此前随机批135/200账本哈希，旧计数不清零。

### 新批第一局TvT及后续修复

`random-i9702q2l/match-01`在507.3游戏秒因P2 `context_budget`中断，双方Unknown，
复盘后新批37/200（P1战斗18、P2战斗17、复盘各1）。没有重置上一批135/200。
`tool-audit.json`保存两个原生拒绝及一次名称格式拒绝；复盘称无工具缺陷不能替代原始证据。

- `p2-tank-queue-replay.json`正常迷雾核验7070至7300循环：工厂坦克训练从99.6%到100%，
  之后持续保留591订单，区间内未见新坦克。`CantFindPlacementLocation`时已确认的订单
  不等于未购买成功。宏现在保留生产数量和队列，报告`blocked_outputs`，不自动增购/搬家。
  队列后来消失且仍无对应产出时，8游戏秒观察宽限后明确失败；原900游戏秒宏时限保留。
  产出后才确认完成。该执行器修复在第二局启动后完成，只归因于后续加载它的对局。
- 未确认的SCV补给站建造返回`CouldntReachTarget`，原来直接失败；现在纳入已有最多5次
  重试，未计购买数量、清除未确认保留并避开原位置。不是退款或产出确认。
- 模型明确选出的`Marine`/`SiegeTank`现在唯一匹配本族目录`marine`/`siege_tank`，
  数量、queue_mode、目标和原因原样保留。未知、别族或歧义名称仍拒绝。
  同样支持侦察/移动选择器和macro_plan豁免产品的大小写/下划线差异。
  第二局运行时又出现`Overlord`选择器和豁免名称拒绝；这部分兼容补修只在后续新局加载。
  非法`build_supply count=8`仍明确拒绝，未替模型改选工具或产量。
- 共享编码进一步复用重复原生标签和短名称，只选择有净节省的条目，保持原值/类型；
  四个真实失败上下文仍可还原。TvT真实夹具
  `testdata/duel-terran-mirror-context.json.gz`完整输入42,797字符，不提高45,000上限。
  密集交战加满1800字符上限的用户建议批次也可完整送入并还原，不私自删建议或当前反馈。

第二局P1请求5记录JSONDecodeError、随后成功恢复。旧日志缺少解析阶段，不能追溯归因于
API外层或模型公开回答。新Go worker区分`api_envelope`与`public_answer`，前者临时接口
错误、后者临时输出错误；后续新观测请求仍逐次计数和退避，没有隐藏重试或换模型。
错误不记录响应正文或隐藏思考，保留可用的已报告usage；错误模型身份立即停止，不回退。
worker每个结果单独记录加载时源码SHA-256，区分对局启动快照与后续子进程实际版本。

旧攻击备用执行路径也排除未完成单位，39项相关检查通过；主路径已有完成状态检查。
新增测试的修复前失败与修复后结果分别保存在`logs/random-tools-*-before.log`和对应after日志。

### Go额度充足后的输入保护与第三局结果

第二局ZvZ在524.8游戏秒仍因原45,000字符保护终止，双方Unknown，新批复盘后73/200。
原大规模样本`testdata/duel-large-zerg-context.json.gz`含100个能力actor和96个单位/中立目标。
继续堆叠压缩不能可靠适应当前观察增长；按用户明确Go额度充足的目标，将Go客户端完整战斗
输入保护改为90,000字符，其他客户端仍45,000。经济压缩继续按原软阈值工作，当前事实和用户
建议完整保留。此改动不代表查到了Codex剩余，不改变24次请求、1800游戏秒/1860墙钟秒。
该样本完整输入56,592字符，通过全部当前原生表、反馈、活动任务和高地地形逐项还原检查。

第三局TvT实际跑到墙钟时限：783.3游戏秒、1860.7墙钟秒，`time_budget`，双方Unknown，
拒绝0；复盘后123/200。双方各24次战斗尝试，P1两次ReadTimeout随后继续。
P1最高57,310字符、13次超过45,000；P2最高59,223字符、11次超过45,000。
这是真实Go接收更大输入的验证，不把P2优势局面当引擎胜利。原始请求和报告保留。

第三局另外发现数量写进args.count、任务reason放在外层，以及明确请求25单位被旧20上限拒绝。
Go适配现在仅把训练args.count移到外层（外层缺省/1或相同数量时；不同非默认数量仍拒绝），
把控兵任务的reason移到args并保留全部文字。训练/变形单批改为1..200，仍只有训练支持append，
活动总量<=200、每产品每轮一次、请求去重；不夹数量、不分拆模型决定、不自动循环暴兵。

### 原生生产/科研队列取消

`cancel_native_queue args={task_id:owned unit/research macro ID}`停止原宏，持有其关联生产建筑，
只对当前全部属于该购买/科研的已观察队列发取消。外来订单、超出关联购买数量或其他所有者
明确拒绝。每个producer每次发单后都等新观察的队列减少，最多30游戏秒；多个建筑可并行。
已完成产品/科研不撤销，运行时仅确认队列观察变化，不推断退款或取消因果。
尚不涵盖基地升级、虫卵/移动单位变异、弹药及工人建造（工人建造由另一接口支持）。

`logs/native-queue-cancel-random-tools-01/result.json`原生零模型调用验收通过：
SCV训练取消后未新增SCV，步兵攻击升级取消后未获得升级，两个原始宏均停止，各一单，拒绝0。
扣除真实采集分数变化后分别观察50矿和100矿100气返回；明确debug资源/建筑布场，无fast build。
这是控制测试，不计对局胜利，不改变任何模型预算账本。

### 远距离女王注卵期限

第二局ZvZ的P2任务37未发生已记录的原生拒绝：女王从[140.99,29.49]持续向
[73.5,138.5]的己方分矿接近，90秒截止时距离仍约8.9，能量未扣减。后续观察
已经接近到[78.13,133.05]。这属于原接口期限先于跨地图步行完成，不能称模型没有发令。
原始请求/反馈保持不变，解码证据另存`match-02/p2-injection-travel-audit.json`。

`inject_larva`现在允许模型显式提供`args.timeout_s`整数1..300，默认90。
总期限包含等待与移动；8秒失去订单/移动、20秒停止接近检查仍保留；单次施法不重发。
71项相关回归通过，包括持续移动跨过90秒后扣能量+buff确认，以及扩大期限但停滞仍失败。
`verify_queen_travel_live.py`准备了零模型调用远程注卵控制，尚未运行，不提前声称原生验证通过。
改动在第四局启动后完成，该局已加载的执行器不自动更新。

### 有限建造批次与原生建筑绑定

第四局TvP的人族请求10、12明确选择多个补给，却被原count=1接口整批拒绝。
`build_supply/build_structure/expand`现支持明确有限count1..200，普通建筑逐个确认建成才
购买下一个；重复活动目标仍coalesce，无append。王虫是单位生产，使用正常虫卵确认。
批次按原900秒总截止和真实资源/前置执行，不分拆模型选择、不自动循环补给。

同时修复Probe结束建造命令、Drone消耗后丢失未完成建筑绑定的问题：持续保留真实建筑tag和
位置供进度/取消，释放Probe；建筑死亡失败反馈，不将Drone消耗当未完成建筑无限等待。
51项批量/取消相关检查和371项完整回归通过；随后补充建筑死亡不重购检查，见
`logs/random-tools-building-lifecycle-regressions.log`。原生批次/延迟取消脚本已准备，尚未运行。
这些修改也在第四局启动后完成，原局拒绝记录保留，从下一局加载新执行器。

### 实战与零模型原生控制的后续验收

第二批第四局TvP跑到783.66游戏秒/1860.54墙钟秒，时间限制、双方Unknown、拒绝0。
模型公开JSON一次无效，P2三次ReadTimeout随后继续；末轮重复防守数量字段已作等值位置适配，
数量不乘、不夹，不相同的字段仍拒绝。原P2模型复盘的“仅一次超时/连续Gateway拒绝”不符合
原始记录，应以审计与原始请求为准。批次现172/200，P1复盘超原60000字符保护未发请求。
Go复盘保护现90000含系统提示，真实失败样本回归保留全部世界时间线；预览79438字符。
自动审批拒绝补充复盘诊断外发，用户授权问题待回复；未再次请求、未改原计数。

三族`building-batch-random-tools-{terran,protoss,zerg}-01`各恰好生成2个本族补给，原生拒绝0。
`native-building-cancel-random-tools-late-{protoss,zerg}-01`在开建后8游戏秒仍可取消绑定建筑，
原批量count2只购买1次，分别观测75/225矿返回，Zerg恢复工人；拒绝0。
`queen-travel-random-tools-01`女王单次注卵91.03游戏秒确认，发单1，拒绝0，无自动再施法。

第四局两辆未架起坦克停在基地。`p1-stalled-tanks-replay-03.json`己方视野回放显示其原生路径
为0，SCV对照127.79/130.98。新活动空间任务每3游戏秒报告逐单位路径及附近己方建筑，
查询缺失/异常为unavailable，不自动降补给、挪建筑、换目标或替换兵。
独立`blocked-tank-egress-random-tools-06`围堵控制验证：起初路径14/12，Raised Depot围堵后0；
显式降低6站，路径恢复后重发相同目标，两辆坦克实际到达，拒绝0。形态出现后还需0.85游戏秒
才观察路径恢复；前5个布场/确认时机失败记录保留，不将其当原对局根因或对局胜负。

第三批`random-e3u9pe2n`保存seed20261016的TvP/PvP/PvP/ZvP随机赛程，旧账本哈希保留。
待复盘外发授权期间使用`--skip-model-reviews`，只写本地诊断，未通过其他入口外发第四局证据。
第三批开始后又核查出同类建筑在不同基地、同类形态在不同任务互相计入完成数的问题。
新增回归先复现两座Pylon被一座的完成错误计数，再以实际发单位置/建筑tag/选中形态actor绑定
完成结果。Gateway自动转换仍沿购买位置关联新WarpGate，原有WarpGate保持基线排除。
修复从下一局加载，完整回归日志在`random-tools-bound-products-full-regressions.log`。

### 第二场神族镜像与建筑升级取消

第三批第二局PvP结束：783.8游戏秒，1860.59墙钟秒，time_budget、双方Unknown；91/200。
6次原生拒绝：1次Gateway在发单窗口开始1518转换而拒绝916训练，1次WarpGate位置拒绝
按既有限制重试恢复，4次单位在观察到发单之间确认死亡。后两类不冒充无拒绝或未知根因已定位。
Gateway特例只在未确认购买且当前确有Gateway转换/已成WarpGate证据时允许有限重选，
保持已接受的其他训练队列；无此证据的NotSupported仍失败。69项相关检查通过。
模型scout.targets单元素["expansion_sites"]等值适配为既有字符串路由，混合列表保持不改；
cancel_task遗漏rally_units也已修复，支持取消旧集结并接受原定增援/新目标，61项相关检查通过。

`cancel_native_morph`新增己方付费建筑升级取消，只针对关联任务的已观察订单/未完成新形态，
一次发单、最多30游戏秒、停止后续购买。未知/外来订单、缺失绑定、丢失所有权不推断成功。
保留已完成形态，运行时不推断退款或取消因果；普通建造、科研、移动变异/虫卵仍用各自接口。
`native-morph-cancel-random-tools-01`零模型原生控制已验Lair/Orbital：开升5秒后取消，
同建筑恢复原形、原count2批次只购买1次，取消发单1，拒绝0；各观测113矿返回，Lair另75气。
Hive/Planetary/GreaterSpire完成目录/边界检查，尚未逐项原生验证，不声称全部已实战验收。
本轮完整442项回归通过，日志`random-tools-morph-latest-complete-regressions.log`。
第三局PvP已加载上述全部修复继续，外部复盘授权仍待用户回复，skip-model-reviews保持true。

### 2026-10-15 接续：环境中断与定点折跃

第三批第三局旧进程随环境消失，最后世界采样700.5游戏秒，日志约711秒。
独立主机进程核查未见旧runner/游戏；原始match.json和series.json先备份，
另写environment-interruption.json，将该局标记interrupted/unverified，未伪造录像、胜负或清理成功。
预算139/200原字节哈希不变；每方本局各24次，历史账本哈希逐项核对通过。
现有反馈无plan_rejected、无原生拒绝；P1/P2各一次ReadTimeout后恢复，
P2哨兵死亡导致守护者之盾无法发出、基地/建造者死亡导致宏失败，均保留实际失败。

神族train_unit可显式warp_target:[x,y]，仅WarpGate，搜索限制3格，失败等待不去主矿。
不同目标分别保留购买和新单位身份，普通Gateway生产不能盗用另一任务折跃出的单位。
447项完整回归通过；新增模型接口说明后52项相关回归通过，旧客户端45k保护保持。
原生控制正在运行，结果以logs/warp-target-random-tools-01/result.json为准。

原生控制warp-target-random-tools-01已通过：目标[39.5,141.5]和[61.5,111.5]，
分别在[39,141]、[61,111]完成追猎者1个，单位tag不同，发单各1、拒绝0。
debug仅布场/前置资源，不启用快速建造，模型调用0，控制回放已保存。
第四局ZvP接续，保持原始赛程和预算，不补打中断局或重置计数。

定点折跃追加边界另有回归：同轮同产品用不同warp_target追加两次，旧代码未拒绝，
现按产品ID守护每轮一次限制，仍按目的地绑定各自目标/请求去重。
修复前失败证据random-tools-warp-append-before.log，修复后98项检查通过。
该补充在第四局启动后完成，第四局源码快照不含它，下一局才加载。

还复现了旧折跃被原生拒绝后，重下同点任务的出生确认被旧购买记录阻塞：
旧/新记录同时占有同一位置，新任务一直running。原拒绝证据保留，现给未确认且
明确收到对应原生拒绝的购买标记native_rejected，后续不得认领新单位；
已确认队列不据拒绝清空，已绑定实际单位不转移。修复前失败日志
random-tools-warp-rejected-binding-before.log；修复后106项生产/折跃/取消检查通过。
该补充同样在第四局启动后完成，下局加载，不修改当前源码快照。

升级取消控制新增--products选项，可选择hive/planetary_fortress/greater_spire补齐
原生覆盖；当前只通过脚本编译/CLI检查，尚待第四局结束后实际运行。

### 第四局真实拒绝：queue_mode字段层级

ZvP约295游戏秒，P1请求10想注卵、追加6蟑螂/4工人、补2王虫并侦察，
却把两个queue_mode:"append"写在任务外层，导致整批原子拒绝。
现仅对train_worker/train_unit将明确append/coalesce等值移至args；内外冲突、
未知模式和其他技能不改。原回答不重写，翻译记录沿用representation.json。
原始数量、产品、目标和reason保持，未自动加兵。修复前测试失败，
修复后107项适配/Go/训练/预算检查通过。当前进程已加载旧适配器，本局不热更，下局加载。

随机对局本地工具覆盖快照已保存logs/go-duels/random-tool-coverage-local.json，
12局记录包含当前运行和中断局，不把任务状态当完整战术效果证据，也不混入debug控制。
仅一次use_ability排入任务，施法者死亡未发出；取消队列/升级等尚无随机局排入证据，
因此需要已有/新增的独立原生控制补齐，不能宣称所有游戏操作已经实战覆盖。
另清除battle_plans旧通用提示中“禁止坐标/tag”与具体技能参数的矛盾，
改为只在明确接受该参数的字段使用，未改变执行权限或目标。

### 第四局中断与防守命令确认

环境转换后原runner875和两SC2进程均Z，日志停588.8游戏秒，最后采样575.4。
原始match/series保留；第四局记execution_environment_interrupted/unverified，
第三批累计179/200，预算原字节哈希未变。无引擎终局、未保存录像，不能宣称胜利/自然完赛。
P1真实拒绝22：3跳虫在发单窗口死亡；19女王在出生后短窗口仍活、无订单，
约一秒后移动，不能把未知原生原因写成已根治。
防守执行器逐帧重发已由测试复现；现待确认1游戏秒，明确拒绝1/2/4秒退避，
连续5次暂停该演员并在command_rejections报告，保留原目标/占有，其他单位继续。
83项防守检查通过，连同输出/Go检查155项通过；原生女王控制正在运行。

P1请求15结束原因为length，completion8000=reasoning8000；不存在可执行公开回答。
后续战斗输出上限16000（服务商同模型已有接受24000复盘的记录），不保证不会再截断；
保留公开回答校验、超时和请求次数，不隐式重试/执行不完整回答，不保存隐藏推理正文。

补充原生控制random-tools-remaining-01已验证Hive/行星要塞/大龙塔升级取消，
各真实发单1、原形恢复、原批次停止、拒绝0。扣采矿后分别返150矿113气、
113矿113气、75矿113气，运行时不据此猜退款；模型调用0，fast_build=false。

女王原生控制01未通过：布场观察者消耗1供给，Queen需2供给而仅剩1，
生产从未启动、明确等待，没有证明出生/退避。已保留记录，02增加真实Overlord后重跑；
脚本另加资源/供给前置断言以便早停布场错误。

女王控制02已通过：正常训练出生、实际移动及打伤Marine，原生拒绝1后按1秒退避恢复。
出生瞬间完成度1，但原生ready仅[1,4,251,1694]、没有移动/攻击。
据此在防守发单前查询真实原生能力，未开放则保留单位等待，每0.5游戏秒重查。
控制03通过：同样正常出生、ready暂缺移动/攻击，最终实际移动/伤敌、拒绝0，发单2。
没有靠默认完成度猜能操控，也没有启用快速建造。124项相关回归通过。

普通调兵已有能力查询，但暂不可用时立即失败/STOP，新增最多8游戏秒的原编队等待，
仍受原任务deadline约束、每0.5游戏秒重查、不换目标/单位/部分派遣。
96项空间/防守检查通过；新增原生order_units控制正在运行。
回归中旧formation-arrival“攻击中不能到达”负例实为无订单，两版源码均完成；
基线对照random-tools-spatial-formation-baseline.log证实预存，现负例明确ATTACK订单。

普通调兵原生queen-defense-random-tools-spatial-01通过：出生时done1而ready无移动/攻击，
等待0.54游戏秒后向同一目标发单1次，女王实际移动并伤敌，拒绝0。
验证结束时任务仍running，没有把战斗伤害当作到达/胜利。
最新综合443项回归通过（含全部autonomy-regression模块）；旧GoStructured越界测试
仍写死45000，与已生效的Go90000保护不符，现按实际planner.context_limit造越界事实，
仍验证模型请求前阻断、计数0，不降低运行时保护。

第四批random-sn3mcspg已启动，赛程ZvT/ZvT/PvT/ZvP、seed20261017。
双方真实请求max_tokens16000、timeout60；每方24次和Slower/时限保持。
旧随机账本135/172/179全部冻结哈希保留，外部复盘仍关闭。

### 第四批首局局后修复与完整验收

首局783.8游戏秒/1860.65墙钟秒，time_budget/Unknown；0原生拒绝，回放与清理完成，
预算48/200。P1请求2一次ReadTimeout已恢复；后段3次plan_rejected均为累计产量封顶，
P1一次注卵丢失新目标、一次主力全灭、一次Queen产卵基地死亡；P2一次空间任务20秒停滞。
P2停滞时40单位全空闲、中心距目标约4.21，数个单位距目标6.39..7.79，
29个未到达单位的原生路径为0，具体阻挡未知；不放宽条件伪称已到达。
模型P2末轮确实想转推主矿，含增援的整批被旧累计产量上限挡住，不把这次失败归咎于没下令。

当轮输入中P1zergling requested136/completed36再追加80，remaining=180；
另一次186/46追加50，remaining=190；P2marine198/60追加20，remaining=158。
这些都被“requested累计<=200”挡住。现每个活动批次remaining<=200，
每次追加1..200、每产品每回复一次、请求重送去重保持；requested累计可超200，历史不重置。
新单个合法取消未发单旧目标并重下时，已取消目标不占新队列上限；非法任务仍整批拒绝。
首笔本任务购买前完成的旧生产队列也刷新入baseline，不会算成新单已产出。

注卵回放p1-inject-approach-replay-01.json显示主矿原生251订单尚在执行，
二矿新订单未执行。原开赛源码控制inject-reuse-random-tools-before-01复现：
主矿完成、二矿8秒失败，发单2、拒绝0。新版after-01两基地同一女王均实际扣能量并出现buff，
发单2、拒绝0，36.21游戏秒完成，未自动重施。
等待旧原生251订单结束再复用，pending.native_order_observed仅匹配本目标基地。
通用use_ability也等待旧注卵结束，并严格按单位/点目标识别订单确认，仍不把ACK当完整战术效果。
5项原生闪烁/守护盾/加速/装卸复验native-abilities-random-tools-target-ack-01全通过、拒绝0。

混编控制01因目标被地形吸附过近未实际派遣，保留failed；
02先校验距离及Marine/Infestor逐单位原生路径，7类型同一attack_move全部实际到达，
发单1、拒绝0，支持19 ScanMove与23 Attack同根3674，无需改引擎映射。
最新综合452项通过，日志random-tools-latest-complete-regressions.log；第二局ZvT继续原赛程加载修复。

### 第四批第二局与注卵复验（2026-10-15）

match-02虫族对人族完整结束：700.8游戏秒、1664.09墙钟秒，engine_end，
engine_player_result确认虫族Defeat/人族Victory，原生拒绝0；回放保存、清理完成、
开赛源码快照一致。预算累计96/200，双方各48次，旧赛程账本哈希全部一致。
P1请求4一次ReadTimeout后恢复。请求17将补给数量2写在args.count，整轮曾被拒；
现对原生目录内建筑/扩张/形态批次也做数量字段等值转换，保留数量与目标、拒绝冲突。
请求20同产品两次append仍违反每回复一次的公开契约；不默默合并。
末段基地/生产者已被摧毁、目标expansion无己方已完成基地、[10,170]附近8格无地面，
均有对应明确失败证据，不把模型选择和战损伪称为原生工具拒绝。

注卵等待进一步按真实cast_range加双方radius判定，只等待已进入施法范围的旧251订单；
仍在远距行走时允许明确改目标。专用注卵和通用use_ability共用该判定，订单ACK要求
同一目标tag/点。454项综合回归通过，见random-tools-batch-range-complete-regressions.log。
异步开局测试使用释放门验证模型未完成时tick已返回，避免200毫秒调度抖动导致误报。

最新原生inject-reuse-random-tools-after-range-01通过：36.03游戏秒、同女王两基地
各一次实际扣能量+buff、发单2、拒绝0；没有重施，也没有声称已孵出幼虫。
模型调用0、debug_fixture=true、fast_build=false，queen.py SHA
89818347c19956cc3b416d95065bcc0d57492aad98fb9600eb87b2f3d627755e。
上次启动因自动审批服务用量错误未执行，本次继续后正常审批、实际运行完成。
第三局PvT已按原随机赛程启动，日志random-tools-series-04-match-03-live.log；
未重置历史、未打开外部复盘、未改变Slower和请求/时间上限。

### 第三局侦察路线控制补充

约324游戏秒的本地反馈显示双方各11次scout都因对方SCV/Probe接触停止，
所有goal_arrived=false。双方公开决策反复要求侦察敌基地，仍报告只见工人。
停止符合原默认契约，order_units move也已支持继续；现补充scout自身的显式
stop_on_contact:false，以报告接触后继续原路线，仍只发move，不自动攻击/撤退/替换单位。
默认true不变，不将遭遇接触冒充到达。稳定的接触集合不逐帧刷事件；新/重现接触仍报告。
新增5项回归覆盖已见工人时直接发单、连续接触/多路点实际到达、接触重现、
原截止时间、严格bool与scout限定，schema与系统合同同步。
扩大综合503项运行，501通过、2项旧占位场景跳过，日志random-tools-scout-complete-regressions.log。
当前已启动的第三局不热更新；verify_scout_contact_live.py原生零模型控制待验。

原生scout-contact-random-tools-01现已通过：默认在可见SCV接触处停止，显式false后
同一Probe仅1次MOVE、实际移动15.42格并距原目标3.61格，任务按实际到达完成；
接触事件1条且mission_stopped_on_contact=false。拒绝0、模型调用0、fast_build=false。

第三局最终783.8游戏秒/1860.50墙钟秒，time_budget/未确认；回放保存、清理完成、
源码快照一致，累计144/200。P1第19次ReadTimeout，第20次恢复；1次原生拒绝对应
Marine观察到发单窗口死亡。P1水晶塔绑定建筑死亡、双方若干小队全灭均有明确记录。
时空加速实际观察能量消耗+目标buff，仍不冒充完整战术效果。

神族请求22的补给count2仍issued0，24落点原生CantBuildLocationInvalid；另有7格间距
启发式，正在验证是否漏掉合法密集落点。只读回放查询01原/密集候选全返回通用Error，
缺少成功对照，结果不可判定；原报告保留，assessment单独保存，不把它当不存在落点。
新原生布局控制verify_pylon_spacing_live.py before-01正在进行，未改运行时布点规则。

### 水晶塔候选间距修复与原生建成验收

before-01在布场前失败：整图留白使镜像公式不匹配出生点。before-02在相反出生方向上
旧算法找到1个合法点，不能复现原局；记录保留。before-03使用原出生点、22座已见建筑
布局和原任务预留位置，旧24候选全部原生CantBuildLocationInvalid；密集30候选14个合法，
首点[33,133]。这是受控布局验证，矿物耗尽、移动单位和真实挂起任务不同，非精确重放。

据此仅在普通Pylon候选为空/原生全拒后查询去重的密集候选，放宽7格间距偏好；
建筑碰撞/矿区/预留任务/排除点/地图边界与原生查询保留。普通查询成功不启动回退，
两次都拒绝不发单/扣费/宣称完成。新增5项有意义回归覆盖上述成功、失败、空候选及约束。
macro_placement_spacing_relaxed只报告候选原生合法，建成仍由原生产生命周期确认。

after-01因再次抽到相反出生点未复现，保留为失败控制。同种子不能保证出生方向；
脚本现最多4次零模型启动选择原方向，不匹配就失败。after-02第一抽即匹配，实际宏
购买1次并原生建成恰好1座[33,133]水晶塔，拒绝0、无快速建造、模型调用0。
macro.py SHA318dba2b3e6f7814e6cb87bc00862d4b371bc9ae606f4ab95bfeb450a5039dae。

DeepSeek实战实际使用BOT_RULES精简提示，已同步侦察可选参数，合并重复说明后3745字符。
第一次增长触及旧45k合成边界用例，失败日志保留；缩短说明后原输入/保护不变且回归通过。
最终综合512项运行，510通过、2项原有占位场景skip，random-tools-final-complete-regressions.log。
第四局ZvP续原赛程，所有历史和144次已发请求保留。

### 第四局完成、实战侦察证据与幼虫确认保护

第四局784.0游戏秒/1860.65墙钟秒，time_budget/未确认，回放/清理/源码快照校验完成。
本批四局finished，192/200（双方各96次），旧账本哈希保留通过。第4局无模型请求错误。
原生拒绝2：P1二矿Extractor位置拒绝、P2折跃CantFindPlacementLocation；后者已有限重选并继续
下单，但整批局末仍等矿，不能报整批完成。P1还有一次注卵总截止和一笔跳虫未确认。

双方模型实际选择scout stop_on_contact:false，P1穿过工人后继续见到控制核心/折跃门，
已实际抵达敌主基地和多个矿区路点；P2继续侦察后被击杀，按死亡结束，没有补派。
P1同Queen对两矿多次真实注卵确认。证据continued-scout-injection-audit-01.json。
注卵58等待约72秒后才发单，90秒总截止时仍在向正确主矿赶路；保留失败，无延长/重施。

Extractor原点[124.5,63.5]普通工蜂控制原生建成，4气泉查询合法；固定坏点假说不成立。
carrying-01四次出生方向不符，未完成布场。carrying-02调试占位被原生挪位，实际建了另一口，
原result保留且assessment明确对原目标不可判定。脚本改用真实建造占住另一口、断言最终目标；
carrying-03实际buff271工蜂在原点建成、拒绝0。原局拒绝原因仍未知，未修改gas执行分支。

跳虫任务44一笔718.75秒指令8秒未确认；回放16100同帧派蟑螂/跳虫，但仅16103蟑螂实际执行，
未扣跳虫矿、原请求Larva仍闲，16162才被后续蟑螂使用。资源920/65、空人口3足够两笔。
证据p1-larva-unacknowledged-audit-01.json与p1-larva-command-replay-01.json。
单基地3空人口的larva-batch-random-tools-before-01两单均成功，因此未声称同帧必然覆盖。

增加未确认幼虫购买互斥：原生购买观察确认后才允许下一笔，不盲目重试；轮转已有产品目标，
保留数量/请求去重/付费等待，不让先排的产品独占，不阻塞Queen等其他生产者。
原生after-01实际分两批（loop55/67）完成蟑1+狗2，0拒绝；mixed-01完成蟑2+王虫1，0拒绝。
新保护尚未进入下一场付费比赛。macro SHA6c310406cd677fb9cbb59884ec363d19e33ec936d0ca36c3451d1f077ab9c4f5。
5项专用回归覆盖未确认互斥、异位虫卵、目标轮转、缺气不挡下一产品、Queen并行与无隐式重购；
最新综合日志random-tools-completion-regressions.log，原519项综合已无失败（2旧占位跳过）。

## 第五批：新幼虫保护实战与建造付款核查

`random-04f97o8v`，seed20261021，抽签PvZ/PvT/ZvZ/ZvP。每次一局，
保留Slower realtime、24次/方、1800游戏秒/1860墙钟秒，外部模型战后复盘关闭。
旧随机A135/B172/C179/D192及其他账本哈希冻结，不能重置。比赛状态以series.json为准。

新增`verify_construction_payment_live.py`零模型原生诊断，停止己方采矿后派远处工人建造，
记录真实矿量、原生建造订单、建筑首次出现与预留释放：
- `logs/construction-payment-random-tools-before-protoss-01/result.json`：
  约0.134游戏秒扣100矿，约15.804秒首次观察到水晶塔建筑，原生拒绝0。
- `logs/construction-payment-random-tools-before-zerg-01/result.json`：
  约0.223游戏秒扣200矿，约15.938秒首次观察到孵化池建筑，原生拒绝0。

两例都明确给资源、没有fast-build、0模型调用、只发单1次。这验证了接到建造订单就已扣款，
排除“工人赶路时未扣款但预留提前释放”的假设，没有据此改资源执行器。
只验证建筑已开工和付款时机，不声称完整建成、正式胜场，亦不解释第四批气泉拒绝根因。

### 第五批首局发现并修复：集结点执行链

原始证据`random-04f97o8v/match-01/p2-rally-unfinished-audit.json`：
P2 request8看到二矿59%，请求`rally_units target=expansion_entrance`，task29执行失败，
提示没有已完成二矿；同一观察中的自动集结已经指向该入口。

- 显式rally地标允许己方已建至少25%的地面基地作锚点，与自动集结的进度阈值一致。
  选作产能的建筑仍需已完成；不自动把未完成建筑加入原有显式订单。
- 显式rally入口接受可见敌军压力，保留静态地形/建筑占位检查；自动集结继续避敌。
- 迟到回复校验允许取消请求时已知的己方rally；已结束订单是no-op。
  未知/请求之后新增的任务仍拒绝，校验本身不改任务。
- P2迟到防守用传入的己方主矿生成基地标签，修复写死homes[0]。
- 无法解析坐标/缺少唯一原生rally能力时，任务明确failed并释放自动集结所有权，
  不再永久running且不发单。

新增9项回归，其中正/负用例均有旧码失败证据。完整529项运行，527通过、2原有占位skip：
`logs/random-tools-rally-release-complete-regressions.log`。
中途一条检查命令误用了不存在的test_battle_plans模块，原错误日志保留；已由真实34模块
和全部test_autonomy_regression_*综合检查替代，不将那条失败命令报作通过。

原生`logs/unfinished-rally-random-tools-after-01/result.json`：真实expand将二矿建到30%，
调试布置一辆可见架起坦克；普通自动候选被威胁过滤，显式订单仍实际写入主矿
`raw.rally_targets=[137.5,65.5]`，确认时二矿31.7%，0原生拒绝、0模型调用、无fast-build。
此正例源码哈希保存在result；后续两行失败释放逻辑没有改变该正例路径。
首局尚在运行且使用开局源码；下一局才加载这些修复，不能把首局旧失败改写成成功。

### 已复现并修复：气矿建筑占了一个采气工人名额

首局两方`workers_per_refinery=3/allocated=6`，两口气矿长期各显示2工。
`logs/gas-allocation-random-tools-before-zerg-01/result.json`原生复现：
`gas_assignments`含`ExtractorTag -> ExtractorTag`，实际2工，25秒增气48、无原生拒绝。
根因是Probe/Drone的建造收据绑定为建筑后，完成分支把pending.tags中的建筑登记成工人。

完成登记现在只接受当前真实己方工人；维护分配时清除已观察为建筑/其他非工人的
名额和释放列表。暂时进入气矿不可见的真实工人仍保留，死亡处理不变。
新增4项回归覆盖三族完成登记、无效名额修复、隐藏工人和关闭采气时的释放。
旧码相关用例失败，68项针对性检查通过；扩大533项运行、531通过、2旧占位skip，
`logs/random-tools-gas-complete-regressions.log`。

原生修复后两族通过、0模型调用/0动作拒绝/无fast-build：
- `logs/gas-allocation-random-tools-after-zerg-01/result.json`：实际3工，25秒增气60。
- `logs/gas-allocation-random-tools-after-protoss-01/result.json`：实际3工，25秒增气64。
只证明这两个受控对照，不将增气差量推广成固定收益率。
第二局在此修复前已启动；第三局才会加载采气修复。

首局正式比赛进程后来确认defunct，最后快照590.4/日志约591.9游戏秒，未取得胜负或回放。
原始series/match均有before-environment-interruption副本，中断证据另存；37次请求保持原字节。
下一局按原赛程编号2续跑，不能重打首局、补回请求或把诊断夹具计为胜场。

### 已复现并修复：集结建筑编号随死亡/新生漂移

原producer_inventory每次按当前tag重排编号，前一座死亡后幸存hatchery#2会变成#1，
迟到校验又只数类型数量，可能让旧#1指令误选幸存/新增建筑。
现在每个arena、玩家、建筑类型保存首次发布的编号，已发布编号不重用；形态变换获得
对应类型的编号，其他幸存建筑不重排。capture记录请求时label->tag；迟到显式selector
必须仍对应原tag，请求后才出现的建筑需要新决定。“all”仍绑定执行时的已完成己方产能。
不增加模型输入或原生路径查询：内部身份快照使用include_routes=False。

5项新回归旧码均失败，修复后92项相关检查通过；综合538项、536通过、2旧占位skip，
`logs/random-tools-rally-identity-complete-regressions.log`。
原生`logs/rally-identity-random-tools-after-01/result.json`：独立debug布场，移除旧主矿后
原hatchery#2实际收到集结点，新增基地编号#3，旧#1迟到指令明确拒绝，0模型/0动作拒绝。
不计正式胜场。当前第二局已使用开局源码，第三局才加载此编号及采气修复。

传送门未确认的零模型对照`logs/gateway-training-random-tools-transition-01/result.json`
完成真实Gateway建造->原生1518形态转换->1413折跃->1个实际Zealot，0错误。
未发916，未复现首局旧916丢失，因此不能声称修复了原始漏确认；没有据此增加隐式重购。

### Lair升级中的注卵：原生核查排除误判

`verify_morphing_base_injection_live.py`零模型控制，真实下达Lair升级再调用inject_larva。
第一次before-01误以为升级中会立即变成type100且progress<1，夹具判断失败，原记录保留，
没有据此改运行时。按实际1216升级订单识别后，order-control-02通过：
- 基地仍是type86/progress1.0/orders=[1216]，不能把普通建筑完成字段当作Lair完成。
- 高层inject_larva真实施放，Queen约50.88->26.51，基地buff11出现，1216升级仍在进行。
- 高层任务completed，0原生拒绝、0模型调用，无fast-build；Queen执行器SHA保持89818347...。

原结果`logs/morphing-inject-random-tools-order-control-02/result.json`。
未复现高层注卵限制，没有修改注卵执行器，也不把buff/能量确认说成幼虫已出生。
第三局ZvZ约145秒已经实际观察到两方气矿各3/3工，见match-03/gas-allocation-field-check-01.json。

### 注卵反馈：区分等待、赶路与基地丢失

第四局P1 task21实战超时表明增强反馈已加载：约72秒等待后才发出251，
90秒总截止到达时女王仍在赶路，phase=after_dispatch；原生目标、位置、能量和仍存活基地
均明确保留。该失败符合总期限契约，未延时/重施/补女王。

失败事件现包含phase、last_wait_reason、own_queens、own_bases；未绑定目标的null不能
误解为己方基地丢失。另在活动任务中增加request_created_game_s、deadline_game_s、remaining_game_s，
按排队时刻计算，不在发单时重置。这三个时间字段是第四局启动后增加，仅有单元/综合验证，
当前正式局未加载，不混作本局实战结果。

最新综合540项运行，538通过、2原有占位skip：
`logs/random-tools-final-deadline-complete-regressions.log`。
新增测试明确检查等待50秒后发单仍在90秒截止、只剩40秒，超时仍不重发。

## 第五批最终结果与后补修复

| 局 | 对阵 | 结果 | 累计尝试 |
|---|---|---|---:|
| 1 | PvZ | 环境中断，约592游戏秒；无回放/无胜负，不重赛 | 37 |
| 2 | PvT | 783.7游戏秒，time_budget/未确认；清理/回放/源码校验通过 | 85 |
| 3 | ZvZ | 783.8游戏秒，time_budget/未确认；清理/回放/源码校验通过 | 133 |
| 4 | ZvP | 784.1游戏秒，time_budget/未确认；清理/回放/源码校验通过 | 181 |

双方始终DeepSeek、Slower realtime，每完整局24次/方，无外部战后复盘。
十个旧账本哈希全一致。完整三局不是胜场；首局中断原始副本/证据保留。

后补的关键缺陷：第四局新蟑螂实际购买后被旧已确认收据抢绑。只读正常迷雾回放
`match-04/p1-roach-unacknowledged-replay-01.json`及`p1-roach-receipt-assessment.json`：
loop16824实际购买新卵4395630613；旧卵4354211843在16830结束，旧收据在16834再次
执行macro_larva_egg_observed，抢走新卵，造成原请求幼虫4405854245收据错误超时。
现在seen=true的收据不再认领新卵，原卵结束后正常释放。

原生因果控制`logs/larva-receipt-random-tools-{before-01,after-01}/result.json`：
明确controlled_receipt_redirection=true，夹具在第二笔真实购买后把其请求tag设为空闲幼虫，
构造实战已观察到的改选；不是宣称自然比赛又复现。两笔真实购买、2个实际工蜂、0原生拒绝；
旧码错误failed，新码completed且新收据ACK正确。合成测试也先旧码失败后新码通过。

新增stop_production，只停未购买数量，已付费卵/变异/队列继续；原requested历史保留，
cancelled_unissued单列。append只加新数量；默认coalesce不恢复已停止部分；停止和新追加
同轮仍检查剩余200上限，非法混合计划不改变旧目标，已结束目标是no-op。
原生`logs/stop-production-random-tools-after-01/result.json`：20只请求，已买一枚卵后停止，
实际2只、18只停止、1笔购买、0拒绝/0模型；没有取消虫卵或推断退款。
核弹/拦截机在stop_production和native_queue_cancel均明确排除，避免依赖目录不存在的ammo字段。

气泉恢复`logs/gas-approach-random-tools-{before-01,after-01}/result.json`：
先真实接收工蜂1154赶路订单，再由夹具敌方工蜂真实占用目标，原生42/CantBuildOnThat。
旧码直接失败且原工蜂仍104、无己方开工；新码按已有5次上限重选另一口，最终1座完成。
每例含故意构造的1次原生拒绝，不能报0。新恢复仅限42/44且原工蜂仍未消耗、无绑定/实际建筑；
消失工蜂、已开工、超5次不补买。旧第四批44的原始触发原因仍未知，未冒充精确重现。

第四局光子炮失败由实际Forge战损导致：只读`p2-forge-cannon-replay-01.json`/assessment
确认下令loop13852、Forge死亡13868、原生40出现在14186，执行器没有自动重建科技。

最终556项运行、554通过、2旧占位skip：logs/random-tools-final-scope-verified-regressions.log。
最后的气泉恢复/stop_production/旧卵绑定修复及注卵时间字段在第四局运行中或局后完成，
已做单元/原生对照，未计作第四局已加载效果。下一轮需加载新源码，不重置181次历史。
