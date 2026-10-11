## 更新（2026-10-11 八版：删掉"只吃基地周边"的启发式 + 修好部队归属抖动

**用户的质疑（对的）**：为什么会存在"多条防守令时各令只吃自己基地周边"这条规则？不应该是"基地被攻击后，LLM 看清自己所有部队与位置，再决定派哪些兵去执行防守"吗？

**结论：这条规则删了。** 它的动机是"怕第一条令把兵吸干、后面没兵"，但那是**脚本替模型管配额**，与最初定的分工（模型决定派谁去哪、脚本只执行）冲突。现在：
- **全军团对任何一条防守令都是可选的**，不管它在哪；脚本只拒绝"已被其它任务/其它防守令占用"的单位；
- 要分兵就由模型**给每条令写明兵种/数量**（`count`/`units`）；
- 只有 `units:"all"` 且**没写 count**（即模型没表达配额）时，脚本才用默认规则：约威胁的 2 倍（至少 4、至多 16），多条令并存时**按令平均分享**现有兵力。写了 count 就完全按模型的来。

**实测暴露并修掉两个真 bug（都属于"看日志才发现"的那种）**：
1. **部队归属每拍抖动**：队列开头的全局 `used` 守卫把防守令**自己的旧标签**也算成"已被占用"，导致一条令每拍都拿不回自己上一拍的兵（选兵集合每拍变化），多条令并存时还会互相把对方的兵释放掉（实测 `held 6↔0` 反复）。修法：在选兵时把**自己上一拍持有的标签**从排除集合里减掉，同时把"取消当拍"的守卫限定为**非活跃**任务。
2. 之前那条"跨基地抽兵"限制本身也是我加的，与本版删除的启发式是同一处，一并去掉。

**新增测试**：`test_a_distant_squad_can_be_ordered_to_any_base`（远处部队是合法援军）、`test_count_is_how_the_model_splits_the_army_between_orders`（分兵靠模型写 count）、`test_units_all_without_count_commits_the_reachable_army_for_a_sole_order`、`test_units_all_without_count_is_capped_when_several_orders_share_the_army`、`test_two_orders_do_not_swap_ownership_every_tick`（钉住抖动）。全套 **448 项通过**。

**原生实测（同一双线场景）**：远处 6 名陆战队在**下令当轮**就被认领（此前要等他们自己走近），并**稳定保持**（`held 3` 持续，不再 6↔0 抖动）；模型中途改用新令替换同基地旧令（`superseded`），单位被继承，最终两处基地都有人驻守。


## 更新（2026-10-11 七版：让 LLM 更偏向战斗（实测 A/B 有效）

**用户要求**：改提示词，让 LLM 更偏向战斗。**依据**：上一轮双线原生实测显示，两处基地挨打、SCV 已被打到 2 个时，模型前 4 次回复仍只有 `macro_plan`/`train_worker`，第 5 次才下防守令。根因不是模型不会，而是**提示词里只有"扩张经济"的指令，没有任何战斗优先级规则**。

**三处改动**：
1. **`defense_report` 增加机械紧迫度**（`arena/defense_orders.py`）：每行给出 `seconds_to_loss`（不改变现状时基地被打掉的秒数 = base.health / 可见敌方 DPS）与 `urgency`（critical ≤15s / urgent ≤45s / contested / quiet）。**只是算术，不含建议**——判断仍归模型，避免又变成脚本替它决定。
2. **`DEFENSE_PROTOCOL` 补"紧迫性决策"**：任何基地 contested/urgent/critical 时，同一回复里就要有对应的 `defend_base`；"基地在掉血却只回经济任务"被明确点名为**等于决定放弃该基地**；分兵/只守一处/全都放弃都合法，但必须是**有意识的选择而不是没注意到**。
3. **主提示词结尾新增 `DECISION PRIORITY`**（5 条，`arena/battle_plans.py`）：受袭=防守决策而非经济决策；critical/urgent 时本回复必须下单；远处部队在只有一条令时允许召回（"迟到的部队胜过永远不来的完美部队"）；落下的经济在基地守住之后再补；每轮对照 `defense_report` 复核既有防守令（安静的取消或替换，仍在争夺的保留）。放在 SYSTEM 末尾，尽量不破坏前缀缓存。

**A/B 实测（同一场景、同一模型、同一配置）**：
| 指标 | 改前 | 改后 |
|---|---|---|
| 首次下防守令 | 第 **5** 次请求 | 第 **2** 次请求 |
| 模型防守令总数 | 3 条 | 3 条 |
| 涉及基地 | home + site_ | home + site_ |
| 首次下单时己方 | Marine 6 / SCV 3 | Marine 6 / SCV 3 |
结论：**响应更早（5→2）**，两处仍都会被下令；但第 1 次请求依旧纯经济（它尚未见过 `seconds_to_loss`，下一轮才用上），这是模型侧行为。全套 **445 项通过**。

**仍然存在的机械边界（未改）**：存在多条防守令时，各令只吃自己基地周边的兵。实测中"远处 6 个兵"因此在下令当轮拿不到、要等它们自己走近才被认领（`held 0 → 3`）。若要让"基地受袭时的召回"优先于"别互相抽干"，需要再放宽一次该规则（例如 critical 且该基地附近无兵时允许跨令召回）。是否放宽待定。


## 更新（2026-10-11 六版：双线防守单测 + 原生实测（含 3 个真 bug）

**用户要求**：造一个"两个矿同时被不同数量部队进攻"的场景实测，并单元测试；期间指出两个错误——(1) 给人族放了虫族二矿，(2) 部队摆在基地旁一出生就开打，而他要的是"**部队在别处，由 LLM 决定派哪些兵去守哪一处**"。

**新增单测 `test_two_front_defense.py`**（真实 Commander + 脚本化模型回包，不是手搓假世界）：
- 两处同时受袭（3 vs 1），报告分别统计 → 模型按压力下令两处 → 脚本各派就近部队，两条令并存且不共用单位；
- 断言 `defense_report` 两处压力分别正确、未下令时显示未防守。
`test_defense_orders.py` 扩到 16 项，新增：同基地覆盖且**继承原部队**、两处并存各持其兵、**唯一一条令 + 该基地受袭时可召回远处部队**、无威胁时不得抽走别处基地旁的兵、同拍并发不重叠。全套 **445 项通过**。

**原生实测脚本 `verify_two_front_defense_live.py`**（`logs/two_front_defense_native.json` + 审计日志）：
- 场景形态按用户要求：部队 6 名陆战队**集中在离两处基地 170~195 格的远处**，两处基地各被 3/1 名敌军压境；
- **脚本内前提校验**（不成立即判本次无效）：第二基地按**玩家实际种族**生成并确认真的出现、四个生成组都数得到、报告两处各自统计到受袭、两处附近**没有**己方部队。

**实测结果**（真实模型 deepseek-flash）：模型**确实会分兵两处**——先后下达 `base=home`(count=2) 与 `base=site_4296278017`(count=3) 两条防守令，并配 `cancel_task`/`order_units` 调整。但它的**首要反应是经济**：前 4 次请求两处都在挨打、SCV 已被打到 2 个，它的回复仍只有 `macro_plan`/`train_worker`；直到第 5 次请求才下防守令，此时主矿 SCV 打光、一处基地随后丢失。位置类指令（派兵/调兵）它下得很晚，这属于模型侧行为，机械层无法替它决定。

**实测暴露并修掉的 3 个真 bug**：
1. **`units:["marine"]`（小写选择器）被整条计划拒绝**：`defend_base.units` 的校验与筛选只认 `world.own` 的显示名（`Marine`），模型写小写就报 `unknown unit kind(s) marine` → **防守令全部无法落地**。现在两种写法都接受（`defense_orders.unit_names` 同时登记显示名与小写选择器），错误信息也只列玩家真实拥有的名字。
2. **跨基地抽兵规则过激**：`home_pull_radius` 原本无条件禁止动用远处部队，导致"只有一条防守令且该基地正被攻击"时远处援军也调不动（实测：同一批 6 个兵被指派后立刻丢弃，`no_free_units`）。现在规则为：**该基地受袭 且 全图只有这一条防守令 → 允许召回远处部队**；存在多条防守令时各自只吃自己周边，避免互相抽干。
3. **命名源不统一**：`defense_report.own_nearby`/`unit_name` 曾用 `backend.types` 名称而 `world.own` 用 `NAMES`，同一个兵会有两个名字，模型照报告念却被拒。已统一为与 `world.own` 完全同一规则。

**场景搭建踩过的坑（写进脚本注释，避免复现）**：debug 生成对不可建造点/超出地形会**静默失败**（实测人族二矿放 `x=182.5` 就没了），所以每个生成组都要"生成后数一遍"；城镇中心坐标是footprint 左上角，按它做碰撞判断会算错偏移；`MapState` 只有 `visibility`/`creep`，**没有** terrain grid，地形只能靠"试放+观测"验证。


## 更新（2026-10-11 五版：同基地覆盖；修两个"多防守令共存"的真缺陷）

**用户要求**：把防守令改成"可以覆盖"——否则模型调整防守部署（改兵种/数量/姿态）必须先用一次决策去 cancel，太贵。

**实现（`arena/tasks.py` + `arena/defense_orders.py`）**：
- **同基地再下令 = 覆盖**：入队时把同 `base` 的活跃防守令标记 `superseded`（reason 明确写"superseded by a new order for the same base; its units are inherited"）并进 `cancellations`；新令**继承原部队**（`supersedes=[旧任务]` → `inherited_tags`），改数量/姿态不会静默换掉正在防守的单位。不同基地仍然是两条并存的令。

**顺带修掉两个**在"多防守令共存"场景下暴露的真缺陷**：
1. **全局预留互相踩**：`release_defense`/`take_defense` 用的是**一个** `defense_tags` 集合，谁最后 tick 谁赢 → 后下的令永远抢不到兵（实测：第二处 `waiting: no free unit` 而第一处拿着全部兵）。现在每 tick 由**第一个**防守令先快照 `defense_tags_snapshot`，各自选兵时把它算作被占用；而**继承来的单位对自己不设限**，覆盖才算真正生效。
2. **跨基地抽兵**：`units:"all"` 会把**全图**空闲战斗单位都算进第一处基地，包括站在二矿旁边的兵 → 第二条令注定拿不到。新增 `home_pull_radius=24`：**只动用自己基地附近的兵，不去抽别的基地旁的部队**；因此拿不到兵时报告里写明"units near another owned base are not pulled away"，而不是含糊的"no free unit"。

**验证**：`test_defense_orders.py` 13 项判别测试，新增 4 项——同基地覆盖且**继承原部队**、不同基地**两条令并存且各持其兵**、**不得跨基地抽兵**、同拍并发不重叠不双指挥。全套 **442 项通过**。提示词（`DEFENSE_PROTOCOL`）同步写明：覆盖语义、每处令只动用自己基地附近的兵、拿不到兵时报告而非去别处抢。


## 更新（2026-10-11 四版：持续防守与其他派兵令的关系（不冲突，但优先级是硬的））

**问题**：`defend_base`（持续防守）与模型主动的 `order_units`/`scout`（派兵去某处）会不会互相冲突、双指挥同一批兵？

**结论：不会双指挥，但有明确优先级。**

- **硬隔离**：`protected()` 含 `defense_tags`，而 `SpatialMission` 选兵条件是 `u.tag not in used and u.tag not in q.protected()`（`arena/spatial.py:298`）。所以**防守令持有的兵，scout/order_units 一律拿不到**；反过来 `DefendBase._select_units` 也用 `q.protected()|used`，**不偷**显式任务的兵。
- **同拍冲突的实际语义**（主循环按 task id 顺序执行，`order_units` 先于 `defend_base` 分配）：显式派兵先拿兵，防守令只能拿剩下的空闲单位。**"谁赢"属于调度实现，真正的 bug 只会是"重叠"**，所以测试断言的是不变量：同一拍不得对同一单位下两次令、两条令的持有集合不得重叠。
- **拿不到兵时是可见的**：`order_units` 会 `waiting` 并给出 "requested free squad unavailable; no substitute unit"，**不会静默换别的单位**。
- **要动被防守令持有的兵，必须先取消**（`cancel_task`），因此规则与"转攻必须先取消"一致，模型只需记一条：**standing order 持有期间，显式派兵绕开它**。

**改动**：`DEFENSE_PROTOCOL` 补上这段关系说明（`arena/defense_orders.py`）；`test_defense_orders.py` 增加 2 项测试——`test_explicit_dispatch_cannot_steal_units_held_by_a_defence_order`（持有期间显式派兵必须可见地等待，不能偷）、`test_same_reply_dispatch_wins_but_never_shares_a_unit`（同一回复里两条令并发：断言不重叠、不双指挥、防守预留仍保护其单位）。全套 **439 项通过**。


## 更新（2026-10-11 三版：可调用性审计；修 rally_units 漏 schema）

**用户要求**：检查所有行为 AI 是否都能调用（例如设置集结点）。

**审计方法**（三方对齐）：`TaskQueue.ALLOWED` ↔ 系统提示词 ↔ `battle_plans.output_schema`。

**抓到的真 bug**：`rally_units`（2026-10-09 实现的按建筑设集结点）在**队列允许**、**提示词也写了用法**，但**从未加入输出 schema**。provider 侧按 schema 校验参数，所以模型在真实对局里选它会被挡掉 —— 功能等于不可用。已在 `arena/battle_plans.py` 补上该变体（producers 支持 `"all"` 或 `rally_producers` 的 `类型#序号` 枚举；target 支持 `rally_landmarks` 具名地点或 `[x,y]`；可选 `duration_s`/`reason`）。

**审计结论（修后）**：三族 schema 覆盖完整，无缺口、无越权暴露。
- 人族 25 个技能（含 `scan`/`call_mule`/`supply_drop`/`barracks`/`supply_depot`）；
- 虫族 20、神族 20（各自的种族专属变体已按 `tree['race']` 分支注册）；
- 反向检查：schema 里没有队列不接受的技能。

**端到端入队校验**：用各族**真实单位/建筑/升级名**逐个调用，全部可入队（`train_worker`/`train_unit`/`build_supply`/`attack_nearest`/`move_out`/`gather`/`no_op`/`build_structure`/`research`/`gather_gas`/`expand`/`macro_plan`/`morph_unit`/`rally_units`/`scout`/`order_units`/`defend_base` + 人族三个轨道技能）；取消路径 `cancel_task`/`cancel_unit_order` 在目标活跃时可用（目标不活跃时拒绝，属正确行为）。

**词汇表说明（避免混淆）**：`scout`/`order_units` 用**小写选择器**（`battlefield.unit_selectors`，来自 `python_skills.KINDS`）；`defend_base.units` 用 `world.own`/`defense_report.own_nearby` 的**显示名**（如 `Zergling`）。两套并存是既有设计，各自在提示词里写明来源；`defend_base` 对拼错的名字直接拒绝并指向 `defense_report.own_nearby`。

**新增护栏测试**：`test_multirace.py::test_every_advertised_skill_is_also_in_the_output_schema` —— 取提示词里出现且队列认可的技能名，断言它们都在 schema 中，并反向断言 schema 不暴露队列不接受的技能。**已验证该测试确实能抓到本次的 bug**（临时移除 `rally_units` 变体后报 `AssertionError: ['rally_units'] != []`）。全套 **437 项通过**。


## 更新（2026-10-11 二版：防守改为"模型显式指名",脚本不再替它决定）

**用户原则（原话大意）**：星际的魅力就在于遇到双线进攻时玩家要自己思考——派哪些兵、守哪处、还是干脆放弃一处。所以防守的**决策权必须在模型**，脚本只负责执行。

**因此推翻了本文件上一版的设计**：那版 `defend_base` 会自己挑"离威胁最近的基地"并把全部空闲兵派过去，等于脚本替模型做了"救哪处"的决定，而且"放弃一处"根本无法表达。

**现在（`arena/defense_orders.py` 重写）**：
- `defend_base args={base:"home|site_N|[x,y]" 必填, units:["Marine",...] 或 "all" 必填, count?:1..32, stance?:"hold|trade|screen", timeout_s?:30..900, reason}`。
- **一条令只守一处**：双线就是两条令，模型自己分配兵种与数量；**不下的那处 = 主动放弃**。脚本绝不自动派兵去没下令的基地。
- 持续性：令持续生效（每 tick 重发，否则单位到达即发呆），**但不会自动改目标**；单位死光/基地丢失只**上报**，由模型重新决定。取消用 `cancel_task args={task_id}`。
- 新增 `defense_report`（每轮 payload）：每个己方基地的**可见进攻者数量/兵种/DPS**、**附近可用己方兵力（按兵种）**、最近敌人距离。这是模型做"守/弃"取舍的依据。
- 兵种名必须与 `world.own`/`defense_report.own_nearby` **完全一致**，拼错在入队时直接拒绝（不再静默 `waiting`）。

**实现中修掉的 4 个真 bug**（都会被静默吞掉，值得记住）：
1. `close()` 清空 `tags` → 取消时拿不到单位 → `release_defense` 不执行 → 单位被**永久锁死**。改为像 `SpatialMission` 一样把 tags 复制到 `stop_tags`。
2. `cancel_task` **自己被当成任务创建**（走进 `SPECS` → `KeyError`）。已加入创建跳过名单。
3. `defend_base` 的**意图键缺失**（上游把 `key()` 重构成按 json 生成），导致两条不同基地的令被当成同一意图**合并成一条**。已补 `defend_base` 分支（base/units/count/stance 入键，reason 排除）。
4. **取消当拍的后续命令会静默丢失**：进攻令在单位仍被 `defense_tags` 预留时下发 → `issue()` 拦掉，但任务已标 `completed`。现在取消当拍把这些 tag 视为占用，后续命令下一拍真正拿到单位（仅对持有防守预留的任务生效，Python 技能的原子取消重派不受影响）。

**验证**：`test_defense_orders.py` 8 项判别测试——未下令的基地**绝不自动防守**且报告显示未防守、报告含可见进攻者与附近兵力、模型可把不同兵种/数量分到两处基地且两条令各持其兵、显式任务持有的单位不被抢、取消后进攻能重新指挥那批单位、无敌情时不自动结束只上报、`base`/`units` 必填与非法参数拒绝。全套 **435 项通过**。

**未做**：原生对局实测（模型在双线压力下是否真的分兵/弃守）。机械层只能证明"下令就执行、不令就不动"，模型是否下令属模型侧行为。


## 更新（2026-10-11：模型下令的持续防守 defend_base + 可取消）

**背景（用户实况观察）**：单 agent 模式下，敌人的兵站着不动、不救基地。核查确认这是**结构性缺口**而不是模型笨：防守能力有三套（`DefenseCoordinator` 副官／`ReflexNet` 全局反射／战斗 agent），单 agent 配置把三套全关了（`match.json`: `tactics_enabled=false, defense_enabled=false`，且 `combat_reflex` 在代码里恒 false）。于是下单路径只剩 `attack_nearest`/`move_out`：**一次性 ATTACK 命令**，发完任务即 `completed`，兵到达后停在原地；主 agent 只能等下一次 6 游戏秒决策才可能改口。它还**没有"防守"这个动作可下**。

**实现**（新增 `arena/defense_orders.py`，接入 `arena/tasks.py`／`arena/commander.py`／`arena/battle_plans.py`）：
- `defend_base args={reason, timeout_s 30..600 默认 180}`：**模型下令后才生效**的持续任务。每 tick 找"己方任一基地半径 26 内可见的敌方战斗单位"，把**空闲**战斗单位派去拦截；同一目标不重复下单；威胁消失 **4 游戏秒**后自动 `completed` 并释放单位；到 timeout 也结束。
- `cancel_task args={task_id}`：通用取消入口，作用于 `defend_base`/`scout`/`order_units`。取消时先记下单位再 `release_defense`，然后发停止命令。
- 控制权：防守期间单位进 `defense_tags`（`protected()` 已含），**不会**被其它任务抢；结束时释放。
- 模型可见性：提示词新增 `DEFENSE_PROTOCOL`（含"要转进攻必须先取消"的明确指令）、`control_policy.defense_orders_available`、`control_policy.defense_orders`、`defense_orders`（最近 4 个任务的 summary），输出 schema 增加两个任务形状。无 tech_tree 的旧后端会把协议文本摘掉。

**过程中修掉两个真 bug**：
1. 取消时只写 `pending_stops` 而**没释放 `defense_tags`** → 那批单位会被永久锁住，之后任何进攻命令都指挥不动。现在 `stop_orders` 里按"先取 tags、再 release"的顺序处理。
2. `cancel_task` **被当成普通任务创建**（走进 `SPECS` 分支 → `KeyError: 'cancel_task'`）。已加入创建循环的跳过名单。

**验证**：新增 `test_defense_orders.py` 8 项判别测试（受袭必下拦截命令／已占用单位不被抢／同目标不刷单／威胁消失自动结束并释放／**取消后进攻命令必须能重新指挥那批单位**／非法参数拒绝／同意图合并）；全套 **435 项通过**。模型可见性已断言：提示词含两个技能名与"cancel it first"、`defense_orders_available=true`、schema 含两个形状。

**未做**：原生对局实测（"基地被压时它是否真的下 defend_base 并回防"）。需要一局真实对局观察，且模型是否下令属模型侧行为，机械层不能替它证明。


## 更新（2026-10-11：受控实验证明前缀缓存按"前缀+块"工作；tech_tree 压缩）

### 受控前缀缓存实验（probe_cache_prefix.py，变量单改）

写探针 `probe_cache_prefix.py`（落盘可复跑，只读 usage，不做决策）:取 2026-10-10 那局真实 prompt（117,085 字符 / 45,139 token，SYSTEM 14,594 字符），固定 SYSTEM、只改 USER，逐个变体间隔 6 秒发一次，`max_tokens=1`、thinking 关闭。结果 `logs/cache_prefix_experiment.json`：

| 变体 | prompt_tok | hit_tok | 命中率 |
|---|---|---|---|
| warmup（首次） | 45139 | 0 | 0.00% |
| same（逐字节相同） | 45139 | 44928 | **99.53%** |
| tail（只改结尾 40 字符） | 45141 | 44928 | **99.53%** |
| move_cold（把 374 字符的 world 移到最前） | 45139 | 3072 | 6.81% |
| move_hot（world 放回末尾） | 45139 | 44928 | 99.53% |
| head（只改开头 20 字符） | 45144 | 3072 | 6.80% |
| same_again | 45139 | 44928 | 99.53% |

**结论**：
1. 缓存真实有效且几乎全量（99.5%），但**需要第二次请求才命中**（warmup=0，same=44928）——官方说的"尽力而为、可能需要后续请求构建"得到验证。
2. **只有改动点之前的部分能命中**：`head` 与 `move_cold` 都只保住 3,072 token（约 7k 字符）；`tail` 与 `same` 保住 44,928。这就是"前缀"语义。
3. **字段顺序本身就是变量**：只把 374 字符的 `world` 从第 6 位移到第 1 位，命中从 99.53% 掉到 6.81% —— 同样的内容、不同的顺序，缓存失效。反过来 `move_hot` 恢复 99.53%。**所以"把易变字段后移"确实能提升命中，不是猜测。**
4. 为什么真实对局只有 38%：对局里 payload 每次都在前部变化（world/intelligence/decision_memory 都在前半段），每次只保住约 3k token；而探针里同一 payload 重发能保住 45k。**差距来自"前部是否变化"，不是缓存本身不可用。**

### 下一步（未做，需一次受控对局）
把主指挥 payload 的易变字段（world / intelligence / decision_memory / battlefield / economy_report / active_tasks / feedback / gas_policy / previous_strategy / technology_report）全部后移到稳定块之后，实测公共前缀可从 22,840 提升到 27,316 字符。**但真收益必须用真实对局的 usage 对比确认**（同一配置、改前改后各若干局），因为受控探针只证明了机制，没证明对局中的净收益。探针可复跑：`set -a; . ./.env; set +a; .venv311/bin/python probe_cache_prefix.py`。

### tech_tree 压缩（arena/tech_tree.py）
- `requires` 里的 `pylon_or_phasing_prism_power`（29 字符、60+ 处重复）压成 `power` + 一行 limitations 图例；
- 移除 `data_adjustments`（引擎 internal ability-ID 重绑定诊断，673 字符，不参与任何决策，加载时已打印 `[tech-data adjustment]`）。
- 体积:人 14,626→13,492 / 神 12,837 / 虫 15,038。**没有删 `requires`**（曾试过删，省 23% 但会让模型看不到前置链，否决）。
- 回归测试 `test_multirace.py::test_published_catalogue_stays_compact_without_losing_prerequisites`:三族体积预算 + requires 非空真实 + 短 token 有图例 + 其它族不得出现该 token。全套 **427 项通过**。

### 本会话 prompt 体积累计
告警上限+降精度 −44,585；army_tactics 去 request_trace −3,984；tech_tree −1,789。**单次请求 141,599 → 91,241 字符（−36%）**，输入 token 占这套总花费 95%。


## 更新（2026-10-11：prompt 瘦身；侦察记忆确认；沙箱起不了原生）

**动机**：实测单次请求 `system 14,594 + payload 127,005` 字符，其中 `intelligence.pending_alerts` 独占 56,776（45%）。原因是 `pending_alerts` 无上限，且 commander 只在 `delivered` 时 `intelligence.acknowledge()`（commander.py:290），所以**每次超时/失败都会让告警永久积压** → prompt 变大 → 响应更慢 → 更易超时 → 积压更多（正反馈）。2026-10-10 那局 32 条告警就是这么来的。

**改动**（`arena/intelligence.py`、`arena/battle_agent.py`）：

1. `pending_alerts` 上限 **8 条**（`MAX_PENDING_ALERTS`），旧的折叠成 `pending_alerts_omitted`（total / counts_by_kind / newest_omitted_game_s / full_record 指针）——**不是静默丢弃**。
2. 告警内坐标降精度到 1 位小数（`compact_positions`）：原来带 `67.277587890625` 这类 16 位小数，单此一项省 11%。
3. 战斗 payload 去掉 `army_tactics.request_trace`（2,289 字符墙钟计时；核查无任何消费者，模型/UI/测试都不读，仅保留在实例与审计中）。`recent_feedback` 在 payload 里 12→6 条，更旧的仍进 journal。

**实测收益**（用 2026-10-10 那局真实 payload 复算）：`pending_alerts` 56,776→12,191（−79%）、`army_tactics` 7,316→3,332（−54%），**单次请求 141,599→~93,030 字符（−34%）**。输入 token 占这套总花费 95%，所以这条直接作用在成本主项上。

**验证**：425 项单测通过；新增 3 项（告警上限+折叠可见性、坐标降精度且死亡告警保留附近目击、侦察记忆）。

**侦察记忆结论（用户问过）**：主 agent **能**知道侦察兵死前看到的敌方单位。目击按 tag 持久保留在 `enemies` 中，侦察兵阵亡只从 `own` 移除，不影响情报；`public()` 继续发布为 `last_seen`（建筑或 180 秒内）/`old_unconfirmed`，带 age 区间。三条边界：① 位置只到 **12×12 格区域中心**（`area_center`），不是精确坐标；② 记忆不等于敌人存活（视野消失不等于死亡）；③ 记忆不是可打击目标——`nearest_enemy` 无可见目标时直接报错、不退回记忆。**战斗 agent 的 payload 里没有记忆位置**（只有 visible_enemies 与 known_threats 计数），所以开战斗 agent 时该情报只有主 agent 有；用户已确认这样即可。

**环境限制（重要）**：从沙箱化的助手会话启动启动器会失败——该会话看不到 `/dev/dri`，SC2 起来就因 DXVK 建不出 Vulkan instance 而秒退，表现为 `ConnectError: Failed to connect to the SC2 websocket`（2026-10-11 的 run `match-mdd_k__2` 即此原因，非配置问题）。**启动器必须在桌面终端里起**，助手只负责发配置、查日志、分析。


## 更新（2026-10-09：允许低思考主指挥与战斗 Agent 并行）

用户在对局输出窗口发现 DeepSeek 主请求 `finish_reason=length`：请求 `prompt_tokens=25843`、`completion_tokens=6000`，仍未完成 JSON。这是主指挥回复被输出上限截断，不是引擎动作拒绝。控制台原先因为双 Agent 默认高思考而禁用了思考强度下拉框，已修正。

现在 `tactics=true` 可与 `reasoning=low` 同时使用：主指挥按用户选择 high/low，战斗 Agent 始终 `thinking=disabled`、快速独立处理战场事件。删除了后端“战术模式必须 high”校验和 native_llm 启动时强制改回 high 的逻辑。控制台低思考下拉框可操作，启用战斗 Agent 后提示与实际行为一致；当前浏览器已实测 `tactics=true, reasoning=low, start_disabled=false`。选择 low 不会关闭战斗 Agent。

验证：`low_reasoning_checks.log` 中 test_launcher + test_battle_agent 26项通过；其中沙箱第一次运行的本地端口权限错误不属于代码失败，宿主复核通过。控制台服务已重载，无运行对局。若降低思考后仍遇 `finish_reason=length`，再缩短主指挥 prompt 或调整主请求 token 预算；不要把被截断回复静默当成有效决策。

## 更新（2026-10-09：高思考主指挥 + 非思考战斗 Agent，远程增援实测）

用户选择继续双 Agent，并授权结束当前局做短时三矿受袭实验。原 launcher-runtime/match-rp0t756m 已自然结束（P1负/P2胜），stop 调用明确报告没有活动对局；未杀无关进程。独占原生锁已释放。

新增 arena/battle_agent.py，存在显式 tactical_client 的 Commander --tactics 接入该类；官方战斗模型 deepseek-flash 的 thinking=disabled、1200输出token、10秒硬超时，主指挥 high。控制台默认双 Agent，启用时固定主指挥高思考；既有最慢速度/双方种族选择保留。旧 ArmyTactics 只保留为明确记录 tactical_baseline_only 的无模型测试基线，真实启动不会静默回退。不能同时开旧 defense/全局逃跑反射。

每观察帧检查己方基地26格内当前可见武装敌军或己方建筑/工人掉血；近期损伤保留4游戏秒。无威胁且未获进攻授权不轮询模型。新告警/空闲军队集合变化触发；最短1.5墙钟秒间隔，威胁中6秒复核、授权进攻时12秒复核，单请求在途。主指挥 attack_nearest/move_out 只给进攻授权；战斗 LLM 自选小队和defend/attack/hold/withdraw目标。没有脚本自动增援/战略撤退，也不写自动搜索矿区。输入包含当前己方基地坐标/血量、告警、远处小队/到各基地距离、己方可见敌军、现有命令与执行反馈；enemy_start源于己方game_info，并明确不保证仍有敌军。API失败时错误可见、旧指令继续，未造出备用方案。

不再先把援军送回主矿集结。回复根据当前目标与控制权、最多12墙钟秒/12游戏秒年龄核查；敌人移动不再按全观察签名丢弃有效防守回复。自定义Python/JSON、显式scout/order_units、运输等持有的单位不可抢用；变更控制权则整条计划明确拒绝。仅直接控制空闲机动武装单位，不能把这一版称为支持任意支援/运输单位。局部通行点只在原目标附近有界解析，并记录请求/实际坐标；能力/引擎拒绝停止重发、交回模型。实际到达是观察证据，不是防守胜利。双方中文输出窗口显示小队→基地的公开正文和完整失败信息。

最小判别 before.py/before.json（0 API）：旧层收到支援三矿回复，远处6只蟑螂仍发往主矿(22,30)，敌人x120→125就令回复过期。先证实，再改实现。

原生最终证据 ../_probe/battle_response_20261009/native.json、native.log、feedback/p1-*.jsonl（每次测试独立日志，精确文件名见该目录）；SC2 5.0.15.97579、Acropolis Normal、realtime=True、正常战争迷雾、headless。debug仅布置三座己方CC、主矿远处12枪兵与三矿附近3敌枪兵；host只命令敌方攻击，友方增援指令完全来自真实官方DeepSeek，主指挥0调用。最终7项检查通过：静默期不发API、实际三矿告警、模型选远处小队、实际抵达、基地确实掉血、引擎拒绝0、主指挥0调用。12枪兵从约65～70格外直接奔赴该基地，未绕回主矿；抵达定义是全部12兵进入基地14格防守邻域，尚未验证击退/胜负。模型0.916秒，告警→发令1.231秒，发令→首次移动0.669秒，掉血→发令0.932秒，告警→全部抵达47.571秒。实时推进15.862 loops/墙钟秒（Normal期望16），game_s/墙钟0.708（期望0.714），speed_check=ok。该比例是22.4loops定义的游戏时间，非客户端FPS。

保留失败证据：native_setup_failure.* 是敌兵创建后未等待观察、0API；native_threat_only.* 是host敌方按tag攻击收到3次NotSupported，真实模型增援/抵达成功但验收判失败，1API。修正为攻击移动坐标后最终局1API通过。合计真实API2次，少于用户说明的3次上限；所有公开请求/回复/usage在报告calls内，不保存私有推理/凭据。测试均已cleanup_complete=true，未擅自启动完整付费对局。

验证：final_full_checks.log 全套420项/35.539秒通过（不是两次执行相加）；final_checks.log 新层与控制台等98项通过。浏览器实际核对tactics=true、reasoning=high且锁定、speed=slower、无页面错误。只有一轮完整原生受控场景，不构成三族各兵种、多威胁调度、主战两模型并发延迟或胜率统计。下一局加载生效。接口与配置见 BATTLE_AGENT.md。

## 更新（2026-10-09：LLM 显式侦察与调兵）

新增 `scout`（明确派遣后搜索矿区或指定路线）、`order_units`（指定地点移动/攻击移动）、`cancel_unit_order`。禁止根据主矿清空/对局未结束自动启动搜索或改战略目标。`battlefield` 与 `spatial_reports` 给模型提供己方位置、视野覆盖、观察时间和行动报告；模型自行推断、决定与调用。合同见 SPATIAL_CONTROL.md，原始证据见 STATUS.md 最新条目和 ../_probe/llm_spatial_20261009/。本轮没有付费模型调用；原生受控执行通过不代表已验证真实模型自主搜索策略。新局加载生效。

RallyOrder 原先直接 append 动作、漏记上下文，导致开启 combat_micro 时整局崩溃，现改走 TaskQueue.issue。轨道能力检查兼容未解析的 `.spec=None`。原生实测目标被建筑占住时不可达，空间命令只允许有界靠近点并公开请求/实际坐标，绝不换战略目标。区域“部分可见”必须保留，实际到达不等于基地被清空或获胜。矿区聚类有观察集合缓存，视野仍每帧更新，避免引入每帧约6ms的重复计算。

## 更新（2026-10-10：上一局余额耗尽；账户级失败改为 fatal）

复查上一局（launcher run `match-1pjcwgsn`，10-10 00:16）发现：**比赛第 15~17 游戏分钟 DeepSeek 返回 HTTP 402 Insufficient Balance**，双方各失败 34 / 44 次（共 78 次），此后没有任何模型决策；对局又继续跑了约 2 分钟才结束。不是代码 bug，是账户余额耗尽，但代码把这种"关级拒绝"当成了瞬时错误。

本次修复（`arena/llm.py`、`arena/commander.py`、`llm_output_window.py`、`arena_launcher.py`）：

- 新增 `FATAL_HTTP_CODES=(401,402,403)` 与 `http_error()`：这些响应解码为带 `fatal=True`／`status_code` 的 `LLMError`，**不再重试**（此前 `make_player` 默认 `retries=2`，会白打 3 次）。
- `Commander._account_level_failure()` 识别关级失败，置 `client_fatal`，写 `model_credentials_failed` 事件（含 handling 说明），**并停止再发模型请求**——此前会按 6 秒节奏重试到比赛结束，刷同一条错。
- 状态可观测：`stats()` 新增 `model_decisions_available` / `model_credentials_failed`；输出窗口中文字面为"账户级失败：余额/密钥/权限，已停止请求该模型"；launcher 每个玩家显示 `model_stopped_reason`。
- 回归测试：`test_battle_agent.py::test_account_level_http_error_is_fatal_and_not_retried`（402 只尝试 1 次且 fatal 向上传递）。全套 **422 项单测通过**。

同局其他报错复核结论（**都不是本次要修的问题**）：`engine_action_rejected` 5 次（`CantFindPlacementLocation` / `CouldntReachTarget`，引擎正常拒绝）、`macro_failed` 6 次（8 游戏秒内未观测到结果，既有显式失败路径）、`plan_rejected` 7 次、`tactical_plan_rejected` 38/19 次（多数是同源 402 或被新观测取代）、`model_response_invalid` 2 次（模型 JSON 截断，与已上调的 8000 输出预算同源，待下次对局观察）。

下一步（用户待定）：两条成本杠杆尚未动用——战斗 agent 最小间隔 1.5 秒（占 69% 调用量）、主指挥 payload 与每局时长。


## 更新（2026-10-09：主指挥输出预算 6000 → 8000）

现场证据：观战窗口出现 `LLMError: Incomplete DeepSeek reply`（07:05），metadata 为 `finish_reason="length"`、`completion_tokens=6000` 且 `completion_tokens_details.reasoning_tokens=6000` —— 6000 全部消耗在推理上，正文（`response_format=json_object`）没有开始写。`arena/deepseek_planner.py` 只接受 `finish_reason=='stop'`，因此显式失败，不会把截断 JSON 当计划收下。

改动：`DeepSeekFlashPlanner` 基类 `max_tokens` 6000 → 8000（`arena/deepseek_planner.py`），并加注释说明原因。影响面：主指挥 high **与 low 都变 8000**（两者共用同一基类默认值，用户只要求改 high）；战术 384、battle agent 1200、developer 12000 均不变。请求体已实测 `max_tokens=8000`、`reasoning_effort=high`。

测试：`test_tactics.py` 的请求体回归补了断言——high 时 `max_tokens==8000` 且严格大于战术 384、小于 developer 12000。全套 420 项单测 / 34.7 秒通过。**未做真实 API 调用验证**：这次改动没有实际发一次 high 请求确认能完整产出正文，下次对局才是证据。

遗留风险（未处理，需拍板）：**timeout 仍是 40 秒**。从历史 4253 条 `request_finished` 审计看，p50=3.76s、p90=17.41s、max=60.09s（max 来自旧配置的 60 秒客户端）；也就是说这一路本来就有请求贴到 40 秒以上。输出上限抬高后，"想得更久"的请求会更容易撞到 40 秒超时，而 `LivePlanner.chat` 明确 `retries=0`（不做隐藏重试），超时等于整拍丢弃。是否连带把主指挥 timeout 提到 60 秒、或改为精简 payload（本次失败请求的 prompt_tokens=25843，缓存命中 11264 / 未命中 14579）需要显式决定，本次未动。


## 更新（2026-10-09：LLM 按建筑指定产能集结点 `rally_units`）

用户观看一局时发现：虫族 AI 知道要爆兵防守，但集结点一直设在二矿口（敌军所在处），出一个兵送一个。只读核查确认集结点完全由 Python 计算，模型只能表达 `mode/objective/min_group`，不能指定位置；诊断见 test_rally_orders.py 的四条判别用例（先写判别、后写实现）：(a) 产能 tag 只要进入 busy，DefensiveRally 就不重发；(b) 候选点全被威胁否决时 rally.tick 只记 waiting 并**保留旧集结点**（正是送兵的机制）。

新增 `tasks.rally_units`：args={producers:"all"|["hatchery#2",...], target:具名地点|[x,y], duration_s?:1..3600, reason}。落地在 arena/rally.py：`producer_inventory`（按 tag 排序的 `类型#序号`，与 site_N 同一稳定编号惯例）、`named_rally_point`（**唯一**地名解析器，不再新增第四份 home/rally/enemy_start 几何）、`RallyOrder`（每建筑下发 GeneralRallyUnits=3673、按该建筑自身 `raw.rally_targets` 确认）。被订单持有的建筑由 commander 传入 DefensiveRally 的 busy 集合，自动集结点不再覆盖；`rally_producers`/`rally_orders`/`rally_landmarks` 随每轮输入发布给模型。坐标被夹到最近可通行格心，但不做安全判断（放前线也是模型的决定）。

验证（两层都已完成）：
- 受控层：`test_rally_orders.py` 12 项通过（判别 4 项 + 队列/坐标/拒绝/缺失建筑/指挥层 busy 传递/模型输入可见性）；全套 368 项单测 / 31.2 秒通过；`test_multirace.py` 的 available_abilities 夹具补了 `ignore_resources` 参数。
- 原生层：`verify_rally_orders_live.py` 在 SC2 5.0.15.97579 通过，checks={'explicit_target_confirmed': True, 'explicit_target_survives_automatic_rally': True}，action_errors=0，cleanup_complete=true，结果 logs/rally_orders_native.json。关键现场：同一座孵化场(tag 4348444673)的 `raw.rally_targets` = [[23.5,138.5],[28.0,143.5]]，其中 [23.5,138.5] 是指定坐标、[28.0,143.5] 是矿点；而自动集结点此刻是 `landmark=safe_assembly, pos=[39.16,132.84]`（同一 base_tag）——两者不同且显式点存活，说明自动路径确实没有把集结点改回去。
- 环境要点：本机有 Radeon RX 9070(amdgpu 已加载，/sys/class/drm/renderD128 存在)，但**受限模式下 bwrap 不把 /dev/dri 带进沙箱**，沙箱内 `CapEff=0` 也无法 mknod，因此 DXVK 建不出 Vulkan instance、SC2 秒退（既有的 `verify_tactics_live.py` 同样失败）。原生层只能在 `danger-full-access`（DSH file policy 放宽后）或用户终端里跑；本机没有 lavapipe，无法靠软件 Vulkan 绕过。
- 边界（不要外推）：原生只验证了单建筑 + 坐标目标；多建筑/`all`/具名地点目标目前只有队列层(受控 fixture)证据，未在原生里逐个走过。原生也没跑双模型对局，不构成胜率或战略效果证据。


## 更新（2026-10-08：官方 DeepSeek 现场新增/修复 Python 技能，下一局启用）

用户问能否现场补技能，随后明确选择“保留当前局，下一局启用”。未重启或主动中断当前比赛。开发期间原局 official-runtime/match-krctza57 已自行结束：P1 Terran Victory / P2 Zerg Defeat，surrender=null，shutdown_signals.events=[]，cleanup_status=complete，录像该 run/match.SC2Replay。现有旧进程不支持新增入口，不能把本次实现说成已接入这一局。

先本地最小判别 ../_probe/live_skill_authoring_20261008/before.json：Queen 底层 tactical cast 已含 effect_inject_larva / 251，但原 battle schema 无开发字段、official launcher 未启用源码维护。没有先盲改引擎，没有新增原生测试局或付费模型调用。

新增 arena/live_skills.py：官方启动器默认启用，可 --without-live-skills 关闭。主模型通过顶层 develop_skill={name,unit,goal,verification}、tasks=[] 请求后台写一个新技能；此回复不能混入作战任务/JSON技能定义，旧任务继续。技能缺失由主模型判断，不自动代替其决定宏观策略。独立官方 deepseek-flash 子进程使用 high、12000输出token上限、90秒请求超时；主指挥仍 low/40秒，副官配置未改。每方每局最多3个开发任务（含修复）、每任务最多2次实际API尝试，网络/额度错误不重试；总任务240墙钟秒超时杀子进程。终局终止在途开发，不迟到安装。

开发模型收到合同、己方当前观察/局部ctx、目标、现有源码（修复时）、实际失败上下文/错误，以及首轮失败的完整测试诊断。模型只提交固定三个文件（源码/manifest/unittest），无host执行模型代码。沿用bwrap无网络/无key环境的自测、独立SkillLab验收/失败回放；queen_inject profile另查正例、低能量、已有buff、无目标、无女王、连续帧避免重复、观察buff后等待到期再注卵。普通 local_tactic 仅承诺已有通用合法性检查，不能验证任意自然语言目标的战术效果。注卵专门验收通过也不是原生引擎效果证明。

通过后由游戏主线程加载冻结源码，更新python_skill_library/control_policy，主模型收到ready后自行派遣NEW任务。旧任务持有旧冻结版本，不自动重新派遣。源码失败自动请求有界修复并阻止新调用；任务死亡/期限/引擎结果不当作已证明的源码缺陷。开发忙/预算耗尽/失败均有可见事件，不用旧行为假装修好。完整错误、阶段和ready状态接入中文双方输出窗口；请求/源码/回执在各run/pN/live-development/job-N/，developer-usage.jsonl记录开发调用用量，不记录私有推理。

已验收技能持久化至 skill-library/live-official/<race>/<skill>/ 下，以 deepseek/deepseek-flash 精确身份发布；下局启动重新验收并恢复，保留注卵profile。与原OpenCode人族来源库分开，避免虫族覆盖人族。相同种族同名技能并发发布若HEAD已变，明确拒绝旧版本发布/安装，不覆盖。已有非法库会报错，不静默退回。新增目录空命名空间无发布版本时明确提示不加载。未发布任何“伪称DeepSeek写的”测试技能到真实持久库。

验收：checks.log 198项通过/19.514秒，含16项新增和既有延迟、修复/持久化、Python隔离、三族、运营、轨道、防守、微操、生命周期、投降、prompt回归。新增实际子进程+IPC+隔离worker链使用明确mock模型回复/host-authored Queen fixture，未调用真实模型/原生游戏；不能称DeepSeek已经自行写好或真实注卵。首轮测试的fixture错误（冻结Snapshot直接赋值和预期错误层级不符）已改正，非引擎修复。下一局的模型实际开发成功率/引擎效果待观测。

official_duel.py --prepare-only --p1-race terran --p2-race zerg --game-speed slower --without-defense 通过，run=official-runtime/match-inrptzns；main_usage两边0调用，live_skill_authoring/source_repair配置true，prepare-only finally关闭空coordinator，所以run末尾development.enabled=false表示已关闭准备过程，不是配置关闭。未改当前run指针/未开启新局。证据 ../_probe/live_skill_authoring_20261008/prepare.log 和 run.json；汇总 logs/live_skill_authoring_summary.json。



## 当前观战局（2026-10-08：官方DeepSeek TvZ，最慢速度，副官关闭）

用户要求启动人族对虫族供观看。已启动 official-runtime/match-krctza57，原生 native-runtime/match-kwvxhu_f；独立后台launcher/session65483，中文输出窗口65555，原生游戏PID65822/65828。宿主逐一确认存活。日志 logs/official_tvz_watch-gw33lms0.log，启动证据 logs/deepseek_official_tvz_started.json 和该run/started.json；当前指针logs/deepseek_official_duel_current.json。保留供观看，未等待终局/重启。

P1 Terran / P2 Zerg，双方直连官方deepseek-flash、同DEEPSEEK_API_KEY、https://api.deepseek.com/v1，各自独立上下文。realtimeTrue、Slower、normalfog、90分钟墙钟、60FPS/VSync配置；防守副官OFF、旧全局反射OFF、combat_microON（适用于明确任务的人族生化/坦克）。无debug资源或单位注入、无固定脚本开局。新开局手册/macro_plan/预算/运营反馈已实际出现在双方请求，两边均已选择economic计划；只证明请求/计划接通，不保证策略优良或完整比赛已验证。

official_duel.py新增--p1-race/--p2-race并明确传给native_llm。启动前读取持久库确认全部是Marine脚本；P1复用已验收DeepSeek来源的stim_push版本68a5e4...，P2明确使用本族宏指令、科技/经济/作战接口，不加载人族专用Python，没有虫族自定义微操库，不是静默回退。report记录player_skill_modes，P2prepared_strategy也明确能力边界，使用本族opening_playbook。先prepare-only的match-x0waw_kl本地恢复/隔离验收通过，0模型请求/无游戏；正式局重新验收后才发车。没有修改持久库版本或伪造模型技能作者。

启动截面 accepted_plans=[12,14]、planner_errors=[0,0]、引擎拒绝0；task failed=[0,2]，不是全程零错。P2初始train_worker/drone和build_supply/overlord都发生“macro order was not observed within 8 seconds”，原始logs/feedback/p2-vfvb0rzc.jsonl保留macro_failed及task_status。随后真实观察Drone从8到12、Overlord从1到3，说明生产实际发生；确认路径问题尚未排查修复，不应把错误说成引擎拒绝或生产完全失效，也不能假称已修。已向用户明确说明。本场正常执行继续供观看；尚无自然胜负、完整造兵链/虫族全部技能验证。

原生初始观察仍双方8工人，本场未尝试升级安装或修改地图开局。最后速度窗口约9.53loops/s、游戏/墙钟0.4255，期望9.6/0.42857，speed_check=ok；这是模拟速度不是显示FPS。观看仍是两个原生玩家视角窗口和中文正文窗口，不是第三观察席位。


## 更新（2026-10-08：模型开局手册、扩张预算与运营反馈）

新增 arena/economy.py，模型收到本族经济/限时压制/防守三种条件式开局；没有自动固定开局。macro_plan(count=1) 明确 opening、target_bases、worker_target、expand_by_game_s、reserve_expansion、reserve_for_s(30..180)、exempt_products(<=8)、reason，全部必填，保持到模型修改；每回复最多一个，整条计划校验失败不部分应用。数据在 economy_report 中每轮独立于历史窗口/反馈分页发送。旧无tech_tree后端明确不支持、system不诱导调用；赛前实验室GAME_KNOWLEDGE和中文观战窗口也接入。文档SC2_GAME_KNOWLEDGE.md及AI_SKILL_MANUAL.md末尾说明合同和证据边界。

预留仅服务显式expand任务，采用本场tech_tree配方造价；第一个未发出的expand优先于可选支出，宏/JSON组合生产只能使用未预留的矿气。低于阶段工人数目标的工人、<=4余量的补给、模型指定产品可例外使用资金，故不保证任何时候都最快开矿。没有expand不存钱且可见告警；配方费用不唯一明确报告unsupported；预算到期、任务失败/取消、基地数量开工目标达成/发单后释放，未静默回退。允许cancel_expansion撤回未发出的扩张；已发出请求明确拒绝取消（不能虚称原生建筑被取消）。解除预算不撤销已有订单；续期需模型重新发计划。过期预算仍保留目标和旧expand任务。改计划在队列满时仍允许，迟到预算/取消指令需新观察而拒绝复用。

记录初次观察工人/经济、当前饱和度与目标进度、第二座基地首次观察开工/完工、采样卡人口/空闲工人秒/空闲基地秒/训练基地秒、请求支出分类、每30游戏秒检查点。观测间隔>2游戏秒部分计为未采样；虫族幼虫利用率为null，发单成本可包含拒绝/重试而非已扣款，两个基地不代表两个独立有效矿区。内存检查点20条/prompt6条，支出明细12条。玩家之间实例独立，固定手册在缓存前缀、动态经济报告在后缀。只因模型已有明确进攻任务的微操/副官设置未改。

先做旧队列最小判别，../_probe/macro_opening_20261008/before.json；后续同一受控场景comparison.json：300矿无预算先Marine能力560、二矿等钱；显式预算后先CC能力318。不是原生等额资源对抗。prompt_sample.json为生产payload格式的合成样本，progress_fixture.json为合成观察里程碑，并非原生建造时长。

最终 checks_latest.log：217项回归全通过，7.840秒（25项新增运营检查加既有系统；中间checks.log216项/8.025秒、checks_final.log66项为重叠，不相加）。覆盖同资金优先级、实时配方变400的适应、跨观察存钱、到期/取消/失败、必要生产例外、目标/饱和度/采样空档、schema/实际Commander请求和中文Feed、旧接口兼容，以及三族、Python隔离、微操、副官、轨道、生命周期等。无付费API测试。

旧观战 match-uj0ytzbm 在开发过程中自然结束（P1 Defeat/P2 Victory），未主动终止或自动重开。宿主确认原生锁和进程均释放后进行单玩家原生判别：native-runtime/match-lrpr6s1j、5.0.15.97579、Slower/realtimeTrue/normalfog/无画面。未注入前初始观察game8.5s仍8SCV、原始food_workers=8、实时CC配方300矿；用户告知今天更新为12工人，但本项目API运行环境本次没有观察到12，差异原因未定位，不能宣称已同步最新版。代码不写死8或12，也不把缓存造价当本场造价。

随后受控debug资源及友方SCV侦察视野（无敌方/全图揭示）后，用生产TaskQueue和显式宏计划实际建起第二座CC，观察progress=0.010625，0引擎拒绝；只验证开工，没等待建成，不是AI自然选开局/自主侦察/胜率。native_probe.json/log及native_start.json保留全部原始证据。测试结束关闭引擎，cleanup.json宿主进程0、锁空闲。0新增模型API测试；未修改USB、未升级pysc2/原生引擎/地图或重开付费对局。下一次启动加载新机制。

汇总 logs/macro_opening_summary.json；所有本轮探针在项目外 ../_probe/macro_opening_20261008/。


## 当前观战局（2026-10-08：官方DeepSeek双方，副官关闭，最慢速度）

用户要求开局，双方沿用官方deepseek-flash、https://api.deepseek.com/v1、DEEPSEEK_API_KEY；无OpenCode生成调用。运行 match-uj0ytzbm，原生 match-gyrqnnzw；独立后台launcher 55998/session同PID、中文输出窗口 56140、原生游戏 [56381, 56387]。命令 official_duel.py --seconds 5400 --game-speed slower --without-defense；防守副官不存在、不产生副官请求。仍保留combat_micro（明确进攻任务中的兴奋剂/坦克形态）、轨道能力、持久Python技能；旧全局战斗反射OFF。正常迷雾/realtimeTrue/60FPS与VSync配置/90分钟墙钟上限。无固定脚本开局或debug注入。保持本局供用户观看，未等待终局。

最慢档新增为slower（Galaxy c_gameSpeedSlower），AcropolisSlower.SC2Map单改MapScript.galaxy，原图和USB未动。先项目外原生无模型判别：20.0497墙钟秒191loops=9.5263loops/s，证据 ../_probe/slower_watch_20261008/speed_slower.json；探针复用旧faster期望所以该探针report的speed_check=DRIFT，是预期比较结果，并非把旧速度标签当新配置。生产随后接入9.6loops/s期望、地图hash检查、启动8秒双方逐一速度校验与最慢中文标签。正式本局校验双方9.59165loops/s；最新窗口110.57墙钟秒平均9.5230loops/s，speed_check=ok。此为模拟速度，不是画面FPS测量。

开局已接收双方真实计划[8, 8]，规划错误[0, 0]，引擎拒绝0，仅是启动截面；未宣称自然全局无错/胜率。19项速度/生命周期/配置回归通过（checks.log），未额外花费模型连通性测试。官方launcher报告/日志明确defense=OFF与game_speed=slower，避免“thinking disabled”误表示副官关闭。底层支持slower/normal/faster；通用CLI默认normal和official默认带副官未全局更改，本次及后续按用户最新偏好明确传上述参数。

启动证据 logs/deepseek_slower_no_defense_started.json、本run/started.json；原始日志 /home/tyrion/Projects/wolf/sc2-arena/logs/official_slower_no_defense-i0sm2hmp.log；实时状态看本run/match.json，当前指针logs/deepseek_official_duel_current.json。副本构建器支持--speed slower并读回对比只有脚本改变；数据hash与判别探针完全一致。双原生玩家窗口及独立中文输出已启动，仍不是第三观察者席位。


## 更新（2026-10-07：轨道能力补齐；下一局普通速度）

用户选择保留当前局、下一局启用。核验时旧match-7i4qyg4y的launcher/viewer/游戏进程已不存在；宿主boot_epoch=1791384097晚于旧match.json mtime=1791383659.778。没有主动关闭旧局、没有开新的付费观战局，未把历史running当作仍存活。本轮无付费模型调用；无画面原生测试均已关闭，宿主项目游戏进程0、native-match.lock空闲。

旧局不是双方都未升级：P2 game261.47秒要求orbital_command，最后game569.9样本已有完成的OrbitalCommand；P1仍是两个CommandCenter。P2理由还提了矿骡和扫描，但旧高层schema/TaskQueue拒绝scan/call_mule/supply_drop，world.bases也没有能量。先用真实旧日志与合成接口判别，证据../_probe/orbital_speed_20261007/interface_before.json；未把合成检查称为原生实测。

新增arena/orbital.py：scan(target=home|expansion|enemy_start|nearest_enemy)、call_mule(base=home|expansion)、supply_drop(base=home|expansion)，outer count=1，三者共享每次50能量。与生产TaskQueue、格式schema、迟到计划拒绝、反馈、中文输出窗口相连；world.bases显示energy/energy_max/orbital_charges，原始观察读当前effects；Python技术目录也输出建筑spells。升级仍用build_structure、由LLM决定，提示明确Barracks→Orbital经济/侦察、EngineeringBay→Planetary地面防守且无扫描矿骡，以及工人生产占用升级/预留扫描能量的取舍。

执行不偷看迷雾、不借用自定义技能拥有的基地；扫描仅到明确地标，可扫描敌方已知出生地；缺分矿/目标不退回主矿。先查原生可用能力，再发指令，之后必须同时观察到能量消耗与效果6/新MULE268/补给buff25才完成。施法期间保留施法者避免重复消耗，同一补给站不能并行重复投放；能量不足等待，scan30游戏秒、其他120秒过期可见失败；已发而8游戏秒无效果失败且不盲目重放。MULE任务仅承诺召唤确认，不假称已采到矿。可与SCV训练共存。

真实SC2 5.0.15.97579、normal fog、realtime=True、无画面/受控debug注入资源和前置建筑/固定测试指令：native_before.json原版更快速度升级Orbital约25.15墙钟秒，确扣150矿；扫描从无可见敌軍变为看见敌基地，MULE独立采回25矿，补给34→42，Planetary建成且ground_dps20/energy_max0，原生拒绝0。native_after.json改走生产TaskQueue，在普通速度升级约35.44墙钟秒；scan显现注入的DarkTemplar（raw cloak=2），三项orbital_confirmed；同一轨道基地施放MULE时仍保留SCV训练订单524；第一笔MULE收入时新SCV尚未产完，不能据此说训练已完成。103.424墙钟秒推进73.214标准游戏秒、15.857loops/s；原生拒绝0。不是自然LLM选技能效果/完整胜率测试。Planetary本机绑定价格为250矿150气，测试末资源受持续采矿影响，不能用首尾差反推费用。

普通速度：官方API实时默认Faster且RequestCreateGame无speed参数；在Acropolis副本MapScript.galaxy的MeleeInitOptions后设置GameSetSpeedValue(c_gameSpeedNormal)并锁定。StormLib v9.30读回比较，地图所列文件只有MapScript.galaxy内容改变；原地图和用户USB未改。副本engine/StarCraftII/Maps/Arena/AcropolisNormal.SC2Map、配套SHA256清单和Galaxy源码。可复现构建器arena/build_speed_map.py（显式传本地StormLib，不自动下载/回退）；探针rebuild_check验证同样只改该条目。生产arena/game_speed.py检查原图/副本hash，Normal只支持已验证Acropolis，其他地图明确拒绝，可显式--game-speed faster。

native_llm/official_duel/opencode_match/session_match的CLI默认--game-speed normal，NativeArena底层默认faster保留现有专用调用兼容。Normal启动后8墙钟秒逐玩家量测，非16loops/s附近直接报错关闭，不偷偷改Faster或RequestStep。GameClock按期望16/22.4检查，保留原loop/22.4计时合同，报告实际loops/s和期望比例；UI显示普通速度。90分钟墙钟上限与60FPS/VSync呈现配置不变，未把API轮询频率说成画面FPS。

速度证据：speed_normal.json单玩家20.052墙钟秒/318loops=15.859loops/s；原版baseline=22.301loops/s。speed_dual.json双API玩家（无模型）启动校验15.855/15.979loops/s，后续20.064秒双方均15.850loops/s、speed_check=ok。不是仅单玩家推断双玩家，也不是用sleep降API调用冒充游戏减速。

测试：checks_final.log宿主175项通过/5.365秒；最后加目标预留/自定义所有权，checks_orbital_latest.log18项通过/0.113秒；有重叠不相加。viewer_events.json验证生产Feed可读实际6条轨道事件，未声称新窗口截图验收。总证据logs/orbital_normal_speed_summary.json。
保留失败：native_before.log首个双客户端启动发生SC2“Process has write access”目录锁崩溃，后续单客户端及最终双客户端正常，未假称修复所有原生启动竞争；checks_final_attempt1.log沙箱bwrap NETLINK受限和测试依赖缺失，移除未安装的测试依赖并在宿主跑；native_after_attempt1.*探针把SCV训练50矿支出混入MULE收入基准，纠正到训练扣款后重跑。未静默掩盖。

参考协议 https://github.com/Blizzard/s2client-proto/blob/master/docs/protocol.md#game-speed ；游戏Galaxy原始声明 https://github.com/SC2Mapster/SC2GameData/blob/master/mods/core.sc2mod/base.sc2data/TriggerLibs/natives.galaxy 。


## 当前观战局（2026-10-07：关闭残留后独立后台重开）

用户明确要求关旧局并开新局。旧match-wt_2tct6已通过API请求退出并对仍存活的精确旧游戏PID发送SIGTERM，旧游戏314767/314773和输出314613均退出；无广泛kill、用户U盘未改。关闭前成功保存68932字节 interrupted_before_close.SC2Replay，仅为孤立旧局关闭前录像，不算自然终局。清理证据 logs/official_micro_stopped_by_user.json。

新局 match-7i4qyg4y，原生 match-onkqe8tq；official_duel.py --seconds 5400，独立后台PID328415/session328415，输出窗口PID328557，游戏PID[328732, 328738]。不再依赖长时间exec会话，stdin为DEVNULL、stdout/stderr写 /home/tyrion/Projects/wolf/sc2-arena/logs/official_detached_watch-wiket9r_.log。先以3秒无API短进程确认原启动命令退出后独立会话仍完成，证据 ../_probe/match_stale_20261007/detach_check.json；新局宿主核验父进程已非短启动命令。之前SIGTERM发送者仍未知，不能据此宣称所有外部终止已根治。

双方仍官方 deepseek-flash / DEEPSEEK_API_KEY，防守副官+兴奋剂/坦克微操启用，旧全局反射OFF，正常迷雾/realtimeTrue/90分钟/60FPS配置、无固定脚本开局或debug注入，加载上次修复的原子终态保存与重复信号清理；无OpenCode调用，无付费源码修复。开局截面双方接受计划[4, 4]，规划错误[0, 0]，引擎拒绝0，游戏/墙钟1.01989倍；未等待终局，继续供用户观看。logs/deepseek_official_detached_started.json 和本run/started.json保留证据，实时看match.json，当前指针 logs/deepseek_official_duel_current.json。开启的是双玩家视角原生窗口，尚未实现第三观察者。


## 当前状态（2026-10-07：控制进程中断，旧状态误显示运行中已修）

match-wt_2tct6 在最后实际回复game1038.705秒（17:18.7）附近收到SIGTERM；原始 logs/official_micro_watch-3wztt6fk.log 第一异常在arena.step/observe，第二次SIGTERM又打断Commander.stop/Defense.close。native_llm旧finally因而跳过P2停止、引擎关闭、录像与match.json收尾；official_duel的run.json已interrupted/KeyboardInterrupt: stop signal，match.json仍是game1030.8秒的running。不能称为模型卡住、额度耗尽或90分钟到时；信号发送者未记录，来源未知，不能将修复状态保存漏洞说成已根治外部终止。

宿主PID核验 ../_probe/match_stale_20261007/before.json：launcher314471消失，中文窗口314613和SC2进程314767/314773仍存活；项目锁已释放，因此以后不能只据锁空闲宣称无残留游戏。SC2 comm为Play Main Threa，须按实际/proc命令行匹配，不能按SC2_x64简称判断。当前未关残留进程、未重开、未继续调用API。原始match.json保留为本run/match_before_interrupt_status_repair.json，已按run日志+宿主事实纠正本run/match.json为interrupted，附来源与纠正时间，窗口显示停止。未伪造终局或保存成功的录像。

修改：arena/lifecycle.py提供原子JSON写入、首次信号中断/后续信号仅记录（清理不中途重入）、信号名/时间记录但sender仍未知。native_llm在清理前先保存终止状态，各清理步骤独立留错并继续，末尾再次保存、恢复原信号处理器；official_duel的run.json也原子写。输出窗口交叉核对run.json终态，25秒过期运行报告显示状态失联，不继续称实时；自然胜负字典也修正为可渲染，避免旧版.get(dict)异常。现有窗口读取已纠正的match.json即可显示停止，新版完整读状态机制下次加载生效。

最小判别先执行旧生产native_llm+假引擎/假client、两次真实self SIGTERM，复现仅stop_0且saved_outcome=running（reproduce_before.json）；不是自然SC2实验。修后checks_final.log共72项通过/3.056秒，其中真实两次SIGTERM验证终态先落盘、双方清理及引擎关闭均到达；另覆盖清理异常、原子写失败、失联/终局显示及副官/微操/投降回归。stopped_viewer.png为新窗口离屏读取本场真实日志的截图，已检查显示“对局已停止”与原始错误。汇总 logs/match_stale_diagnosis.json；所有本轮验证无付费API、无新游戏。

附本场实际微操结果（截至日志停止，不是胜率）：P1确认{'stim': 89, 'siege': 17, 'unsiege': 14}；P2确认{'stim': 61, 'siege': 13, 'unsiege': 10}，均来自combat_micro_confirmed原始事件。本场已有真实坦克形态与兴奋剂效果，但未自然结束，不记胜负。


## 当前观战局（2026-10-07：官方DeepSeek双方，新微操已启用）

用户要求修改后重开，已启动 official_duel.py --seconds 5400。运行目录 match-wt_2tct6，launcher PID314471、exec session99302，原生 match-eu599bhh，游戏PID314767/314773，中文窗口PID314613。日志 logs/official_micro_watch-3wztt6fk.log，启动证据 logs/deepseek_official_micro_started.json 和该run/started.json。双方直接官方deepseek-flash、DEEPSEEK_API_KEY，无OpenCode生成请求；同版已验收技能正常恢复。保持realtime=True、正常迷雾、60fps/VSync配置、90分钟上限、无固定脚本开局/无debug注入。双方combat_micro与defense实际开启，原全局反射OFF，后台源码改写未启用。

启动截面双方已接收真实计划 [5, 5]，规划错误 [0, 0]，引擎拒绝0；游戏/墙钟1.01007倍。仍是早期开局，尚不宣称自然比赛的兴奋剂/坦克实效或胜率。本轮不等待终局，保留双方原生窗口和中文输出让用户观看；实时状态看本run/match.json。宿主核验launcher/viewer及两个SC2实际命令行存活；SC2的comm为Play Main Threa，首次按sc2_x64进程名匹配为空不能当作没有游戏，已用/proc/cmdline纠正。无额外生成连通性测试。


## 最新修复（2026-10-07：普通作战与副官补齐兴奋剂/坦克形态）

原观战局 match-k9q3snxs 已自然结束，P1 Defeat/P2 Victory；游戏1238.04秒，墙钟1213.08秒。未重开观战局。下一次 official_duel.py 默认 combat_micro=True，可 --without-combat-micro 显式关闭；其他 native_llm.py 调用需加 --combat-micro。realtime=True、正常迷雾、90分钟/60FPS配置、旧全局反射OFF保持，本轮付费模型调用0。

先验诊断 ../_probe/combat_micro_20261007/before.json：真实日志 logs/feedback/p1-btusgvlm.jsonl、p2-ukcli9uw.jsonl。P1无stim/morph计划；P2末期20辆移动形态坦克，没有架起计划，但曾在game732.23秒（12:12）实际给8Marine发stim，后续观察7个存活成员buff27，不能说从未打针。P2四次stim_push派遣分别有无可见敌人、50ms worker超时、成员损失、50ms超时，源码控制路径不稳定；主要使用attack_nearest/move_out，而这两种基础命令与旧副官均只会移动/攻击/停止。合成最小判别使用已研究stim的Marine+Tank，旧副官仅发两个3674攻击命令。

新增 arena/combat_micro.py，只服务有明确move_out/attack_nearest目标或副官授权的部队，不创造战略目标、不因伤兵全队撤退、不接管活跃Python/JSON/形态任务。Marine/Marauder研究完成、健康且近敌时个体打针；坦克遇地面目标/到达部署点架起，继续推进或副官撤后时先收起再走，失联有5秒滞后。先查真实可用能力，确认buff24/27或类型改变且变形订单消失，失败/不可用/交接未确认均有公开事件。主/副模型prompt明确能力与边界，独立中文窗口显示请求与实际结果。没有修改旧持久技能版本或宣称其全部问题已修。

最终证据 logs/combat_micro_summary.json；项目外 ../_probe/combat_micro_20261007/native.json、native.log、native-feedback/p1-fbs3fdxc.jsonl。真实原生SC2 5.0.15.97579、无画面、debug注入单位/升级、固定测试指挥、正常迷雾、realtime=True：5次能力指令，4次实际确认（Marine27、Marauder24、坦克架起、完全架起后收到主指挥move_out并收起），1次副官交还控制权前的收起未确认并明确记录；引擎拒绝0，能力失败0。坦克收起后前进3.183格，18.75游戏秒/18.370墙钟秒=1.02066倍。不是自然完整比赛/胜率/画面FPS/真实API延迟测试。checks_final.log：202项回归全过（18.309秒），含23项新增微操、原副官/三族/宏/反馈/Python隔离/修复/持久库。viewer_events.json仅为生产Feed读取真实10条微操事件，未声称新截图验收。cleanup.json在宿主确认SC2进程0、原生锁释放。

失败证据保留：native_attempt1为debug注入后立即读取尚未出现单位，探针改为等实际观察；native_defense_phase是旧“类型变化就确认”的较弱验证；native_attempt3发现3秒确认窗偏短，真实变形订单约3.17秒才结束，生产改为坦克6游戏秒/兴奋剂3游戏秒并加长变形回归。没有删失败或只看类型就宣称完整变形成功。自定义Python的50ms超时、成员损失及原技能审计问题仍独立待办。


## 当前对局（2026-10-07：官方 DeepSeek 双方 + 防守副官，90分钟）

用户明确要求启动，已运行 official_duel.py --seconds 5400。运行目录 match-k9q3snxs，launcher PID 306613、exec session23400；原生目录 match-j0jo0eui，日志 /home/tyrion/Projects/wolf/sc2-arena/logs/official_defense_watch-x4pmumq7.log，独立中文正文窗口 PID 306756。双方直接用 DEEPSEEK_API_KEY、https://api.deepseek.com/v1、模型deepseek-flash；无OpenCode消费。各自主指挥low、防守副官thinking disabled，后者只在基地警报触发；实时、正常迷雾、60fps/VSync配置及90分钟上限保留。沿用同一已验收持久技能版本68a5e4ff7765…，未声称修复旧技能审计问题，未启用付费后台源码改写。

启动证据 logs/deepseek_official_defense_started.json 及运行目录started.json：双方已接收真实主指挥计划，规划/引擎错误均0；防守副官已启用，启动截面尚未遇敌所以请求0，不冒称真实防守LLM时延已测。运行留给用户观看；本轮未等待终局。实时结果应读当前match.json，run.json在过程中仍标starting_native_game。


## 对局时限更新（2026-10-07）

用户要求每局上限90分钟。official_duel.py、opencode_match.py、native_llm.py、session_match.py 的默认 --seconds 统一为5400（开局完成后的墙钟秒）；自然胜负仍提前结束，技能/API请求/赛前准备超时不受此改动影响。原生对局报告新增 max_wall_seconds 记录实际参数。AI_SKILL_MANUAL 的启动示例已同步。静态编译及提取实际参数定义后的默认解析检查通过，证据 logs/match_duration_90min_check.json；没有启动游戏或调用API。旧对局归档脚本/历史日志保持原始值。


## 已实现待下一场启用（2026-10-07：主指挥 + 独立防守副官）

用户同意实现，但明确选择“保留当前局，下一场启用”。未中止旧局；旧官方对局 match-ff_2t4h4 已自行结束（P1 Defeat / P2 Victory），未自动开新观战局。旧文档“当前运行”为历史。

新增 `arena/defense.py` 和可复用 `official_duel.py`。下一场用 `./run_sc2.sh .venv311/bin/python official_duel.py`，默认官方 deepseek-flash 双方各一名主指挥（low、25游戏秒）+独立防守副官（thinking disabled、384输出tokens、3游戏秒最短间隔、单在途、I/O timeout8秒，回复超过6游戏/墙钟秒拒绝）。局部守备脚本先响应基地警报，模型随后选接战/守位/撤后；无警报不轮询付费模型。实时推进/正常迷雾/60帧配置不变，旧全局战斗 ReflexNet 仍OFF。独立正文窗口已显示副官及公开错误。

冲突 Python 任务整体暂停、关闭旧worker并丢弃迟到动作，冻结期限；交还后保留state，用新worker和新观察继续。缺员明确要求重新派遣而非自动修代码。旧 Stop 和主指挥过期战术不能覆盖副官。只借用基地附近45格可用战斗单位，通常4–12、整队最多32；不借工人、不偷生产/JSON工作流、不指挥架起或埋地单位移动；当前集中防守一个基地。旧技能审计问题没有一并修复。详情 AI_SKILL_MANUAL.md 末节。

证据目录 `../_probe/defense_20261007/`：before.json 合成判别证明原有忙碌主模型无回防动作；checks_final.log 179项通过（19.358秒），包括原宏/三族/Python隔离/修复/持久库回归。isolated_resume.json 为真实隔离worker+合成观察，旧PID303376→新PID303380，state ticks1→2。native.json/native.log 为真实SC2 5.0.15受控无画面debug注入测试：主模型仍等待，首次可见警报同轮发回防，4次固定测试副官回复、0引擎拒绝，18.084墙钟秒/18.438游戏秒=1.01957倍；全体Marine最大位移8.23格。native_owned.* 补充单独记录实际分配单位的位移。不是自然比赛胜率，不是实际DeepSeek延迟或画面FPS。viewer.png 为离屏中文窗口预览。prepare.log 已成功本地恢复双方同版持久技能，prepare-only运行目录match-r__5nmsb；0付费请求，未切持久库HEAD。

回归初次 checks.log 的179项中仅撤退滞后测试失败：3秒经22.4Hz取整不足3秒，未产生请求；将采样点改为3.1秒后全过。未放宽生产限流。无静默回退；模型/API/格式/过期失败都有独立事件。本轮没有新增付费API请求，真实非思考副官响应速度与自然防守胜率需下一局记录。
补充验收：native_owned.json 单独确认4个实际被接管Marine分别移动7.73/8.72/7.04/7.68格，0引擎拒绝，游戏/墙钟1.01956倍；无画面引擎已关闭，native-match锁释放。最后增加“无可分配机动部队”显式事件和状态，checks_no_units.log 21项通过（与179项重叠，不相加）。汇总 logs/defense_adjutant_summary.json。


## 当前运行（2026-10-07：官方 DeepSeek V4.1 Flash 自我对战）

用户要求双方都用其DeepSeek官方key。已启动 `official-runtime/match-ff_2t4h4/run.py`（进程会话45417），真实模型`deepseek-flash`，端点`https://api.deepseek.com/v1`，两侧同一DEEPSEEK_API_KEY、独立client/观察/状态；没有OpenCode Go消费、无mock。免费GET models鉴权成功后直接开局，没有额外生成连通性探针。双方沿用DeepSeek已有Go作者库版本68a5e4ff7765…，各自复制冻结并本地重跑自测/宿主检查；不改作者来源，不假称审计问题已修复。本局未接后台源码改写服务（已向用户说明），宏/任务执行仍由LLM决策，全局战斗反射OFF、realtime=True、无固定脚本开局、无debug注入。最长1800墙钟秒或自然结束，保留运行供用户观看。

日志`official-runtime/match-ff_2t4h4/match.log`，周期状态`match.json`，启动证据`started.json`；原生引擎`native-runtime/match-fu9gsh0y`，独立中文输出窗口PID295189，日志output-window.log。真实双方计划已接收，初30墙钟秒各5计划、规划/引擎错误0，游戏/墙钟1.0213×；不是全局无错误或终局证明。当前状态入口`logs/deepseek_official_duel_current.json`。

60FPS/VSync新配置首次在原生游戏验证：DXVK effective configuration显示d3d9.presentInterval=1/maxFrameRate=60，present mode=FIFO；游戏59秒时截图P1 HUD59.9fps、P2 59.8fps，`window-b.png`/`window-a.png`，配置日志另存display-startup-d3d9.log。这是两张瞬时显示样本，不能当持续稳定帧率/与旧图的严格A/B。native_llm.py共享凭据提示改为识别provider，直接官方client不再被误称OpenCode登录。

## 技能审计（2026-10-07：本地复现，未修改模型技能/HEAD）

用户要求检查可改进之处。审计五套持久HEAD：DeepSeek Flash stim_push、Kimi stim_push、Luna steady_marine、Qwen Flash skirmish_push、Qwen Max marine_push；均为Marine小队技能，宏经济/三族单位API属于另一层。证据全部在 `../_probe/skill_review_20261007/`，`review.py/review.log/results.json` 为真实隔离worker中的合成场景和实战失败输入重放，`self_tests.py/self_tests.log/existing_self_tests.json` 为复制快照上的原自测，`partial_loss.json` 为stub worker的TaskQueue fixture。没有开游戏、调用付费模型、修改源码/持久库或切换HEAD。以下不能冒充新整局实测。

- 五套原自测共66项全部通过（DeepSeek13、Luna8、Kimi10、Qwen Flash24、Qwen Max11）；它们没有阻止以下行为，有的甚至明确断言伤兵阻止恢复，所以改进需补战略行为验收，不能只追求原测试全绿。
- 五套：8Marine中1个10HP、无敌军，整队撤退；90游戏秒后到家且血量未恢复仍running/撤退。建议个体退后与整队撤退分开，持续无进展应交回指挥官，不能无限等待恢复。
- 五套：4Marine遇仅有400矿无武器CommandCenter，估值比2.0导致撤退；4Marine+旁边20友军面对8敌Marine仍按4对8撤退。已有ground_dps/air_dps/attack_range/allies未被利用。应按攻击能力/射程/友军支援估计威胁，不能按所有可见敌方资产总价决定撤退。
- Kimi/DeepSeek： `_has_stim_buff` 仅检查字符串，而接口为数字buff（本机pysc2 Stimpack=27、Marauder=24）。已带27的小队仍输出stim，真实生产validate_result在合成观察上拒绝 `stim requires researched, unstimmed Marine with >10 HP`。需数字ID/明确类型合同与重新派遣携buff回归。
- 除Luna外四套：只有MOVE(16)到objective也因为order_positions相同而被称为已有attack_move，actions为空。命令去重应同时比对订单类型与目标。
- 宿主：2分配Marine掉1个，TaskQueue立即failed并向活着的那个发Stop(4)，详见partial_loss.json；不是声称实战该场出现。建议可配置允许剩余分配成员继续，仍不得挪用未分配兵，并向模型报告损失。
- 上场Kimi task39确实于game313.71秒发生50ms worker wall-time超时、被Stop，随后开启付费后台修复但遭周额度拒绝。相同输入10单位/0敌军在当前空闲机器隔离重放100次无错，均值0.307ms、P95 0.379ms、max0.461ms；这不证明原场超时原因。应先区分IPC/调度迟到与源码异常，不能仅据一次wall-time超时认定脚本需重写；增加cpu/wall诊断与本地重放后再决定模型修复。

建议顺序：先修数值buff/订单识别/伤亡与超时的公共合同和检查，再更新共享战术参考并按原持久库验收流程发布新版本，之后补坦克阵地、医疗运输支援、侦察撤离。任何当前HEAD仍为原模型验收版本，本次没有假称修复已生效。

## 最新状态（2026-10-07：用户因 OpenCode 周额度耗尽关闭对局）

`match-glwspbli` 已按用户要求中止，双方原生游戏及独立正文窗口退出，原生锁释放；`run.json` 为 interrupted。证据 `logs/kimi_deepseek_go_stopped.json`，原始错误在 `logs/kimi_deepseek_go_watch.log`：weekly usage limit reached。没有重新开局或新增验证API请求，不据中止局宣称胜负。下文“当前运行”为历史记录。

用户另要求垂直同步+60 FPS上限。最小检查证实实际SC2为DXVK D3D9（`native-runtime/logs/SC2_x64_d3d9.log`），原present mode为IMMEDIATE。`arena/native_runtime.py` 启动时追加 `d3d9.presentInterval=1;d3d9.maxFrameRate=60;dxgi.syncInterval=1;dxgi.maxFrameRate=60` 到DXVK_CONFIG，保留其他继承选项并打印NATIVE_DISPLAY。DXGI选项兼容日后渲染器改变；不改realtime/游戏推进/API轮询。依据DXVK官方配置 https://github.com/doitsujin/dxvk/blob/master/dxvk.conf ，本机DXVK DLL含这些配置项。`logs/native_display_60_config_check.json` 仅验证启动环境传递/覆盖与可见日志（启动子进程mock），没有重新启动游戏、没有实测新FPS。下一场需核验DXVK effective configuration、present mode及HUD，不能提前宣称已稳定60帧。用户U盘、显示器刷新率及全局配置未改。

## 当前运行（2026-10-07：Kimi K3 对 DeepSeek V4.1 Flash，双方 OpenCode Go）

用户已把工作区Regions改为Global；准确模型 `opencode-go/kimi-k3` 与 `opencode-go/deepseek-v4.1-flash` 的真实连接均成功，未使用DeepSeek官方key或替换型号。正常观战对局 `opencode-runtime/match-glwspbli`，原生 `native-runtime/match-9no79iws`；日志 `logs/kimi_deepseek_go_watch.log`，启动证据 `logs/kimi_deepseek_go_started.json`。两个原生GPU玩家窗口和独立中文正文窗口已启动，后者Noto CJK加载成功。保持realtime=True、正常迷雾、全局战斗反射OFF、后台Python修复/持久技能库ON，无固定脚本开局/无debug资源单位注入。对局留给用户观看，最长1800墙钟秒；未等待终局，不能写胜负/胜率或显示FPS。

用户要求现有最好技能，双方从同一份已验收Kimi stim_push源码与测试起步；基于功能/验收/实战记录选取，未宣称统计最强。两位实际通过实验室工具读取源码、跑测试和宿主检查。Kimi原样保留，10项自测通过，准备26.16秒；DeepSeek发现运输内成员不应误判小队损失，自己改源码并增加3项回归，先有1项失败，随后自己修复，最终13项全过，准备97.08秒。各自改进均已持久化，保留共享来源；最终源码不相同（共同起点，允许自主修复）。Kimi技能sha=89796c908175725c99785a6944455a0eb0ef33a6b53165e65c1885794e53ce7c；DeepSeek=a1b66fd78b595a4494912d534d174b4e2c9679a73d26909ada68c59ec1623524。

真实Python派遣演练Kimi首轮通过；DeepSeek首轮多了顶层字段被拒绝，收到反馈后第二轮通过。连续5个合成局势真实模型计划两方均通过：Kimi3.59–6.62秒，DeepSeek7.44–10.76秒。属于派遣/响应能力检查，不是原生微操全部通过。开始游戏后双方已接收实际战略计划并执行采矿、造工人/补给；启动截面实时速率与错误数以started.json为准。首个10秒截面双方规划/引擎错误0，不能保证后续永远不报错。

上方共享种子已经被本场采用/改进。未来新局正常使用持久库最新版本，勿再拿旧 `--drafts-from shared-kimi-deepseek-dqlfcajm` 覆盖DeepSeek的运输修复。下文“等待Global”“没有运行对局”为历史状态。

## 等待开赛（2026-10-07：Kimi K3 对 DeepSeek V4.1 Flash，双方 OpenCode Go）

用户指定这两个模型并要求给双方现有最好的技能。选用当前已验收且有实战记录的Kimi `stim_push`（含推进/兴奋剂/局部撤退与滞后/订单去重，不宣称胜率最强）；原作者和版本保留。双方相同源码及测试已预填到 `opencode-runtime/shared-kimi-deepseek-dqlfcajm`，源码digest相同，宿主重跑自测和隔离检查都通过，但**两位选手尚未审阅/正式采用，不冒称模型已准备完成**。`arena/lab_prepare.py`现提示这是用户共享种子、要求读取来源、检查源码并真实测试，保留作者归属；16项相关检查通过。

Kimi真实连接5.595秒通过，DeepSeek `opencode-go/deepseek-v4.1-flash`被服务端拒绝，原因Go需要Global。失败检查 `opencode-runtime/match-m09sm0g0`、`logs/kimi_deepseek_go_connectivity.log`；服务已清理，无游戏启动。用户正在按指引调整Global；浏览器实际看到Personal工作区Settings→Privacy→Regions选中Europe & United States，Global未选。已打开Privacy页面供用户操作，未替用户修改。准备证据及精确待启动参数在 `logs/kimi_deepseek_ready.json`；用户改好后复测并用 `--drafts-from` 加 `--reference-model opencode-go/kimi-k3 --qualify` 启动模型检查/演练及原生观战。不要误用DeepSeek官方key、静默换模型或未确认地区改变便循环付费重试。

## 最新状态（2026-10-07：核对 DeepSeek 建议、敌情记忆与缓存前缀）

用户提供的建议**部分属实，但入口和行号已经过时**。最小判别 `../_probe/sc2_context_20261007/before.py/.log/.json` 与 `native_before_prompt.json`：旧 `World.summary` 确实只有敌方总数；当前 `sc2_client.py`、`live_viewer.py`、`native_llm.py` 全部走 `Commander`，原生 `world.visible_enemy` 早已有兵种计数，上一轮还加了 `intelligence` 侦察记忆。原生 raw 观察不能用旧 `World.on_screen()` 的64×64镜头过滤，否则会漏掉镜头外、本方视野内敌军。修改前实际用户prompt共同前缀只有19字符，变化world确实排在固定科技/知识目录前；不是“所有敌情都没有输出”。

实现：`arena/context.py` 提供固定前缀重排、最近8条+每60游戏秒一条/最多6条战略意图，以及交战观察窗口。闭合窗口最多20条+当前窗口，prompt最多6条/条目合计7000字符，各类计数最多16种并列明省略，已知单位容量4096且淘汰可见。记录本方原始观察的明确死亡事件，离开/进入视野独立计数；不将单位数下降冒充战损，不归因击杀者，未知tag不推断身份。观察窗不是精确空间分割的每一场战斗；全体己方/可见敌军计数受新生产、形态、运输等影响。旧screen路径明确镜头变化且不伪造死亡。闭合窗口直写 `engagement_observed`，不堵住任务错误反馈。

固定 `tech_tree/game_knowledge/control_policy/prepared_strategy/context_policy` 前置，接着是会随修复变化的技能目录，然后world/情报/交战/决策记忆/执行反馈。可见敌军用途从 `game_knowledge` 拆到动态 `visible_enemy_roles`。固定对象键规范排序；不增加无意义填充，不累积无界聊天。旧同步/异步Policy共用同一历史记录和prompt函数；旧summary补上有界 `ENEMY_ON_SCREEN`。API usage统计原先有工具函数但请求路径没调用，现正式接通并落在 `request_finished.usage`；缺失缓存指标是None/unavailable，不能记成0%命中。

### 官方 DeepSeek 缓存最小对照（6次、已停止，无额外调用）

`cache_probe_native.py/.log`、`cache_results.json` 位于上述项目外探针目录。实际官方模型返回 `deepseek-flash`。选取原生实时mock对局的3份真实 `request_queued` prompt；每对JSON内容相同，只把固定块放在动态块之前/之后。before/after交替发送、间隔8秒、共用相同SYSTEM，因此不是独立冷缓存对照。thinking禁用、max_tokens=24，仅看usage；finish_reason=length是有意限制输出，**这些片段未解析成计划也未派遣，生产环境推理/输出上限不变**。

| 游戏loop / 时间 | 改前输入 / 命中 | 改后输入 / 命中 | 改后命中率 |
| --- | --- | --- | --- |
| 248 / 11.1秒 | 8701 / 0 | 8701 / 1024 | 11.77% |
| 1466 / 65.4秒 | 8614 / 1024 | 8614 / 6528 | 75.78% |
| 8855 / 395.3秒 | 10114 / 1024 | 10113 / 6528 | 64.55% |

同一后期状态改前10.12% → 改后64.55%；共同用户前缀19 → 14915字符。token有1个差异是序列化顺序/tokenization结果，内容比较完全相等；计数来自服务器usage，未按字符估算。`prompt_token_curve.csv/.svg`只连接这3个真实采样点，不冒充逐帧测量；长度8701→8614→10113不单调增长，固定目录占主体、动态状态和有界记忆会增减。不要为了“让prompt增长”人为塞历史。

`prompt_1_actual.json` 无交战窗口；`prompt_3_actual.json` 实际含 `world.visible_enemy={"Marine":2}`，累计8个窗口、prompt保留6个。窗口7（364.64–383.93秒）本方Marine计数8→5，但确认死亡4；敌方确认死亡5、离开视野3次。这正说明人数相减不等于伤亡，也不能把消失算击杀。完整原始证据在 `match-feedback/p1-*.jsonl`、`p2-*.jsonl`。

旧 `_probe/cache_probe*.py` 是旧deepseek-chat和部分人工重复RULES，未找到对应数字的原始结果文件，本轮没有冒充复现983/768等历史数字，也没有再运行那些付费脚本。当前官方文档 https://api-docs.deepseek.com/zh-cn/guides/kv_cache/ 说明共同前缀复用及尽力缓存，可能需要后续请求/构建时间；140token连发两次零命中不证明通用门槛。本次实测不能保证未来每次相同命中率。

### 回归与运行验证

`checks_final.log`：189项回归通过（20.478秒），含上下文、旧同步/异步、雾区消失/重现/死亡、镜头移动、上车/形态变化、截断与容量、三族宏、超时/反馈、Python隔离、修复与持久库。`checks_initial.log` 的75项属于此前较小集合，不相加。`context_hotpath.json` 合成1000单位、1000次更新，平均0.894毫秒、P95 1.136毫秒；这是热路径微基准，不是原生画面FPS。首次误用系统python缺少pysc2，改用项目.venv311重跑；失败说明保留。

完整对局验证已按用户要求主动中止，随后“等一下”的消息到达时进程已经退出；**无自然胜负、未通过完整终局验收，不重开**。`../_probe/sc2_context_20261007/mock_match.json/.log`、`stop_request.json`、`cleanup.json` 和 `summary.json` 保存过程。最后周期样本1109.3墙钟秒、游戏1137.2秒；周期样本间游戏/墙钟 1.02068×（排除启动基线）。双方实际在114.375/114.152游戏秒完成SupplyDepot+Barracks+Marine链，整个已记录期间请求错误0/0、引擎拒绝0/0。最后P1有10Marine，P2同一周期样本剩5SCV，下一份P2请求剩2SCV，P1明显占优，但不能视为正式获胜。P1/P2完整日志分别记录38/33个闭合观察窗。录像41502字节，相关进程全退出、原生锁释放。全程显式mock/mock、自然资源/建造、realtime=True、正常迷雾、无debug注入；不证明真实LLM策略质量或新显示FPS。付费请求仅上面的6次缓存探针。没有修改SC2Env关键开关、pysc2版本、用户U盘或全局反射设置。

中止报告原始outcome仍是上次周期采样的running（伴随KeyboardInterrupt）；原件保留为 `mock_match_raw.json`，最终报告明确改为interrupted_by_user，绝不据陈旧字段宣称仍在运行。原采样的chain时间数组共用引用，表示全局首次完成时间，早期样本不证明当时已完成；总结只按114秒真实首次完成时间和日志验收。补充修正usage聚合中“只有cached而没有prompt token”的边界后，`checks_usage_edge.log`的46项相关检查通过（2.502秒），与189项重叠，不相加。


## 最新状态（2026-10-07：超时、单工人施工、防守集结、侦察记忆）

已按用户四点请求实现，下一场自动生效；realtime=True、全局战斗反射OFF保持。当前无新观战局，受控验证均关闭，原生锁释放；本轮没有付费模型调用。汇总 `logs/runtime_optimization_summary.json`，清理 `logs/runtime_optimization_cleanup.json`。

1. **超时**：当前会话邮箱改为按需请求快照，只有 `observe` 请求后才开始25秒回复计时；读取前不制造周期性超时，提交ID/期限校验与120秒离线停局保留。DeepSeek增加慢请求状态及响应耗时/输入大小/公开usage元数据，不更换模型、不暴露推理。原来三族宏超过30游戏秒一律丢弃，现对生产数量、生产者、结构/科技损失、敌情、补给、研究与采气意图做保守复验；形态/新JSON技能仍需新计划，绝对70秒上限保留。5次缩时邮箱验收零超时，但**不是新整局真实模型超时率**。真实引擎故意延迟35墙钟秒的计划被复验接受，实际造出2Marine，0拒绝，游戏/墙钟0.98143。原生证据 `logs/late_macro_native.json/.log`，缩时 `logs/mailbox_pacing_after.json`。
2. **单SCV施工**：原生订单319是坐标目标，target_unit_tag=0。旧代码因此误派第二个SCV（原生前提 `logs/runtime_optimization_native_before.*`）。现在保留唯一owner，按位置订单/进度判断；活工人中断由本人恢复，明确死亡才换人，暂时不可见不假定死亡。原生连续5秒施工多派0次，死亡后只派1名替补，最终建成。
3. **产能集结**：`arena/rally.py` 使用各自玩家的GameInfo地形/地面路径及引擎可达查询，主矿坡口内侧→暴露分矿入口，威胁/阻挡明确等待。只设置产能建筑UNIT rally，不召回已有兵、不改工人采矿点；请求后读取raw.rally_targets确认，失败有记录和有限重试。人族新Marine分别抵达主矿/二矿点，老Marine留在原点；虫族Zergling和神族Zealot抵达各自坡口，虫族worker矿点仍在。覆盖三族常规产能类型，但原生代表线不是每种产能逐项验证；warp-in/移动单位变异位置与已有战术任务仍由模型/脚本控制。
4. **侦察记忆**：`arena/intelligence.py` 每次己方观察更新，独立于LLM请求频率；短暂看到的敌兵/建筑、最后位置/时间、明确死亡事件保留到下一次决策。`intelligence`直接进入每次prompt，不排在任务反馈后；未返回请求的旧revision不会吞掉新事件。历史记忆明确标为last_seen/old_unconfirmed，不作当前可见目标、不读取对方私有观察。受控模型忙碌期间Observer看到Medivac后死亡，下一次prompt当前敌军为空但历史情报和死亡事件仍存在；实际死亡事件30.67游戏秒，下一次请求33.6秒，约2.93秒。正在处理的API请求无法中途追加prompt，下一次请求优先带新情报。

原生综合 `logs/runtime_optimization_native_after.json/.log`：0动作拒绝，62.323墙钟秒推进61.161游戏秒（0.98136倍）。三族补充 `logs/rally_races_native.json/.log`：Zerg/Protoss0拒绝、1.02310倍。176项相关回归 `logs/runtime_optimization_final_checks.log`通过（18.655秒），包括隔离Python、修复、持久库。均为受控debug资源/单位/部分快速建造或本地fixture，**不是完整自然对局/胜率/显示FPS**；约10Hz是观察频率，不是画面帧率。

失败/边界保留：`runtime_optimization_native_attempt1.*`施工子项已通过，但最初侦察布置没形成稳定可见目标而超时，后改飞行侦察/运输单位才整体验证通过；未据此隐藏生产错误。`late_macro_native_attempt1.*`测试准备漏了兵营的补给站前置，等待超时；补齐前置重测通过。综合after原始报告里scouting.death_game_s误命名为等待视野消失的时间，实际死亡以death_alert.game_s为准；汇总已明确更正，验证脚本未来输出分开两个时间，原日志保留。主矿没有可识别坡道时会明确标main_ground_approach；寻路不可达/能力缺失不会假称集结成功。

## 最新状态（2026-10-07：当前 Codex 对 DeepSeek 官方 V4.1 Flash 已完成一次实测）

用户要求当前助手亲自参赛，不使用GPT API key，并指定官方付费DeepSeek-V4.1-Flash。新增 `session_match.py`、`arena/external_planner.py`、`arena/deepseek_planner.py`。P1为当前会话逐次读己方文件邮箱并提交真实计划，P2用DEEPSEEK_API_KEY直连官方 `/v1/chat/completions`，准确model为 `deepseek-flash`，与官方模型列表name=DeepSeek-V4.1-Flash核对；low推理、JSON输出，仅记录公开正文，无OpenCode/无模型替换。不是把Codex订阅变成隐藏API；会话不持续工作就没有新决策。

自然原生实时对局 `session-runtime/codex-vs-deepseek-flash-20261007`，原生 `native-runtime/match-8svew76h`，主日志 `logs/codex_deepseek_flash_watch.log`。1200.066墙钟秒推进1224.777游戏秒（1.02059×），到预设20分钟墙钟上限正常结束，outcome=`time limit`，**没有引擎胜负判定**。终场P1人口111/130、2基地、22SCV；P2人口7/88、0基地、1SCV，P1明显占优但不可写成正式获胜。录像58117字节。双方引擎拒绝0，未测显示FPS。

P1接受28条当前会话实际计划；28次请求超过25秒，成功回复平均17.464秒。P2接受137条计划、2次格式/参数拒绝，成功回复平均7.900秒。全部日志保留，无静默代打。整局任务失败P1=8、P2=6（不是最终Commander有界历史中的4/0）；P1有单位损失、移动/等待超时、误选架起坦克等，后续确实提交过修订JSON技能。双方本入口都没有预装Python技能或Python后台修复，使用相同宏与JSON组合技能接口；既有OpenCode Python/持久库入口未改。本场尚非正式公平胜率基准，双方响应期限25/40秒不同，当前会话工具延迟显著。

所有比赛/模型/原生及本场输出窗口进程已退出，邮箱closed，原生锁释放；无继续计费请求。证据 `logs/codex_deepseek_final.json`、`logs/codex_deepseek_cleanup.json`。这次没有让比赛在助手最终回复后假装继续自动决策。

前提：`logs/codex_external_premise.json`是合成Commander非阻塞邮箱判别，非游戏；`logs/deepseek_official_models.json`真实模型列表；`logs/deepseek_flash_premise.json`小请求5.855秒；`logs/deepseek_flash_full_plan.json`完整科技目录合成局势真实API回复2.002秒、执行层验收通过，不冒充原生开局。`logs/external_session_checks.log`64项相关回归通过；补充边界后`logs/external_session_final_checks2.log`8项通过（与64重叠，不能相加）。第一版补充测试文件有引号语法错误，原失败`external_session_final_checks.log`保留，已修正。

后续优先：当前会话25秒回应窗口造成一半请求超时，不宜直接当无人值守比赛入口。增大timeout还需同步处理Commander默认30游戏秒旧计划限制，不能只改一个常数。移动任务多单位严格到位判据、局势缺乏单位位置/路径进度、任务反馈积压值得独立判别后修复，不能凭这场直接归因。手册新增当前会话参赛章节。CLI observe会等待至少15秒有效期的新请求；submit严格匹配ID/期限、拒绝重复提交；120秒无成功回应会显式停止比赛，旧任务只在等待期继续。

## 最新状态（2026-10-07：用户已停止Kimi/Luna对局）

用户看到OpenCode Go的5小时额度耗尽后，明确要求关闭。`opencode-runtime/match-ffaa512v`已通过SIGINT正常进入清理，run.status和match.outcome均为interrupted；独立输出窗口已关闭。录像 `match.SC2Replay` 保存24283字节，本局游戏/模型/输出窗口相关进程已退出，原生锁释放，双方临时认证副本已清除。证据 `logs/kimi_luna_stop_request.json`、`logs/kimi_luna_stopped.json`。没有启用余额扣费、没有安排自动重开；下方运行状态为历史记录。

## 最新运行（2026-10-07：Kimi K3 对 GPT-6 Luna，已共享旧技能参考）

用户要求开一场观战，指定Kimi K3、另一名由助手选择，并授权参考Luna/Qwen的既有技能。当前正常对局 `opencode-runtime/match-ffaa512v`（原生 `native-runtime/match-s4iglwvz`），日志 `logs/kimi_luna_reference_watch.log`，独立中文输出窗口已启动。双方人族，realtime=True、全局战斗反射OFF、后台修复和持久库ON；无debug注入、无固定脚本开局。

最初选Qwen3.8 Max，实际连接通过、源码最终验收通过，但两次派遣演练共6次未正确调用自己的Python技能，因此明确宣布改为GPT-6 Luna，没有静默换模型。失败局均未启动游戏，源码/错误保留。当前Kimi使用自身参考改编并验收的stim_push；Luna加载自身最近实战修复版steady_marine。两方实验室均可读取同一对已验收Luna/Qwen参考；Kimi及原候选Qwen Max实际读取记录存在，不冒称Luna本轮重新研读/改写过。

启动截面墙钟103.571秒、游戏101.429秒、0.97932倍；双方接受14/17条真实计划，规划错误0/0、引擎拒绝0/0，瓦斯112/176。仅启动状态，未宣称整局胜率或显示FPS。证据 `logs/kimi_luna_reference_started.json`。下方“当前没有新观战局”为此前三族开发完成时的历史状态。

新入口参数 `--reference-model` 可重复指定原人族持久库作者；固定并校验版本，以只读fixture提供源码/清单/测试/策略；不直接冒充新选手已验收源码。来源记录在run.json及新版本发布origin中。实现 `arena/skill_references.py` 与准备提示，使用方式见AI_SKILL_MANUAL。参考读权限与写拒绝已实际检查，24项相关回归通过。

Qwen Max当前实际OpenCode低推理变体为 `low` / `effort=low`，原来的通用reasoningEffort不是它公布的选项；桥接已显式选该变体，缺失则报错。一个代码回复探针8.156秒成功，但它并未解决完整派遣问题，不能称Qwen Max已可上场。

## 最新状态（2026-10-07：三族单位、科技、采气与开矿）

用户已明确要求此次覆盖人族、虫族、神族。当前生产接口已从 Marine 为主扩展为三族目录：58个单位/库存/召唤产品条目、58个建筑条目、89个科研条目，另有可逆形态和辅助单位用途。范围是普通多人对战，不含战役/合作指挥官。**目录覆盖不等于所有科研和法术逐项实战通过。**

- `arena/tech_tree.py` 绑定实际引擎元数据，`arena/macro.py` 执行生产/建造/研究/形态/采气/扩张；`arena/knowledge.py` 和 `arena/unit_roles_zh.json` 提供中文用途、资源、前置和经济知识。战斗提示每次包含本族目录、可见敌军用途、基地/气矿饱和度及扩张候选。策略由模型选择，等待任务不会自动决定科技路线。
- 新宏：`build_structure/research/morph_unit/build_supply/gather_gas`，`train_unit/train_worker/expand/gather` 已泛化。采气分配持续维持，工人进入气矿不算失踪；扩张观察使用中立资源地图快照，不读对方私有观察；过饱和矿区可向缺工分矿转工。
- 人族附属建筑空间由原生2×2地块判定，阻挡时可显式升起/迁移/降落再建；反应堆支持第二生产槽。虫族消耗工蜂、幼虫、成对跳虫和变异差价均区分；神族供能、折跃、两名圣堂合体区分。
- Python 选择器扩到三族，增加局部 `cast`、能量/载员/可用能力上下文。运输内成员记录为 `embarked`，不冒充死亡；army 不捞进机库拦截机、核弹、短命召唤物或人口王虫。具体单位仍可单独选择。准备实验室按本族构造观察，并拒绝无本族单位可分配的选择器；后台修复仍需隔离验收，不改其他技能来蒙混过关。
- `opencode_match.py` 和 `native_llm.py` 支持 `--p1-race/--p2-race`，原生还支持 `--bot-race`。默认仍双方人族；非人族持久技能分别位于 `skill-library/race-zerg` 和 `skill-library/race-protoss`，保留原人族库，不把旧枪兵代码强行复用给别族。
- 可转发完整手册：`SC2_GAME_KNOWLEDGE.md`；Python合同：`AI_SKILL_MANUAL.md`。使用 `./run_sc2.sh .venv311/bin/python render_game_knowledge.py` 从实际模型同源目录重新生成手册。赛前快照在 `arena/native_catalog.pb`；比赛会重新读取引擎，不能把快照当实时能力确认。

**最终证据**：`logs/multirace_final_summary.json` 汇总来源；全套151项回归通过（`multirace_final_checks2.log`）。实时原生三族经济链完成8/9/10步骤，气矿实际收入增加、每口3工人、扩张完成；主要单位科技链完成40/53/41步骤，所选成功片段0动作拒绝。游戏/墙钟约0.981倍。真实Qwen/Luna三族指令演练6/6通过，Qwen神族需2次明确纠错。受控资源/快速建造测试未绕过科技树，不是自然胜率；所有89科研/全部法术未逐项实测，未测新FPS。附属迁移/核弹/拦截机子项通过的整合脚本最终仍因闪烁位置阈值断言失败，单独闪烁/运输原生测试随后通过，不能把失败整轮称为通过。详见STATUS。

**当前没有新观战局**：先前 `match-3d9uz_j7` 已自然结束（Qwen Victory / Luna Defeat，840.372墙钟秒）；本次只启动受控验证，验证进程已退出且原生对局锁释放。下方“当前运行”标题均为历史记录。下一场默认使用新接口、旧模型各自已验收的人族技能；选择新种族时走对应准备/技能库流程。realtime=True、全局战斗反射OFF保持。

### 本次新增的实测坑

1. 原生5.0.15和Linux4.10的地图初始工人数、部分费用不同；使用引擎返回值。最新开源映射的部分新增能力数值ID偏移，Baneling的4121在原生代表Observer变形，真实毒爆是4124；按实际名称身份绑定并打印调整，不能只检查ID存在。
2. 原生单单位能力查询不列出执政官合并1766，但两名本方真实圣堂同一条命令可成功；有专门判别，仍等新Archon出现确认。
3. 核弹/航母库存不会完整以单位数量出现在观察里。核弹需本方Ghost可用核打击能力确认；无Ghost明确等待。拦截机满库存是无异常空闲航母的能力容量推断，显式标记而不伪报精确数量；两种补货任务count只能1。
4. 附属建筑自身placement查询有降落语义，可能拒绝实际合法附属建造；用2×2SupplyDepot占地查询筛选，再靠实际动作结果和建筑观察确认。保留原失败实验。

## 最新修复（2026-10-07：双玩家 enemy_start 视角错误）

- Luna说推进但不出门的直接原因是宿主共用了P1的GameInfo出生点列表，导致P2目标=自己基地。真实原生判别已确认，不是凭猜测改模型。
- RawArena/NativeArena现按各自API玩家读取start_locations；无非己方候选点明确拒绝，无私有对手home回退。开局日志map-target、比赛报告map_targets可审计。
- 62项相关回归通过；原双方技能在受控真实引擎中10.4秒内全部离家35格以上，0引擎拒绝（注入测试兵，不是自然胜率）。证据logs/enemy_start_before_probe.*、luna_home_target_before.json、enemy_start_after_live.*。
- 旧match-_ig80d8w已由Luna主动投降结束并保存录像；该局有宿主缺陷，不能据此比较模型强弱。50ms worker超时和模型格式错误仍是独立问题。用户要求修复后重开，当前正常观战局match-3d9uz_j7（原生match-dea1h2up）已启动，日志logs/enemy_start_fixed_watch.log、证据enemy_start_fixed_started.json。双方自主持久技能和独立中文窗口保留；启动状态见STATUS末尾。

# SC2 AI 对战项目 — 交接文档

> 给接手者的完整上下文。**先读完「踩过的坑」再动手**，那里的每一条都是实测出来的，
> 凭直觉写会 100% 重踩。

配套开发手册：[AI 可编写技能手册](AI_SKILL_MANUAL.md)，包含当前协议、可用示例和待实现条件战术的开发合同，可单独转发给其他 AI。

## 当前状态：持久技能库已启用，新局自动继承修复（2026-10-07）

用户要求实现持久技能库，并选择做好后重开立即启用。当前新局 `opencode-runtime/match-_ig80d8w`，日志 `logs/persistent_library_repaired_watch.log`，原生 `native-runtime/match-6y78y1mp`。启动命令仅 `opencode_match.py --qualify --seconds 1800`，没有指定旧对局目录；双方从各自持久库自动加载并复验。Qwen源码a0b71bcf…、Luna源码e81a957c…（真实上一局修复版）。40.158秒墙钟推进40.982秒游戏，1.02051×；双方接受5/7次真实计划，错误2/0，错误没有隐瞒。独立中文输出窗口继续运行。证据 `logs/persistent_library_repaired_started.json`；未验证整局胜率，未测原生显示FPS。

新增 `arena/persistent_skills.py`、`skill_library.py`。库在`skill-library/`，按完整provider/model隔离，HEAD与版本manifest精确校验身份；保存源码/manifest、自编测试、全部fixture、策略与验证证据。内容哈希校验包括fixture和证据，分阶段fsync、同模型文件锁、原子HEAD替换和预期父版本检查防止旧更新覆盖新版本；旧目录保留。加载重新执行隔离自测与当前宿主检查；缺失版本明确首次准备，损坏/复验失败明确停止，不回退。

准备完成后首次入库；默认新局自动加载。原有显式来源必须与当前库源码一致，否则拒绝隐式回滚；可用`--no-skill-library`显式隔离历史实验，`--skill-library-root`指定测试库。后台SkillRepair在开发验收后、游戏线程安装前完成快照复验和持久发布，保存失败/并发冲突不伪报ready。安装后状态含持久版本号，仍由模型重新派遣，旧任务快照不热改。耗时校验/文件写入在后台线程。

### 最小前提与完整性验证
- `logs/persistent_library_premise.json`证实原accepted目录只冻结源码，没有测试/fixture；因此不能直接把旧accepted目录当完整持久包。
- `logs/persistent_library_real_source_probe.log`：使用Qwen/Luna真实已验收源码和测试，在临时库实际执行隔离检查并保存；不是重新调用模型或游戏战绩。
- `logs/persistent_library_full_regression.log`全套127项通过；后续实测134508字节故障fixture触发初版128KB限制，保留失败日志 `logs/persistent_library_actual_repair_import.log`。将fixture保存上限改为16MiB（源码/测试64KiB、总payload64MiB），不截断；新增大fixture完整性测试，`logs/persistent_library_final_checks.log`13项通过，与全套有重叠不相加。
- 覆盖源/测试未验收拒绝、模型隔离、fixture篡改拒绝且不回退、旧并发写拒绝、原子指针写失败保留旧HEAD、宿主规则改变后拒绝加载、大复现数据不截断；受控fixture链验证后台修复→持久发布→本局安装→新Python进程重验并加载相同SHA，原活跃任务仍旧快照。该链的开发模型是测试替身，不冒充真实模型自修复。

### 实际修复的跨局继承
旧`match-gg7rlqak`最终Qwen修复300秒超时失败，没有accepted报告；Luna修复ready、源码从7e686357…改为e81a957c…，7项测试及宿主检查通过，报告`p2/repairs/revision-1/accepted-retry-report.json`。旧进程未加载新机制，本次对该成功修复做一次显式迁移：核对ready的旧/新SHA、未变的源码测试digest，重新验证完整快照后发布。证据 `logs/persistent_library_actual_repair_import2.log/.json`。原始库Luna版本51c4899d…保留，新HEAD为f30e9229db0da6fe2995a46845805119d2f4b4dd63d24e549a6fbac4e1508148；Qwen HEAD为6e14def1a105bc25a004f4d9a939499a322fbc3e104b0aab850db81edc28cf52。

旧`match-gg7rlqak`正常停止并保存30401字节录像。第一次持久库重开`match-iszlnw3y`已在发现Luna刚完成的成功修复后显式停止，以便最终新局直接继承该真实修复；其日志 `logs/persistent_library_watch.log`保留，不冒充最终版本。最终新局Qwen资格第一整轮缺args被拒绝后第二轮通过，Luna第一轮通过。

此机制保存可验证Python技能，不是模型权重训练，也没有把所有JSON纠错自动转成永久战略记忆；过关不代表战术必然更强。查看与运行命令见AI_SKILL_MANUAL第15节。

## 当前状态：Qwen 对 GPT-6 Luna 新局已启动（2026-10-07）

用户明确要求将GLM替换为GPT-6 Luna，保留Qwen。当前默认模型与运行阵容为 `opencode-go/qwen3.8-flash` / `opencode-go/gpt-6-luna`；没有使用gpt-5.6-luna或其他供应商替代。真实结构化调用探针 `logs/gpt6_luna_connectivity.log/.json` 3.364秒，返回身份与生产计划校验均通过，沿用已有Go登录/地区设置。

新局 `opencode-runtime/match-gg7rlqak`，主日志 `logs/qwen_luna_watch.log`，原生 `native-runtime/match-6a86sa45`。独立中文输出窗口已自动启动，右栏按身份显示gpt-6-luna；保持realtime=True、游戏内无正文叠字、全局战斗反射OFF与后台修复ON。前10.034秒墙钟推进10.223秒游戏，比例1.01884，双方各接受2次真实计划，启动截面规划错误0/0，非整局保证。最新截面 `logs/qwen_luna_started.json`；尚未测完整胜率和原生显示FPS。

准备目录 `match-n6myyne2`，`logs/qwen_luna_prepare.log`。新增--p1-prepared-from/--p2-prepared-from可按选手单独复用精确验收源，与全局来源互斥；原身份/哈希/重验门槛保留。本次Qwen复用match-vm6o1ldo（24项自测），GPT-6 Luna从空lab自行使用工具写steady_marine源码/manifest/6项测试。初次2项失败后真实修改代码和测试，第二次6/6及宿主检查通过，准备墙钟40.392秒；SHA7e68635756ecd757303fa57cd16c09cd0dbda3ef43f4c5b48ae07f523d94918b。不是把GLM旧代码换标签。

实际派遣演练Luna第一次多带skills字段被拒绝、第二次通过；五决策资格检查Luna首轮通过1.471–1.874秒。Qwen首轮兵营count=2非法，被反馈后第二整轮通过2.309–3.671秒。完整失败保留。新局精确复用刚验收的双方技能，重跑源码/测试检查，没有重复调用已通过的五决策资格流程。14项既有相关回归通过，日志 `logs/qwen_luna_selection_checks.log`；自然对局表现不能由合成资格数据推断。

旧 `match-cqqhx8ds` 已SIGINT正常退出、报告interrupted，录像21136字节，旧输出窗口已关闭；其后台修复草稿未拿来冒充已验收源码。手册默认阵容已更新。

## 当前状态：独立中文输出窗口，新局运行中（2026-10-07）

用户要求撤掉游戏内LLM正文，改为我们自己的双模型输出窗口，并同意做好后重开。当前 `opencode-runtime/match-cqqhx8ds`，主日志 `logs/dual_output_window_watch.log`，原生游戏 `native-runtime/match-jkouzs4x`，输出窗口日志 `<本局>/output-window.log`。双方真实中文计划已接收；70.187秒墙钟推进71.696秒游戏，1.02151×，双方接受11/7、规划错误1/3（启动截面，不隐瞒错误）。证据 `logs/dual_output_window_started.json` 保存后续截面。保持realtime=True与全局反射OFF。

新增 `llm_output_window.py`：独立pygame桌面窗口「星际 AI 对战 · 双方输出」，双栏正文/指令摘要、完整JSON切换、错误筛选与滚轮历史。使用系统Noto CJK字体，实际渲染截图已检查中文正常；`logs/dual_output_window_preview.png`、`logs/dual_output_window_errors.png`。只读取本局公开回复和feedback journal，不读内部推理，不额外调用模型；关闭窗口不终止游戏。半行JSONL等待写完、解析/读取错误/过期状态可见，不把回复或任务派遣等同于任务完成。展示每0.5秒刷新，状态采样10秒；缓存文本换行，离屏绘制平均6.60ms，仅窗口渲染指标，不能冒充游戏FPS。

`opencode_match.py` 完成验收后自动启动窗口（`--no-output-window`显式关闭）；native_llm移除正文叠字，保留运行状态。`logs/dual_output_window_regression.log` 19项相关现有测试通过；另实测增量日志不重复、半行等待、拒绝状态关联与渲染，完整证据 `logs/dual_output_window_render_check.json`。

新局明确复用 `match-vm6o1ldo` 的完整验收版本，因为旧局战中修复草稿已改变且未完成验收。再次自测24/25项、宿主检查、真实派遣/五次连续决策通过；GLM第一轮因tasks字符串被拒绝，反馈后第二整轮通过；失败没有跳过。

旧 `match-07ipg3rk` 用户要求停止：SIGINT已进入KeyboardInterrupt，但主进程随后在清理中退出（exec143），未完成最终报告/录像。游戏进程已退出，6个仅属该局的残留模型/MCP服务已终止，临时auth副本清除。run/match报告显式加shutdown_recovery并标interrupted，保留原始样本；没有伪造录像。证据 `logs/dual_output_shutdown_recovery.json` 和旧主日志末尾。该清理中断根因未判明，不宣称已修复。

> 最新语言设置：当前 `match-07ipg3rk` 双方观战正文已直接切为简体中文，无需重启；后续对局/准备/修复默认中文自然语言，协议字段保持原样。证据 `logs/native_chinese_output_verified.json`。

## 当前状态：原生窗口显示模型决策正文，新局已启动（2026-10-07）

用户选择结束旧局并重开使叠字生效。旧 `match-vm6o1ldo` 已SIGINT正常结束，outcome=interrupted，录像16139字节。当前新局 `opencode-runtime/match-07ipg3rk`，原生日志 `native-runtime/match-qynqlhwe`，主日志 `logs/native_reason_overlay_watch.log`。复用双方已经测试的同一Python版本，重跑自测/宿主检查/实际派遣练习和连续五次资格检查后开赛；realtime=True，全局战斗反射OFF。

原生窗口的LLM THINKING/ACTIVE下方新增LLM says：模型最近一次已接受计划的reason原文、游戏时间和距今秒数。等待新回复时保留，拒绝/过期计划不覆盖；空正文明确提示，最多3行，超长显式[...]。不读取内部推理、不增加LLM调用。实现为Commander.last_decision + arena/native_overlay.py + native_llm.py。

`logs/native_reason_overlay_regression.log` 52项现有相关回归通过，另验证实际回复reason存在及DebugText protobuf传输、中英文换行/截断。新局READY，30.099秒墙钟推进30.714秒游戏，双方已接受5/4次计划且last_decision包含真实正文；GLM有1次规划错误，未隐藏。`logs/native_reason_overlay_started.json` 保留后续实际运行截面及对应显示文本。验证范围是原生运行/叠字调用与模型数据，未成功获取桌面截图，不声称中文字体像素效果已验收，亦未测显示FPS或整局胜率。

## 当前状态：指导修复并通过资格检查的新局已启动（2026-10-07）

用户要求原模型自行修复后重赛，并允许提供指导。当前运行 `opencode-runtime/match-vm6o1ldo`，日志 `logs/guided_repair_qualified_match2.log`，原生窗口 `native-runtime/match-3av_j5vt`。旧 `match-fllgcyro` 已1800秒到时结束，不再运行。保持realtime=True、全局战斗反射OFF、后台模型自修复ON，未启用8766浏览器服务。

GLM根据宿主复现的“射程内敌军执行时消失”问题，实际自行编辑Python，将失效tag攻击改为attack_move；新SHA3fd685ef…。Qwen当前a0b71bcf…已通过同一检查，保留源码并补测试。两方自编测试24/25项及宿主独立检查通过，精确源码来源 `match-so7ln9uw`。宿主没有替选手编辑技能。前几次错误演练/资格检查均明确停止且未开游戏，详情见STATUS。

两方实际Python调用练习和连续五次真实模型决策均通过：Qwen 2.450–3.267s、GLM 1.381–4.565s（合成观察+生产校验，不是游戏动作验收）。开赛后50.257秒墙钟推进51.250秒游戏、比例1.01977；Qwen接受8/GLM6次真实计划，完成4/2项任务，0引擎拒绝。GLM曾将tasks写成字符串，生产校验明确拒绝并反馈，下一次真实回复纠正为数组并继续；不能宣称所有格式错误已消失。证据 `logs/guided_repair_watch_started.json` 及主日志。尚未验证整局胜率，没有重新测显示FPS。

战斗请求改用实际OpenCode StructuredOutput，仅该格式工具允许，缺失结构结果报错，无纯文本回退。Qwen仅战斗使用arena_fast（thinking disabled），同一Go模型；开发/修复仍保留推理。准备复用会复制必要旧fixture，但当前宿主execution_race.json不被覆盖。`--qualify` 开启连续五次真实决策门槛，失败反馈后必须整轮重做；运行中仍22秒软提示、60秒硬截止。本轮本地回归118项通过（fixture，不冒充自然局）。

## 当前状态：复盘后重赛已启动（2026-10-07）

用户明确要求原 Qwen/GLM 检查自己的脚本、允许修复Python后再次对战。当前新局 `opencode-runtime/match-fllgcyro`，日志 `logs/opencode_review_rematch_20261007.log`，原生窗口 `native-runtime/match-uxu1lft0`。realtime=True、全局反射OFF、后台修复ON，新的精简请求/22秒软期限/60秒硬期限已启用。记录墙钟230.2s时双方已接受计划[0, 23]、规划错误[4, 7]，时间比例1.02052。Qwen实战起步仍受超时/JSON错误影响，不能声称开赛后始终正常。旧局 `match-o_egaz3l` 保持停止且已归档。

双方通过工具读取本方复盘及源码，各补充测试，Qwen17项/GLM20项和宿主检查通过；最终技能源码hash均保留上一局最后版本，没有为了展示“修复”而强制改源码。真实战斗调用演练GLM第1次通过，Qwen先JSON失败、再60s超时、第3次返回合法完整args通过。演练仅证明合成观察下调用格式，不等于自然对局成功；实际运行进展以本局日志为准。

## 当前状态：用户已停止对局（2026-10-07）

`match-o_egaz3l` 已按用户要求通过 SIGINT 正常退出，结果 `interrupted`。录像 `opencode-runtime/match-o_egaz3l/match.SC2Replay` 已保存（50528字节），本局模型服务退出、临时认证副本清理；不要把下方历史“running”记录当作仍运行，也不要自动重开。

## 最新改动（2026-10-07：OpenCode 战斗超时处理）

已加入精简且不累积历史的战斗请求、22秒慢响应提示/60秒硬中止、超过30游戏秒的计划复核（70游戏秒绝对上限）。完整错误反馈继续保留，准备/修复历史不重置。当前 `match-o_egaz3l` 未重启，仍运行加载时的旧规则；新机制在下一次启动生效。

本地全套107项加2项版本/取消边界检查通过。真实模型人工场景：GLM 3.961s/3.469s返回合法计划；Qwen 22.085s显示慢响应，60s仍无正文后中止且收到确认，数据库仅有推理部分/无正文。不能宣称Qwen超时已消除或胜率提高。系统提示6149→2667字符（-56.63%），证据与限制见STATUS末节和 `logs/battle_*`。

## 最新重开局（2026-10-07 11:00）

用户要求重开，旧 `match-bkvz5ycy` 已停止并保存录像。当前新局 `opencode-runtime/match-o_egaz3l`，日志 `logs/opencode_restart_20261007_110053.log`；Qwen/GLM均已下达真实计划，realtime=True，后台修复开启，全局反射OFF。详情见STATUS末尾。

## 当前运行（2026-10-07：自主调试后再开赛）

Qwen3.8 Flash / GLM-5.3-Flash 已分别自行编写、调用工具运行并通过14/17项测试与宿主检查，新局已启动：`opencode-runtime/match-bkvz5ycy`，`logs/autonomous_lab_watch.log`。两个原生窗口、realtime=True，全局reflex OFF，后台技能修复开启；前20秒时间比例1.01878、双方真实计划已接收。旧对局已经停止，新局源码来源 `opencode-runtime/match-sqzk74f4`；`--prepared-from`校验身份/哈希并重跑检查，没有临时改写选手技能。

后台修复真实受控验证通过：GLM收到NameError→自编修复/回归测试→隔离验收/观察重放→新SHA安装→战斗LLM重新派遣，游戏66.473s/墙钟67.748s，0引擎拒绝。证据 `logs/skill_repair_live.*`；不是自然胜率证明。详细边界、首次失败、回归验证见STATUS末尾。

## 最新开发（自主 Python 调试与战中修复）

用户已要求关闭上一场 Qwen/GLM 对局；已正常停止并保存 `opencode-runtime/watch-recovery-qzjd49ed/match.SC2Replay`。不要将下方历史运行记录当作仍运行。

新实现 `arena/skill_lab.py` / `skill_lab_mcp.py` / `lab_runner.py` / `lab_prepare.py`：模型实际使用 MCP 读写本方文件、写 unittest 并执行隔离测试；当前文件哈希的自测和宿主检查均通过才接受。`opencode_match.py` 每方独立服务与开发/战斗会话，默认准备600秒，支持 `--prepare-only`、`--prepared-from`、`--no-live-repair`。

`arena/skill_repair.py` 监听真实 Python 失败，在独立后台开发会话修复，通过记录观察重放再安装不可变新版本；战斗模型收到 `skill_repairs` 后自己派遣。失败可见，旧版本任务不被热改；任务死亡/截止等不冒充源码缺陷。具体预算与限制见 AI_SKILL_MANUAL 新节。实测证据与最终对局状态见 STATUS 末尾（开发过程中逐步追加）。

## 最新观战运行（2026-10-07）

Qwen3.8 Flash 对 GLM-5.3-Flash 已按用户要求启动；双方自编技能准备通过，真实模型计划开始执行。当前运行 `opencode-runtime/watch-recovery-qzjd49ed`，日志 `logs/opencode_watch_recovery.log`。首次原生启动超时后显式复用已验源码并在新会话恢复上下文；没有查明首次超时根因。双原生窗口、realtime=True、全局反射OFF；前20秒游戏/墙钟1.02005，0引擎拒绝。完整对局/自然技能效果未宣称验证，详情见STATUS末尾。

## 最新入口（2026-10-07：OpenCode 双模型准备与比赛）

- 新入口 `./run_sc2.sh .venv311/bin/python opencode_match.py`，默认 `opencode-go/qwen3.8-flash` 对 `opencode-go/glm-5.3-flash`。两方使用已有同一 Go 登录，但模型会话/目录/任务独立，不冒充独立key。
- 先检查双方真实模型，再每方最多300秒准备与修正Python源码（最多4轮，可提前完成）；冻结校验后才调用原生双玩家入口。比赛不开脚本固定开局，继续由模型决策，realtime=True。`--check-only` / `--prepare-only` 不开游戏。
- 通过本机OpenCode1.18.35 HTTP服务接入，非绕过OpenCode直连Go网关。独立XDG目录、随机本机服务密码、工具权限全部deny；模型返回源码JSON，宿主按严格路径规则写本方文件。默认reasoningEffort=low。
- GLM真实连通、生成源码、收到明确越界错误后修正、隔离Python合成观察检查已通过（87.101秒、2轮，marine_skirmish）；83项fixture回归通过。证据 `logs/opencode_glm_prepare.*`、`logs/opencode_bridge_tests_current.log`。
- **当前双模型自然对局已启动，完整结果尚未验证，详见顶部最新观战运行。用户要求换掉受 Global 限制的选手，已显式把 P1 改为 Qwen3.8 Flash，保留 P2 GLM。** 当前 Go 设置下双方最小真实 JSON 请求均通过（3.669s/2.642s），见 `logs/opencode_qwen_replacement_check.log` 与 `opencode-runtime/match-rih_elgk/run.json`；无需修改地区设置，没有换官方key。
- `logs/opencode_model_probe.json`保留真实模型身份与地区原始错误；默认推理100秒无正文超时在 `logs/opencode_glm_prepare_default_timeout.*`。默认low后GLM可完成，不能将此当成严格A/B，因为准备任务也收紧为单技能。
- 每次运行目录为 `opencode-runtime/match-.../`，含 p1/p2 源码、OpenCode正文/错误审计、run.json、match.json及录像。目录私有，已加入gitignore；服务关闭时删除临时auth副本。原HOME配置、pysc2和U盘游戏未改。

## 当前能力（2026-10-07：Python 战术运行接口）

- `arena/python_skills.py` + `python_worker.py`：赛前读取 `.json + .py`，冻结源码/记录 SHA-256；各任务在 bwrap 隔离的 CPython 子进程中执行 `tick(ctx,state)`，游戏线程不等待管道响应。允许标准库和一般 Python 控制流，非游戏进程内 eval。
- LLM 仍决定任务、人数、战略目标、撤退目标、参数与取消；Python 只控制分配的 SCV/Marine/Tank，接收局部可见观察。可 move/attack_move/attack_unit/stop/stim/siege/unsiege/gather，不能造兵/研究/扩张/投降。原有 JSON 建造回矿等技能保留。
- `native_llm.py --p1-skills <目录> --p2-skills <目录>` 显式启用各方技能库；未指定时行为不变。附 `python_skills/examples/cautious_push.*`，按局部可见价格比/血量选择推进或撤退，并有滞回/冷却。全局战斗反射仍默认关闭。
- 新命令 `cancel_python_task`，只能取消本方活跃 Python 任务，可同一计划取消后重派。50ms 单轮响应时限，过期/越权/异常/引擎拒绝失败可见，保留 traceback/观察/动作关联。任务完成只是脚本报告完成，不代表主机证明目标或胜利。
- 验证：`logs/python_skill_tests.log` 78项通过（62旧测试+16新增）；`logs/python_skills_live.log/.json/.SC2Replay` 真实 DeepSeek 下令→隔离 Python 推进→模型人为等待5秒时局部撤退→真实模型取消，0引擎拒绝。游戏13.259s/墙钟13.510s；原生控制频率约10.06Hz，不是显示FPS。
- 该实测注入双方 Marine 和 god 调试模式，是受控接口测试，不是自然对局/胜率/模型编写源码测试。初次测试场景枚举错误保留于 `logs/python_skills_live_setup_failed.*`。
- OpenCode 后续入口已补充，当前验证限制见顶部。Python使用合同、参数、返回动作和边界见 `AI_SKILL_MANUAL.md` 第0节。测试已结束，未启动观战输出服务。

## 当前能力（2026-10-07：LLM 主动投降）

- 原生NativeArena现支持LLM顶层调用 `surrender`，args.reason为1至500字符，必须单独一条任务，不能嵌入组合技能或指定对方。
- 模型输入的control_policy.surrender_available显示后端支持情况。旧Linux/pygame路径尚未接入，会明确拒绝；没有升级或改写pysc2。
- Commander校验后把比赛控制请求交给NativeArena.step，在发单位动作前处理；先保存录像，再调用现有leave接口。没有脚本自动判断劣势投降。
- 双API模式从对手取得引擎正式胜负和终局录像；单API对Bot按已确认主动认输规则判负，保存退出前录像。
  最终报告surrender.result_source/replay_scope区分两种情况，不能把单API模式称作取得了退出后的引擎终局结果。
- 完整拒绝/失败/未知结果可见；退出请求不确定时不编造胜负、不自动重试。结束后活跃任务cancelled，终局反馈保存在JSONL。
- 先行probe_surrender.py；62项fixture通过；真实DeepSeek对Bot主动认输、脚本P2对API对手认输均经生产链验证。
  原始证据logs/surrender_probe.*、surrender_tests.log、surrender_live.*，详细范围见STATUS末尾。
- 新接口已同步AI_SKILL_MANUAL.md。测试无窗口、realtime=True；先前正文服务仍关闭，没有重开观战。

## 当前能力（2026-10-07：LLM 自定义组合技能）

- Commander 接受 `skills` 定义 + `tasks` 调用；模型自己起名、组合步骤，每方本局技能库独立，后续请求收到 `skill_library` 可复用。
- `arena/composite_skills.py` 提供有限步骤执行器：build/gather/move/attack_move/stim/siege/unsiege/wait(enemy_near)。
  选单位、坐标、建筑选址由脚本承担；此 JSON 协议不接受 Python 源码；独立的赛前 Python 文件接口见上节。新底层能力仍需扩充执行器。
- SCV 建造→采矿、坦克移动→架起支持 `queue:true`（SC2 原生 Shift 队列）。其余步骤按观察确认顺序执行。
- 选定单位保持占用，其他宏任务和空闲采矿不抢占。缺单位/科技/资源显式等待；死亡、拒绝、超时显式失败，
  已发命令的失败技能请求 STOP 清队列，并记录该请求尚未确认，不宣称已取消成功。
- 定义与任务整批校验，非法字段/命令不会部分落地；不能改写正在执行的同名技能。
- skill_defined/skill_step_started/skill_step_completed 全部进入结构化反馈；完成附 buff、类型、命令队列、位置等观察证据。
- 默认战斗反射仍 OFF；没有恢复自动撤退/防守。用户已停止的观战局不重开。
- 已先跑原生能力判别 `probe_skill_primitives.py`；受控真实 DeepSeek→生产 Commander→原生引擎验证见 STATUS 末尾。
- 坦克生产与兴奋剂研究的完整科技链尚未接入；相关技能要求预先具备单位/研究，不能用 debug 受控场景冒充自然对局能力。
- 技能定义保存在本局内存与反馈 JSONL；跨对局自动加载尚未实现。所有入口仍通过 `./run_sc2.sh`。

## 当前行为（2026-10-07：战斗反射默认关闭）

- 用户因部队反复逃跑/拉扯要求关闭：当前对局已停止并保存录像，不要自动重开。
- Commander/ReflexNet默认不做自动撤退或自动防守，部队不再被反射层抢占；保留工人采矿与任务队列。
- 模型输入、反馈和原生UI明确Combat reflex OFF。旧算法只供显式开启的测试使用。
- 35项fixture通过；未重新启动自然对局。证据logs/disable_reflex_before.log、disable_reflex_tests.log。

## 最新入口（2026-10-07：DeepSeek 对内置电脑）

- 原生观看：`./run_sc2.sh .venv311/bin/python native_llm.py --p1 deepseek --bot medium --seconds 1800 --output logs/native_deepseek_vs_medium.json`。
- 已实际启动并收到真实模型计划；realtime=True，DeepSeek玩家视角、正常迷雾；结束保存录像。
- `--bot`支持very_easy/easy/medium/medium_hard/hard/harder/very_hard；不传仍是双LLM。
- 当前这场日志/结果：logs/native_deepseek_vs_medium.*。本次启动不能当作已完成胜率测试。

## 最新进展（2026-10-07：完整 LLM 反馈通道）

- Commander新增独立结构化feedback.events，不再依赖最近8条日志；计划校验列出具体字段/值/规则，
  任务状态与引擎拒绝均反馈给模型；每方JSONL保留实际请求、回复和交付状态。
- 引擎失败附动作/任务来源、目标、本方前后观察、时间；未知原因明确写unknown，不猜死亡/失去视野。
- 网络失败保留反馈，下次正常规划携带；分页不丢失，新事件不会被旧请求确认。关闭时记录未交付数量。
- 32项fixture测试通过。原生实时受控实验产生两类真实错误，已在一次真实DeepSeek请求中交付并返回合法计划。
- 原始证据logs/feedback_before_probe.log、feedback_all_tests.log、feedback_live.log/.json（结果名feedback_live_result.json），
  完整请求在logs/feedback_live/*.jsonl。实验退出时另有8条新任务反馈待发，已如实保存。
- 尚未修复战斗竞态、未验证胜率；进程重启后的上一局待发反馈尚不自动恢复。详细边界见STATUS末尾。

## 最新进展（2026-10-07：真实DeepSeek已接入双原生玩家窗口）

- 入口：`./run_sc2.sh .venv311/bin/python native_llm.py --seconds 600`。
- 同版本Windows引擎+GE-Proton11-7，复用Commander/任务队列/反射层；真实模型新增任务已实际完成。
- **原生API拒绝双玩家+第三Observer**；用户选择两个原生玩家窗口，有各自迷雾，不能称全图观战。
- 双mock 12秒延迟建造链、双真实DeepSeek建造链均通过；时间比例约1.02×，控制10Hz。
- 双mock窗口瞬时约264/183FPS；真实LLM运行时桌面锁屏，未测其显示FPS，不可混淆。
- tasks=[]已修正为可见显式等待；22项fixture测试通过。真实模型后续出现非法数量计划，执行器已明确拒绝。
  战斗中也记录到少量原生动作拒绝，未完成根因细分；不能宣称模型/引擎错误始终为0。
- 每方可配置ARENA_P1/P2_PROVIDER、ARENA_P1/P2_API_KEY、ARENA_P1/P2_MODEL；空key禁止回退。
  本次使用已有同一DeepSeek key，不同key隔离只做fixture测试。
- 原U盘/Lutris配置未改；旧Linux/pygame入口保留。证据见STATUS末尾及logs/native_deepseek_verified.*。

## 最新进展（2026-10-07：原生观战最小实验）

- 用户授权只读使用 U 盘 Windows SC2；复制资源至项目 native-runtime/data-copy，原游戏/原 Lutris 配置未改。
- 按用户指定 GE-Proton11-7 + 独立前缀，已运行原生 SC2 Observer（所有玩家视角）。
- **内置电脑对战验证，未接 LLM**：1600×900，DXVK/RX 9070 XT，开局截图451.4fps；
  非整局平均。realtime=True，倒计时后30秒游戏/墙钟比例0.98114。
- 入口：`./run_sc2.sh .venv311/bin/python native_observer.py --seconds 600`。
- 实际API版本5.0.15.97579；安装清单标5.0.16.97579。不能假定与Linux4.10联机兼容。
- 原生UI已截图，键鼠自由镜头尚需人工体验；原始记录见STATUS末尾及logs/native_*。
- 下一步才是让项目AI接入一致版本的原生引擎；现有sc2_client.py仍是Linux/pygame路径。

## 最新进展（2026-10-07：强制 realtime=True）

当前生产入口已改用 raw 单位控制，以下旧架构说明仅保留作历史记录。
- sc2_client.py / live_viewer.py → RawArena → Commander（后台规划+任务队列+独立ReflexNet）。
- 镜头与单位动作同批发送；移动镜头不会替换AI动作。屏幕坐标执行器保留在旧文件中，但新入口不使用它。
- 12秒延迟规划器下，双方镜头离开基地仍建成SCV/补给站/兵营/Marine；真实时间比例约1.02×。
- 受控真实引擎防守/撤退测试与自然完整对局评测严格分开；Very Easy自然局脚本基线首轮1/1获胜（非真实LLM胜率，样本不足以推断稳定性）；详细证据见STATUS末尾。
- SPACE只停止AI下单，不能暂停实时引擎；画面会明确提示。
- 新执行器目前聚焦Terran SCV/补给站/兵营/Marine，不要假设旧技能库全部移植完毕。
- 桌面最终观测约21.8fps、游戏/墙钟1.021×；15项单元测试、真实镜头外建造、受控反射、浏览器输出通过。
- 下一次排障优先读 logs/realtime_*.log 和 STATUS.md 本节对应记录。

## 最新进展（2026-10-06 性能优化）

- 已先做真实引擎性能分解，再优化。1024×768 双 AsyncPolicy/mock 桌面实测
  **16.77–16.84 fps → 24.85–25.29 fps（约 +49%）**，未降低画面分辨率。
- 主成本是 env.step 中的 observation 链路；关闭未显示的 P2 RGB 是主要收益。
- Surface 改为 frombuffer，但必须保留 int32→uint8 转换（pysc2 v3 的真实返回类型）。
- World 从每帧三次解析改为两次并复用；技能必须每步更新 World，不能跟随 LLM 的24步频率。
- 新增 profile_client.py（完整桌面循环分段计时）和 verify_client_perf.py（真实引擎技能链/镜头验证）。
- 真实引擎确定技能回归在 step_mul=1、3582 步完成双方 SCV/补给站/兵营/Marine，镜头移动检查通过（logs/perf_chain_single.log）。
- 具体前提、各轮数据、失败实验和复现命令见 STATUS.md 最后“性能分解与优化”一节。
- 后续性能工作应继续分解 observation 内部渲染/协议/transform 成本；不要重复猜测 parse_world。

## 0. 一句话目标
让多个 LLM 接入各自 apikey，在星际争霸2 里对战；**LLM 只做高层决策，脚本层负责所有微操**，
用户能像看自己玩游戏一样观看（清晰画面 + 自由移动镜头），不需要自己操作。

## 1. 环境（这台机器特有的约束，别假设标准环境）

| 项目 | 值 |
|---|---|
| 系统 | CachyOS (Arch 系)，Linux 7.2.9，**无 sudo 权限**（no-new-privileges） |
| GPU | AMD RX 9070 (RDNA4)，Mesa 26.2.4 |
| 项目根 | `/home/tyrion/Projects/wolf/sc2-arena` |
| 引擎 | SC2 **4.10** headless Linux 官方包（`engine/StarCraftII/`） |
| 渲染 | **OSMesa 软件渲染**（llvmpipe），SC2 的 EGL 路径是坏的 |
| Python | `.venv311/bin/python` (3.11.17，uv 装的隔离环境) |
| pysc2 | **v3.0**（源码在 `pysc2-src/`，已打补丁） |
| 入口脚本 | `./run_sc2.sh <命令>` — 必须用它，它设置了 SC2PATH/LD_LIBRARY_PATH/PYSC2_OSMESA 并加载 `.env` |
| 网络 | pypi.org 直连 SSL 不稳，**用清华镜像**；GitHub 可达 |

## 2. 架构（三层，已完成）

```
LLM 层    arena/llm.py + arena/policy.py
          输出: {"skill": "train_unit", "args": {"unit": "marine"}}
          坐标从不进入 LLM 视野。AsyncPolicy 在后台线程调用，不阻塞游戏。
Skill 层  arena/core.py  (11 个技能)
          supply_depot / barracks / train_unit / train_worker / gather /
          attack_nearest / move_out / expand / build_structure / no_op
          负责: 选工人、选址、等前置、自检成败、换位置、熔断
引擎层    pysc2 v3.0 + SC2 4.10 + OSMesa
```

## 3. 已完成并验证的功能

- 端到端对局：`Barracks x1, CommandCenter x1, Marine x2, SCV x21, SupplyDepot x2`，
  `supply 24/31`，双方同时达成（`logs/chain_v4.log`）
- 9 家 LLM provider 适配（openai/anthropic/deepseek/moonshot/dashscope/zhipu/groq/ollama/mock）
- `live_viewer.py`：浏览器 MJPEG + 双方 LLM 决策面板
- `sc2_client.py`：**pygame 桌面客户端**，1024x768，支持方向键/WASD/鼠标边缘移动镜头
- 熔断器：技能连续 3 次真失败就废弃并重新决策（`waiting` 状态不计入）

## 4. 原始待办（以下为优化前记录，最新结果见顶部和 STATUS.md 末尾）

**帧率偏低**：桌面客户端 1024x768 只有 **17fps**（双 AI），用户对比自己玩 SC2 的几百帧。

已知的分辨率/帧率基准（单 AI、`logs/res_test.log`）：

| 分辨率 | fps |
|---|---|
| 256x192 | 51.6 |
| 512x384 | 48.8 |
| 640x480 | 41.3 |
| 1024x768 | 31.0 |

**注意**：自己玩 SC2 是 GPU 原生渲染，本项目是
`SC2 → OSMesa CPU 光栅化 → protobuf → websocket → Python/numpy → pygame`，
每帧走一整圈，**不可能达到几百帧**。但 17fps 明显偏低，有优化空间。

**下一步建议**（尚未做，被余额中断）：
1. 性能分解：分别测 `env.step()` / `parse_world()` / `numpy→pygame surface` 的耗时，定位真瓶颈
   （`parse_world` 目前**每帧调用两次** P1/P2，但 AI 每 24 步才决策一次 —— 明显浪费）
2. 只在需要决策时解析 World；渲染路径单独优化（`pygame.image.frombuffer` 比 `surfarray.make_surface` 快）
3. 考虑把 SC2 的输出分辨率与窗口分辨率解耦

## 5. 踩过的坑（**核心资产，逐条都是实测**）

### 5.1 OSMesa 渲染链（跨发行版拼装，因为 Mesa 26 移除了 OSMesa）
- `SC2.4.10` 官方 Linux 包只有 EGL/OSMesa 两种渲染后端；**EGL 在这台机器上必失败**：
  `Failed to create and initialize a valid EGL display! Devices tried: 0`
  （libEGL 能加载，但建不出 display；surfaceless/gbm/x11/device 四种平台全试过）
- Mesa 26.2.4 **不再提供 libOSMesa**；AUR / Arch 官方源 / conda-forge 都没有
- 解法：从 Debian 取 `libosmesa6_25.0.7` + `libllvm19` + `libz3-4`，加上系统 `libedit.so.0` 改名成
  `libedit.so.2`，全部放 `osmesa-lib/`，靠 `LD_LIBRARY_PATH` 生效（无 root，装不进 /usr/local/lib）
- 验证：`Successfully loaded OSMesa library!` + `Renderer: llvmpipe (LLVM 19.1.7, 256 bits)`

### 5.2 pysc2 的三个补丁（缺一个就静默失败）
1. **`platforms.py`**：移除 `known_gl_libs` 里的 EGL 项（否则优先选坏的 EGL）
2. **`platforms.py`**：加 `PYSC2_OSMESA` 显式覆盖 —— **`ldconfig -p` 完全忽略
   `LD_LIBRARY_PATH`**，探测不到就静默加 `-headlessNoRender`，RGB 永远黑着且不报错
3. **`platforms.py`**：不要注入 SC2 自带的 `Libs/` —— 那个 `libstdc++.so.6` 是 2019 年的
   (GLIBCXX_3.4.21)，会让 libOSMesa 报 `Failed to load library file!` 然后悄悄退回 stub renderer

### 5.3 建造（最难的一环，五层叠加）
1. **`SC2Env(ensure_available_actions=False)` 必须设**。`Build_*` 的 `avail_fn is None`，
   是否出现在 `available_actions` 完全看游戏；默认检查会拒绝一切建造命令
2. **`Build_*` 能力只在「已选中工人」后才上报**。在选人**之前**查它 → 永远 False →
   技能停在"等待"，连工人都没选 → 死锁
3. **`Build_*` 有前置条件**：兵营在无补给站时**不给能力**（345 矿也不给）。
   所以"不可用"要区分「前置/资源未满足 → 等待」和「这块地不行 → 换位置」
4. **实测偏移列表必须过 `_blocked()` 检查**，否则兵营会去重试被自己补给站占掉的格子
5. **`feature_units.is_selected` 不可靠**（恒为 False）。要用自跟踪的 `_picked` 状态

### 5.4 坐标系（我在这上面浪费了大量时间）
- `feature_units` 的 x/y **就是屏幕像素坐标**（0..64），可直接给 `select_point`
  （证据：`F.select_point('select',[36,35])` 选中成功）
- **视野外的单位返回负坐标**（见过矿物在 `(-25,-30)`），直接发动作会
  `Argument is out of range`。必须过滤 `0 <= x < 64`
- `camera_position` 是**另一套空间**（minimap 像素），别拿它和 feature_units 混算
- `move_camera` 的参数 ≈ `camera_position / 2`
- 动作要**直接调 `FUNCTIONS.xxx(...)`**（官方写法），不要手工拼 `FunctionCall(id, args)`

### 5.5 其他
- **pysc2 v4.0 有 `extra_ports` bug**（透传给 `subprocess.Popen` 导致 TypeError），
  且 v4.0 面向 SC2 5.x；**v3.0 才对应 SC2 4.10**
- pysc2 v3.0 在 Python 3.11 上需要补丁：`colors.py` 的
  `random.shuffle(palette, lambda: 0.5)`（双参数形式 Py3.11 已移除）
- **Python 3.14 装不上 pysc2**（`enum34` 需要已移除的 `distutils`）→ 用 3.11
- 沙箱：`$HOME` 只读（uv 缓存、pip 缓存都要重定向到工作区）、**无 sudo**
- `absl.flags` 必须经过 `app.run(main)` 解析，否则 `UnparsedFlagAccessError`
- `save_replay(replay_dir)` v3.0 只接受一个位置参数
- `agent.step()` 要传**整个 timestep**（`run_loop.py:42` 的权威用法），不是 `obs.observation`

## 6. 怎么跑 / 怎么验证

```bash
cd /home/tyrion/Projects/wolf/sc2-arena

# 填 key（已有一个 .env，权限 600）
nano .env          # ARENA_PROVIDER=deepseek / DEEPSEEK_API_KEY=sk-xxx

# 先 10 秒验证 key 和输出格式（不发车）
./run_sc2.sh .venv311/bin/python check_key.py

# 桌面客户端（用户要的这个）
./run_sc2.sh .venv311/bin/python sc2_client.py Acropolis
#   方向键/WASD 或鼠标移到边缘 = 移动镜头；空格仅暂停AI下单（游戏继续）；F 统计；ESC 退出

# 浏览器版（旧）
./run_sc2.sh .venv311/bin/python live_viewer.py Acropolis 8099
```

**验证纪律**（用户强制要求）：
- 先说清结论建立在什么前提上；没验证的前提要标明
- **禁止静默回退** —— "找不到就退回旧行为"必须留下可见痕迹
- 先跑最小判别实验再写实现；不要把"在我的 fixture 里通过"说成"已验证"
- 用户明确讨厌：功能 100% 失效却看起来一切正常

## 7. 关键文件

| 文件 | 作用 |
|---|---|
| `run_sc2.sh` | **唯一入口**，设置所有环境变量 + 加载 `.env` |
| `arena/core.py` | World 解析 + 11 个技能（最核心） |
| `arena/llm.py` | 9 家 provider |
| `arena/policy.py` | `Policy`(同步) + `AsyncPolicy`(后台线程，非阻塞) |
| `sc2_client.py` | pygame 桌面客户端（用户当前要的） |
| `live_viewer.py` | 浏览器 MJPEG 版 |
| `STATUS.md` | 更详细的证据链（含每次实验的日志文件名） |
| `logs/*.log` | 所有实验的原始日志，排查时先看这里 |
