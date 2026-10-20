# 自主 bot 对战交接

## 最新实战：随机 PvZ 三族微操观察（已完成）

用户要求随机挑两个种族打一场，观察模型是否使用新增能力。一次抽签为 GLM 神族 vs DeepSeek 虫族，
目录 logs/go-step-duels/micro-random-6rjhnasg；draw.json 保留随机结果/seed=269111064，未重抽。
仍使用 Go glm-5.3-flash / deepseek-v4.1-flash，无回退，正常迷雾、非实时共同暂停、无debug/固定开局/用户建议。
游戏18:20.36、墙钟24:11.47，GLM失去全部基地/工人/军队/产能后主动认输，离开确认；
DeepSeek胜，result_source=acknowledged_concession_rule，不是自然清空所有建筑的引擎胜利。

新微操真实触发：哨兵守护盾1次buff确认、力场2次能量回执（普通全军任务与常驻守备各1次）；
女王输血6请求/5回执/1未确认。GLM主动生产两只哨兵并提到护盾，微操脚本择时施法，不能说每次施法点由模型选择。
GLM第32轮主动use_ability时空加速，能量消耗+目标buff变化回执。DeepSeek未直接use_ability。
精确基地标签注卵44发令/43能量与buff确认，字符串和整数标签都有真实成功，4基地；另1次接近停滞失败。
模型没造破坏者、潜伏者、电兵，胆汁/埋地/风暴/反馈本场未覆盖，不能算通过或失败。
真实入模combat_micro.automatic与近期回执可查observer-audit.json；ability-evidence.json链接原始请求/任务/标签确认。

对战账本82/192（GLM40、DeepSeek42），82请求全部返回指定模型，无超时/API错误；另独立预检2/2。
接受计划37/41份，4份被拒（缺revision_reason两次、重复append、错误取消已发生产任务）。
原生拒绝1条光子炮CouldntReachTarget，保留原始状态，未证明唯一成因；生产者死亡另计真实战损。
输血1次6秒未确认、女王注卵接近停滞、神族残局供电/前置选址、模型少量补兵/不断延后出击/200上限误判均见REPORT.md。
没有把所有资源积压归于模型或程序，重建选址与未确认技能需要独立复现；本场未改生产源码。

75份历史账本哈希不变，source_unchanged=true，当前/归档源码及回放SHA核验通过，暂停违规0、清理完成。
回放match-01/match.SC2Replay，SHA=b0efc8b66a30e8b61242717e0cb745085b401c337702644f62e844ca7bfd889c。
原有微操1135pass/2skip与12原生场景的源码仍完全相同；本轮只新增比赛记录和说明，未提交推送。

## 当前工作区：精确基地标签注卵与三族局部微操（尚未提交）

用户要求先修复原始基地标签，再补破坏者胆汁、潜伏者进攻埋地、电兵/哨兵技能并检查其他技能。
已新增 arena/combat_spells.py，将 CombatMicro 扩为三族执行器，保持原有部队/任务目标和控制权边界。
自动覆盖规则详见 MICRO_CONTROLS.md；显式 use_ability、侦察、袭矿/空投、自定义任务仍自行控制技能。
施法先检查原生可用性、能量、当前可见目标、射程和当前友军位置；区域技能去重，通道不被旧攻击立即打断。
技能仅回执 combat_micro_acknowledged；buff/完整形态用 confirmed；没有推断伤害、击杀或胜利。
潜伏者/寡妇雷在明确移动前先起身，地面空间任务也支持该机械转换。

注卵 base 现在接受己方基地 tag 整数/十进制字符串，统一表示后合并同一未完成请求；精确匹配，不回落别的基地。
标准/ChatGPT schema、Go 实际规则和 zerg_injection 观测同时更新。缺失/敌方/未完工/非基地均不施法。
同时修复无常规武器施法单位被全军/战术/防守筛选漏掉、常驻守备预先挡住埋地单位的移动、
HighTemplar 与 high_templar 等显示名/引擎标准名不一致，schema 与执行选择均已接通。
模型实际 control_policy、combat_micro.automatic 与系统规则也明确三族支持，不再只宣称人族技能。

新测试 test_multirace_micro.py、扩展 test_queen_injection.py；原生零模型验证入口 verify_multirace_micro_live.py。
原生验证使用正常迷雾、明确 debug 夹具，逐场等待旧胆汁/力场消失并检查同高度地形；不是模型比赛或胜率证明。
早期布场污染、视野遮挡、旧引擎渡鸦矩阵50能量确认门槛、守备命名失败记录均留在 logs/multirace-micro-*。
最终完整回归：1137 项，1135 通过、2 原有 skip，63.602 秒；日志 logs/multirace-micro-complete-tests.log，
对应 manifest 核验 318 份 Python 源码前后不变。原生最终目录 logs/multirace-micro-ynax8145：
12 项全部通过（含真实 Commander 常驻守备风暴和指定宏观孵化场标签注卵），engine_action_errors=0、
model_calls=0、source_unchanged=true、historical_unchanged=true、cleanup_complete=true。
回放 SHA、归档与当前源码另经 final-verification.json 核验；技能目录 ability-coverage.json 含591条绑定
（29 automatic_local、410 native_order、152 explicit_model_order），是目录覆盖分类，不是591项技能效果实测。
新增真实守备验证还抓出风暴错误避开己方建筑，已按风暴不伤建筑修复；友军地面/空中单位保护保留。
此前不同布场/源版本的成功和失败均留存，不将旧成功源快照冒称为其他版本验证。

上一场真实模型对局为 logs/go-step-duels/glm-deepseek-tvz-76d7gl0q：Go GLM 人族 vs DeepSeek 虫族，
17:59.60 引擎确认人族胜，非实时，81/192（39/42），另有独立连通性2/2。目录包含 REPORT.md、
observer-audit.json、highlights.json、回放和源码，历史73份文件哈希当时核验一致。该场使用本次修复前的微操，
不能把新三族技能追认为它已使用。复盘指出旧微操只有人族自动能力，不能直接推断 GLM 模型更强。
已有 go_step_series.py 的显式双方模型/factory 改动、Go Gemini 白名单及训练隐私拒绝处理、VIDEO_ROADMAP.md
继续保留，没有重置 GPT/Gemini 失败预检和其他历史账本。当前未新打收费模型对局，也未提交/推送本轮改动。

## 本轮提交：主动进攻与跨轮计划

基于当前分支codex/lan-ai-rooms的39771ab，用户要求增强进攻性并开始修改。
用户已要求提交并推送这些改动到当前远端分支；日志和历史账本按既有规则留在本地，不随代码上传。
新增arena/offensive_policy.py，更新INITIAL_STRATEGY、普通Commander和Go实际系统规则，允许主动选择
小队试探/袭矿/空投/压扩张，不再把等待完整侦察设为统一前提。当前用户战术建议继续参与决策。

模型可在顶层返回offensive_plan：action、objective、squad、launch_condition、abort_condition、
review_after_s和revision_reason。仅在整份计划校验、任务入队成功后保存；省略/null保留当前计划和期限。
offensive_policy随真实后续请求与Commander.stats输出，当前条件/期限在上下文压缩后仍保留。
连续等待最早复查期限不随反复修订延后。到期只提醒，不增加请求、不自动调兵、不选择目标。
收到进攻任务请求可结束连续等待记录，但不等同实际分配/出发/胜利。旧tasks/reason调用继续兼容。

普通schema、ChatGPT/Go结构化入口支持新字段；同时补上结构化入口此前漏掉的raid/drop_raid。
Go只在请求声明offensive_policy.enabled时注入对应契约，45k/90k原有输入上限与请求上限不变。
首次回归暴露45k边界，已压缩重复说明并按能力注入，新旧大场景均通过；失败日志保留。
说明与例子见OFFENSIVE_POLICY.md，新增test_offensive_policy.py共11项。
最终完整回归1104项：1102通过、2原有skip，57.261秒，logs/offensive-policy-final-regressions-20261019.log。

额外模型验证原拟重放历史观察，被自动审批以未明确授权再次外发该批历史观测拒绝，原脚本未执行。
保留logs/offensive-policy-model/probe-3wgfkmdd/approval-rejection.json，未绕过拒绝。
改用完全手工合成场景，独立预算2/2：logs/offensive-policy-model/synthetic-13qdw1n5/report.json。
实际返回模型均deepseek-v4.1-flash；虫族成军前压、神族战后拆分矿，两份进攻计划及任务schema有效。
该验证未读取历史对局观测、无引擎动作，不是新比赛或胜率证明。源码哈希未变。

上一场PvZ仍100/128（P1=48/P2=52）、finished/time_budget，旧四局342/512与更早181/200均未重置。
已有11份更早历史文件SHA核验一致。上一场的原生折跃拒绝、进攻停滞等记录保留；本轮未声称修复这些
独立执行现象。后续实战应比较首攻时间、局部胜利后续目标及残局定位，不把合成场景结果当整局胜利。

## 2026-10-18 最终：四场非实时实战完成

用户要求的4场已完成，系列`logs/go-step-duels/four-do4lgd37`状态finished，无活动游戏。
双方固定Go DeepSeek deepseek-v4.1-flash，Linux SC2 4.10正常迷雾、共同暂停，无实战debug/固定开局/额外复盘。
第1场TvP人族胜（已确认认输规则）；第2场PvZ墙钟上限无确认胜负；第3场ZvT人族胜（认输规则）；
第4场TvZ人族胜，真正engine_player_result。认输规则不能称引擎自然结束；第2场优势不计胜。
逐局指标、版本顺序、工具修复、原生对照、战术观察及限制见STEP_DUELS.md。

独立账本342/512，P1=166/P2=176；334成功返回全为deepseek-v4.1-flash，8次未成功返回也计数。
10份历史文件SHA不变，旧random-04f97o8v仍181/200（89/92）、finished，未重置。
四场均source_unchanged=true、0暂停违规、清理完成、回放与源码SHA验证通过。
最终总核验：系列/final-observer-verification.json。各局observer-audit.json与源码、回放完整。
第1/2场每次60秒请求窗口，第3/4场120秒；每方64请求，每场1800游戏秒/2400墙钟秒，不按超时猜胜负。

已合入生产确认（Gateway出生位移/旧工人身份）、Go进攻契约、混合侦察路线、精确参数表示纠正、
显式选址扩张、已占用/缺失矿区与气矿枯竭反馈、逐成员位移进度、闲置原队员有限重发、
袭矿坦克机械收起及二次撤退、数量说明和非实时请求窗口。所有动作保持模型原小队/目标/截止，
不自动选新战略或购买替补。局间修复均先隔离验证、结束核旧SHA后合入，版本不能追认为首局已有。
三份repair-evidence-after-match-XX.json保留因果证据索引与源哈希。

实战第2场16蟑螂raid触发8守军门槛，6存活者到LLM点holding；第3场主动升级轨道指挥再扫描高坡，
原生50能量消耗+effect6确认（match-03/scan-78-observer-evidence.json），不推断扫描导致胜利。
第3场raid含6架起坦克无MOVE能力的真实证据也存档；零模型修复对照完整通过。
未有实战主动空投，不能把旧三族debug空投夹具当模型选择证明。早期失败/覆盖不完整探针全部保留。

赛后仅补纯事实economy_report.standard_supply_limit=200及can_increase_supply_cap，直接保留规则；
不拦截模型为其他用途建补给。此项不在4场实战里，不宣称改善胜率，见reporting-metadata-after-match-04.json。
最终源码完整1093项：1091pass、2原有skip、56.327秒，logs/go-step-final-reporting-regressions.log，
对应manifest覆盖312份Python源码。末局原版本完整回归另有final-source日志/manifest，不覆盖历史。

剩余观察：成军后久等、200人口仍补供给、正面撞坦克、未选择空投、残局侦察遇王虫即停、建筑附近大单位
原生路径0。后者是真实无路；附近建筑列表不证明唯一阻挡物，需模型选择清障或改令，自动选址可继续改进。
原生6条拒绝有完整日志；在途生产不重复购买，死亡不补买。模型错误与物理约束不冒称工具全部无异常。

新增Linux非实时入口go_step_series.py每次一场，--resume只续未完成系列中的下一局，不重打已完成局。
当前系列已完成；再次开始4场用无--resume的新系列，独立新账本，同时保留全部旧账本。说明见README.md。
当前分支codex/lan-ai-rooms；用户已要求提交并推送本轮进攻工具、非实时对战入口、修复与报告。
全部本地对局记录和历史计数继续保留；logs目录按既有忽略规则不随代码上传。

## 2026-10-18 当前：分兵袭矿、空投与模型指定撤退

按用户要求 fetch 远端所有分支，当前 `codex/lan-ai-rooms` 从 `278642c` 快进到
`f6c3528`，保留历史对局、账本和计数。在该版本上新增进攻工具，本轮尚未提交/推送。

- `raid`：模型选择兵种、小队数量、明确有序矿区路线、停留时间、可见守军门槛与撤退点。
  只分配自由地面战斗单位，不偷其他任务的部队。抵达矿区后优先打当前可见工人、基地和气矿；
  迷雾不是空矿，目标消失/离开授权矿区会取消追击，升空且无法攻击的基地明确报告为不可攻击。
- `drop_raid`：单架空载医疗运输机/移动棱镜/运输王虫，按原生 cargo_size 校验容量，逐个装载，
  按模型空中航点飞行，观察全体存活乘客实际落地后袭矿。卸载前遇模型授权的守军门槛则携兵撤退；
  卸载后运输机先返回模型点，地面部队按任务袭击/撤退。没有自动接回、多机、多次空投或自动变形。
- 两种任务均要求 `retreat_target`，可用地名、site_N 或具体坐标；`retreat_enemy_count` 与
  `threat_radius` 也是明确的模型授权。只看当前己方视野，不能证明新见守军确实从别处回防。
  路线/停留结束也撤退，到达后地面驻守且保留归属，避免马上被主军控制器拉走。
  超时不重置，确认死亡不替补，暂时未观察到的成员不假设死亡/登机。支持已有 cancel_unit_order/cancel_task。
- 新 `arena/raids.py` 经 TaskQueue、工具 schema、主指挥系统契约、实际请求、历史空间报告和拼写转换接通。
  `offensive_operations` 提供己方运输容量/乘客/分配与自由兵力；battlefield 矿区补充当前可见工人、基地、守军数。
  地面目的地查询按小队/目标缓存，原生单位路径仍定期报告，避免每个控制帧对每名成员重复查询。
  局域网提示词校验复用更新后的固定契约，两端须更新一致。说明与示例见 `OFFENSIVE_MISSIONS.md`。

最终全量回归：1060 项，1058 通过、2 原有 skip，83.722 秒；日志
`logs/offensive-frozen-source-regressions.log`。新增 38 项场景含真实 Commander 请求捕获。
原生最终证据：`logs/offensive-native-imklic5a/result.json`，三族全部 passed、source_unchanged=true、
action_errors=0；使用真实 Linux SC2 4.10、正常迷雾、两个参与者及明确 debug 生成的夹具单位。
分别实际观察到装载、飞行、卸载、执行器攻击令、敌方基地掉血、可见守军触发撤退、指定点驻守；
另行派遣的地面袭矿也完成撤退驻守。每族有回放与源码 SHA，stdout 在 `logs/offensive-native-final-verified.log`。
早期试跑的失败证据保留；修正了探针的原生攻击能力别名识别、卸载瞬间的判定以及 debug 清理生效前的跨场景时序。
这些是工具阳性对照，0 外部模型请求，不是 DeepSeek 战术选择验证、比赛胜场或胜率提升结论，亦非双机 Windows/Linux 联测。

十个旧账本哈希核验一致，旧第五批仍 finished、181/200（P1=89/P2=92），不重置、不补回。
旧记录中的未提交/运行状态按当时事实保留，最新进度以本节为准。

## 2026-10-16 当前：默认改为双端本机原生游戏

用户明确不接受无画面推流的集中式方案，选择Windows与Linux混用。已改为协调器只负责房间、
一次性配对、地图/引擎校验和联机阶段；每端运行自己的原生SC2、本方模型与Commander。
Windows走本机SC2，Linux默认走既有Proton原生窗口。默认`bash start_room.sh`；Windows可用
`start_room.ps1`或直接Python。两端从网页取得2分钟有效的一次性配对码，在本机运行网页命令，
随后选本机模型、确认提示词、准备。凭据不上传协调器，网页身份与客户端令牌不能混用。

主要文件：arena/distributed_room.py（协调协议），arena/lan_native.py（单本机SC2、host_ip及四端口），
arena/lan_client.py（本方模型/Commander与消息），arena/client_requests.py（调用前持久化所有尝试），
arena_client.py与start_client.sh/ps1。启动阶段依次prepare/create/join/play，只有P1建房；
引擎Base/data_version、地图SHA和工具契约不匹配即停止，不启动模型、不改用两个独立游戏。
本方后端slot0与房间P2显示身份分开，P2仍只持有自己的一个控制器。游戏控制API仅listen127.0.0.1。
四个游戏通信端口默认15000～15003，真实LAN需双方放通TCP/UDP；窗口由各自电脑渲染。
聊天保留server消息ID进入本方OperatorAdvice，下一轮正常决策读取，重发不产生第二条建议；
没有接收能力时不能借聊天恢复请求。失败细节只展示本方，失联不计胜，不自动重开未知比赛。
模型调用前记录每场requests.json，包含失败尝试，存在的账本拒绝初始化覆盖。

最终真实集成：logs/lan-full-native-k7gysjvj/result.json。真实HTTP协调器、两个LocalClient、
两个LinuxSC2进程，各一个本方控制器；双方12工到13工、双向建议进入各自后续请求且不串线，
0动作拒绝，两份回放SHA验证通过。每端账本attempted=3/successful=3/failed=0，均为明确标记
的本地夹具模型，0外部推理调用；不是付费实战或胜场。它是同机双进程验证，不是双机混合系统验证。
Chromium双会话的配对、私有模型目录、确认/准备、联机阶段、聊天、刷新、390px布局通过，
logs/lan-native-browser-preview/保存截图与结果；这里Windows/Linux客户端报告是模拟的。

尚未在两台真实Windows/Linux电脑上跑通GPU窗口与跨网卡/防火墙组合，不能声称已实机跨系统验收。
两端必须使用相同Base/data_version，Linux4.10 API引擎不能直接混入Windows5.x；Linux应使用兼容Proton版本。
完整环境与命令见LAN_ROOMS.md，`verify_lan_clients_live.py`可重复零模型的本机原生集成检查。
最终相关回归674项，672通过、2原有占位skip，见logs/lan-native-final-regressions.log。
十个旧账本哈希不变，上一批仍181/200；没有新增付费实战，不重置历史。
分支仍codex/lan-ai-rooms，尚未提交/推送。下面集中式首版记录保留为历史，默认已替换；旧版仅`--central`兼容。

## 2026-10-16 前版：集中式局域网房间首版（已被上方默认替代）

此前修复已提交为`aa39646`并快进合并到远端`main`。当前新开发分支为
`codex/lan-ai-rooms`，本轮功能修改尚未提交或推送。

`bash start_room.sh`启动局域网房间，默认端口8768。房主P1创建房间，P2通过链接或六位码入场；
各自选模型/种族、编辑战术提示词，检查系统工具契约后准备，双方在线且确认最新版本才能由房主开战。
配置变更取消双方准备。每方只读取本方公开决策与聊天，建议复用原OperatorAdvice，在下一轮正常请求读取。
每场feedback目录独立，房间和自身比赛进程可重启恢复，未知启动状态不自动重复开局。
共用主机模型账号；存在open_api_key时支持直接Go适配器。房间使用内置工具、玩家已确认的完整战术，
不加载持久库战术或固定开局。检查内容与真实Commander请求逐字比对通过，1200字符战术不截断。
网页暂为房间/决策/聊天，原生画面仍在主机，未实现远端画面推流。完整说明见LAN_ROOMS.md。

另修复启动器写死旧Python环境路径和凭据脱敏遗漏小写open_api_key；扩大回归发现旧技能沙箱
把/lib64错映射为/usr/lib，现只读绑定真实/lib64，真实隔离worker及实验室33项检查通过。
最终相关651项运行、649通过、2项原有占位skip：logs/lan-room-regressions.log。
Chromium两个独立会话完整流程、中文IME、刷新重连及390px布局通过，零页面异常；
logs/lan-room-browser-preview/保存截图与结果，明确使用本地假引擎/消息消费器，无真实模型或SC2调用。
十个旧账本SHA核验全一致，上一批仍finished、181/200（P1=89/P2=92），没有新增实战或重置历史。

## 2026-10-15 最新：第五批已结束，修复已验收

分支`codex/battle-llm-chat`，保留原有全部未提交修改；本轮没有提交或推送。
`logs/go-duels/current.json`指向`random-04f97o8v`，series.state=finished，共4条记录：
首局PvZ进程中断，保留37次请求及原始before-interruption副本，不计胜负、无回放；
第二局PvT、第三局ZvZ、第四局ZvP完整结束，均time_budget/未确认，不报胜。
三局回放保存、cleanup_complete=true、各自源码快照哈希通过。
本批累计181/200（P1=89/P2=92），十个旧历史账本哈希全一致；不重置、不补回。
当前没有新的付费对局。用户已明确忽略余额，不再等待Codex余额答复。

本轮已确认并修复：
- 气矿建筑误占工人名额，导致请求3工实际2工。完成登记只接受真实工人，清掉已观察为非工人
  的名额并保留气矿内隐藏工人。虫族/神族原生复验及第三/四局实战均实际3/3工。
- 显式rally支持已建25%的己方基地作地标锚点和明确受威胁入口；保持地形/占位检查，
  自动集结仍避敌。产能编号按玩家/类型首次发布后稳定，不因死亡/新生重排或重用。
  迟到指令核对label->原tag，已知rally可取消，P2防守使用自己的home；解析失败会释放所有权。
  unfinished-rally与rally-identity两套原生控制都通过。
- 第四局蟑螂假未确认已定位：新单在loop16824实际购买新卵4395630613，旧卵4354211843在16830
  消失，旧已确认收据在16834抢绑新卵，新收据4405854245因此等不到ACK。
  现在只有尚未确认的购买可认领新卵，旧卵结束后释放原收据。
  live回放及assessment保留；原生before/after因果夹具各有两笔真实购买、合计2工蜂，旧码却失败，
  新码正确完成。夹具明确调整新收据请求tag来构造已观察到的改选情形，不是未改动的比赛。
- 新增stop_production args={task_id:己方工人/部队生产ID}，只停后续未购买数量。
  已付费卵/变异/队列继续观察，requested历史保留，cancelled_unissued单列，无退款假设。
  后续append只加新数量，coalesce不恢复停止部分；混合非法计划保持原子拒绝。
  原生20只目标买出1卵后停止：最终2只、18只停止、1次购买、0拒绝。只读/单测也排除弹药；
  顺便修正native_queue_cancel原先依赖不存在ammo字段的范围判断，仍不支持核弹/拦截机。
- 工蜂接到气矿建造令后遭42/44位置拒绝：仅当原工蜂仍为己方Drone、未消耗、目标无己方
  建筑且未绑定开工建筑时允许既有最多5次重选，排除已拒绝气泉。已开工/工蜂消失不补买。
  原生before复现42且无开工，after换第二口实际建成1座；故意注入的1次原生拒绝属负对照，
  不能写0拒绝。原始第四批44的具体触发原因仍未知；44分支有合成验证，不冒充同坐标原生复现。
- 注卵失败反馈增加phase/last_wait_reason/当前己方女王与基地事实；第四局已实战显示等待
  约72秒后发单、90秒总截止仍赶路的事实，未延时/重施。再增创建/截止/剩余游戏秒字段，
  排队50秒后发单仍截止90秒的单测通过；注卵三个时间字段、气泉拒绝恢复、stop_production和旧卵绑定修复均在第四局
  启动后增加，只算单元/原生对照验收，未跑新的付费对局。

最终综合556项运行、554通过、2项旧占位skip：logs/random-tools-final-scope-verified-regressions.log。
`git diff --check`通过。原生控制均0模型调用、无fast-build，不算比赛胜场。
第四局唯一原生拒绝为光子炮887/40：loop13852下令，Forge4391436290在13868死亡，
Probe赶路到14186才报BuildTechRequirementsNotMet；只读回放确认是真实前置战损。
本轮没有外部模型战后复盘，既有数据流审批仍未回答，继续skip-model-reviews。

后续重点：用最新代码开新的有限随机赛程（可seed20261025），冻结本批181及所有旧账本，
每次一局。旧Gateway916自动转折跃门时未确认仍未复现；此前普通控制只出现1413且产出1，
不能把它算作916问题已修复。更早第四批跳虫实际未执行的原生原因也未最终确定，保留原证据；
本轮蟑螂假未确认有独立完整证据，不把所有旧异常一概归为同因。不要盲目重购。

## 第四局与前三局过程记录（进度以顶部为准）

第三局ZvZ完整结束：783.8游戏秒/1860.57墙钟秒、time_budget/未确认。
0原生拒绝、0生产宏失败、0整轮计划拒绝，回放保存、cleanup_complete=true、源码快照一致。
双方气矿扩建后均实际3/3工；注卵施放13/9次确认，macro_larva_egg_observed事件31/22条（关联事件数，非唯一购买/产出总数）。
每方24请求、各2次ReadTimeout，均有后续正常返回。女王战损后等待注卵超时1/4笔，按契约未补买/延时。
本批累计133/200（P1=65/P2=68），十个旧账本哈希一致。
第四局ZvP已按原赛程启动，加载注卵失败反馈phase/last_wait_reason/own_queens/own_bases。
该反馈已实战出现：P1 task21等待约72秒才发单，90秒总截止仍赶路，phase=after_dispatch、
女王原生251目标/位置/能量、正常存活的基地均完整保留，没把超时误报为基地丢失。
随后仅增加InjectRun.summary的request_created_game_s/deadline_game_s/remaining_game_s，
按原排队时刻计算，不在发单时重置；49项相关检查通过，最后综合日志random-tools-final-deadline-complete-regressions.log。
这三个时间字段在第四局开始后新增，本局未加载；不涉及发令/超时行为变更。
本局是本批最后一局；日志random-tools-series-05-match-04-live.log，先查series.pid实际存活再判断进度。
最新综合540项运行、538通过、2旧占位skip，logs/random-tools-final-deadline-complete-regressions.log。
仍为Slower realtime、每方24请求、1800游戏秒/1860墙钟秒，外部模型复盘关闭，余额不作为停止条件。

## 第三局过程记录（进度以顶部为准）

第二局PvT已完整结束，1860.71墙钟秒、time_budget/未确认，0原生动作拒绝。
回放保存、cleanup_complete=true、source快照校验通过，十个旧历史账本哈希全通过。
本批累计85/200（P1=41/P2=44）：首局中断37不重打/不补回，第二局每方24。
P1两个计划拒绝均为公开契约（重复Stalker append、expansion_sites放混合数组）；
P2请求14/21/24 ReadTimeout，14和21后均恢复，24为最后预算不补发。
P2一笔Marine宏因兵营确认被击毁而失败，其他控兵/侦察失败均需按原始死亡证据判断。
第三局约585秒仍在运行，P1/P2各20请求；0原生拒绝/0生产宏失败。
P2两次注卵task63/64超时，前一等待原因均为need free own Queen with25energy，女王已在交战中损失，
未发出注卵订单，符合90秒总截止契约；没有改deadline或自动补Queen。
为避免pending=null被误读成基地消失，现补充失败证据phase/last_wait_reason/own_queens/own_bases。
48项相关检查通过，扩大检查logs/random-tools-inject-feedback-complete-regressions.log；第四局才加载此反馈增强。
第三局ZvZ在约145游戏秒已观察到双方气矿各3/3工并持续增气，无原生拒绝；
独立只读证据match-03/gas-allocation-field-check-01.json，非胜负判定。
第三局ZvZ已经启动、加载新采气+稳定编号+集结点修复；日志random-tools-series-05-match-03-live.log。
仍按Slower realtime、24次/方、1800游戏秒/1860墙钟秒，外部模型复盘关闭。
每次先核对series.json和series.pid的/proc状态，不能拿旧快照当作运行、不能重复开局。

## 第五批修复与前两局记录（状态以顶部为准）

用户明确忽略余额，继续随机实战修复工具；无需再等待Codex余额答复。
新批次`random-04f97o8v`，seed20261021，共4局；每次只启动一局。
首局进程使用幼虫购买确认/轮转修复，尚未加载本局新发现的集结点修复。
首局P2 request8/task29：二矿约59%时显式expansion_entrance失败，而自动集结已使用同入口。
现显式rally支持至少25%建造的己方地面基地，并允许明确选择受威胁入口；原生几何/占位检查保留。
新增原生unfinished-rally-random-tools-after-01已通过：二矿实际30%~31.7%、可见架起坦克，
主矿真实raw.rally_targets=[137.5,65.5]、0拒绝；自动威胁筛选仍拒绝该入口，0模型/非胜场。
另修复迟到回复不能cancel_task取消已知rally、P2迟到防守错用P1主矿算基地标签，
以及集结点无法解析/能力身份缺失后任务一直running占住自动集结的情况。
相关失败用例均先在旧码复现；最新综合529项运行、527通过、2旧占位skip，见logs/random-tools-rally-release-complete-regressions.log。
后续对局加载这些修复。仍为Slower realtime、每方24请求、
1800游戏秒/1860墙钟秒，外部战后复盘关闭。旧账本（含第四批192）哈希冻结，不能重置。
以current.json、series.json、实际进程确认进度；不能重复运行正在进行的局。

首局随后确认进程9328为Z、原SC2已消失，最后采样590.4游戏秒/日志约591.9秒，未保存回放。
具体中断触发原因未知；已保存match/series的before-environment-interruption副本和environment-interruption.json，
登记execution_environment_interrupted/unverified/cleanup_complete=false，无胜负；预算37/200（P1=17/P2=20）保持原字节。
十个旧历史账本哈希仍通过。第二局PvT已按原赛程续跑，使用新集结点/迟到校验/失败释放修复。
首局两方gather_gas要求3但长期显示2，原生gas-allocation-random-tools-before-zerg-01已确诊：
MacroRun绑定建造中的Extractor/Assimilator后pending.tags变成建筑tag，完成时被错误登记到gas_assignments，
3个名额含气矿本身，实际只有2工。原生25秒仅2工、增气48，零动作拒绝，原始失败记录保留。
已修复：完成登记只接受当前真实己方工人；balance_gas清除已观察为非工人的名额/释放列表，
隐藏在气矿内的真实工人仍保留，避免多派。4项新测试旧码失败，68项相关检查通过。
原生复验after-zerg-01和after-protoss-01均已通过：实际3工、增气60/64、0拒绝、0模型调用。
最新综合533项运行、531通过、2旧占位skip，logs/random-tools-gas-complete-regressions.log。
第二局已启动在这次修复之前，下一局才加载采气修复。
第二局约240游戏秒仍在运行，原生拒绝0；人族两口气矿实际各3工，神族旧码一口仍2工。
新零模型verify_gateway_training_live.py正在做真实新Gateway+单只Zealot、已完成WarpGate升级
情况下的自动形态转换训练对照，目录gateway-training-random-tools-transition-01；以result为准。
传送门对照transition-01已完成：真实Gateway->1518->WarpGate->1413，产出Zealot1、0拒绝，
未发916，未复现原始漏确认，不得宣称它已修复/不得盲目重购。
另修复producer_inventory每次按当前tag重新编号：改为每玩家每类型首次发布后稳定编号；
capture保存label->tag，迟到selector只能仍指向请求时原tag，新增编号不能冒充旧建筑。
原生rally-identity-random-tools-after-01通过：旧主矿被debug移除后原hatchery#2收到真实集结点，
新增基地#3，旧#1迟到指令被拒绝，0模型/0原生拒绝。第三局才加载编号修复。
Lair升级中注卵的额外零模型控制morphing-inject-random-tools-order-control-02已通过：
原Hatchery type86/progress1/orders1216仍在升级，高层inject_larva真实扣能量+buff11且完成，0拒绝。
before-01夹具误认升级中类型立即变100而失败，未改运行时；不要把这次假设当作新缺陷。
最新综合538项运行、536通过、2旧占位skip：logs/random-tools-rally-identity-complete-regressions.log。
每次都需检查series.pid的/proc状态；上次用户继续时错误地把旧快照当作仍运行，已纠正并登记中断。P1task47在491.6秒对Gateway发916后8秒无确认且变133，
原始p1-gateway-unacknowledged-audit.json保存，因中断没有回放，原因尚未确认，不盲目重购。


## 第四批完成记录（以下当前字样已被第五批覆盖）

分支仍为`codex/battle-llm-chat`，已有大量未提交修复/测试，不能重置或覆盖。
`logs/go-duels/current.json`仍指向`random-sn3mcspg`，四局全部结束，预算**192/200**，
双方各96次；没有新的付费对局正在运行，不要重复第四局。
赛程ZvT/ZvT/PvT/ZvP，第二局引擎确认人族获胜；其他三局time_budget/未确认，不能报胜。
最新第四局784.0游戏秒、1860.65墙钟秒；回放/清理/源码快照校验完成。
旧随机A135、B172、C179以及旧bot/场景账本全部保留，历史哈希仍校验通过。

本次新增并验收：
- scout可选`stop_on_contact:false`，默认仍遇敌停止。新选项报告接触并继续原move路线，
  不自动攻击/撤退/补派。schema、完整SYSTEM和实际Go用的精简BOT_RULES都已同步。
  原生scout-contact-random-tools-01实际移动15.42格抵达、拒绝0。
  第四局双方模型主动使用，P1继续经过工人并实际抵达敌主基地/多个路点，已验证实战可用。
- 水晶塔普通候选为空/全被原生拒绝时，查询放宽7格间距偏好的密集候选；
  保留碰撞、矿区、预留任务、排除点与原生查询。before-03复现旧24点全拒，
  after-02真实建成1座[33,133]、拒绝0；不是精确重放/比赛胜场。
- 第四局一笔跳虫购买未实际执行，回放无对应动作/扣矿，同帧蟑螂执行且后来用了原请求幼虫。
  “同帧必然覆盖”未证实：3空人口的before-01两单可成功。新增的保护是单笔原生幼虫
  购买确认后再发下一笔，并轮转已有产品目标；不改产品/数量，不隐式重购，不挡Queen。
  larva-batch-random-tools-after-01恰好蟑1+狗2，分两批、0拒绝；
  larva-confirmation-random-tools-mutex-mixed-01恰好蟑2+王虫1，全完成、0拒绝。
  **新幼虫保护尚未跑下一场付费对局**。macro SHA6c310406cd677fb9cbb59884ec363d19e33ec936d0ca36c3451d1f077ab9c4f5。

第四局气泉拒绝原因仍未知；普通工蜂与携矿buff271的工蜂均已原生建成原坐标
[124.5,63.5]且0拒绝，所以未修改gas分支/不自动补买。carrying-03才通过原目标断言；
carrying-02实际建了另一口气泉，原result保留、assessment明确不可判定，不能混计。
女王58等待约72秒才发单，90秒总截止时仍在赶路，按既有契约失败，无自动延时/重施。
P2一笔折跃41错误后已有限重选/继续购买，但整批局末仍等矿，不能声称全完成。

最新综合检查见`logs/random-tools-completion-regressions.log`（520项，查末尾实际结果）；
此前519运行、517通过、2旧占位skip已确认。原生控制都是0模型调用/非胜场。
当前没有可靠Codex余额：CLI有account/rateLimits/read，但本环境control socket不存在。
用户最新明确“不管余额了，继续对战，把已知问题都修复下”，已取消等待余额/最后10%停止条件。
Go额度充足，原种族随机/Slower/24次/1860墙钟上限不变；外部战后复盘仍未获明确授权，继续关闭。
下一批必须新建有限随机赛程并冻结本批192及其他旧账本哈希，每次只运行一局，不覆盖旧记录。

## 第四批过程快照（以下“当前”字样以顶部为准）

**当前最新：第四批random-sn3mcspg四局全部结束，192/200；正在局后零模型核查，不是新付费对局**，以
`logs/go-duels/current.json`、series.json和实际进程为准，别重复开局。
首局ZvT已完整结束：783.8游戏秒、1860.65墙钟秒、time_budget/Unknown，0原生拒绝，
回放保存、cleanup_complete=true、source快照校验通过。预算48/200，双方各24次。
第二局700.8游戏秒、1664.09墙钟秒，engine_end，引擎确认虫族Defeat/人族Victory；
原生拒绝0、回放保存、清理完成、源码快照一致。预算96/200，累计双方各48次。
主日志第二局为logs/random-tools-series-04-match-02-live.log，审计见match-02/tool-audit.json。
保持赛程ZvT/ZvT/PvT/ZvP、seed20261017、Slower realtime、每方24次、
1800游戏秒/1860墙钟秒、输出16000 token/60秒超时，skip_model_reviews=true。
历史随机A135、B172、C179保留且哈希全部通过。C第三/四局环境中断不计胜负。
最新综合454项通过：logs/random-tools-batch-range-complete-regressions.log。
第二局P1请求17的build_supply.args.count=2位置差异曾挡住整轮；已等值搬到外层count，
也覆盖原生目录下的建筑/扩张/形态，不猜数量冲突、不改战术。请求20同产品两次append
仍按公开契约拒绝；失去基地/演员及地图不可走角落另有明确失败，不冒充工具拒绝。
首局3次计划拒绝来自历史累计产量200封顶，现按每批remaining<=200、每次追加<=200，
累计requested不重置；去重/每产品每轮一次/原子拒绝保持。合法取消未发单旧目标后重下也验收。
注卵旧源码原生复现“一女王主矿成功后二矿未执行”，新版等待旧原生251订单结束、
按目标基地匹配；after-01同女王两基地各一次真实扣能量+buff，发单2、拒绝0。
后续收窄为仅在旧注卵已进入实际施法范围时等待，远距行走允许模型明确改目标；
回归已过。最新源码原生复验inject-reuse-random-tools-after-range-01通过：同一女王两基地
各一次扣能量+buff，2次发单、0拒绝，36.03游戏秒；不声称幼虫已出生。女王源码SHA为
89818347c19956cc3b416d95065bcc0d57492aad98fb9600eb87b2f3d627755e。
此前同条复验因自动审批服务用量错误未执行；本次继续后审批恢复、控制通过。
该控制不调用模型、不计比赛胜场、不重置历史。第三局日志为
logs/random-tools-series-04-match-03-live.log，每次续跑只启动一局。
第三局发现反复侦察均被对方工人提前接触截停：约324游戏秒时双方各11次，全部goal_arrived=false。
旧默认行为符合契约；新增scout可选stop_on_contact:false，显式继续原move路线并报告接触，
不自动忽略工人、不改默认、不补派/延长截止时间。事件按可见接触tag集合变化去重。
当前对局仍用旧源码，下一局加载。新增5项行为回归；扩大综合503项，501通过、
2项test_battlefield_scenarios原有占位场景skip，日志random-tools-scout-complete-regressions.log。
verify_scout_contact_live.py原生scout-contact-random-tools-01已通过：默认遇到可见SCV停止，
显式false后同一Probe发单1次、实际移动15.42格、距原目标3.61格并完成，0原生拒绝/0模型调用。
第三局已结束：783.8游戏秒/1860.50墙钟秒，time_budget/未确认，无引擎胜负；
回放保存、cleanup_complete=true、源码快照一致。预算144/200，双方累计各72次。
原生拒绝1为Marine观察后发单窗口确认死亡；P1请求19一次ReadTimeout，20正常恢复。
神族实际发出时空加速，观察到能量消耗与目标buff变化，运行时仍未宣称完整战术效果。
神族卡103人口的请求22已有build_supply count2/issued0，原生24候选位置全拒；
_positions另有Pylon间距7格启发式，尚未修改，正在只读回放核查更密落点。
脚本inspect_pylon_placement_replay.py、日志random-tools-pylon-placement-replay-01.log，
结果写match-03/p1-pylon-placement-replay-01.json。scout-worker-contact-audit.json与
p1-supply-placement-audit.json保留原观测。不要把尚未证实的落点假说当作已修复缺陷。
回放查询01已结束：原24候选数量复现，但原/密集候选全部返回通用Error，与实战的
CantBuildLocationInvalid不一致；没有成功落点对照，结论inconclusive，assessment另存。
verify_pylon_spacing_live.py原生before-03复现：同出生点、22座已有建筑及原任务预留点，
旧24点全部CantBuildLocationInvalid；密集30候选有14点合法，首点[33,133]。
现仅当普通Pylon候选为空/原生全拒时放宽7格间距偏好，再做原生查询；碰撞/矿区/
任务预留/排除点/边界校验保留。原生after-02实际发单1、建成1座[33,133]、拒绝0，
macro.py SHA318dba2b3e6f7814e6cb87bc00862d4b371bc9ae606f4ab95bfeb450a5039dae。
不是精确重放，矿物耗尽、移动单位和真实挂起任务不同，预留位置仅重建为几何约束。
before-01镜像用了地图留白导致布场前失败；before-02/after-01抽到反向出生点，旧算法
有合法点，未复现。原记录保留。SC2同种子仍可能换出生点，脚本最多4次零模型启动选
原方向；after-02第一抽即匹配。所有控制0模型，不计比赛胜场。
最新综合512项运行、510通过、2项旧占位场景skip：logs/random-tools-final-complete-regressions.log。
精简BOT_RULES也同步stop_on_contact:false；删重复说明后3745字符，45k边界复验通过。
此前host-prompt与prompt-fit日志失败保留，不能将旧失败冒充当前状态。
Codex CLI已发现account/rateLimits/read接口，但本环境control socket不存在，无法获取
真实余额；没有创建新Codex线程或读取凭据。仍不能将Go计数代替Codex剩余10%。
第四局日志logs/random-tools-series-04-match-04-live.log，每方24、Slower、外部复盘关闭。
第四局已实际启动，双方首请求的Go model/16000 token/60秒及新scout参数提示均核对通过。
约177.77游戏秒P1二矿Extractor任务24失败：Drone4364435457活着，原生1154走到
geyser4332716033/[124.5,63.5]后CantBuildLocationInvalid。roach_warren在[141.5,38.5]，
未发现直接建筑冲突；原因未知，未更改gas分支/未自动重购。原观测另存
match-04/p1-extractor-rejection-audit.json；verify_extractor_site_live.py已准备，待局后零模型核查。
当前预算至少162/200，查实际budget.json；勿重开第四局。原生scout及Pylon新修复均已加载。
第四局最终784.0游戏秒触及1860.65墙钟秒，time_budget/未确认，回放保存、
清理完成；series.finished，累计192（双方各96），无外部复盘。以match.json/timeline精确秒数为准。
新scout双方都主动选false，P1已实际抵达敌主基地/多个路点；同Queen两矿多次注卵确认，
见continued-scout-injection-audit-01.json。注卵58在等待约72秒后才发单，90秒总截止时
仍在向正确主矿赶路，按原契约失败，不延长/重施。P2折跃41错误已有限重选，后续继续购买。

第四局局后待核查（尚未修改运行时）：
1. Extractor [124.5,63.5]：普通工蜂零模型控制extractor-site-random-tools-01已实际建成、
   拒绝0，4气泉查询都合法，固定坏落点假说不成立。携矿01四抽出生点都不符，布场未完成；
   新脚本以显式占用另一个气泉固定原目标，待补携矿状态控制，不再依赖出生抽签。
2. 跳虫任务44约726.92秒因一笔718.75秒订单未确认而失败，actor4396417034随后被蟑螂
   使用。p1-larva-unacknowledged-audit-01.json与p1-larva-command-replay-01.json已保存。
   16100同时派1351/1343，但回放仅记录16103的1351，随后没扣跳虫矿、原Larva仍空闲；
   16162才变1351卵。资源920/65、空人口3足够两单。原生同时两幼虫购买假说待控。
   verify_larva_batch_live.py before-01正在运行：精确3空人口，蟑1+跳虫2，零模型。
   单Hatchery、3空人口、蟑1+狗2的before-01实际全部成功，不能称“同帧必然覆盖”。
   新保护现已实现：任一原生幼虫购买未确认时不再派第二笔；ACK后才发下笔，并在
   活动幼虫生产目标间轮转，不改产品/数量、不隐式重试/补买，也不阻塞Queen等其他产能。
   4项专用回归；当前综合519运行、517通过、2旧占位skip，日志random-tools-larva-complete-regressions.log。
   新macro SHA6c310406cd677fb9cbb59884ec363d19e33ec936d0ca36c3451d1f077ab9c4f5。
   larva-batch-random-tools-after-01与larva-confirmation-random-tools-mutex-mixed-01原生复验正在运行，
   查result.json，不得预先声称通过。新保护尚未跑新的付费对局。

携矿气泉补控：carrying-02调试占位Extractor被引擎移离气泉，实际建成另一个点，
原result保留，assessment明确对原目标不可判定。脚本已加目标断言，用真实建造占住另口气泉。
carrying-03原生通过：buff271的Drone对[124.5,63.5]真实建成1Extractor，拒绝0。
所以固定落点/携矿状态均未复现原拒绝，原因仍未知，未改gas运行时分支/未自动重试。
通用技能订单确认也按单位/点目标匹配，注卵复用等待；闪烁/护盾/加速/装卸5项原生复验通过。
混编support-army-random-tools-02原生通过，7种武装/支援单位同条attack_move实际到达，
发单1、拒绝0。01布场目标太近未实际派遣，保留为未通过，不能混计。
首笔购买前已到达的旧队列产出不再满足新目标，新增回归已通过。
下文较早“正在运行/正在验收/尚未建立”段落是过程快照，以本段及current.json为准。

当前分支`codex/battle-llm-chat`有尚未提交的工具修复和新测试，必须保留。
当前用户目标是继续DeepSeek双方随机种族实战、排查工具，直至**Codex**额度剩余约10%；
用户明确Go额度充足。环境不能查询Codex余额，已请求用户提供百分比，不能用Go调用数代替。
原困难Terran目标已有后续新胜局，下面的第22局状态属于旧交接快照，不是当前计数。

活跃赛程以`logs/go-duels/current.json`为准。`random-qs_5mo05`保留135/200；
最新`random-e3u9pe2n`第一局TvP已完成：602.6游戏秒，人族投降，引擎Defeat/Victory；
43/200，拒绝1发生于单个Marine观察后死亡的发单窗口。失败生产均有相应基地被摧毁证据。
第二局PvP完成于783.8游戏秒、time_budget/Unknown，当前91/200；拒绝6，其中1Gateway转换、
1WarpGate位置拒绝（已按既有有限重试恢复）、4观察到发单窗口内单位死亡。
第三局PvP因执行环境重启中断，最后采样700.5游戏秒、日志约711秒；双方各24次，
当前139/200，未取得引擎胜负或保存回放。原始match/series保存为before-environment-interruption，
中断证据见match-03/environment-interruption.json；计数不变，下一局编号4。
保存随机赛程TvP/PvP/PvP/ZvP，
seed20261016；`--skip-model-reviews`为true，等待复盘外发授权期间仅保留本地诊断。
`random-i9702q2l`已完成4局，当前172/200；第四局Terran vs Protoss在783.7游戏秒触及墙钟
时限，双方Unknown，原生命令拒绝0。P1完整复盘被旧60000字符保护挡住，已修复并保存
79438字符的请求预览；补充复盘外发被自动审批拒绝，已向用户请求明确授权，仍未发出、未计费。
未经回复不要运行`/tmp/recover_random_duel_p1_review.py`或通过其他入口发送该第四局诊断。
新批次已冻结旧账本哈希；即使获得授权，也不要在新批次运行中增写旧B账本，否则其历史
校验会失败。先完成当前新批次再处理补充复盘，并记录合法追加的账本前后快照。
下一批随机实战可用`--skip-model-reviews`继续，只留本地诊断、不发送战后复盘，策略和计数均保留。
该选项会保存于新赛程，续跑时继续生效；不修改默认赛程行为。
先检查该赛程是否已有运行中的对局，再用`go_duel_series.py --resume`继续，每次只启动一局。
旧840/962、278/312 bot账本以及其他duel账本均保留，续跑程序验证历史哈希，不恢复旧包覆盖新数据。

本轮多次中断的45,000字符是宿主输入保护，非Go额度。按当前用户“Go额度充足、保证工具”
目标，**Go战斗输入现设90,000字符硬上限**，其他客户端仍45,000。继续做已有经济压缩；
当前事实、反馈、用户建议不删，实际输入上限和大小进报告。每局每方24次请求、Slower realtime、
1800游戏秒/1860墙钟秒保持。第三局实战已成功发送最高59,223字符，0原生命令拒绝，
最后783.3游戏秒因墙钟时限结束、双方Unknown，不能冒充胜利。

最新工具包括`use_ability`、`cancel_native_building`和`cancel_native_queue`。
第二局PvP启动后新加`cancel_native_morph`，关联paid building-upgrade宏，支持Lair/Hive、
Orbital/Planetary、Greater Spire等已观察订单/未完成形态的单次取消；已完成不撤销。
普通建造、科研、虫卵/移动单位变异不在此接口范围。下一局加载；原生控制脚本
`verify_native_morph_cancel_live.py`原生控制`native-morph-cancel-random-tools-01`已通过：
Lair/Orbital取消各恢复原形、停止count2的剩余购买，发单1、拒绝0，观测各113矿及Lair75气返回。
运行时仍不推断退款/取消因果；Hive/Planetary/Greater Spire只做目录与边界测试，未逐项原生验收。
第二局另观察到Gateway自动开始1518转换时拒绝未确认的916训练；新代码仅在有此转换证据且
未确认付款的NotSupported上有限重选，保留其他已接受队列，不泛化所有NotSupported。
scout的`targets:["expansion_sites"]`也等值转换为已有字符串选择器，混合列表不改写。
这些改动均下一局加载。424项完整回归曾通过，后续新增回归以最新日志为准。
第二局请求20另试图cancel_task取消活动rally_units任务36，以换前线集结点，原校验漏了
rally_units导致整轮拒绝；已补充，支持已结束己方集结令noop，未知ID仍拒绝。
第四局两辆未架起坦克没有移动，己方视野回放确认它们的原生路径为0，而SCV对照有正距离。
活动空间任务现在每3游戏秒报告逐单位`native_paths`及附近己方建筑，查询失败不冒充不可达；
不自动降补给/挪建筑或改目标。独立补给站围堵场景清路控制`blocked-tank-egress-random-tools-06`
已通过：原生路径正向对照14/12，围堵后0/0，明确降下6站、路径恢复、重发原目标后两辆坦克
实际到达，拒绝0。这不证明原对局具体是哪些建筑阻挡；前5次布场/确认时机失败记录均保留。
降低形态已出现后原生路径还需0.85游戏秒才恢复，不能把命令确认或形态变化当作已清路。
`inject_larva`新增可选`timeout_s`整数1..300（默认90），用于模型显式留出跨地图步行时间；
失去命令/停止移动仍提前失败，且不重发。第四局启动后才新增，下一局执行器加载。
第四局实际拒绝多补给请求后，`build_supply/build_structure/expand`也支持有限count1..200；
建筑逐个建成后购买下一个，重复活动目标仍合并。Probe/Drone建造后保留真实未完成建筑绑定
以支持稍晚取消，同时释放Probe；建筑死亡不重购。371项完整回归通过，后续生命周期检查
见`logs/random-tools-building-lifecycle-regressions.log`。新原生控制脚本尚待本局结束运行。
队列取消支持关联的建筑单位训练/科研，停止原宏且仅取消其已观察队列；原生零模型调用控制已验
SCV训练和步兵升级取消，各一次发单、拒绝0，扣采矿后分别观察50矿和100矿100气返回。
运行时不推断退款或取消因果；基地升级、虫卵/移动单位变异与弹药取消尚未由此工具覆盖。
建造取消、三族生产、原生即时拒绝反馈、观测压缩和超时恢复证据见`RANDOM_TOOL_VALIDATION.md`。

原生三族补给批次各恰好建成2个、拒绝0；Probe/Drone建造8秒后取消分别返75/225矿并停止
余下批次。女王跨地图单次注卵在91.03游戏秒成功扣能量+出现buff，发单1、拒绝0。
训练/免费变形单批1..200；append总批量仍<=200、每产品每轮一次、请求重送去重，非循环自动生产。
Go输入适配只改唯一目录名称拼写、数量字段位置和任务reason位置，保留选择/数量/目标并写翻译记录。
累计计数不回退，原模型回答、失败比赛和源码快照不重写。已启动进程不自动加载新的执行器修复；
Go请求子进程单独记录其实际源码SHA-256。第三批第一局启动后修复同类建筑/形态跨任务
错误计数，购买只关联发单位置或建筑tag，形态只关联选中actor；下一局加载。
最新完整回归447项通过，在`logs/random-tools-warp-latest-complete-regressions.log`；
新增折跃位置说明后52项相关回归通过，`logs/random-tools-warp-prompt-regressions.log`。
修复后原生`building-batch-random-tools-bound-products-protoss-01`也恰好建成2水晶塔、拒绝0。

神族train_unit新增warp_target:[x,y]，只用WarpGate在指定点3格内原生折跃，
无电/视野/合法位置则等待，不回退主矿Gateway；不同目标独立购买与出生绑定。
原生控制warp-target-random-tools-01通过：主矿和前线各1追猎者，均在3格内独立确认，
原生拒绝0、模型调用0。另修复不同目的地绕过同轮同产品一次append限制，
修复前回归失败、修复后98项通过；另修复原生拒绝的旧购买阻塞后续同点折跃确认，
修复前复现、新106项生产/取消回归通过。第四局启动后新增，下局加载。第四局ZvP在环境starting/ready转换后进程变为僵尸，日志停约589游戏秒；
已备份原始记录并登记中断，第三批finished、预算179/200，未确认胜负，未保存回放。
先查最新控制/新赛程状态，不重开第四局。

以下为旧交接原文，保留作历史说明。

本分支包含此前云会话中未提交的执行器修复、ChatGPT/Go 接入、回归测试和续跑数据。
它不包含机密值、登录文件、服务数据库或原始 API 会话。新会话无需继承旧聊天即可继续。

## 当前目标与状态

用户已授权自主迭代，直至取得**新的困难 Terran bot 引擎胜利**。每局完成后检查细节：
有明确证据的执行缺陷先复现、修复、验证；否则改进提示词与战术。模型选择生产、扩张、
侦察、编队、防守及出击，脚本只执行显式命令和已分配军队的微操。

- 下局模型：OpenCode Go `opencode-go/deepseek-v4.1-flash`，接口
  `https://opencode.ai/zen/go/v1`，云机密引用变量为小写 `open_api_key`。
- 当前账本：`logs/autonomous-bot/20261012-093031-xhnbux9u`，22 个编号记录和一次独立重试。
- 本轮请求尝试为 **451/572**（战斗 427、复盘 24）；此前试验的 278 次另行保留，累计 729 次。
- 第 22 局在 725.2 游戏秒由引擎确认 Defeat，24 次战斗回答完成，动作错误为 0，回放
  23,011 字节，清理完成。本轮 `goal_reached=false`，没有新的困难胜利。
- 历史 12 局试验中的困难胜利仅作为经过回放核验的参考，不能替代本轮目标。
- 上限保持每局 24 次战斗请求、1800 游戏秒、1860 墙钟秒；明显败势主动投降。
  用户允许继续直到困难胜利，所以新增一局可登记 26 次预算扩展，不能清零历史。
- 旧会话的 Go 接入检查停在机密引用缺失，**没有验证付费 Go 请求**。新会话须检查自己的
  实际注入状态；旧会话的环境报告不能用于判断新会话认证或额度。

## 接续代码及账本

先检查另一个会话自己的 Git 改动和正在进行的对局。合并本分支时保留它的新工作，
不要重置工作区或覆盖它已产生的比赛记录。没有现有账本的新检出可恢复仓库中的交接包：

```bash
cd /workspace/sc2Llm
.venv311/bin/python tools/restore_bot_campaign.py --verify-only
.venv311/bin/python tools/restore_bot_campaign.py
```

`handoff/bot-campaigns.zip` 约 1 MB，包含两轮已保存的账本、策略、报告、复盘和可用回放。
`handoff/manifest.json` 保存 SHA-256、文件清单和原预算。恢复时将旧绝对路径迁移到
当前检出目录，保持计数及游戏结果；发现同名账本就拒绝写入，避免覆盖新会话的进展。
`--root` 可以指定其他检出目录。恢复操作不发模型请求、不启动游戏。

完整原始反馈日志、CLI 请求和服务状态只保留在原云环境；归档中的 `journal` 路径是
来源引用，文件不随包恢复。归档提供完整比赛世界采样和已有复盘，可供后续最近三局
历史比较及历史胜局核验，但不应声称能够逐条回查未提供的原始反馈。

确认已有/恢复后的账本最新程度，再检查实际接入：

```bash
.venv311/bin/python bin/cloud-go-status.py
```

它只发送一个最小请求，返回 `available: true` 才算 Go 实际接入成功。
`credential_binding_missing` 表示本地没有机密引用，`request_sent: false`；它不是
服务商返回的 HTTP 401。环境配置需允许机密发送到 `opencode.ai`，保持代理与 TLS 校验。
不要把机密写到聊天、源码或交接包，不要换用另一个模型制造成功。

接入成功后续跑：

```bash
./run_sc2.sh .venv311/bin/python autonomous_bot.py \
  --resume logs/autonomous-bot/20261012-093031-xhnbux9u \
  --until-hard-win --switch-model opencode-go/deepseek-v4.1-flash
```

切换被记录到 `model_changes`，后续战斗和复盘均使用 Go，推理强度为 low；
不自动回退到 ChatGPT。若本轮已有 Go 战斗，普通 `--resume ... --until-hard-win`
沿用账本模型，不会因为 CLI 默认值而改回 ChatGPT。

Go 复盘发现功能缺口时，程序暂停新局；当前编码会话需检查真实错误证据，写出行为复现，
验证原代码失败、修复后通过，再续跑。Go 接入不会调用 ChatGPT 的后台源码修复 CLI。

## 提示词与最后复盘

系统提示已在 `arena/autonomous_bot.py` 的 `BOT_RULES`、`INITIAL_STRATEGY` 和
`arena/autonomous_review.py` 的 `REVIEW_RULES` 中版本管理。`handoff/prompts.json`
另外保存提交时快照与完整的最新准备战术，`handoff/review-20.json` 至
`handoff/review-22.json` 可直接审阅；更早复盘在归档中。

运营原则是合理预留当前扩张和必要前置，把余下矿气及时转成真实军队；检查空闲产能、
吞吐、供给和已完成数量。允许扩张运营、科技、防守反击、时机进攻及其他可执行选择，
不是固定快攻。重复失败需比较两条具体路线，不能只是提高同一生化编队人数。

第 22 局主要偏差：只有一座兵营时过早扩张和补工；到 500 秒实际 16 枪兵、4 掠夺者；
资源持续积累，交战后部队迅速减员；转坦克发生太晚，没有观察到坦克完成。
最终仍有 6855 矿、1037 气，但已无单位。复盘判定 strategy，无已证实的新执行缺口。
下一局准备战术选择**单矿坦克防守反击，再有条件扩张**，并比较了生化整队路线。
它是模型选择的候选方案，须按新观测调整，不是宿主强制开局或胜利保证。

## 已验证的执行器能力

已有修复包括防守动作来源、观测分矿标签进入输出约束、负人口余量下零人口建筑恢复、
持续集结点不独占训练设施、撤防后 all 派兵等候释放、科技实验室专用生产与普通生产的
设施分配、已请求挂件不被长训练批次饿死、空间任务成员损失后的继续执行及有效交战
不误判停滞。当前观察使用可还原的表格压缩，45,000 字符战斗输入上限保持不变。

请求用尽后已有命令继续执行。只有连续 120 秒既无可推进命令、军队原生命令，也无
可见敌军时才按 `request_budget_idle` 停止；结果为 Unknown。Unknown、主动投降与引擎
自然结果分开记录，只有新的 `engine_player_result: Victory` 才算达成目标。

依赖及 Linux SC2/地图/OSMesa 准备见 README；运行工具、游戏与认证不随 Git 分支安装。
本次交接提交的 309 项相关检查通过，包含执行器、Go 接入、账本切换及恢复行为。
另外在临时检出路径完整恢复了 146 个文件，核实计数不变、最近三局为 20/21/22，
且历史胜局回放可核验。机器可读记录在 `handoff/validation.json`。
历史全套测试有两项因缺少派生速度地图失败及两项跳过，不应称为全套已通过。

## 续跑原则

模型亲自下令；不加入固定开局、隐藏敌军统计、作弊或自动战略脚本以获得胜利。
同产品活动生产命令合并，不是追加数量；实际训练量由已确认产出判断。
宿主不会自动补前置，编队不会自动吸收新兵。记录本局实际加载的源文件与种子，
不要把运行中未加载的修复归因到战果。看到明显败势及时止损，避免无进展消耗。

## 2026-10-15 后续待办补充

第三批第四局ZvP现已中断，原始series另存series.before-match-04-environment-interruption.json；
P1请求10外层queue_mode使整轮拒绝，
已新增训练模式字段等值移位，内外冲突仍拒绝；107项检查通过，下局加载。
主日志logs/random-tools-series-03-match-04-live.log。
本地tool-audit-before-interruption已保存；不发送外部复盘，不再启动第四局。
然后可运行零模型补充控制：
```sh
./run_sc2.sh .venv/bin/python verify_native_morph_cancel_live.py --label random-tools-remaining-01 --products hive planetary_fortress greater_spire
```
上述控制random-tools-remaining-01已全部通过：各取消发单1、恢复原形、原生拒绝0，
Hive返150矿113气、行星要塞113矿113气、大龙塔75矿113气（均扣实际采矿收入）。
这些是debug控制结果，运行时仍不推断退款。保持fast_build=false。
最后继续下一批4局独立随机seed20261017（ZvT/ZvT/PvT/ZvP），保持上限与历史哈希：
```sh
./run_sc2.sh .venv/bin/python go_duel_series.py --random-races --games 4 --seed 20261017 --skip-model-reviews
```
上述新赛程尚未建立，不能称为已开赛。

第四局新增22次原生拒绝：3跳虫发单窗口死亡；19女王存活、出生后短窗口攻击拒绝，
随后女王移动，准确引擎原因未知。DefendBase已复现并修复逐帧重发：等待1游戏秒确认，
明确拒绝按1/2/4秒退避，连续5次暂停该演员（保留占有、报告模型，其他守军继续），
不擅自改防守基地或改派单位；155项防守/Go相关回归通过。
新verify_queen_defense_live.py控制01缺供给而未训练，02补足供给后通过，
实际移动/伤敌，拒绝1并退避恢复。03新增发单前原生可用性检查后通过：
Queen完成度1时ready仅[1,4,251,1694]，等移动/攻击能力开放再发单，
实际移动/伤敌、拒绝0；124项相关检查通过。
当前同脚本--skill order_units的random-tools-spatial-01正在原生验收，
日志random-tools-queen-spatial-live-01.log，先查进程/结果再开新局。先核对再开新局，避免并行游戏。
P1请求15不是超时：finish_reason=length，输出8000且reasoning_tokens8000，无公开答案。
后续Go战斗输出上限提高到16000，review显式24000保持，24次请求/60秒超时不变。
当前旧进程请求仍显式8000；新子进程的实际源码SHA记录，不声称旧父进程热更新。

普通SpatialMission原来查询能力后遇暂不可用就立即失败/STOP，现保留原编队/目标，
最多等8游戏秒，每0.5秒重查且受原deadline约束；超出仍失败，不部分派兵/换单位。
96项空间/防守相关检查通过。旧formation-arrival测试的“攻击中”负例实际无订单，
对照开赛旧源码与当前均会完成，已修正负例为真实ATTACK订单；基线对照日志保存。

第四批第一局接续约525游戏秒，仅P1早期ReadTimeout一次已恢复；
首次主力全灭是原生死亡确认，不是工具失败，新增执行拒绝0。
已准备verify_support_army_live.py零模型混合armed/support army验证，
目标是确认医疗运输机/渡鸦/感染虫/飞蛇/观察者/监察王虫的ScanMove原生别名
在同一attack_move编队中能实际到达；尚未运行，不声称通过。
等当前D第一局结束后可运行：
```sh
./run_sc2.sh .venv/bin/python verify_support_army_live.py --label random-tools-01
```
元数据已核对scan_move19和attack_attack23均remap到attack3674，不改映射。
