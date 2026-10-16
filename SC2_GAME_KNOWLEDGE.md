# 星际争霸 II 三族指挥与技能手册

范围：普通多人对战的人族、虫族、神族，不含战役、合作指挥官及自定义 MOD。以下目录来自已读取的原生 5.0.15.97579 元数据快照；每场游戏仍重新读取引擎并查询可用能力。目录存在不等于任意时刻可执行，也不代表每项均已实战验收。

## 宏任务与经济操作

LLM 决定经济、科技、阵容及作战时机；脚本选择单位、查询合法位置、等待资源和前置，观察实际完成。没有自动替模型选择科技路线。

单位完成度1不保证出生动画期间已开放移动/攻击。防守发单前查询原生可用性；
普通调兵保留原编队和目标最多等待8游戏秒，仍受原任务截止时间约束，
`native_ability_wait`报告缺失能力的己方单位。防守的明确拒绝按1/2/4秒退避，
连续5次则暂停该单位发令，在`command_rejections`报告，由模型决定取消或重下。

| 指令 | 参数及语义 |
|---|---|
| train_worker | count=1..200，生产本族工人；可选 args.queue_mode=append 显式追加 |
| build_supply | 本族人口单位/建筑，外层 count=1..200 有限批次；建筑逐个确认建成，不自动循环 |
| train_unit | args.unit 使用本族目录名，count=1..200；可选 args.queue_mode=append 显式追加 |
| train_unit（神族定点折跃） | 可选 args.warp_target=[x,y]；仅用就绪折跃门，在指定中心3格内查询合法供能落点；无合法落点等待，不改回主矿。不同位置分别计数 |
| inject_larva | 虫族一次注卵，count=1，args.base=home/expansion/已观测site_N；timeout_s可选1..300秒，默认90，跨地图步行应显式留足时间；不自动循环 |
| build_structure | args.structure 使用本族目录名，可选 base=home/expansion/观察里的 site_N，外层 count=1..200 有限批次 |
| research | args.upgrade 使用目录名，外层 count=1；看到升级完成才算成功 |
| morph_unit | args.unit 使用可切换形态名；可选 target=home/rally/enemy_start/nearest_enemy；count=1..200 |
| gather_gas | args.workers_per_refinery=0..3，每口已建成气矿持续维持此分配；0 表示释放回矿 |
| expand | 在已知矿区建新基地；可选 args.base=expansion/观察里的 site_N，count=1..200 有限批次，明确同一site无法重复开矿 |
| gather | 将过饱和矿区的多余工人分配到缺工基地 |
| cancel_task | args.task_id 为己方控兵任务或完全未发单的宏；不取消已经发给引擎的订单/升降，不退款 |
| stop_production | args.task_id 为己方工人/部队生产目标；只停止未购买数量，保留已付费虫卵、变异及队列等待真实产出；不取消原生订单、不推断退款；不用于建筑、科研、免费形态或弹药 |
| cancel_native_morph | args.task_id 为己方付费建筑升级宏；停止原目标，仅对绑定的已观察升级发取消，最多30游戏秒，不撤销已完成形态，不推断退款 |

一般 args={}。生产、建造、扩张与形态切换的 count 是期望新增产品数量，跳虫等按实际批次生产，可能超过奇数请求。科研、采气等非批量任务外层 count 必须为1。建筑批次逐个确认建成后购买下一个；同一活跃意图重复合并，不追加数量。等待不会自动补建缺失前置；模型需显式安排它们。

水晶塔默认优先分散布点；普通候选全被原生拒绝或为空时，再查询更密的候选，
仅放宽水晶塔7格间距偏好，保留建筑碰撞、矿区、任务预留和原生合法性检查。
macro_placement_spacing_relaxed表示原生接受了候选，不代表已经付款或建成。

train_worker/train_unit 默认 queue_mode=coalesce：重复活动意图保留已有数量，不追加。
确实要增兵时，用 `{"skill":"train_unit","count":8,"args":{"unit":"roach","queue_mode":"append"}}`。
它向同产品的活动批次新增8只，保留已发单、虫卵及出生确认；没有活动批次则新建有限批次。
每次新增1..200，同产品每回复只能一条append，每个活动批次剩余待产最多200，已确认产出不占此上限；建筑、形态、弹药不支持追加。
任务requested保留累计下单历史，可能超过200；remaining才是上限口径，原订单、产出和请求去重记录不重置。
同请求重复处理不重复追加。`active_tasks.production_quantity` 报告 requested、issued、observed_completed、unissued、awaiting_completion、remaining。
task_coalesced明确报告added_quantity=0和原任务ID，不能将“已接受计划”误当新增订单。
任何非法参数仍整批拒绝，合法造兵也没有入队；修正后必须显式重新下令。
模型请求耗尽后只完成已下的有限任务，不自动循环生产、补人口、补科技或选择阵容。

`stop_production`保留原requested数量，以cancelled_unissued报告停止的未购买数量；
在途数量仍继续观察，不把停止当作产出。之后明确append只购买新增数量，不恢复已停止部分；
默认coalesce仍不追加。需要取消已经发给引擎的原生生产时，另按专用取消工具的支持范围处理。

`rally_units`用`rally_producers`实际发布的label选择生产建筑，或用`producers:"all"`选择执行时
已完成的己方产能。已发布编号不会因其他建筑死亡或新生而重排；例如#1死亡后原#2仍是#2，
新建筑使用新编号。迟到回复的显式label还必须对应原观测里的真实建筑，不会改选另一座。
`expansion`/入口地标可用已建至少25%的己方地面基地作锚点；生产建筑本身仍须完成。
显式入口允许有可见敌军，模型自行决定风险；地形/建筑占位检查保留，自动集结继续避敌。
集结点只影响所选产能后续单位，不召回现有编队，不设置工人集结，也不替代折跃落点。

基地升级完成须核对实际形态与活动任务。本次Linux4.10原生控制中，Lair升级进行时仍观察到
Hatchery type86、progress1.0和订单1216；普通建筑完成度不代表Lair已升级完成。
该过程中`inject_larva`已原生确认可用：真实扣Queen能量并出现基地buff11，升级订单继续。
这确认一次注卵施放，不表示幼虫已经出生，也不自动安排下一次注卵。

**付费变异使用生产接口**：火蟑螂、潜伏者、巢虫领主、监察王虫用 train_unit；虫穴、主巢、巨型尖塔、轨道指挥用 build_structure。morph_unit 只用于目录里的免费可逆形态，例如架起/解架、埋地/出土、升降及折跃门转换。

`forms`中的ravager表示从ravager_burrowed解除埋地，不表示由蟑螂付费变异；活动形态任务的
`workflow.form_sources`给出所需已有执行者。虫族批量王虫可用train_unit unit=overlord count=N；
两条相同build_supply不会变成两只王虫。其他种族供给建筑仍用单条build_supply并核对实际完成。

实时观察可能比已发出的购买晚一帧。执行器临时预留尚未确认订单的矿、气、人口，防止跨帧重复使用；
看到原生订单/产品或拒绝后释放，不能据预留假定已支付或已完成。
等待任务的budget.unconfirmed_purchases_reserved_at_tick_start记录本帧采用的预留及语义。
幼虫购买可能选到与请求不同的己方幼虫；任一购买未确认时暂不发送下一笔幼虫购买。
确认后在已有幼虫生产目标之间轮转，不改变产品/数量，也不等待完整孵化才让下一目标下单。
等待不触发隐式重购；女王等其他生产者可继续生产。

核弹 nuke 和拦截机 interceptor 是补充库存任务，count 只能为1。核弹通过本方幽灵出现核打击能力来确认，缺少幽灵时会明确等待。航母机库数量在当前观察中不能直接完整读取；满库存由无异常状态、空闲航母的能力查询推断，并明确记录推断依据，不报告虚构的精确数量。

## Python 局部战术

侦察默认遇到可见敌人（包括工人）就停止并报告，接触不代表已经抵达目标。
需要穿过接触点继续查基地时，显式用`scout args.stop_on_contact=false`：仍报告接触，
按原路线移动直到实际到达/死亡/超时/取消，不自动攻击、撤退、补派或延长截止时间。
反复派默认scout可能再次停在同一工人；也可显式用order_units的move模式继续前进。

有`native_abilities.enabled=true`时，单次原生能力也可直接用`use_ability`，不必先写Python：
`args={caster:己方单位tag,ability:目录准确name,target:null或当前单位tag或[x,y],timeout_s:1..120}`。
外层count=1；每个施法者每轮一单，无自动重施。查看实时ready、能量、装载乘客及任务分配，
先释放被其他任务占用的单位；Nexus时空加速可与生产并存。ready只代表当前原生可用性，
不代表目标合法或战术已成功；实际拒绝、失去目标和超时都会反馈。购买/科研仍用宏。
能力报表可能按共同列值、精确坐标整数比例和字段名索引压缩，按context_policy还原；不要直接把缩放整数当作游戏坐标。

普通工人建造取消用`cancel_native_building args={task_id:己方建造宏ID}`：先停止该宏，
再取消关联的未完成建筑或建造者赶路，避免脚本随后再买。已完成建筑明确no-op；不推定退款。
这不是单位训练队列、科研或基地升级的取消接口。原`cancel_task`仍只撤销支持的控兵/未发单宏。

准备1..4个技能，manifest.unit 使用本族单位名，或 army/worker。army 包含常规作战与支援单位，排除工人、建筑、王虫、幼虫、核弹、机库拦截机和短命召唤物等；这些单位可用各自具体选择器。错误种族的选择器在准备验收时拒绝。

manifest.actions 可声明 cast。动作形如 {"action":"cast","unit_tags":[tag],"ability":"ctx.available_spells中实际名称","target":本地tag或[x,y]}。无目标法术省略 target，其他目标类型严格按 available_spells 填写。禁止以 cast 绕过付费生产和科研；实际下令前再次检查单位归属、局部范围、能量/冷却/能力。每个 tick 每单位最多1条指令，战术目标限分配小队20格内。

ctx.units 包含当前可见的已分配单位，并带 energy/energy_max、cargo_taken/cargo_max、passengers、buffs 和 orders；ctx.available_spells 是按当前能力查询过滤的名称、ID与目标类型。装入运输机的队员移入 ctx.embarked（tag/transport_tag/pos），不会直接判为死亡；需等待卸载后再命令该队员。运输机可作为局部观察的中心，但未分配的运输机仍不能下令。任务结束/失败对仍可观察到的非建筑队员请求 Stop；这不是引擎停止确认。

兴奋剂支持陆战队员和掠夺者，研究及生命值必须满足。坦克需先解架才能前进。法术源码由模型编写和隔离测试；已有的 JSON 顺序工作流仍使用原来的有限选择器，三族宏与 Python 才是本次扩展接口。完整 Python 合同见 AI_SKILL_MANUAL.md 第0节。


## 经济与阵容知识

矿物与瓦斯是不同资源。采气先造在喷泉上的气矿，再显式 gather_gas 分配工人；只有气矿不会自动采气。一般每口气矿3工人，按当前战术调整0..3。不要因早期瓦斯为0就永远只造矿兵。

持续生产工人，同时看每个基地和气矿的 assigned/ideal_harvesters；不要把所有工人塞在主矿。采矿工人能提高后续收入，过量采气会损失矿物收入。

expand 在可观察矿区建新基地，需要资源、地形、视野和防守。完成后继续生产工人；gather 将过饱和矿区的多余工人转移到缺工分矿。新基地本身不等于额外收入。

预留人口。build_supply 对人族造补给站、神族造水晶塔、虫族造王虫；供给建造需要时间。人口上限200。

科技前置不会凭空出现：明确安排建筑、附属建筑、采气、升级和部队。宏任务会等待缺失前置，不会代替你决定科研路线。不要重复提交相同活动任务。升级只有出现在 world.upgrades 才能认为完成。

根据侦察到的地面/空中、轻甲/重甲、隐形、范围伤害选择组合；混合输出、治疗、侦测和控制。单一枪兵可能适合一段时机，不应成为接口限制。侦测单位通常输出弱，需要保护。

完整 Python 选择器：adept, archon, armory, army, assimilator, auto_turret, baneling, baneling_nest, banshee, barracks, battlecruiser, brood_lord, broodling, bunker, carrier, changeling, colossus, command_center, corruptor, creep_tumor, creep_tumor_burrowed, creep_tumor_queen, cybernetics_core, cyclone, dark_shrine, dark_templar, disruptor, drone, engineering_bay, evolution_chamber, extractor, factory, fleet_beacon, forge, fusion_core, gateway, ghost, ghost_academy, greater_spire, hatchery, hellbat, hellion, high_templar, hive, hydralisk, hydralisk_den, immortal, infestation_pit, infestor, interceptor, lair, larva, liberator, liberator_ag, locust, locust_flying, lurker, lurker_burrowed, lurker_den, marauder, marine, medivac, missile_turret, mothership, mule, mutalisk, nexus, nuke, nydus_canal, nydus_network, observer, observer_surveillance_mode, oracle, orbital_command, overlord, overlord_transport, overseer, overseer_oversight_mode, phoenix, photon_cannon, planetary_fortress, probe, pylon, queen, ravager, raven, reaper, refinery, roach, roach_warren, robotics_bay, robotics_facility, scv, sensor_tower, sentry, shield_battery, siege_tank, siege_tank_sieged, spawning_pool, spine_crawler, spire, spore_crawler, stalker, stargate, starport, stasis_trap, supply_depot, swarm_host, swarm_host_burrowed, tempest, templar_archive, thor, thor_high_impact_mode, twilight_council, ultralisk, ultralisk_cavern, viking_assault, viking_fighter, viper, void_ray, warp_gate, warp_prism, warp_prism_phasing, widow_mine, widow_mine_burrowed, worker, zealot, zergling

## 人族（terran）

SCV建造期间持续工作，可用新SCV接手未完成建筑；气矿为refinery。兵营→重工厂→星港，科技实验室解锁高级兵，反应堆增加基础单位并行产能；二者占同一附属位置。轨道指挥可用矿骡和扫描，行星要塞偏防御。生化适合机动和医疗运输机配合；坦克架起后射程/范围压制但不能移动；机械化通常更耗瓦斯。升级步兵攻防与兴奋剂/战斗盾牌，或机械/飞船攻防，按主力选择。

### 单位与库存

- **ghost**：高价值施法步兵；EMP压制护盾与能量，狙击针对生物目标，核弹需另外制造。
  配方：`[{"minerals":150,"gas":125,"supply":2.0,"quantity":1,"producer":"barracks","requires":["ghost_academy","attached_tech_lab"]}]`
  局部能力候选（实时可用看 ctx.available_spells）：smart, stop_stop, move_move, patrol_patrol, hold_position_hold, attack_attack, behavior_hold_fire_on_ghost, behavior_cloak_on_ghost, effect_emp, effect_ghost_snipe

- **marauder**：重型步兵，擅长打装甲地面目标；不能对空，常与陆战队和医疗运输机混编。
  配方：`[{"minerals":100,"gas":25,"supply":2.0,"quantity":1,"producer":"barracks","requires":["tech_lab","attached_tech_lab"]}]`
  局部能力候选（实时可用看 ctx.available_spells）：smart, stop_stop, move_move, patrol_patrol, hold_position_hold, attack_attack, effect_stim_marauder

- **marine**：廉价远程步兵，可对地对空；配合医疗运输机、兴奋剂和护盾升级，怕范围伤害。
  配方：`[{"minerals":50,"gas":0,"supply":1.0,"quantity":1,"producer":"barracks","requires":[]}]`
  局部能力候选（实时可用看 ctx.available_spells）：smart, stop_stop, move_move, patrol_patrol, hold_position_hold, attack_attack, effect_stim_marine

- **reaper**：高速侦察和骚扰工人，能跨越部分悬崖；不适合正面承受集中火力。
  配方：`[{"minerals":50,"gas":50,"supply":1.0,"quantity":1,"producer":"barracks","requires":[]}]`
  局部能力候选（实时可用看 ctx.available_spells）：smart, stop_stop, move_move, patrol_patrol, hold_position_hold, attack_attack, effect_kd8_charge

- **scv**：人族工人：采矿、采气、建造及修理机械单位和建筑；施工期间占用工人。
  配方：`[{"minerals":50,"gas":0,"supply":1.0,"quantity":1,"producer":"command_center","requires":[]},{"minerals":50,"gas":0,"supply":1.0,"quantity":1,"producer":"orbital_command","requires":[]},{"minerals":50,"gas":0,"supply":1.0,"quantity":1,"producer":"planetary_fortress","requires":[]}]`
  局部能力候选（实时可用看 ctx.available_spells）：smart, stop_stop, move_move, patrol_patrol, hold_position_hold, attack_attack, effect_spray_terran, harvest_gather_scv, effect_repair_scv

- **cyclone**：机动机械火力，通过锁定追击目标；注意射程、地形和保护。
  配方：`[{"minerals":150,"gas":100,"supply":3.0,"quantity":1,"producer":"factory","requires":["tech_lab","attached_tech_lab"]}]`
  局部能力候选（实时可用看 ctx.available_spells）：smart, stop_stop, move_move, patrol_patrol, hold_position_hold, attack_attack, effect_lock_on

- **hellion**：高速对地骚扰，火焰适合成排轻甲和工人；不能对空。
  配方：`[{"minerals":100,"gas":0,"supply":2.0,"quantity":1,"producer":"factory","requires":[]}]`
  局部能力候选（实时可用看 ctx.available_spells）：smart, stop_stop, move_move, patrol_patrol, hold_position_hold, attack_attack, morph_hellbat

- **hellbat**：火车的近战火焰形态，用于前排和反轻甲；机动性下降。
  配方：`[{"minerals":100,"gas":0,"supply":2.0,"quantity":1,"producer":"factory","requires":["armory"]}]`
  局部能力候选（实时可用看 ctx.available_spells）：smart, stop_stop, move_move, patrol_patrol, hold_position_hold, attack_attack, morph_hellion

- **siege_tank**：机械化远程火力；移动到阵地后架起，可打地面范围伤害，不能对空。
  配方：`[{"minerals":150,"gas":125,"supply":3.0,"quantity":1,"producer":"factory","requires":["tech_lab","attached_tech_lab"]}]`
  局部能力候选（实时可用看 ctx.available_spells）：smart, stop_stop, move_move, patrol_patrol, hold_position_hold, attack_attack, morph_siege_mode

- **thor**：重型机械单位，对地和对空火力强但笨重；切换对空模式以适应目标。
  配方：`[{"minerals":300,"gas":200,"supply":6.0,"quantity":1,"producer":"factory","requires":["armory","attached_tech_lab"]}]`
  局部能力候选（实时可用看 ctx.available_spells）：smart, stop_stop, move_move, patrol_patrol, hold_position_hold, attack_attack, morph_thor_high_impact_mode

- **widow_mine**：埋地后伏击地面或空中目标；攻击有冷却，注意溅射与侦测。
  配方：`[{"minerals":75,"gas":25,"supply":2.0,"quantity":1,"producer":"factory","requires":[]}]`
  局部能力候选（实时可用看 ctx.available_spells）：smart, stop_stop, move_move, patrol_patrol, hold_position_hold, scan_move, burrow_down_widow_mine

- **banshee**：对地空袭和骚扰，可研究隐形；不能对空，需要规避侦测和防空。
  配方：`[{"minerals":150,"gas":100,"supply":3.0,"quantity":1,"producer":"starport","requires":["tech_lab","attached_tech_lab"]}]`
  局部能力候选（实时可用看 ctx.available_spells）：smart, stop_stop, move_move, patrol_patrol, hold_position_hold, attack_attack, behavior_cloak_on_banshee

- **battlecruiser**：昂贵的重型空军，可对地对空，依靠技能机动和集中打击；需要强经济。
  配方：`[{"minerals":400,"gas":300,"supply":6.0,"quantity":1,"producer":"starport","requires":["fusion_core","attached_tech_lab"]}]`
  局部能力候选（实时可用看 ctx.available_spells）：smart, effect_yamato_gun, effect_tactical_jump, attack_battlecruiser, move_battlecruiser, patrol_battlecruiser, hold_position_battlecruiser, stop_battlecruiser

- **liberator**：空中可对空，部署对地模式后封锁指定区域；移动前解放部署。
  配方：`[{"minerals":150,"gas":125,"supply":3.0,"quantity":1,"producer":"starport","requires":[]}]`
  局部能力候选（实时可用看 ctx.available_spells）：smart, stop_stop, move_move, patrol_patrol, hold_position_hold, attack_attack, morph_liberator_ag_mode

- **medivac**：治疗生物部队、运输和空投；自身不负责常规攻击，需要护航。
  配方：`[{"minerals":100,"gas":100,"supply":2.0,"quantity":1,"producer":"starport","requires":[]}]`
  局部能力候选（实时可用看 ctx.available_spells）：smart, stop_stop, move_move, patrol_patrol, hold_position_hold, scan_move, effect_heal, load_medivac, effect_medivac_ignite_afterburners

- **raven**：侦测隐形的支援施法飞机；依赖能量和技能，不应当作普通输出兵。
  配方：`[{"minerals":100,"gas":150,"supply":2.0,"quantity":1,"producer":"starport","requires":["tech_lab","attached_tech_lab"]}]`
  局部能力候选（实时可用看 ctx.available_spells）：smart, stop_stop, move_move, patrol_patrol, hold_position_hold, scan_move, effect_auto_turret, effect_interference_matrix, effect_anti_armor_missile

- **viking_fighter**：维京战机形态用于制空和保护地面阵地；对地需降落。
  配方：`[{"minerals":125,"gas":75,"supply":2.0,"quantity":1,"producer":"starport","requires":[]}]`
  局部能力候选（实时可用看 ctx.available_spells）：smart, stop_stop, move_move, patrol_patrol, hold_position_hold, attack_attack, morph_viking_assault_mode

- **nuke**：幽灵使用的核弹弹药，制造后还需要幽灵施放和引导。
  配方：`[{"minerals":100,"gas":100,"supply":0.0,"quantity":1,"producer":"ghost_academy","requires":[]}]`

### 建筑与附属建筑

- **barracks_tech_lab**：挂接科技实验室：解锁对应生产建筑高级单位和科研；必须附在正确建筑上。
  配方：`[{"minerals":50,"gas":25,"supply":0.0,"quantity":1,"producer":"barracks","requires":[]}]`

- **barracks_reactor**：反应堆：提高对应基础单位生产并行容量；不解锁科技实验室单位。
  配方：`[{"minerals":50,"gas":50,"supply":0.0,"quantity":1,"producer":"barracks","requires":[]}]`

- **orbital_command**：轨道指挥，矿骡采矿和扫描侦测。
  配方：`[{"minerals":150,"gas":0,"supply":0,"quantity":1,"producer":"command_center","requires":["barracks"],"consumes_producer":true}]`

- **planetary_fortress**：基地防御升级，偏地面防守。
  配方：`[{"minerals":250,"gas":150,"supply":0,"quantity":1,"producer":"command_center","requires":["engineering_bay"],"consumes_producer":true}]`

- **factory_tech_lab**：挂接科技实验室：解锁对应生产建筑高级单位和科研；必须附在正确建筑上。
  配方：`[{"minerals":50,"gas":25,"supply":0.0,"quantity":1,"producer":"factory","requires":[]}]`

- **factory_reactor**：反应堆：提高对应基础单位生产并行容量；不解锁科技实验室单位。
  配方：`[{"minerals":50,"gas":50,"supply":0.0,"quantity":1,"producer":"factory","requires":[]}]`

- **auto_turret**：渡鸦临时炮塔。
  配方：`[{"minerals":0,"gas":0,"supply":0,"quantity":1,"producer":"raven","requires":[]}]`

- **armory**：机械/飞船攻防及高级机械前置。
  配方：`[{"minerals":150,"gas":50,"supply":0.0,"quantity":1,"producer":"scv","requires":["factory"]}]`

- **barracks**：人族步兵生产。
  配方：`[{"minerals":150,"gas":0,"supply":0.0,"quantity":1,"producer":"scv","requires":["supply_depot"]}]`

- **bunker**：载入步兵进行阵地防御；空地堡没有步兵火力。
  配方：`[{"minerals":100,"gas":0,"supply":0.0,"quantity":1,"producer":"scv","requires":["barracks"]}]`

- **command_center**：生产SCV、交矿与开矿；可升级轨道指挥或行星要塞。
  配方：`[{"minerals":300,"gas":0,"supply":0.0,"quantity":1,"producer":"scv","requires":[]}]`

- **engineering_bay**：步兵攻防和建筑防御科技。
  配方：`[{"minerals":125,"gas":0,"supply":0.0,"quantity":1,"producer":"scv","requires":["command_center"]}]`

- **factory**：地面机械生产。
  配方：`[{"minerals":150,"gas":100,"supply":0.0,"quantity":1,"producer":"scv","requires":["barracks"]}]`

- **fusion_core**：战列巡航舰等高级空军科技。
  配方：`[{"minerals":150,"gas":150,"supply":0.0,"quantity":1,"producer":"scv","requires":["starport"]}]`

- **ghost_academy**：幽灵科技与核弹。
  配方：`[{"minerals":150,"gas":50,"supply":0.0,"quantity":1,"producer":"scv","requires":["barracks"]}]`

- **missile_turret**：防空和侦测隐形。
  配方：`[{"minerals":100,"gas":0,"supply":0.0,"quantity":1,"producer":"scv","requires":["engineering_bay"]}]`

- **refinery**：建在瓦斯喷泉，需派SCV采气。
  配方：`[{"minerals":75,"gas":0,"supply":0.0,"quantity":1,"producer":"scv","requires":[]}]`

- **sensor_tower**：预警，不能代替侦测隐形。
  配方：`[{"minerals":100,"gas":50,"supply":0.0,"quantity":1,"producer":"scv","requires":["engineering_bay"]}]`

- **starport**：人族空军生产。
  配方：`[{"minerals":150,"gas":100,"supply":0.0,"quantity":1,"producer":"scv","requires":["factory"]}]`

- **supply_depot**：提供人口，可降下让路。
  配方：`[{"minerals":100,"gas":0,"supply":0.0,"quantity":1,"producer":"scv","requires":[]}]`

- **starport_tech_lab**：挂接科技实验室：解锁对应生产建筑高级单位和科研；必须附在正确建筑上。
  配方：`[{"minerals":50,"gas":25,"supply":0.0,"quantity":1,"producer":"starport","requires":[]}]`

- **starport_reactor**：反应堆：提高对应基础单位生产并行容量；不解锁科技实验室单位。
  配方：`[{"minerals":50,"gas":50,"supply":0.0,"quantity":1,"producer":"starport","requires":[]}]`

### 辅助单位与形态用途

- **siege_tank_sieged**：坦克架起形态：射程远但不能移动；前移和撤退前必须解架。

- **widow_mine_burrowed**：埋地地雷，适合路口伏击和区域防守；移动前需要起身。

- **thor_high_impact_mode**：雷神对空模式之一；依据敌方空军类型切换，并为其保留移动空间。

- **viking_assault**：维京地面形态，可攻击地面但失去战机形态的制空作用。

- **liberator_ag**：解放者对地部署形态，控制区域，无法随军直接移动。

- **mule**：轨道指挥中心召唤的临时采矿单位；不替代正常工人采气。

### 科研

- **terran_ship_weapons_level1**：提升名称指定兵种类别的武器伤害；高等级需要前一级与科技前置。
  配方：`{"minerals":100,"gas":100,"recipes":[{"producer":"armory","requires":[]}]}`

- **terran_ship_weapons_level2**：提升名称指定兵种类别的武器伤害；高等级需要前一级与科技前置。
  配方：`{"minerals":175,"gas":175,"recipes":[{"producer":"armory","requires":["terran_ship_weapons_level1"]}]}`

- **terran_ship_weapons_level3**：提升名称指定兵种类别的武器伤害；高等级需要前一级与科技前置。
  配方：`{"minerals":250,"gas":250,"recipes":[{"producer":"armory","requires":["terran_ship_weapons_level2"]}]}`

- **terran_vehicle_and_ship_armors_level1**：提升名称指定兵种类别的护甲；高等级需要前一级与科技前置。
  配方：`{"minerals":100,"gas":100,"recipes":[{"producer":"armory","requires":[]}]}`

- **terran_vehicle_and_ship_armors_level2**：提升名称指定兵种类别的护甲；高等级需要前一级与科技前置。
  配方：`{"minerals":175,"gas":175,"recipes":[{"producer":"armory","requires":["terran_vehicle_and_ship_armors_level1"]}]}`

- **terran_vehicle_and_ship_armors_level3**：提升名称指定兵种类别的护甲；高等级需要前一级与科技前置。
  配方：`{"minerals":250,"gas":250,"recipes":[{"producer":"armory","requires":["terran_vehicle_and_ship_armors_level2"]}]}`

- **terran_vehicle_weapons_level1**：提升名称指定兵种类别的武器伤害；高等级需要前一级与科技前置。
  配方：`{"minerals":100,"gas":100,"recipes":[{"producer":"armory","requires":[]}]}`

- **terran_vehicle_weapons_level2**：提升名称指定兵种类别的武器伤害；高等级需要前一级与科技前置。
  配方：`{"minerals":175,"gas":175,"recipes":[{"producer":"armory","requires":["terran_vehicle_weapons_level1"]}]}`

- **terran_vehicle_weapons_level3**：提升名称指定兵种类别的武器伤害；高等级需要前一级与科技前置。
  配方：`{"minerals":250,"gas":250,"recipes":[{"producer":"armory","requires":["terran_vehicle_weapons_level2"]}]}`

- **concussive_shells**：掠夺者攻击可减速符合条件的目标。
  配方：`{"minerals":50,"gas":50,"recipes":[{"producer":"barracks_tech_lab","requires":[]}]}`

- **combat_shield**：提高陆战队员生命上限。
  配方：`{"minerals":100,"gas":100,"recipes":[{"producer":"barracks_tech_lab","requires":[]}]}`

- **stimpack**：解锁陆战队员/掠夺者兴奋剂，消耗生命换取短时攻速和移速；研究后仍要施放，配合治疗。
  配方：`{"minerals":100,"gas":100,"recipes":[{"producer":"barracks_tech_lab","requires":[]}]}`

- **hi_sec_auto_tracking**：提高人族防御建筑的射程。
  配方：`{"minerals":100,"gas":100,"recipes":[{"producer":"engineering_bay","requires":[]}]}`

- **terran_structure_armor**：提高建筑护甲。
  配方：`{"minerals":150,"gas":150,"recipes":[{"producer":"engineering_bay","requires":[]}]}`

- **terran_infantry_armors_level1**：提升名称指定兵种类别的护甲；高等级需要前一级与科技前置。
  配方：`{"minerals":100,"gas":100,"recipes":[{"producer":"engineering_bay","requires":[]}]}`

- **terran_infantry_armors_level2**：提升名称指定兵种类别的护甲；高等级需要前一级与科技前置。
  配方：`{"minerals":150,"gas":150,"recipes":[{"producer":"engineering_bay","requires":["armory","terran_infantry_armors_level1"]}]}`

- **terran_infantry_armors_level3**：提升名称指定兵种类别的护甲；高等级需要前一级与科技前置。
  配方：`{"minerals":200,"gas":200,"recipes":[{"producer":"engineering_bay","requires":["armory","terran_infantry_armors_level2"]}]}`

- **terran_infantry_weapons_level1**：提升名称指定兵种类别的武器伤害；高等级需要前一级与科技前置。
  配方：`{"minerals":100,"gas":100,"recipes":[{"producer":"engineering_bay","requires":[]}]}`

- **terran_infantry_weapons_level2**：提升名称指定兵种类别的武器伤害；高等级需要前一级与科技前置。
  配方：`{"minerals":150,"gas":150,"recipes":[{"producer":"engineering_bay","requires":["armory","terran_infantry_weapons_level1"]}]}`

- **terran_infantry_weapons_level3**：提升名称指定兵种类别的武器伤害；高等级需要前一级与科技前置。
  配方：`{"minerals":200,"gas":200,"recipes":[{"producer":"engineering_bay","requires":["armory","terran_infantry_weapons_level2"]}]}`

- **lock_on**：旋风锁定相关强化；具体效果服从当前地图版本。
  配方：`{"minerals":100,"gas":100,"recipes":[{"producer":"factory_tech_lab","requires":[]}]}`

- **drilling_claws**：强化寡妇雷钻地。
  配方：`{"minerals":75,"gas":75,"recipes":[{"producer":"factory_tech_lab","requires":["armory"]}]}`

- **infernal_preigniter**：提高恶火/恶蝠对轻甲威胁。
  配方：`{"minerals":100,"gas":100,"recipes":[{"producer":"factory_tech_lab","requires":[]}]}`

- **smart_servos**：加快机械单位模式转换。
  配方：`{"minerals":100,"gas":100,"recipes":[{"producer":"factory_tech_lab","requires":["armory"]}]}`

- **weapon_refit**：战列巡航舰大和炮相关科技。
  配方：`{"minerals":150,"gas":150,"recipes":[{"producer":"fusion_core","requires":[]}]}`

- **advanced_ballistics**：解放者对地模式射程强化。
  配方：`{"minerals":150,"gas":150,"recipes":[{"producer":"fusion_core","requires":[]}]}`

- **medivac_caduceus_reactor**：医疗运输机能量/治疗相关强化，依当前版本。
  配方：`{"minerals":100,"gas":100,"recipes":[{"producer":"fusion_core","requires":[]}]}`

- **personal_cloaking**：幽灵隐形。
  配方：`{"minerals":150,"gas":150,"recipes":[{"producer":"ghost_academy","requires":[]}]}`

- **cloaking_field**：女妖隐形。
  配方：`{"minerals":100,"gas":100,"recipes":[{"producer":"starport_tech_lab","requires":[]}]}`

- **hyperflight_rotors**：女妖移动强化。
  配方：`{"minerals":125,"gas":125,"recipes":[{"producer":"starport_tech_lab","requires":[]}]}`

- **interference_matrix**：渡鸦干扰矩阵科技。
  配方：`{"minerals":50,"gas":50,"recipes":[{"producer":"starport_tech_lab","requires":[]}]}`

- **corvid_reactor**：渡鸦能量相关强化。
  配方：`{"minerals":150,"gas":150,"recipes":[{"producer":"tech_lab","requires":[]}]}`

### 可切换形态

siege_tank_sieged, siege_tank, viking_assault, viking_fighter, hellbat, hellion, thor_high_impact_mode, thor, widow_mine_burrowed, widow_mine, supply_depot_lowered, supply_depot, barracks_flying, barracks, command_center_flying, command_center, factory_flying, factory, starport_flying, starport, orbital_command_flying, orbital_command, liberator_ag, liberator

## 虫族（zerg）

幼虫生产工人、王虫和多数兵种；建筑消耗工蜂，记得补工人。女王可注卵、铺菌毯和治疗。建筑多依赖菌毯，气矿为extractor。孵化场→虫穴→主巢解锁科技；变异会消耗原单位，例如蟑螂→火蟑螂、刺蛇→潜伏者、腐化者→巢虫领主。不要把总兵种价值当变异付款。王虫提供人口，监察王虫提供侦测。经济与兵力共享幼虫，要按威胁取舍。

### 单位与库存

- **brood_lord**：由腐化者变形的远程对地攻城空军；缓慢且需要防空保护。
  配方：`[{"minerals":150,"gas":150,"supply":2.0,"quantity":1,"producer":"corruptor","requires":["greater_spire"],"consumes_producer":true}]`
  局部能力候选（实时可用看 ctx.available_spells）：smart, stop_stop, move_move, patrol_patrol, hold_position_hold, attack_attack

- **queen**：防空和基地防守、注卵、铺菌毯与治疗；能量分配由战术决定。
  配方：`[{"minerals":175,"gas":0,"supply":2.0,"quantity":1,"producer":"hatchery","requires":["spawning_pool"]},{"minerals":175,"gas":0,"supply":2.0,"quantity":1,"producer":"hive","requires":["spawning_pool"]},{"minerals":175,"gas":0,"supply":2.0,"quantity":1,"producer":"lair","requires":["spawning_pool"]}]`
  局部能力候选（实时可用看 ctx.available_spells）：smart, stop_stop, move_move, patrol_patrol, hold_position_hold, attack_attack, effect_inject_larva, burrow_down_queen, effect_transfusion, build_creep_tumor_queen, build_creep_tumor

- **lurker**：由刺蛇变形，埋地后形成对地穿透火力；需要侦察、保护和阵地切换。
  配方：`[{"minerals":50,"gas":100,"supply":1.0,"quantity":1,"producer":"hydralisk","requires":["lurker_den"],"consumes_producer":true}]`
  局部能力候选（实时可用看 ctx.available_spells）：smart, stop_stop, move_move, patrol_patrol, hold_position_hold, scan_move, attack_attack, burrow_down_lurker

- **corruptor**：针对敌方空军的单位；常规攻击不能打地面，后续可变形巢虫领主。
  配方：`[{"minerals":150,"gas":100,"supply":2.0,"quantity":1,"producer":"larva","requires":["spire"],"consumes_producer":true}]`
  局部能力候选（实时可用看 ctx.available_spells）：smart, stop_stop, move_move, patrol_patrol, hold_position_hold, attack_attack, effect_caustic_spray

- **drone**：虫族工人：采矿、采气，建造时自身消耗成为建筑；需补回经济人口。
  配方：`[{"minerals":50,"gas":0,"supply":1.0,"quantity":1,"producer":"larva","requires":[],"consumes_producer":true}]`
  局部能力候选（实时可用看 ctx.available_spells）：smart, stop_stop, move_move, patrol_patrol, hold_position_hold, attack_attack, effect_spray_zerg, harvest_gather_drone, burrow_down_drone

- **hydralisk**：可对地对空的远程输出；较脆弱，需要前排及机动和射程升级。
  配方：`[{"minerals":100,"gas":50,"supply":2.0,"quantity":1,"producer":"larva","requires":["hydralisk_den"],"consumes_producer":true}]`
  局部能力候选（实时可用看 ctx.available_spells）：smart, stop_stop, move_move, patrol_patrol, hold_position_hold, attack_attack, burrow_down_hydralisk

- **infestor**：施法控制单位，依赖能量、研究和目标选择；不能当作普通输出兵。
  配方：`[{"minerals":100,"gas":150,"supply":2.0,"quantity":1,"producer":"larva","requires":["infestation_pit"],"consumes_producer":true}]`
  局部能力候选（实时可用看 ctx.available_spells）：smart, stop_stop, move_move, patrol_patrol, hold_position_hold, scan_move, effect_fungal_growth, effect_neural_parasite, burrow_down_infestor_terran, burrow_down_infestor, effect_amorphous_armorcloud

- **mutalisk**：快速飞行骚扰，可对地对空；适合多线和避开正面重防空。
  配方：`[{"minerals":100,"gas":100,"supply":2.0,"quantity":1,"producer":"larva","requires":["spire"],"consumes_producer":true}]`
  局部能力候选（实时可用看 ctx.available_spells）：smart, stop_stop, move_move, patrol_patrol, hold_position_hold, attack_attack

- **overlord**：提供人口上限并侦察；通常不具备反隐形能力，也不负责作战。
  配方：`[{"minerals":100,"gas":0,"supply":0.0,"quantity":1,"producer":"larva","requires":[],"consumes_producer":true}]`
  局部能力候选（实时可用看 ctx.available_spells）：smart, stop_stop, move_move, patrol_patrol, hold_position_hold, scan_move, behavior_generate_creep_on

- **roach**：较耐打的中期对地单位，适合正面推进；不能对空。
  配方：`[{"minerals":75,"gas":25,"supply":2.0,"quantity":1,"producer":"larva","requires":["roach_warren"],"consumes_producer":true}]`
  局部能力候选（实时可用看 ctx.available_spells）：smart, stop_stop, move_move, patrol_patrol, hold_position_hold, attack_attack, burrow_down_roach

- **swarm_host**：通过周期性释放蝗虫施压，适合消耗和攻城；本体需要保护和撤离。
  配方：`[{"minerals":100,"gas":75,"supply":3.0,"quantity":1,"producer":"larva","requires":["infestation_pit"],"consumes_producer":true}]`
  局部能力候选（实时可用看 ctx.available_spells）：smart, stop_stop, move_move, patrol_patrol, hold_position_hold, scan_move, burrow_down_swarm_host, effect_spawn_locusts

- **ultralisk**：高投入的重型近战单位，适合冲击地面阵线；需要通行空间和防空配合。
  配方：`[{"minerals":275,"gas":200,"supply":6.0,"quantity":1,"producer":"larva","requires":["ultralisk_cavern"],"consumes_producer":true}]`
  局部能力候选（实时可用看 ctx.available_spells）：smart, stop_stop, move_move, patrol_patrol, hold_position_hold, attack_attack, burrow_down_ultralisk

- **viper**：高科技支援施法空军，用拉拽和区域技能破阵；自身不负责常规输出。
  配方：`[{"minerals":100,"gas":200,"supply":3.0,"quantity":1,"producer":"larva","requires":["hive"],"consumes_producer":true}]`
  局部能力候选（实时可用看 ctx.available_spells）：smart, stop_stop, move_move, patrol_patrol, hold_position_hold, scan_move, effect_blinding_cloud, effect_abduct, effect_viper_consume, effect_parasitic_bomb

- **zergling**：快速近战单位，侦察、包围和多线袭扰；一批生产两只，易受范围伤害。
  配方：`[{"minerals":50,"gas":0,"supply":1.0,"quantity":2,"producer":"larva","requires":["spawning_pool"],"consumes_producer":true}]`
  局部能力候选（实时可用看 ctx.available_spells）：smart, stop_stop, move_move, patrol_patrol, hold_position_hold, attack_attack, burrow_down_zergling

- **overlord_transport**：运输型王虫，用于装载和投送部队；注意防空和乘员安全。
  配方：`[{"minerals":25,"gas":25,"supply":0,"quantity":1,"producer":"overlord","requires":["lair"],"consumes_producer":true}]`
  局部能力候选（实时可用看 ctx.available_spells）：smart, stop_stop, move_move, patrol_patrol, hold_position_hold, scan_move, load_overlord, behavior_generate_creep_on

- **overseer**：由王虫变形的侦测支援单位；侦测隐形与埋地敌人。
  配方：`[{"minerals":50,"gas":50,"supply":0,"quantity":1,"producer":"overlord","requires":["lair"],"consumes_producer":true},{"minerals":50,"gas":50,"supply":0,"quantity":1,"producer":"overlord_transport","requires":["lair"],"consumes_producer":true}]`
  局部能力候选（实时可用看 ctx.available_spells）：smart, stop_stop, move_move, patrol_patrol, hold_position_hold, scan_move, effect_spawn_changeling, effect_contaminate, morph_oversight_mode

- **changeling**：临时侦察伪装单位；用途是获取信息，不是正面输出。
  配方：`[{"minerals":0,"gas":0,"supply":0.0,"quantity":1,"producer":"overseer","requires":[]},{"minerals":0,"gas":0,"supply":0.0,"quantity":1,"producer":"overseer_oversight_mode","requires":[]}]`
  局部能力候选（实时可用看 ctx.available_spells）：smart, stop_stop, move_move, patrol_patrol, hold_position_hold, scan_move

- **ravager**：由蟑螂变形，用腐蚀胆汁破阵和区域施压；胆汁需要主动施放。
  配方：`[{"minerals":25,"gas":75,"supply":1.0,"quantity":1,"producer":"roach","requires":["hatchery"],"consumes_producer":true}]`
  局部能力候选（实时可用看 ctx.available_spells）：smart, stop_stop, move_move, patrol_patrol, hold_position_hold, attack_attack, effect_corrosive_bile, burrow_down_ravager

- **locust_flying**：飞行中的蝗虫，需要落地后参与对地攻击。
  配方：`[{"minerals":0,"gas":0,"supply":0.0,"quantity":2,"producer":"swarm_host_burrowed","requires":[]},{"minerals":0,"gas":0,"supply":0.0,"quantity":2,"producer":"swarm_host","requires":[]}]`
  局部能力候选（实时可用看 ctx.available_spells）：smart, stop_stop, move_move, patrol_patrol, hold_position_hold, attack_attack, effect_locust_swoop

- **baneling**：由跳虫变形的一次性爆炸单位，对密集轻甲有效；需要接近目标。
  配方：`[{"minerals":25,"gas":25,"supply":0,"quantity":1,"producer":"zergling","requires":["baneling_nest"],"consumes_producer":true}]`
  局部能力候选（实时可用看 ctx.available_spells）：smart, stop_stop, move_move, patrol_patrol, hold_position_hold, attack_attack, effect_explode, burrow_down_baneling, behavior_building_attack_on

### 建筑与附属建筑

- **creep_tumor**：扩展菌毯。
  配方：`[{"minerals":0,"gas":0,"supply":0.0,"quantity":1,"producer":"creep_tumor","requires":[]},{"minerals":0,"gas":0,"supply":0.0,"quantity":1,"producer":"creep_tumor_burrowed","requires":[]},{"minerals":0,"gas":0,"supply":0.0,"quantity":1,"producer":"queen","requires":[]}]`

- **baneling_nest**：毒爆虫变异和升级。
  配方：`[{"minerals":100,"gas":50,"supply":0,"quantity":1,"producer":"drone","requires":["spawning_pool"],"consumes_producer":true}]`

- **evolution_chamber**：地面攻防。
  配方：`[{"minerals":75,"gas":0,"supply":0,"quantity":1,"producer":"drone","requires":["hatchery"],"consumes_producer":true}]`

- **extractor**：建在喷泉，需派工蜂采气。
  配方：`[{"minerals":25,"gas":0,"supply":0,"quantity":1,"producer":"drone","requires":[],"consumes_producer":true}]`

- **hatchery**：虫族交矿、幼虫、女王与分矿。
  配方：`[{"minerals":300,"gas":0,"supply":0,"quantity":1,"producer":"drone","requires":[],"consumes_producer":true}]`

- **hydralisk_den**：刺蛇与升级。
  配方：`[{"minerals":100,"gas":100,"supply":0,"quantity":1,"producer":"drone","requires":["lair"],"consumes_producer":true}]`

- **infestation_pit**：感染虫、虫群宿主、主巢前置。
  配方：`[{"minerals":100,"gas":100,"supply":0,"quantity":1,"producer":"drone","requires":["lair"],"consumes_producer":true}]`

- **lurker_den**：潜伏者变异和升级。
  配方：`[{"minerals":100,"gas":150,"supply":0,"quantity":1,"producer":"drone","requires":["hydralisk_den"],"consumes_producer":true}]`

- **nydus_network**：虫道运输网络。
  配方：`[{"minerals":150,"gas":150,"supply":0,"quantity":1,"producer":"drone","requires":["lair"],"consumes_producer":true}]`

- **roach_warren**：蟑螂生产与升级。
  配方：`[{"minerals":150,"gas":0,"supply":0,"quantity":1,"producer":"drone","requires":["spawning_pool"],"consumes_producer":true}]`

- **spawning_pool**：跳虫、女王和基础虫族科技前置。
  配方：`[{"minerals":200,"gas":0,"supply":0,"quantity":1,"producer":"drone","requires":["hatchery"],"consumes_producer":true}]`

- **spine_crawler**：对地静态防御，可拔起移动，扎根需合法地形。
  配方：`[{"minerals":100,"gas":0,"supply":0,"quantity":1,"producer":"drone","requires":["spawning_pool"],"consumes_producer":true}]`

- **spire**：飞龙、腐化者及空军攻防。
  配方：`[{"minerals":150,"gas":150,"supply":0,"quantity":1,"producer":"drone","requires":["lair"],"consumes_producer":true}]`

- **spore_crawler**：防空和侦测，可拔起移动。
  配方：`[{"minerals":75,"gas":0,"supply":0,"quantity":1,"producer":"drone","requires":["spawning_pool"],"consumes_producer":true}]`

- **ultralisk_cavern**：雷兽及升级。
  配方：`[{"minerals":150,"gas":200,"supply":0,"quantity":1,"producer":"drone","requires":["hive"],"consumes_producer":true}]`

- **lair**：二本科技，保留孵化场功能。
  配方：`[{"minerals":150,"gas":100,"supply":0,"quantity":1,"producer":"hatchery","requires":["spawning_pool"],"consumes_producer":true}]`

- **hive**：三本科技，保留基地功能。
  配方：`[{"minerals":200,"gas":150,"supply":0,"quantity":1,"producer":"lair","requires":["infestation_pit"],"consumes_producer":true}]`

- **nydus_canal**：虫道出口，配合装载/卸载。
  配方：`[{"minerals":75,"gas":75,"supply":0.0,"quantity":1,"producer":"nydus_network","requires":["nydus_network"]}]`

- **creep_tumor_queen**：女王铺设的菌毯肿瘤。
  配方：`[{"minerals":0,"gas":0,"supply":0.0,"quantity":1,"producer":"queen","requires":[]}]`

- **greater_spire**：巢虫领主变异与空军升级。
  配方：`[{"minerals":150,"gas":200,"supply":0,"quantity":1,"producer":"spire","requires":["hive"],"consumes_producer":true}]`

### 辅助单位与形态用途

- **larva**：幼虫是虫族大多数单位的生产来源；孵化场与女王注卵影响产能。

- **overseer_oversight_mode**：监察王虫的固定观察形态；用于视野与侦测，移动前切换。

- **lurker_burrowed**：潜伏者埋地形态，负责对地阵地火力；转移前起身。

- **locust**：虫群宿主产生的临时作战单位；生命周期有限，应及时接战。

- **broodling**：巢虫领主等产生的临时近战单位，用于消耗和牵制。

### 科研

- **centrifical_hooks**：毒爆虫机动强化。
  配方：`{"minerals":100,"gas":100,"recipes":[{"producer":"baneling_nest","requires":["lair"]}]}`

- **zerg_ground_armors_level1**：提升名称指定兵种类别的护甲；高等级需要前一级与科技前置。
  配方：`{"minerals":100,"gas":100,"recipes":[{"producer":"evolution_chamber","requires":[]}]}`

- **zerg_ground_armors_level2**：提升名称指定兵种类别的护甲；高等级需要前一级与科技前置。
  配方：`{"minerals":150,"gas":150,"recipes":[{"producer":"evolution_chamber","requires":["lair","zerg_ground_armors_level1"]}]}`

- **zerg_ground_armors_level3**：提升名称指定兵种类别的护甲；高等级需要前一级与科技前置。
  配方：`{"minerals":200,"gas":200,"recipes":[{"producer":"evolution_chamber","requires":["hive","zerg_ground_armors_level2"]}]}`

- **zerg_melee_weapons_level1**：提升名称指定兵种类别的武器伤害；高等级需要前一级与科技前置。
  配方：`{"minerals":100,"gas":100,"recipes":[{"producer":"evolution_chamber","requires":[]}]}`

- **zerg_melee_weapons_level2**：提升名称指定兵种类别的武器伤害；高等级需要前一级与科技前置。
  配方：`{"minerals":150,"gas":150,"recipes":[{"producer":"evolution_chamber","requires":["lair","zerg_melee_weapons_level1"]}]}`

- **zerg_melee_weapons_level3**：提升名称指定兵种类别的武器伤害；高等级需要前一级与科技前置。
  配方：`{"minerals":200,"gas":200,"recipes":[{"producer":"evolution_chamber","requires":["hive","zerg_melee_weapons_level2"]}]}`

- **zerg_missile_weapons_level1**：提升名称指定兵种类别的武器伤害；高等级需要前一级与科技前置。
  配方：`{"minerals":100,"gas":100,"recipes":[{"producer":"evolution_chamber","requires":[]}]}`

- **zerg_missile_weapons_level2**：提升名称指定兵种类别的武器伤害；高等级需要前一级与科技前置。
  配方：`{"minerals":150,"gas":150,"recipes":[{"producer":"evolution_chamber","requires":["lair","zerg_missile_weapons_level1"]}]}`

- **zerg_missile_weapons_level3**：提升名称指定兵种类别的武器伤害；高等级需要前一级与科技前置。
  配方：`{"minerals":200,"gas":200,"recipes":[{"producer":"evolution_chamber","requires":["hive","zerg_missile_weapons_level2"]}]}`

- **zerg_flyer_armors_level1**：提升名称指定兵种类别的护甲；高等级需要前一级与科技前置。
  配方：`{"minerals":100,"gas":100,"recipes":[{"producer":"greater_spire","requires":[]},{"producer":"spire","requires":[]}]}`

- **zerg_flyer_armors_level2**：提升名称指定兵种类别的护甲；高等级需要前一级与科技前置。
  配方：`{"minerals":175,"gas":175,"recipes":[{"producer":"greater_spire","requires":["lair","zerg_flyer_armors_level1"]},{"producer":"spire","requires":["lair","zerg_flyer_armors_level1"]}]}`

- **zerg_flyer_armors_level3**：提升名称指定兵种类别的护甲；高等级需要前一级与科技前置。
  配方：`{"minerals":250,"gas":250,"recipes":[{"producer":"greater_spire","requires":["hive","zerg_flyer_armors_level2"]},{"producer":"spire","requires":["hive","zerg_flyer_armors_level2"]}]}`

- **zerg_flyer_weapons_level1**：提升名称指定兵种类别的武器伤害；高等级需要前一级与科技前置。
  配方：`{"minerals":100,"gas":100,"recipes":[{"producer":"greater_spire","requires":[]},{"producer":"spire","requires":[]}]}`

- **zerg_flyer_weapons_level2**：提升名称指定兵种类别的武器伤害；高等级需要前一级与科技前置。
  配方：`{"minerals":175,"gas":175,"recipes":[{"producer":"greater_spire","requires":["lair","zerg_flyer_weapons_level1"]},{"producer":"spire","requires":["lair","zerg_flyer_weapons_level1"]}]}`

- **zerg_flyer_weapons_level3**：提升名称指定兵种类别的武器伤害；高等级需要前一级与科技前置。
  配方：`{"minerals":250,"gas":250,"recipes":[{"producer":"greater_spire","requires":["hive","zerg_flyer_weapons_level2"]},{"producer":"spire","requires":["hive","zerg_flyer_weapons_level2"]}]}`

- **burrow**：解锁虫族埋地；埋地不自动等于能攻击。
  配方：`{"minerals":100,"gas":100,"recipes":[{"producer":"hatchery","requires":[]},{"producer":"hive","requires":[]},{"producer":"lair","requires":[]}]}`

- **pneumatized_carapace**：王虫移动速度强化。
  配方：`{"minerals":100,"gas":100,"recipes":[{"producer":"hatchery","requires":[]},{"producer":"hive","requires":[]},{"producer":"lair","requires":[]}]}`

- **grooved_spines**：刺蛇射程强化。
  配方：`{"minerals":75,"gas":75,"recipes":[{"producer":"hydralisk_den","requires":[]}]}`

- **muscular_augments**：刺蛇移动强化。
  配方：`{"minerals":100,"gas":100,"recipes":[{"producer":"hydralisk_den","requires":[]}]}`

- **frenzy**：刺蛇狂暴/机动技能相关科技；以当前引擎可用能力为准。
  配方：`{"minerals":100,"gas":100,"recipes":[{"producer":"hydralisk_den","requires":["hive"]}]}`

- **neural_parasite**：感染虫神经寄生。
  配方：`{"minerals":150,"gas":150,"recipes":[{"producer":"infestation_pit","requires":[]}]}`

- **adaptive_talons**：潜伏者钻地强化。
  配方：`{"minerals":100,"gas":100,"recipes":[{"producer":"lurker_den","requires":["hive"]}]}`

- **lurker_range**：潜伏者射程强化。
  配方：`{"minerals":150,"gas":150,"recipes":[{"producer":"lurker_den","requires":["hive"]}]}`

- **glial_reconstitution**：蟑螂移动强化。
  配方：`{"minerals":100,"gas":100,"recipes":[{"producer":"roach_warren","requires":["lair"]}]}`

- **tunneling_claws**：蟑螂埋地移动相关强化。
  配方：`{"minerals":100,"gas":100,"recipes":[{"producer":"roach_warren","requires":["lair"]}]}`

- **adrenal_glands**：跳虫攻击速度强化。
  配方：`{"minerals":200,"gas":200,"recipes":[{"producer":"spawning_pool","requires":["hive"]}]}`

- **metabolic_boost**：跳虫移动速度强化。
  配方：`{"minerals":100,"gas":100,"recipes":[{"producer":"spawning_pool","requires":[]}]}`

- **anabolic_synthesis**：雷兽机动强化。
  配方：`{"minerals":150,"gas":150,"recipes":[{"producer":"ultralisk_cavern","requires":[]}]}`

- **chitinous_plating**：雷兽护甲强化。
  配方：`{"minerals":150,"gas":150,"recipes":[{"producer":"ultralisk_cavern","requires":[]}]}`

### 可切换形态

overseer_oversight_mode, overseer, spine_crawler_uprooted, spine_crawler, spore_crawler_uprooted, spore_crawler, baneling_burrowed, baneling, drone_burrowed, drone, hydralisk_burrowed, hydralisk, infestor_burrowed, infestor, lurker_burrowed, lurker, queen_burrowed, queen, ravager_burrowed, ravager, roach_burrowed, roach, swarm_host_burrowed, swarm_host, ultralisk_burrowed, ultralisk, zergling_burrowed, zergling

## 神族（protoss）

探机开启折跃后可继续采矿；多数建筑和传送门折跃点需要水晶塔/相位棱镜供能，基地和同化炉例外。气矿为assimilator。控制核心、暮光议会、机械台/机场分科技路线；折跃门科技完成后用morph_unit转换传送门，折跃受供能与冷却限制。高阶圣堂/黑暗圣堂两名合成执政官。观察者侦测、运输棱镜机动、哨兵和施法单位需要微操，不只是攻击移动。

### 单位与库存

- **adept**：偏重反轻甲的对地单位，可用灵能转移侦察和骚扰；不能对空。
  配方：`[{"minerals":100,"gas":25,"supply":2.0,"quantity":1,"producer":"gateway","requires":["cybernetics_core","pylon_or_phasing_prism_power"]},{"minerals":100,"gas":25,"supply":2.0,"quantity":1,"producer":"warp_gate","requires":["cybernetics_core","pylon_or_phasing_prism_power"]}]`
  局部能力候选（实时可用看 ctx.available_spells）：smart, stop_stop, move_move, patrol_patrol, hold_position_hold, attack_attack, effect_adept_phase_shift

- **dark_templar**：隐形近战骚扰单位；遇到侦测后生存能力下降，可参与执政官合成。
  配方：`[{"minerals":125,"gas":125,"supply":2.0,"quantity":1,"producer":"gateway","requires":["dark_shrine","pylon_or_phasing_prism_power"]},{"minerals":125,"gas":125,"supply":2.0,"quantity":1,"producer":"warp_gate","requires":["dark_shrine","pylon_or_phasing_prism_power"]}]`
  局部能力候选（实时可用看 ctx.available_spells）：smart, stop_stop, move_move, patrol_patrol, hold_position_hold, attack_attack, morph_archon, effect_shadow_stride

- **high_templar**：高阶施法单位，灵能风暴需要研究；能量用尽后可与另一圣堂合成执政官。
  配方：`[{"minerals":50,"gas":150,"supply":2.0,"quantity":1,"producer":"gateway","requires":["templar_archive","pylon_or_phasing_prism_power"]},{"minerals":50,"gas":150,"supply":2.0,"quantity":1,"producer":"warp_gate","requires":["templar_archive","pylon_or_phasing_prism_power"]}]`
  局部能力候选（实时可用看 ctx.available_spells）：smart, stop_stop, move_move, patrol_patrol, hold_position_hold, scan_move, attack_attack, effect_feedback, effect_psi_storm, morph_archon

- **sentry**：支援施法单位，用立场、守护者之盾等保护阵形；注意能量和地形。
  配方：`[{"minerals":50,"gas":100,"supply":2.0,"quantity":1,"producer":"gateway","requires":["cybernetics_core","pylon_or_phasing_prism_power"]},{"minerals":50,"gas":100,"supply":2.0,"quantity":1,"producer":"warp_gate","requires":["cybernetics_core","pylon_or_phasing_prism_power"]}]`
  局部能力候选（实时可用看 ctx.available_spells）：smart, stop_stop, move_move, patrol_patrol, hold_position_hold, attack_attack, effect_guardian_shield, hallucination_archon, hallucination_colossus, hallucination_high_templar, hallucination_immortal, hallucination_phoenix, hallucination_probe, hallucination_stalker, hallucination_void_ray, hallucination_warp_prism, hallucination_zealot, effect_force_field, hallucination_oracle, hallucination_disruptor, hallucination_adept

- **stalker**：可对地对空的机动远程单位；闪现需要研究并主动使用。
  配方：`[{"minerals":125,"gas":50,"supply":2.0,"quantity":1,"producer":"gateway","requires":["cybernetics_core","pylon_or_phasing_prism_power"]},{"minerals":125,"gas":50,"supply":2.0,"quantity":1,"producer":"warp_gate","requires":["cybernetics_core","pylon_or_phasing_prism_power"]}]`
  局部能力候选（实时可用看 ctx.available_spells）：smart, stop_stop, move_move, patrol_patrol, hold_position_hold, attack_attack, effect_blink_stalker

- **zealot**：基础近战前排，冲锋升级后适合包围和侧翼；不能对空。
  配方：`[{"minerals":100,"gas":0,"supply":2.0,"quantity":1,"producer":"gateway","requires":["pylon_or_phasing_prism_power"]},{"minerals":100,"gas":0,"supply":2.0,"quantity":1,"producer":"warp_gate","requires":["pylon_or_phasing_prism_power"]}]`
  局部能力候选（实时可用看 ctx.available_spells）：smart, stop_stop, move_move, patrol_patrol, hold_position_hold, attack_attack, effect_charge

- **mothership**：后期高投入支援空军，提供特殊技能和部队协同；注意能量、冷却与保护。
  配方：`[{"minerals":400,"gas":400,"supply":8.0,"quantity":1,"producer":"nexus","requires":["fleet_beacon"]}]`
  局部能力候选（实时可用看 ctx.available_spells）：smart, stop_stop, move_move, patrol_patrol, hold_position_hold, attack_attack, effect_mass_recall_strategic_recall, effect_time_warp, effect_oracle_cloak_field

- **probe**：神族工人：采矿、采气、启动建筑折跃后可以离开继续采集。
  配方：`[{"minerals":50,"gas":0,"supply":1.0,"quantity":1,"producer":"nexus","requires":[]}]`
  局部能力候选（实时可用看 ctx.available_spells）：smart, stop_stop, move_move, patrol_patrol, hold_position_hold, attack_attack, effect_spray_protoss, harvest_gather_probe

- **colossus**：远程对地范围火力，克制密集地面单位；体型特殊，可被部分对空攻击命中。
  配方：`[{"minerals":300,"gas":200,"supply":6.0,"quantity":1,"producer":"robotics_facility","requires":["robotics_bay","pylon_or_phasing_prism_power"]}]`
  局部能力候选（实时可用看 ctx.available_spells）：smart, stop_stop, move_move, patrol_patrol, hold_position_hold, attack_attack

- **disruptor**：依赖主动技能产生高爆发范围伤害；需要控制弹体并避免误伤友军。
  配方：`[{"minerals":150,"gas":150,"supply":4.0,"quantity":1,"producer":"robotics_facility","requires":["robotics_bay","pylon_or_phasing_prism_power"]}]`
  局部能力候选（实时可用看 ctx.available_spells）：smart, stop_stop, move_move, patrol_patrol, hold_position_hold, scan_move, effect_purification_nova

- **immortal**：对装甲地面目标强，护盾屏障提升生存；不能对空，需要护航。
  配方：`[{"minerals":250,"gas":100,"supply":4.0,"quantity":1,"producer":"robotics_facility","requires":["pylon_or_phasing_prism_power"]}]`
  局部能力候选（实时可用看 ctx.available_spells）：smart, stop_stop, move_move, patrol_patrol, hold_position_hold, attack_attack

- **observer**：隐形侦察和反隐形单位，没有常规攻击；不要派去正面送死。
  配方：`[{"minerals":25,"gas":75,"supply":1.0,"quantity":1,"producer":"robotics_facility","requires":["pylon_or_phasing_prism_power"]}]`
  局部能力候选（实时可用看 ctx.available_spells）：smart, stop_stop, move_move, patrol_patrol, hold_position_hold, scan_move, morph_surveillance_mode

- **warp_prism**：运输和机动供能平台，可协助前线折跃；需保护乘员和补兵点。
  配方：`[{"minerals":250,"gas":0,"supply":2.0,"quantity":1,"producer":"robotics_facility","requires":["pylon_or_phasing_prism_power"]}]`
  局部能力候选（实时可用看 ctx.available_spells）：smart, stop_stop, move_move, patrol_patrol, hold_position_hold, scan_move, load_warp_prism, morph_warp_prism_phasing_mode

- **carrier**：依靠拦截机作战的高投入空军；检查拦截机补充、经济和集火环境。
  配方：`[{"minerals":350,"gas":250,"supply":6.0,"quantity":1,"producer":"stargate","requires":["fleet_beacon","pylon_or_phasing_prism_power"]}]`
  局部能力候选（实时可用看 ctx.available_spells）：smart, stop_stop, move_move, patrol_patrol, hold_position_hold, attack_attack, cancel_hangar_queue5

- **oracle**：侦察、骚扰工人和布设静滞陷阱的能量单位；进攻与侦察争用能量。
  配方：`[{"minerals":150,"gas":150,"supply":3.0,"quantity":1,"producer":"stargate","requires":["pylon_or_phasing_prism_power"]}]`
  局部能力候选（实时可用看 ctx.available_spells）：smart, stop_stop, move_move, patrol_patrol, hold_position_hold, scan_move, attack_attack, effect_oracle_revelation, behavior_pulsar_beam_on, build_stasis_trap

- **phoenix**：机动制空和骚扰单位，可用引力技能控制部分地面单位。
  配方：`[{"minerals":150,"gas":100,"supply":2.0,"quantity":1,"producer":"stargate","requires":["pylon_or_phasing_prism_power"]}]`
  局部能力候选（实时可用看 ctx.available_spells）：smart, stop_stop, move_move, patrol_patrol, hold_position_hold, attack_attack, effect_graviton_beam

- **tempest**：远程空中攻城和消耗，适合集火高价值目标；需视野与近距离护航。
  配方：`[{"minerals":250,"gas":175,"supply":4.0,"quantity":1,"producer":"stargate","requires":["fleet_beacon","pylon_or_phasing_prism_power"]}]`
  局部能力候选（实时可用看 ctx.available_spells）：smart, stop_stop, move_move, patrol_patrol, hold_position_hold, attack_attack

- **void_ray**：持续对地对空火力，针对装甲目标有优势；注意集火、技能和防空环境。
  配方：`[{"minerals":250,"gas":150,"supply":4.0,"quantity":1,"producer":"stargate","requires":["pylon_or_phasing_prism_power"]}]`
  局部能力候选（实时可用看 ctx.available_spells）：smart, stop_stop, move_move, patrol_patrol, hold_position_hold, attack_attack, effect_void_ray_prismatic_alignment

- **archon**：由两名高阶或黑暗圣堂合成的重型短程单位，可对地对空；大部分生命来自护盾。
  配方：`[{"minerals":0,"gas":0,"supply":0,"quantity":1,"producer":"high_templar","requires":[],"consumes_producer":true,"producer_count":2}]`
  局部能力候选（实时可用看 ctx.available_spells）：smart, stop_stop, move_move, patrol_patrol, hold_position_hold, attack_attack

- **interceptor**：航母搭载的小型作战飞机；由航母生产和控制，不独立承担战略任务。
  配方：`[{"minerals":15,"gas":0,"supply":0.0,"quantity":1,"producer":"carrier","requires":[]}]`
  局部能力候选（实时可用看 ctx.available_spells）：smart, stop_stop, move_move, patrol_patrol, hold_position_hold, attack_attack

### 建筑与附属建筑

- **stasis_trap**：先知的停滞陷阱。
  配方：`[{"minerals":0,"gas":0,"supply":0.0,"quantity":1,"producer":"oracle","requires":[]}]`

- **assimilator**：建在喷泉，需派探机采气。
  配方：`[{"minerals":75,"gas":0,"supply":0.0,"quantity":1,"producer":"probe","requires":[]}]`

- **cybernetics_core**：追猎者等单位、折跃门、空军攻防。
  配方：`[{"minerals":150,"gas":0,"supply":0.0,"quantity":1,"producer":"probe","requires":["gateway"]}]`

- **dark_shrine**：黑暗圣堂与暗影步。
  配方：`[{"minerals":150,"gas":150,"supply":0.0,"quantity":1,"producer":"probe","requires":["twilight_council"]}]`

- **fleet_beacon**：高级空军及科技。
  配方：`[{"minerals":300,"gas":200,"supply":0.0,"quantity":1,"producer":"probe","requires":["stargate"]}]`

- **forge**：地面攻防与护盾科技、防御炮台前置。
  配方：`[{"minerals":150,"gas":0,"supply":0.0,"quantity":1,"producer":"probe","requires":["pylon"]}]`

- **gateway**：基础地面部队生产，可研究后转换折跃门。
  配方：`[{"minerals":150,"gas":0,"supply":0.0,"quantity":1,"producer":"probe","requires":["pylon"]}]`

- **nexus**：生产探机、交矿、分矿与能量技能。
  配方：`[{"minerals":400,"gas":0,"supply":0.0,"quantity":1,"producer":"probe","requires":[]}]`

- **photon_cannon**：对空对地防御和侦测，需供能。
  配方：`[{"minerals":150,"gas":0,"supply":0.0,"quantity":1,"producer":"probe","requires":["forge"]}]`

- **pylon**：提供人口及供能，保护关键科技的供能。
  配方：`[{"minerals":100,"gas":0,"supply":0.0,"quantity":1,"producer":"probe","requires":[]}]`

- **robotics_bay**：巨像、干扰者与机械升级。
  配方：`[{"minerals":150,"gas":150,"supply":0.0,"quantity":1,"producer":"probe","requires":["robotics_facility"]}]`

- **robotics_facility**：机械地面、运输、侦测单位。
  配方：`[{"minerals":150,"gas":100,"supply":0.0,"quantity":1,"producer":"probe","requires":["cybernetics_core"]}]`

- **shield_battery**：护盾恢复，需要能量和供能。
  配方：`[{"minerals":100,"gas":0,"supply":0.0,"quantity":1,"producer":"probe","requires":["cybernetics_core"]}]`

- **stargate**：空军生产。
  配方：`[{"minerals":150,"gas":150,"supply":0.0,"quantity":1,"producer":"probe","requires":["cybernetics_core"]}]`

- **templar_archive**：高阶圣堂与灵能风暴。
  配方：`[{"minerals":150,"gas":200,"supply":0.0,"quantity":1,"producer":"probe","requires":["twilight_council"]}]`

- **twilight_council**：冲锋、闪烁、使徒强化及圣堂科技前置。
  配方：`[{"minerals":150,"gas":100,"supply":0.0,"quantity":1,"producer":"probe","requires":["cybernetics_core"]}]`

### 辅助单位与形态用途

- **observer_surveillance_mode**：观察者的固定侦察形态；视野增强，转移前切换。

- **warp_prism_phasing**：棱镜供能形态，支持折跃；移动前切回运输形态。

### 科研

- **protoss_air_armors_level1**：提升名称指定兵种类别的护甲；高等级需要前一级与科技前置。
  配方：`{"minerals":100,"gas":100,"recipes":[{"producer":"cybernetics_core","requires":["pylon_or_phasing_prism_power"]}]}`

- **protoss_air_armors_level2**：提升名称指定兵种类别的护甲；高等级需要前一级与科技前置。
  配方：`{"minerals":175,"gas":175,"recipes":[{"producer":"cybernetics_core","requires":["fleet_beacon","protoss_air_armors_level1","pylon_or_phasing_prism_power"]}]}`

- **protoss_air_armors_level3**：提升名称指定兵种类别的护甲；高等级需要前一级与科技前置。
  配方：`{"minerals":250,"gas":250,"recipes":[{"producer":"cybernetics_core","requires":["fleet_beacon","protoss_air_armors_level2","pylon_or_phasing_prism_power"]}]}`

- **protoss_air_weapons_level1**：提升名称指定兵种类别的武器伤害；高等级需要前一级与科技前置。
  配方：`{"minerals":100,"gas":100,"recipes":[{"producer":"cybernetics_core","requires":["pylon_or_phasing_prism_power"]}]}`

- **protoss_air_weapons_level2**：提升名称指定兵种类别的武器伤害；高等级需要前一级与科技前置。
  配方：`{"minerals":175,"gas":175,"recipes":[{"producer":"cybernetics_core","requires":["fleet_beacon","protoss_air_weapons_level1","pylon_or_phasing_prism_power"]}]}`

- **protoss_air_weapons_level3**：提升名称指定兵种类别的武器伤害；高等级需要前一级与科技前置。
  配方：`{"minerals":250,"gas":250,"recipes":[{"producer":"cybernetics_core","requires":["fleet_beacon","protoss_air_weapons_level2","pylon_or_phasing_prism_power"]}]}`

- **warp_gate_research**：解锁折跃门转换；仍需要转换、供能和冷却。
  配方：`{"minerals":50,"gas":50,"recipes":[{"producer":"cybernetics_core","requires":["pylon_or_phasing_prism_power"]}]}`

- **shadow_strike**：黑暗圣堂武士暗影步。
  配方：`{"minerals":100,"gas":100,"recipes":[{"producer":"dark_shrine","requires":["pylon_or_phasing_prism_power"]}]}`

- **anion_pulse_crystals**：凤凰射程强化。
  配方：`{"minerals":150,"gas":150,"recipes":[{"producer":"fleet_beacon","requires":["pylon_or_phasing_prism_power"]}]}`

- **tempest_ground_attack_upgrade**：风暴战舰对建筑伤害强化。
  配方：`{"minerals":150,"gas":150,"recipes":[{"producer":"fleet_beacon","requires":["pylon_or_phasing_prism_power"]}]}`

- **void_ray_speed_upgrade**：虚空辉光舰移动强化。
  配方：`{"minerals":100,"gas":100,"recipes":[{"producer":"fleet_beacon","requires":["pylon_or_phasing_prism_power"]}]}`

- **protoss_ground_armors_level1**：提升名称指定兵种类别的护甲；高等级需要前一级与科技前置。
  配方：`{"minerals":100,"gas":100,"recipes":[{"producer":"forge","requires":["pylon_or_phasing_prism_power"]}]}`

- **protoss_ground_armors_level2**：提升名称指定兵种类别的护甲；高等级需要前一级与科技前置。
  配方：`{"minerals":150,"gas":150,"recipes":[{"producer":"forge","requires":["twilight_council","protoss_ground_armors_level1","pylon_or_phasing_prism_power"]}]}`

- **protoss_ground_armors_level3**：提升名称指定兵种类别的护甲；高等级需要前一级与科技前置。
  配方：`{"minerals":200,"gas":200,"recipes":[{"producer":"forge","requires":["twilight_council","protoss_ground_armors_level2","pylon_or_phasing_prism_power"]}]}`

- **protoss_ground_weapons_level1**：提升名称指定兵种类别的武器伤害；高等级需要前一级与科技前置。
  配方：`{"minerals":100,"gas":100,"recipes":[{"producer":"forge","requires":["pylon_or_phasing_prism_power"]}]}`

- **protoss_ground_weapons_level2**：提升名称指定兵种类别的武器伤害；高等级需要前一级与科技前置。
  配方：`{"minerals":150,"gas":150,"recipes":[{"producer":"forge","requires":["twilight_council","protoss_ground_weapons_level1","pylon_or_phasing_prism_power"]}]}`

- **protoss_ground_weapons_level3**：提升名称指定兵种类别的武器伤害；高等级需要前一级与科技前置。
  配方：`{"minerals":200,"gas":200,"recipes":[{"producer":"forge","requires":["twilight_council","protoss_ground_weapons_level2","pylon_or_phasing_prism_power"]}]}`

- **protoss_shields_level1**：提高神族护盾护甲；高等级需要前一级与科技前置。
  配方：`{"minerals":150,"gas":150,"recipes":[{"producer":"forge","requires":["pylon_or_phasing_prism_power"]}]}`

- **protoss_shields_level2**：提高神族护盾护甲；高等级需要前一级与科技前置。
  配方：`{"minerals":200,"gas":200,"recipes":[{"producer":"forge","requires":["twilight_council","protoss_shields_level1","pylon_or_phasing_prism_power"]}]}`

- **protoss_shields_level3**：提高神族护盾护甲；高等级需要前一级与科技前置。
  配方：`{"minerals":250,"gas":250,"recipes":[{"producer":"forge","requires":["twilight_council","protoss_shields_level2","pylon_or_phasing_prism_power"]}]}`

- **extended_thermal_lance**：巨像射程强化。
  配方：`{"minerals":150,"gas":150,"recipes":[{"producer":"robotics_bay","requires":["pylon_or_phasing_prism_power"]}]}`

- **gravitic_drive**：运输棱镜移动强化。
  配方：`{"minerals":100,"gas":100,"recipes":[{"producer":"robotics_bay","requires":["pylon_or_phasing_prism_power"]}]}`

- **gravitic_booster**：观察者移动强化。
  配方：`{"minerals":100,"gas":100,"recipes":[{"producer":"robotics_bay","requires":["pylon_or_phasing_prism_power"]}]}`

- **psi_storm**：高阶圣堂武士灵能风暴；研究后需施法。
  配方：`{"minerals":200,"gas":200,"recipes":[{"producer":"templar_archive","requires":["pylon_or_phasing_prism_power"]}]}`

- **resonating_glaives**：使徒攻速强化。
  配方：`{"minerals":100,"gas":100,"recipes":[{"producer":"twilight_council","requires":["pylon_or_phasing_prism_power"]}]}`

- **blink**：追猎者闪烁。
  配方：`{"minerals":150,"gas":150,"recipes":[{"producer":"twilight_council","requires":["pylon_or_phasing_prism_power"]}]}`

- **charge**：狂热者冲锋。
  配方：`{"minerals":100,"gas":100,"recipes":[{"producer":"twilight_council","requires":["pylon_or_phasing_prism_power"]}]}`

### 可切换形态

warp_prism_phasing, warp_prism, observer_surveillance_mode, observer, warp_gate, gateway

## 数据来源与验证边界

生产/科研/能力关系采用 reference/python-sc2/source.json 锁定的开源版本，MIT 许可保留同目录；没有安装或升级 pysc2。价格、升级和能力元数据来自实际 SC2 协议。新版本部分数值 ID 有偏移，宿主按实际能力身份绑定并输出 data_adjustments；不能仅因数字存在就认为相同能力。临时炮塔的单位战力价值不是建造付款，虫族变异扣除消耗的源单位，跳虫一卵两只。

当前原生地图数据与 Linux 4.10 的初始工人数和部分费用有差别，不能混用网上固定平衡数值。待执行动作始终服从实际引擎查询，错误和等待均反馈模型；没有静默换模型、换产品或执行旧版本。

已在实时原生引擎验证三族采气、扩张及主要单位科技链；测试给定资源和加快建造，没有跳过科技树。也验证了受控的闪烁、装载、附属建筑迁移等。它们不是自然胜率、模型战略水平或所有89项研究/所有法术逐项通过的证明。详细成功与失败证据见 STATUS.md 和 logs/multirace_final_summary.json。

## 开赛与持久技能

默认双方仍使用人族。示例：

```bash
./run_sc2.sh .venv311/bin/python opencode_match.py --p1-race zerg --p2-race protoss --qualify
```

模型仍为当前配置的 Qwen / GPT-6 Luna。持久库按模型和种族隔离；非人族使用 skill-library/race-zerg 或 race-protoss，不把旧 Marine 技能直接交给别族。准备阶段有本族 fixtures/player.json、本手册和种族对应的测试观察；战斗请求每次附本族目录、中文经济/兵种知识及可见敌方用途。规则变化后仍需重新验收技能。

手册重新生成：`./run_sc2.sh .venv311/bin/python render_game_knowledge.py`。



## 开局选择、扩张预算和运营验收（2026-10-08）

战斗模型在每次请求中收到本族 game_knowledge.opening_playbook。人族包含单兵营侦察扩张、有限期单矿压制、应对已观察早攻；神族和虫族也有各自的经济/压制/防守方案。它们是可选方案，不是自动执行的固定开局。继承的 Python 技能只负责局部战术，不代表已经具备运营计划。

12工人开局可参考单兵营侦察扩张的约19～23人口二矿思路，但实际执行以本局初次观察和实时 tech_tree 为准。更新后的初始状态、价格、前置和游戏速度不能由历史缓存猜测。world.game_s 是 loop/22.4 的游戏时间；最慢速度的墙钟时间不能直接对照 Faster 开局录像。资料例子：https://lotv.spawningtool.com/88844/（2025年人族内战录像，双方先兵营、不同扩张时机；不是当前补丁的硬性基准）。

首轮决策用 macro_plan 明确开局、基地/工人阶段目标、开矿开工期限和取舍。正常的经济扩张原则：
- 人族持续SCV和及时补人口，一兵营及最少侦察/守军后存二矿；明确轨道升级和采气。不要无目标地追加多个兵营、附属建筑和重工厂挤掉预算。
- 神族按传送门/气矿/控制核心的实际前置安排侦察和最低防守，随后枢纽、探机、供能与科技。
- 虫族按侦察在孵化场先行和血池先行之间选择，兼顾王虫、女王、工蜂与防守幼虫；不能照抄人族顺序。
- 只有具体侦察威胁、明确进攻窗口或执行障碍才应成为调整理由。缺少侦察不等于已发现敌方一波。
- 新基地建成后显式补工人、gather转移过饱和工人，并安排合适的产能/科技；基地数量本身不代表多了一片有效经济。

macro_plan 外层 count=1，args 的下列字段全部必填：
opening=economic|pressure|defend|custom；target_bases整数1..8；worker_target整数1..90；
expand_by_game_s=0..5400（绝对游戏时间，0表示无开工期限）；
reserve_expansion布尔；reserve_for_s=30..180游戏秒；
exempt_products最多8个本族产品名、不可重复；reason非空中文说明，最多200字符。
每条回复最多一个 macro_plan，替换旧目标，等待或以后回复省略此指令均保留当前目标。

例如，基础侦察和守军已经到位后：
~~~json
{
  "tasks": [
    {"skill":"macro_plan","args":{
      "opening":"economic","target_bases":2,"worker_target":26,
      "expand_by_game_s":150,"reserve_expansion":true,"reserve_for_s":120,
      "exempt_products":[],"reason":"基础守军已到位，优先完成二矿，再补工人和双矿产能"
    }},
    {"skill":"expand"},
    {"skill":"train_worker","count":3}
  ],
  "reason":"为二矿预留资源，暂停非必要产能，保持基础经济"
}
~~~

这仅演示合同，150秒/26工人不是所有地图、种族或局势的正确目标。首轮还没准备好扩张时可以先 reserve_expansion=false，显式建必要前置，再依据观察更新计划。

预算只保护已经显式派遣的 expand，绝不替模型决定开矿。优先执行第一个未发出的扩张，以本场引擎配方费用预留矿/气；普通宏和JSON组合建造只能使用剩余资金。已有训练/建筑订单仍继续。工人数低于阶段目标时的工人生产、剩余人口<=4的紧急补给，以及模型指定的 exempt_products 可使用预留，因此确实可能推迟扩张。例外列表应只放最低防守和必要前置，不能把所有军队都列入。

到期、目标基地数量已开工、扩张已发出、任务取消/失败后释放预算并记录事件。找不到expand任务或配方费用不唯一时明确报告未预留；不私自建立替代任务。到期后旧expand任务仍存在，需要模型重新评估是否续期或撤销。
模型可设置 reserve_expansion=false 解除存钱；如需撤回尚未发出的开矿，使用 cancel_expansion args={"task_id":任务ID}。已发给原生引擎的建造请求明确拒绝此取消，不能假装建筑被取消；计划更新也不会取消旧任务。
旧的无tech_tree后端不支持这两个接口，显式报错；当前原生LLM路径支持。

每轮 economy_report 独立于历史滑窗和反馈分页，含持久目标、预算状态、目标达成情况、初次观察/当前经济、每基地工人饱和度、第二座基地首次观察到的开工/完成时间、卡人口时长、空闲工人秒、空闲基地秒、工人训练基地秒、发单成本分类和最近检查点。目标未完成、超期、缺任务、预算到期、矿区过饱和均有提示；模型应说明实际障碍，而不是重复声称推进计划。

证据边界：首次观察不一定正好是出生瞬间；两座基地可能在同一矿区，不等于两个有效矿区；时间是采样估计且超过2游戏秒的观察空档单列，不虚构期间状态。多个基地的秒数累加；虫族幼虫利用率未统计，明确为null。发单费用是请求支出，可能含拒绝/重试，不是已确认扣款或收入。每30游戏秒记录检查点，内存最多20条，prompt只取最近6条；支出分类累计、明细最多12条。
中文观战输出窗口显示运营目标、预算变动和观察到的基地里程碑；不会将发单显示为建成。

## 现场补充 Python 技能（官方 DeepSeek，2026-10-08）

启动器 official_duel.py 默认接入现场开发，可用 --without-live-skills 关闭。已有比赛必须下一次加载执行层才生效。此功能独立于防守副官。

主模型发现缺少局部操作时提交：

已有 `inject_larva` 可完成普通单次注卵，无需申请Python技能。下面是需要自定义循环时的开发示例；仅在现场开发已启用时可用。
```json
{"tasks":[],"reason":"缺少女王注卵循环，申请补充技能","develop_skill":{"name":"queen_cycle","unit":"queen","goal":"能量足够时给未处于注卵计时的己方基地注卵，确认后等待下一轮；无目标或低能量时等待","verification":"queen_inject"}}
```
name为小写字母起始的字母/数字/下划线，最长40字；unit必须是本族允许的Python selector；goal 1..800字；verification为local_tactic或queen_inject。所有字段必填。本条回复tasks必须为空，不能同时定义JSON技能；不清空之前的任务。不要在战斗回复放Python源码。已有生产、升级、采气、开矿接口仍由主模型直接调用，现场Python不能绕过这些权限去任意操纵SC2。

在 live_skill_development.jobs 查看 developing/ready/failed；开发期间正常决策，勿反复提交。准备好后按最新 python_skill_library 参数派遣：
```json
{"tasks":[{"skill":"queen_cycle","count":1,"args":{"count":1,"target":"home","retreat_target":"home","timeout_s":300,"params":{}}}],"reason":"派遣一名女王执行已验收的注卵循环"}
```
只有ready且目录实际列出该技能才可使用上述名字。脚本任务最多300游戏秒，期限后需要主模型决定是否再次派遣；不是永久隐式自动行为。

后台开发使用独立官方DeepSeek high请求；每方3个开发任务/局、每任务最多2次API尝试；联网失败直接显示，避免隐式反复计费。开发总时限240墙钟秒，不阻塞游戏。脚本及其自测在隔离环境运行，所有实际指令仍经单位所有权/迷雾/可用能力检查。源码失败会自动有界修复，模型得到错误、相关观察和原文源码；修复通过后仍需主模型新派遣。任务死亡、任务期限、引擎失败不能靠改源码凭空恢复。

通过的版本自动保存到 skill-library/live-official/<race>/<skill>/，下局重新验收后恢复。新版本不修改已运行任务的冻结源码。开发公开状态/错误显示于中文观战窗口，完整文件在对局目录pN/live-development/job-N，费用统计developer-usage.jsonl。没有通过验收时不会加载或静默代用旧逻辑。

验收边界：local_tactic只做通用合法性和当前局部观察回放；queen_inject另做合成注卵周期验证。二者均不能声称“真实游戏效果已确认”。2026-10-08本次测试用明确mock模型输出，没有付费开发调用、没有真实原生注卵验证；真实自主编写/调用效果需下一场观察。
