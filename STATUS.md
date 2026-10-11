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

## 更新（2026-10-09：LLM 主动搜索矿区与通用位置命令）

用户明确要求矿区搜索可以是技能，但必须由 LLM 主动调用。未写“主矿已空→自动搜索/改主力目标”的触发器，未启动新的付费对局。旧 launcher-runtime/match-agtqwe6c 已于游戏约 725.58 秒因 `combat micro requires one context per action` 退出，cleanup_status=complete；不是自然胜负。

先做最小判别 ../_probe/llm_spatial_20261009/before.json：旧 scout 被拒绝，move_out 不接受 target，rally_units+move_out 得到2动作/1上下文。新增 arena/spatial.py、scout/order_units/cancel_unit_order 的队列和 schema；battlefield 发布己方地图/部队位置、区域当前可见比例/上次完整观察时间，spatial_reports 独立于分页给出最近8个终结任务。模型自行推断旧目标是否仍成立并派遣。scout 可访问指定路线或 expansion_sites，只在显式命令后执行，遇当前可见敌人报告并请求停止，不自动攻击/撤退/继续搜索。通用调兵有位置目标，既有微操只接管显式 order_units，侦察与自定义技能保留控制。完整合同 SPATIAL_CONTROL.md。

修复 RallyOrder 直接 append 漏记上下文（改走 TaskQueue.issue），以及轨道能力检查遇 .spec=None 的崩溃。原生判别还发现精确目标被建筑占住时 path query 返回0，因此允许原目标6格附近的可达靠近点（格心误差最多6.75），公开记录请求与实际坐标；不能换矿区、偷看敌军、无声重试。不可达/能力不允许/死亡/期限/20游戏秒停滞都报告；临时观察不到单位不擅自补员。

证据：checks_latest.log 全套405项/30.864秒通过（包含新增22项）；随后增加资源坐标缓存失效检查，spatial_final.log 的新增23项/0.532秒通过。两组不是相加后的428项。原生 native.json/log：SC2 5.0.15.97579、normal fog、realtimeTrue、Normal、无画面；受控友方Marine/Barracks与隐藏敌方Hatchery，host明确派遣、0模型API。13条检查通过：己方视野[y,x]与地图坐标一致、隐藏出生点未判空、集结+侦察+微操不崩、实际侦察到达、指定位置再次到达、无自动新搜索、隐藏建筑直到侦察才可见、接触停止/敌情保存/独立报告/实际请求携带成功；原生引擎拒绝0、cleanup_complete=true。native_prompt_sample.json 是真实原生观察经生产Commander构建的请求，回复是明确本地mock，不是DeepSeek自主思考的证据。两个早期失败实验保留native_first.*（到达覆盖率87%，探针错误地要求100%）与native_blocked.*（建筑占住精确目标），未伪装成成功。

性能合成判别（非原生FPS）：176x184地图、200机动单位+200矿点/25簇、500帧，Battlefield.update 均值6.394→0.683ms，p95 6.438→0.680ms；仅观察到的矿点tag/坐标集合变化时重做聚类，当前视野每帧更新。performance_before.json/performance.json；没有声称完整对局帧率或模型搜索策略已验证。宿主清理后项目游戏进程为空。下一局加载新执行层，原生完整矿区路线/三族各兵种/实际模型自主调用仍待正式对局观察。

## 更新（2026-10-08：官方 DeepSeek 现场新增/修复 Python 技能，下一局启用）

用户问能否现场补技能，随后明确选择“保留当前局，下一局启用”。未重启或主动中断当前比赛。开发期间原局 official-runtime/match-krctza57 已自行结束：P1 Terran Victory / P2 Zerg Defeat，surrender=null，shutdown_signals.events=[]，cleanup_status=complete，录像该 run/match.SC2Replay。现有旧进程不支持新增入口，不能把本次实现说成已接入这一局。

先本地最小判别 ../_probe/live_skill_authoring_20261008/before.json：Queen 底层 tactical cast 已含 effect_inject_larva / 251，但原 battle schema 无开发字段、official launcher 未启用源码维护。没有先盲改引擎，没有新增原生测试局或付费模型调用。

新增 arena/live_skills.py：官方启动器默认启用，可 --without-live-skills 关闭。主模型通过顶层 develop_skill={name,unit,goal,verification}、tasks=[] 请求后台写一个新技能；此回复不能混入作战任务/JSON技能定义，旧任务继续。技能缺失由主模型判断，不自动代替其决定宏观策略。独立官方 deepseek-flash 子进程使用 high、12000输出token上限、90秒请求超时；主指挥仍 low/40秒，副官配置未改。每方每局最多3个开发任务（含修复）、每任务最多2次实际API尝试，网络/额度错误不重试；总任务240墙钟秒超时杀子进程。终局终止在途开发，不迟到安装。

开发模型收到合同、己方当前观察/局部ctx、目标、现有源码（修复时）、实际失败上下文/错误，以及首轮失败的完整测试诊断。模型只提交固定三个文件（源码/manifest/unittest），无host执行模型代码。沿用bwrap无网络/无key环境的自测、独立SkillLab验收/失败回放；queen_inject profile另查正例、低能量、已有buff、无目标、无女王、连续帧避免重复、观察buff后等待到期再注卵。普通 local_tactic 仅承诺已有通用合法性检查，不能验证任意自然语言目标的战术效果。注卵专门验收通过也不是原生引擎效果证明。

通过后由游戏主线程加载冻结源码，更新python_skill_library/control_policy，主模型收到ready后自行派遣NEW任务。旧任务持有旧冻结版本，不自动重新派遣。源码失败自动请求有界修复并阻止新调用；任务死亡/期限/引擎结果不当作已证明的源码缺陷。开发忙/预算耗尽/失败均有可见事件，不用旧行为假装修好。完整错误、阶段和ready状态接入中文双方输出窗口；请求/源码/回执在各run/pN/live-development/job-N/，developer-usage.jsonl记录开发调用用量，不记录私有推理。

已验收技能持久化至 skill-library/live-official/<race>/<skill>/ 下，以 deepseek/deepseek-flash 精确身份发布；下局启动重新验收并恢复，保留注卵profile。与原OpenCode人族来源库分开，避免虫族覆盖人族。相同种族同名技能并发发布若HEAD已变，明确拒绝旧版本发布/安装，不覆盖。已有非法库会报错，不静默退回。新增目录空命名空间无发布版本时明确提示不加载。未发布任何“伪称DeepSeek写的”测试技能到真实持久库。

验收：checks.log 198项通过/19.514秒，含16项新增和既有延迟、修复/持久化、Python隔离、三族、运营、轨道、防守、微操、生命周期、投降、prompt回归。新增实际子进程+IPC+隔离worker链使用明确mock模型回复/host-authored Queen fixture，未调用真实模型/原生游戏；不能称DeepSeek已经自行写好或真实注卵。首轮测试的fixture错误（冻结Snapshot直接赋值和预期错误层级不符）已改正，非引擎修复。下一局的模型实际开发成功率/引擎效果待观测。

official_duel.py --prepare-only --p1-race terran --p2-race zerg --game-speed slower --without-defense 通过，run=official-runtime/match-inrptzns；main_usage两边0调用，live_skill_authoring/source_repair配置true，prepare-only finally关闭空coordinator，所以run末尾development.enabled=false表示已关闭准备过程，不是配置关闭。未改当前run指针/未开启新局。证据 ../_probe/live_skill_authoring_20261008/prepare.log 和 run.json；汇总 logs/live_skill_authoring_summary.json。


# SC2 AI 对战环境 — 现状

## 已证实可用（有日志证据）
- SC2 4.10 Linux 官方客户端 4,115,224,017 bytes，zip 无损坏，ELF 原生
- pysc2 v3.0 + Python 3.11.17（uv 隔离，无需 sudo）
- **端到端对局跑通**：60 步 0.4s，生成合法 .SC2Replay（MPQ / 4.10.0 / build 75689）
- 录像文件: Replays/Simple64_2026-10-06-13-48-51.SC2Replay

## 已证实不可用（别浪费时间重试）
- U盘 SC2 5.0.16：Windows 二进制 + exfat 只读挂载 + 无 Linux API 版本对应
- pip 直连 pypi.org / files.pythonhosted.org：SSL UNEXPECTED_EOF（用清华镜像）
- sudo：不可用（沙箱 no-new-privileges）→ 全程用户级安装
- SC2 的 EGL 渲染：libEGL 加载成功但 "Devices tried: 0"。
  ~~四种 EGL_PLATFORM 全失败 → 2019 二进制 vs Mesa 26.2.4/RDNA4 不兼容~~
  **【此结论已证伪，见下节】真正原因 = 沙箱隔离了 /dev/dri，不是驱动不兼容**
- libgallium-26.2.4 无 OSMesa 符号；AUR/Arch 官方源均无 osmesa 包
- conda-forge 无 linux-64 的 libosmesa

## 关键路径信息
- SC2PATH=/home/tyrion/Projects/wolf/sc2-arena/engine/StarCraftII
- 解释器: .venv311/bin/python (3.11.17)
- pysc2 源码: pysc2-src/ (v3.0, 已打 3.11 兼容补丁 colors.py:124)
- 地图名用地标名(如 Simple64)，不是路径

## 待验证的假设（未证实）
- 4.10 录像能否在 5.0.16 客户端播放 → 大概率不能
- feature 层能否支撑 LLM 决策 → 未测

## 已知坑（踩过的）
1. absl flags 必须 app.run() 解析
2. agent.step() 要传**整个 timestep**，不是 obs.observation (run_loop.py:42)
3. save_replay(replay_dir) — v3.0 只接受一个位置参数
4. random.shuffle 双参数形式 Python 3.11 已移除

## 【重大突破】RGB 画面已打通（有实拍帧为证）
logs/frame0.png = 256x192 真彩色 SC2 渲染帧（指挥中心/矿/SCV 清晰可见）

链路：SC2 -osmesapath libOSMesa.so.8 → OSMesa → llvmpipe (LLVM 19.1.7)
验证：rgb_screen shape=(192,256,3), unique=243, all_zeros=False

### 最终解法（跨发行版拼装，因为 Mesa 26 移除了 OSMesa）
1. Debian libosmesa6_25.0.7-2+deb13u1_amd64.deb → libOSMesa.so.8（有 OSMesaCreateContext 等符号）
2. Debian libllvm19_19.1.7-22+b1_amd64.deb     → libLLVM.so.19.1（Arch 只有 LLVM 23，不兼容）
3. Debian libz3-4_4.13.3-1_amd64.deb           → libz3.so.4
4. 系统 libedit.so.0 复制成 libedit.so.2（名义版本，符号兼容）
   以上全部放在 osmesa-lib/，靠 LD_LIBRARY_PATH 生效（无 root，装不进 /usr/local/lib）

### 三个必须打的 pysc2 补丁（缺一个就静默失败）
- platforms.py known_gl_libs：移除 EGL 项（SC2 4.10 的 EGL 在 Mesa26/RDNA4 上必失败）
- platforms.py start()：PYSC2_OSMESA 显式覆盖，绕开 ldconfig -p 探测
  （ldconfig -p 完全忽略 LD_LIBRARY_PATH，探测不到就静默加 -headlessNoRender）
- platforms.py __init__：不要注入 SC2 自带 Libs/
  （其 libstdc++ 是 2019 的 GLIBCXX_3.4.21，会让 libOSMesa.so.8 报
   "Failed to load library file!" 然后静默退回 stub renderer）

### 混合观察已确认可用
feature_screen(27,64,64) + rgb_screen(192,256,3) 同一局同时拿到 → LLM 看数据、人看画面

## 【Skill 层完成】原子动作 → 可调用技能
arena/units.py  单位类型/名称映射（SCV/Marine/CommandCenter/MINERAL_FIELD...）
arena/core.py   World 解析 + SkillSet（11 个技能）

技能清单: no_op / gather / train_worker / train_unit / supply_depot /
          barracks / build_structure / attack_nearest / move_out / expand

### 三个靠实测才纠正的错误（不是猜的）
1. feature_units 的 x/y 就是**屏幕像素**（0..64），不是世界坐标
   → 证据 logs/coord3.log: F.select_point('select',[36,35]) 选中成功
   → 我先前一直拿它和 camera_position(minimap 空间) 混算，白折腾很久
2. 动作必须用 FUNCTIONS.xxx(...) 调用，不能手工拼 FunctionCall(id,args)
   → 官方 scripted_agent.py:107 的写法；枚举会自动转换
3. train 动作**必须先把建筑选中**才会 available（先有鸡还是先有蛋）
   → 先检查可用性再选中 → 永远失败

### 采矿的正确节奏（logs/ab_test.log, logs/combo3.log）
- 必须**持续**下发 Harvest_Gather，只发一次矿物会停在 50
- 目标矿物要**固定缓存**，每 tick 重选最近矿会让工人反复改道、永远走不到
- 单 SCV 选中比 select_all_type 更稳（50→105）
- 实测: 12→14 workers, supply 12→15, 矿物在 0~50 间正常波动

### 其他修正
- World.army_count 应读 player[8]，我先前误读 player[5]（那是 food_army）
- pysc2 v3.0 的 save_replay(replay_dir) 只接受一个位置参数

### 仍未做
- LLM 适配层（多 provider apikey）← 下一件事
- 建造类技能(SupplyDepot/Barracks)位置仍常被判不可放置，需要更聪明的选址

## 【两层完成 + 实时观战】LLM vs LLM arena
浏览器: http://127.0.0.1:8099/  （画面 MJPEG + 双方决策面板实时刷新）

架构:
  observation -> parse_world -> World.summary() 文本
     -> Policy.build_user_prompt -> LLM (慢, 每 24 游戏步一次)
     -> {"skill": "...", "args": {...}} -> SkillSet -> pysc2 原子动作 (快, 每步)

代码:
  arena/units.py    单位类型/名称
  arena/core.py     World 解析 + 11 个技能 (600 行)
  arena/llm.py      9 家 provider + mock（每家一个环境变量，加家=加数据）
  arena/policy.py   慢决策 + 快执行解耦
  live_viewer.py    LLM vs LLM + 画面 + 决策面板

### 关键数字（实测）
- 100 游戏步只花 5 次 LLM 请求（decide_every=24）
  → 若不这样解耦，一局要 14000 次调用，不可行
- fps 24~26（256x192 JPEG），MJPEG 推流浏览器零依赖
- mock 指挥官已能打出差异：P2 造出 SupplyDepot(supply 19/23) 并进 Barracks，
  P1 卡在 supply 15/15 一直补人口

### 已知待改进
- P1 卡人口：BuildStructure 的 10 个候选位置仍常被判 not placeable，
  需要基于 buildable 特征层选址，而不是按固定偏移
- LLM 决策未做长期规划（每次只给"下一步"，没有战略记忆）
- 还没实际连过真实模型（无 key），只有 mock 端到端验证过

### 接真实模型
export DEEPSEEK_API_KEY=sk-...      # 或 OPENAI/ANTHROPIC/MOONSHOT/DASHSCOPE/ZHIPU/GROQ
export ARENA_PROVIDER=deepseek      # 默认就是 deepseek
./run_sc2.sh .venv311/bin/python live_viewer.py Acropolis 8099
（缺 key 时页面顶部会红字提示并回退 mock，不静默）

## 【本轮修复】async 非阻塞 + 建造根因

### async：修复了一个让整个方案失效的 bug
现象（ARENA_DELAY=3 模拟慢模型）:
  修复前 fps=2.4   loop 每 4s +18    ← 和同步一样卡
  修复后 fps=19.7  loop 每 4s +77    ← 不再阻塞
  基准(0延迟) fps=24.2 loop 每 4s +110

根因: AsyncPolicy.tick() 里 `return super().tick(world)`，而 Policy.tick()
      内部会同步调用 self._decide()。后台线程在做决策，主线程每
      decide_every 步又自己同步调了一次 LLM。
修法: AsyncPolicy 覆写 _decide() 直接 return None —— 主线程永不碰网络，
      决策全部由 worker 线程完成后丢进 self._slot。

### 建造：三层叠加 bug，已解两层
1. 【已修】pysc2 默认 ensure_available_actions=True 会拒绝一切不在
   available_actions 里的动作。而 Build_* 的 avail_fn is None 且
   obs.abilities 恒为 0 → Build_* 永不出现在该列表 → 所有建造 100% 失效。
   修法: SC2Env(ensure_available_actions=False)，让游戏自己裁决。
2. 【已修】选址。buildable 特征层只表示地形合法（它在自家指挥中心头上
   也返回 1），靠它选址会选中 (27,26) 这种紧贴 5x5 基地的死点。
   修法: 用实测成功过的偏移列表打头 ((8,4)/(2,8) 等)，buildable 仅兜底。
3. 【已修】反馈闭环。技能发出建造后拿不到任何反馈，无法知道自己失败。
   修法: 发出后登记 (spot, 建筑数, 14 tick)，超时未增加就拉黑该瓦片、
   换下一个偏移。

结果: 补给站可稳定建成（supply 12/15 -> 12/23）
未成: 兵营。连续 13 个偏移均失败，3x3 的落位空间明显比 2x2 苛刻得多。

### 熔断器（通用防护）
Policy 连续 3 次拿不到动作就废弃当前技能、记入 broken_skills，并立刻触发
重新决策——避免出现"AI 呆立、矿堆到 1520 花不出去"的情况。

## 【已解决】建造链完全打通（补给站 + 兵营）

### 最终结论：LLM 只管"做什么"，脚本层管"在哪做"
这正是原始设计目标。技能名即高层意图（supply_depot / barracks / expand），
坐标、选人、选址、失败重试全部由 Skill 层负责，LLM 永远不碰坐标。

### 根因链（五层，逐层实测剥离）
1. ensure_available_actions=True 会拒绝不在 available_actions 的动作；
   而 Build_* 的 avail_fn 为 None，是否出现完全取决于游戏上报。
   → SC2Env(ensure_available_actions=False)，让游戏自己裁决。
2. Build_* 能力只在「已选中工人」时才上报。在选人之前检查它 => 永远
   False => 技能停在"等待"，连工人都没选 => 死锁。
3. Build_* 能力有前置条件：兵营在"无补给站"时不上报（345 矿也不给），
   补给站建成且矿 ~545 后才出现。所以"不可用"要区分是
   「前置/资源未满足」还是「这块地不行」——前者等待，后者换位置。
4. 实测偏移列表原先绕过了 _blocked() 检查，兵营会去重试被自己补给站
   占掉的格子，于是 9 个偏移全失败。
   → 统一 candidates() 生成器：所有候选都过 _blocked + _excluded。
5. 训练/建造都要"持续同一个技能"，中途插入别的技能会清掉选中状态。
   （Policy 里技能本就是持续的，这层是测试脚本才有的陷阱）

### 验证结果（logs/chain_v4.log）
补给站=1 (try=1)  兵营=1  VERDICT OK
最终: Barracks x1, CommandCenter x1, SCV x12, SupplyDepot x1

### 另外修掉的
- feature_units 对视野外单位返回负坐标；直接拿去发动作会
  "Argument is out of range"。World.on_screen() 统一过滤。
- policy 熔断器：连续 3 次拿不到动作就废弃该技能并重新决策。

## 【最终验证成功】完整经济+军事循环（logs 与 viewer 实测）
P1/P2 双方均达到:
  Barracks x1, CommandCenter x1, Marine x2, SCV x21, SupplyDepot x2
  supply 24/31, army 2, broken_skills = []      ← 熔断器零误伤
fps 25.7 稳定

LLM 决策日志示例（证明分层生效）:
  LLM -> train_unit {'unit': 'marine'}  # marines=1, need army

即：LLM 只说「造机枪兵」，至于选中哪个兵营、兵营在哪、
训练按钮是否可用、失败了怎么重试 —— 全部由 Skill 层承担。
坐标从不进入 LLM 的视野，这正是本项目一开始的设计目标。

## 分层职责（最终形态）
LLM 层    : 选技能名 + 参数（train_unit{unit:marine} / supply_depot / expand）
            频率 ~每 24 游戏步一次；async 后台线程执行，不阻塞游戏
Skill 层  : 选人 / 选址 / 等前置 / 自检成败 / 换位置 / 熔断
引擎层    : pysc2 573 个原子动作 + SC2 4.10 + OSMesa 渲染


## 【2026-10-06 性能分解与优化】1024×768 双 AI：约 16.8 → 25.1 fps

### 测量前提与边界
- 使用真实 SC2 4.10 + OSMesa/llvmpipe + SDL X11 窗口；不是 dummy display 或 fixture。
- Acropolis、两个 Terran Agent、两个 AsyncPolicy + mock、decide_every=24、step_mul=1。
- P1 RGB 与窗口均 1024×768，RGB minimap 256×256；feature 层仍为 64×64。
- 每轮 seed=7，预热 60 帧后测 400 帧，包含输入、策略、env.step、绘制、flip、60fps 限速；不包含启动。
- 固定基准输入（鼠标居中、不接受键盘移动/暂停），每帧两个 policy.tick 均有计数；真实 API 未调用。
- AsyncPolicy 的后台线程按墙钟轮询，因此各轮决策时点/次数不完全相同，不是逐动作确定性回放。
- 这是开局短窗口的重复性能对照，不能外推为大规模交战或真实模型对局的保证帧率。

### 可复查的实测数据
| 项目 | 原版 b | 原版 c | 优化 a | 优化 b |
|---|---:|---:|---:|---:|
| fps | 16.84 | 16.77 | 25.29 | 24.85 |
| 完整帧 ms | 59.373 | 59.642 | 39.546 | 40.244 |
| env.step ms | 55.414 | 55.130 | 37.176 | 37.009 |
| 其中 observations ms | 53.104 | 52.903 | 34.752 | 34.746 |
| 其中 engine_step ms | 1.460 | 1.442 | 1.479 | 1.472 |
| parse_world 总计 ms | 0.268 | 0.267 | 0.188 | 0.187 |
| RGB int32→uint8 ms | 0.757 | 0.756 | 0.543 | 0.553 |
| Surface 创建 ms | 1.050 | 1.041 | 0.007 | 0.007 |
| 帧耗时 P95 ms | 86.82 | 85.85 | 61.36 | 60.96 |

原始日志/结构化数据：
- logs/perf_baseline_b.log / .json，logs/perf_baseline_c.log / .json
- logs/perf_after_a.log / .json / .png，logs/perf_after_b.log / .json / .png
- 分段是嵌套计时：observations、engine_step 已包含在 env.step 内，不能重复相加。
- observations 包含引擎渲染、协议传输/解码和 pysc2 transform_obs；本轮没有把这些进一步拆成独立成本。

### 先判别后实施的改动
1. **关闭 P2 未显示的 RGB**：双方保留完整 features/raw unit 信息，P1 保留原生 RGB。
   只改这一项的探针为 24.79 fps / env.step 37.09 ms（logs/perf_probe_p2.log）。
   该轮计时完成后，RGB 校验因错误假设 uint8 报错；后续 probe_buffer 与最终复测已纠正并完成校验。
2. **pygame.image.frombuffer**：替换 transpose + surfarray.make_surface；保留 uint8 转换。
   pysc2 v3.0 的 features.py 会把 RGB uint8 扩成 int32，直接省掉转换会显示错误。
   logs/perf_probe_buffer.log：25.33 fps，Surface 约 0.0066 ms；真实帧新旧路径逐像素一致。
3. **World 每观察每玩家只解析一次**：原版实际为每帧三次（P1/P2 + P1 overlay），现为两次。
   新观察在 step 返回后解析，同时服务 overlay 和下一步技能；技能仍每步获取最新数据。
   不能每 24 步才解析：选人、建造可用性、位置、完成反馈都依赖逐步更新。
4. P1 缺失 RGB 或尺寸异常直接报错，避免黑屏假正常；未知 provider 的 mock 回退增加可见 warning。

### 实验异常保留
- logs/perf_baseline_a.log：17.45fps，但桌面键盘暂停污染、policy_calls=0，**不作为有效双 AI 基准**。
- 初始沙箱无法分配 localhost 端口，NoFreePortFoundError；实际测量经放行后运行。
- 未升级或修改 pysc2/引擎，未改变渲染分辨率，未访问用户 U 盘。

### 复现
所有命令仍从项目根目录通过 run_sc2.sh 运行；SC2 需要允许 localhost 通信。

```bash
./run_sc2.sh .venv311/bin/python profile_client.py --source logs/sc2_client_before_perf.py --output logs/perf_before_repeat.json
./run_sc2.sh .venv311/bin/python profile_client.py --output logs/perf_after_repeat.json
./run_sc2.sh .venv311/bin/python verify_client_perf.py
```

profile_client.py 对客户端原循环插入计时，不更改生产路径；源代码锚点不匹配会明确失败。
原始生产文件保存在 logs/sc2_client_before_perf.py。基准脚本输出原始策略计数和日志，
帧数不足/暂停会失败；RGB 检查另有 RGB_CHECK，不能只看到 PROFILE_RESULT 就当全部验证通过。
verify_client_perf.py 使用确定的高层技能序列验证真实引擎技能链，不代表真实 LLM 策略验证。


补充：最初 logs/perf_chain.log 的确定技能测试在等待补给站完工时持续重复建造，
双方产生多个补给站，随后兵营迟迟没有落地。本轮被主动中断，**不计入通过证据**。
测试已改成确认一个建筑开工后等待完成再进入下一意图，生产 Skill 实现未因此改动。


### 真实引擎回归通过（2026-10-07 00:00）
- logs/perf_chain_single.log：3582 步完成，含启动总耗时 165.25 秒。
- P1：Barracks×1、CommandCenter×1、Marine×1、SCV×14、SupplyDepot×1。
- P2：Barracks×1、CommandCenter×1、Marine×3、SCV×14、SupplyDepot×1。
- step_mul=1，双方每步重新解析状态，P1 RGB 1024×768；逐步断言 P2 无 rgb_screen。
- CAMERA_OK：相机 (137,151)→(139,162)，游戏步推进、RGB 实际改变。
  此项验证命令能移动相机，不是所有键盘方向/鼠标边缘的人工交互验收。
- 日志含各阶段 STAGE_OK 和最终 VERDICT_OK；截图 logs/perf_chain.png。
- 本测试直接使用确定高层意图 + 真实 SkillSet，并在建筑出现后等待完工，
  用于验证渲染接口变更后的技能可用性；不代表 AsyncPolicy/真实 LLM 的长局策略通过。
- P1 兵营阶段较慢但最终完成；候选选址、重复建造意图与长局性能仍值得单独测量，
  开局约25fps不应被描述为全局恒定帧率。


## 【2026-10-07 realtime/raw/任务队列/反射层】当前生产路径已替换

### 硬约束与接口
- 桌面 sc2_client.py 和浏览器 live_viewer.py 统一使用 arena/raw_engine.py::RawArena。
- 创建 SC2Env 显式 realtime=True、ensure_available_actions=False；没有非实时或屏幕动作回退。
- 用 send_observation_proto=True 读取真实世界坐标/单位 tag，原生 ActionRaw 交给 pysc2 v3
  transform_action 支持的 protobuf 直通分支。没有修改/升级 pysc2 或 SC2。
- 不使用 raw_units 的离散化坐标作为原生世界坐标；镜头也是原生 world-space camera_move。
- 每玩家动作是一个列表：镜头动作与 AI 单位动作同批提交，不再互相覆盖。
- P1 RGB 1024×768；P2 只请求 raw 状态。敌方只保留当前可见单位，未开启全图视野。
- SPACE 现在只是停止下发 AI 命令，游戏与既有单位命令继续；界面明确显示 GAME RUNNING。

### 最小前提实验（先于生产实现）
logs/raw_realtime_probe.log，probe_raw_realtime.py：
- 3.032 秒不调用 step/observe，游戏自行推进 70 loops / 3.125 game seconds，比例1.031。
- 两个玩家相机均离开基地，CommandCenter.is_on_screen=False。
- 双方通过单位tag训练出第13个SCV，并各完成补给站：VERDICT_OK。
- 真正证明了 camera 解耦 + realtime 自主推进，不是只改变配置字段。

### 新模块
- arena/raw_engine.py：单位元数据、可见世界快照、原生动作、放置查询、真实时间计量、终局。
- arena/tasks.py：有界持久任务队列；排队/等待/下单/执行/完成/失败状态；资源预留；
  建造tag确认、超时换点、失败上限；同类未完成意图去重，不重置施工状态。
- arena/reflex.py：每次新观察独立执行；基于可见敌军与兵力/生命值选择防守或撤退；
  优先预留这些单位，任务层不能同帧覆盖安全动作；空闲工人自动恢复采矿。
- arena/commander.py：后台规划邮箱；提交纯文本不可变快照；新计划进入队列，不替换正在执行的任务。
  默认每6游戏秒可发起请求；只容纳一个在途请求；超过30游戏秒的结果明确丢弃并记录。
- 开局队列来自明确标记的 scripted opening：2 SCV、补给站、兵营、6 Marine；
  这是为了首次 LLM 等待期间继续工作，**不是宣称开局由 LLM 预推理生成**。
- 规划器协议 tasks=[{skill,args?,count?}]；兼容单 skill JSON。非法JSON/技能/坐标/数量全部显式报错。
- 新 raw 执行器当前支持 train_worker、train_unit(marine)、supply_depot、barracks、expand、
  attack_nearest、move_out、gather、no_op；**原 screen executor 的全部技能/兵种没有完全移植**。
  expand 有基础实现但本轮没有完整实战验证；不应宣称所有旧技能均已验证。
- mock 现在是明确的 ScriptedPlanner 基线。P1/P2 分别创建 client，可用 ARENA_P2_PROVIDER 指定另一家；
  同 provider 不同个人 key 的配置尚未专门扩展，本轮不宣称该能力已完成。

### 已完成证据（区分自然引擎与人为战斗 fixture）
- logs/realtime_client.log / .png：真实桌面 12.116 wall seconds / 12.321 game seconds，
  比例1.017，21.71fps；实际图像已检查。
- logs/realtime_integration.log：12秒延迟的两个规划器，双方始终镜头离开基地，均造出Marine；
  104.517 wall seconds / 106.696 game seconds，比例1.021，22.50fps。
  随后的无调用3秒测试比例1.018。日志中的 OFFSCREEN_CHAIN_OK / NO_CALLS_REALTIME_OK 有效。
  该脚本最初还附加战斗注入测试，因下一帧尚未观察到新单位而断言失败，不能把整次运行说成全部通过。
- 战斗注入现拆为 verify_reflex_live.py，并等待引擎真实观察到测试单位再进行断言。
  logs/realtime_reflex.log：REFLEX_FIXTURE_OK，1条受伤单位撤退命令，11条防守命令，
  受伤Marine实际移动3.610世界单位，原生动作错误0；realtime=True。
  **该场景用debug注入单位/改生命值，仅验证反射执行，不算自然对局胜率**。
- logs/realtime_unit.log：14项确定性测试通过，覆盖tag动作、建造去重与确认、计划原子校验、
  资源预留、安全动作优先级、施工工人保护、重试失败、慢规划器不阻塞、过期计划丢弃、胜率分母。
  这些属于fixture单元测试，不替代真实引擎回归。

### 真实时间与胜率口径
- GameClock 以 pysc2 自身常量22.4 loops/s计算 game seconds，记录 wall seconds、比例、fps与漏观察loops。
- 超过10秒后比例偏离[0.9,1.1]标记DRIFT，桌面顶部变红；realtime flag并不保证机器永不掉速。
- evaluate_realtime.py 只跑自然对局，无debug、无作弊难度、无全图视野；只从SC2 player_result取得胜负。
- timeout/error/aborted单独统计，不能伪装成Tie或胜负；同时报告已完成局胜率和每启动局获胜比例。
- 每局保存终局世界、时间比例、策略/反射计数、动作错误、种子、配置、录像路径；后续新运行记录代码SHA256。
- 本轮默认评测只用mock脚本基线，不能冒充真实LLM胜率，更不能推断Hard/Harder性能。

### 当前运行与验证命令（都要通过唯一入口）
```bash
./run_sc2.sh .venv311/bin/python sc2_client.py Acropolis mock
./run_sc2.sh .venv311/bin/python profile_client.py --seconds 30 --output logs/realtime_profile.json
./run_sc2.sh .venv311/bin/python -m unittest -v test_realtime_runtime
./run_sc2.sh .venv311/bin/python verify_realtime.py
./run_sc2.sh .venv311/bin/python verify_reflex_live.py
./run_sc2.sh .venv311/bin/python verify_viewer_realtime.py
./run_sc2.sh .venv311/bin/python evaluate_realtime.py --games 3 --difficulty very_easy --seconds 900 --output logs/realtime_evaluation.json
```

profile_client.py 已更新为真实realtime客户端计时；旧版及旧客户端保存在 logs/*before_raw.py，
仅用于历史代码追溯。旧的16.8→25.1fps属于非实时模式，不能和本轮观察帧率直接作性能优劣对比。
verify_client_perf.py 明确提示并转到 verify_realtime.py，不会悄悄跑旧的非实时场景。


### 自然完整对局首轮（样本仅1局，不代表稳定胜率）
- logs/realtime_eval_very_easy.log / .json：Acropolis、seed=7、Terran 对 Very Easy Terran电脑、
  realtime=True、无RGB、ScriptedPlanner(delay=0)，**无debug注入/无全图视野/无作弊**。
- SC2原生结果：P1 Victory、P2 Defeat；启动1局、完成1局、获胜1局，无超时/中断。
- 327.693 wall seconds / 321.518 game seconds，比例0.9812，21.97 observations/s。
- 完成38个任务，规划错误0，任务失败0；反射动作计数：撤退33、防守1244、采矿9。
  计数表示下发次数，并非独立战斗事件数量，也不证明每个反射决策最优。
- 1次引擎动作错误 CantBuildLocationInvalid（ability321，兵营），被显式记录；
  队列换点后成功，不能写成“引擎错误0”。
- 录像路径（由引擎保存）：engine/StarCraftII/Replays/Replays/Acropolis_2026-10-06-22-21-52.SC2Replay。
- 这份首局报告由补充代码SHA256字段之前启动的评测进程生成，因此没有代码哈希；
  后续评测会记录。不能据此报告真实模型1/1胜率或Hard/Harder胜率。

### 同批落点预留补充
- 自然局出现放置错误后，代码检查发现并发建造查询看不到本批尚未发送的另一建筑。
- 已在 candidates 中排除其他未结束任务的预留位置；保留引擎拒绝后的可见重试。
- logs/placement_reservation_probe.log 是**fixture判别**：不预留时两点相距0.707，
  预留后相距13.509；它证明这一重叠风险被消除，不证明上述自然局错误只有这一种原因。
- 单元测试增加同批建造位置检查，logs/realtime_unit.log现为15项通过。
- 完整自然局是在此预留补充之前启动的；没有把fixture通过冒充补充后又打了一局。


### 收尾回归与桌面计时
- logs/realtime_integration_final.log：单独的自然引擎建造回归完整退出0，VERDICT_OK。
  双方镜头外出Marine、每次规划延迟12s，104.567 wall / 106.652 game seconds，比例1.01994；
  无调用推进测试比例1.01761。该长回归进程在同批落点预留补充前已启动。
- logs/realtime_viewer.log：包含预留补充的浏览器生产循环真实运行，104帧，JPEG 1024×768，
  状态可JSON序列化、双方队列可见、正常关闭。测试的是生产循环及输出，不是浏览器人工交互验收。
- logs/realtime_profile_final.log / .json / .png：包含最新补充的真实桌面15秒profile完整通过。
  最后一次观察时 wall=15.087s，game=15.402s，ratio=1.02084，fps=21.806，329 observations。
  每玩家Commander.tick平均0.148ms，双方raw世界解析总计0.490ms，Surface创建0.0086ms，
  flip0.430ms。env.step平均43.69ms包含实时观察等待和解析，不能与非实时纯处理成本直接比较。
  客户端最终报告wall=15.130s还包含最后一帧绘制，计时边界与最后观察快照略有差别。
- logs/realtime_profile.log 为此前被SIGINT中断的计时尝试，不作为有效性能样本。
  开始排查时日志只到显示初始化，后续堆栈已在SC2 observe等待；不能断言根因是pygame。
  pygame初始化10秒有界实验正常；随后100秒有界重跑成功。暂未证实首次延迟的根因。
- logs/realtime_unit.log：最终15项测试通过。

### 当前仍需后续验证的边界
- 未跑付费真实模型对局，未测Hard/Harder稳定胜率，未做多地图/多种子统计；首局1/1只作端到端验收。
- 基础反射使用可见敌军元数据和血量/局部兵力，尚无完整技能施放、复杂地形逃生、编队/多线策略。
- 当前任务队列提供有界并行推进、同类意图去重和完成确认；没有实现复杂战略取消/重排协议。
- 当前两个入口都强制realtime=True；旧实验文件保留历史语义，不应当作当前生产入口。


## 【2026-10-07 原生图形观察者 / GE-Proton11-7】最小实验通过

用户明确授权尝试 U 盘的 Windows SC2。原游戏、原 Lutris 前缀和配置均未修改。
- 读取 Lutris battlenet-cn.yml 确认 GE-Proton11-7-x86_64；未使用原用户前缀启动。
- `.build.info` 标称 5.0.16.97579，但 API ping 与 Graphics.txt 均返回 **5.0.15.97579**，以实际引擎为准。
- 使用项目 `native-runtime/compat-ge` 独立前缀。bwrap 将宿主根目录只读挂载，
  仅项目 native-runtime、/tmp 和设备开放需要的写入；U 盘始终只读。
- 首次尝试只读 SC2Data 链接遇到 NGDP `failed to create storage component`；
  OverlayFS 最小试验挂载返回 Invalid argument，因此复制到本地再试，没有修改源目录。
- 本地资源副本为 native-runtime/data-copy，235 个文件，初始复制后逐文件大小匹配，
  共 27,164,770,129 字节（25.30 GiB）。这不是逐文件哈希校验；现可写副本可能产生新缓存。
- 启动工作目录必须是 Support64，否则 ICU DLL 找不到；logs/native_proton_attempt2.log 有原始错误。
- 最终使用 Proton `run` 完整初始化 DXVK。`runinprefix` 的中间实验曾走 WineD3D，
  不能将其与最终 DXVK 路径混为一谈。

### 本轮实际验证
- 入口 native_observer.py，隔离启动辅助 arena/native_runtime.py；不替换现有 Linux RawArena。
- SC2 原生窗口 1600×900，GE-Proton11-7 + DXVK + RX 9070 XT。
- AcropolisLE，内置 Easy Terran 对 Medium Zerg，外加 Observer；**没有调用 LLM**。
- RequestCreateGame.realtime=True；观察者 observed_player_id=0，返回 player_id=0。
  raw 观察能同时看到 owner=1、2 的单位，原生 UI 显示小地图和所有玩家下拉选项。
- logs/native_observer_gpu.png：对局开局 0:08 的 DXVK HUD 为 **451.4 FPS**。
  这是单帧截图的即时指标，不是整局平均，也不是原双 LLM 客户端的同负载基准。
- logs/native_dxvk_verified.log / native_proton_verified.log 确认 SC2_x64.exe 与 AMD GPU，
  不是 OSMesa/llvmpipe。
- logs/native_observer_live.log / native_observer_result.json：开局倒计时后另取基准，
  30.076 秒墙钟 / 29.509 秒游戏，比例 **0.98114**，NATIVE_OBSERVER_REALTIME_OK。
  观察每5秒一次，原生画面自主绘制；该观察频率不是显示帧率。
- 中间 logs/native_observer_ge_local.log 已进入原生观战，但计时把开局倒计时包括在内，
  30秒比例0.858触发断言并退出。最终计时显式等待 game_loop>=112 后开始，保留了失败记录。
- 原生 raw 观察的 camera 返回 (0,0)，不能拿它来证明镜头没动或键鼠已验收。
  本轮没有自动化验证点击小地图/鼠标边缘控制，需人工体验。

### 复现与边界
```bash
./run_sc2.sh .venv311/bin/python native_observer.py --seconds 600
```
窗口试跑上限10分钟；完整电脑对局提前结束也会退出。日志由外层重定向保存。
目前完成的是原生观察者技术验证。**尚未把项目 Commander/任务队列/LLM 接入这份
Windows 引擎，也没有验证 4.10 Linux 与该 Windows 版本跨版本联机。**
下一步应使用一致的引擎版本验证 API 玩家 + 独立原生观察者，再迁移项目 AI；不要声称 LLM 原生对战已经完成。


## 【2026-10-07 双原生玩家窗口接入真实 LLM】当前入口

### 先判别，再接入
- logs/native_three_clients_rejected.log（原型 logs/probe_native_three_clients.py）：
  两个Participant+Observer被引擎拒绝：`Only 1v1 is supported when using multiple agents`。
  内置电脑+Observer成功不能推导双LLM+第三观察者成功。用户明确选择两个原生玩家窗口。
- logs/native_two_players_probe.log：两个同版本API玩家1v1成功；镜头移到(100,100)，双方
  SCV从8增至9；初始观察无可见远处敌军。35.214墙钟/35.938游戏=1.02054×。
- logs/native_llm_preflight.log：真实DeepSeek/deepseek-chat请求1.239秒，合法任务JSON。

### 接线与配置
- native_llm.py → arena/native_engine.py::NativeArena + 原Commander/TaskQueue/ReflexNet/LLMClient。
- Windows5.0.15.97579 + GE-Proton11-7；两个GPU玩家窗口，正常战争迷雾；无第三观察者。
- realtime=True；不发送RequestStep、不传输RGB；默认控制10Hz，日志control_hz与显示FPS分开。
- 复用RawArena世界解析和放置查询；生产NativeArena不发送镜头动作；raw_affects_selection=False。
- DebugDraw仅绘制阵营/模型/规划/错误/队列文字；不修改game_state、单位、资源或视野。
  `--no-overlay`关闭文字。未升级pysc2，旧Linux/pygame入口仍保留。
- arena/player_config.py支持ARENA_P1_PROVIDER、ARENA_P2_PROVIDER、ARENA_P1_API_KEY、
  ARENA_P2_API_KEY、ARENA_P1_MODEL、ARENA_P2_MODEL。每方client与状态独立。
- 未配置每方key时沿用原provider key，打印来源变量名；显式空key报错，不静默回退。
  本轮双方共用DEEPSEEK_API_KEY；不同key隔离只做了fixture测试，未真实调用两把key。
- 真实请求timeout20秒、无隐藏重试；失败明确进入planner-error；现有队列和反射继续。
- run_sc2.sh保留新增每方变量的环境覆盖；真实wrapper覆盖断言WRAPPER_OVERRIDE_OK。
- 报告保存原始模型回复、玩家状态、map/seed、引擎版本、计时/错误；结束保存SC2Replay。
  中断/超时/错误不算胜负。关闭任一窗口将结束这次连接，连接异常会明确记录。

### 真实模型问题与修复
- logs/native_deepseek_empty_plan_attempt.log/.json/.SC2Replay：模型返回tasks=[]表示不新增任务，
  旧队列只接受1..16项导致planner-error；主动停止本次实验，不计通过。
- 已定义tasks=[]为显式等待：日志`explicit wait; keeping N active tasks`，不清空现有任务。
  非list/未知技能/非法数量仍原子拒绝；提示词已明确空任务语义。实际模型原回复回放通过。
- logs/native_all_unit.log：22项fixture测试通过，包括空计划保留任务、非法等待、独立key、
  空key禁止回退、真实client无隐藏重试；不代替引擎实测。

### 实测证据
- logs/native_mock_chain.log/.json/.SC2Replay：双mock，每次规划12秒延迟，双方完成Depot、
  Barracks、Marine；125.063墙钟/127.545游戏=1.01984×，动作错误0。
- logs/native_mock_windows.png：两个1600×900原生窗口，HUD瞬时P1=264.2FPS、P2=182.5FPS。
  是双mock回归截图，非整局平均，不能冒充真实LLM局FPS或同负载渲染跑分。
- logs/native_deepseek_verified.log/.json为真实模型固定checkpoint；JSON含源码SHA256。
  实时文件logs/native_deepseek_live.*后续仍会更新。双方基础建造链已NATIVE_CHAIN_OK。
- 非仅脚本开局：真实模型新建P1 task#5训练3SCV、P2 task#5训练2SCV，均有完成记录。
  对战已触发原反射层的防守/撤退命令；计数是命令次数，不代表撤退都成功或策略优秀。
- 后续出现非训练任务填了count的模型计划，被明确拒绝：`non-training tasks must have count=1`。
  不能声称全部模型输出都合法；后续合法计划继续执行，拒绝计数在原生窗口可见。
- 本次checkpoint累计6次战斗动作拒绝（NotSupported/Error，ability3674攻击/3794移动），
  均在action-response/action-error日志可见，游戏和后续命令继续。尚未证明具体原因，不能宣称
  都只是目标死亡/视野变化，也不能写成已修复；队列失败计数在该checkpoint仍为0。
- 真实模型运行时桌面锁屏，logs/native_deepseek_windows*_locked.png不算游戏截图或FPS证据。
  后台游戏/模型仍推进。本轮未声称稳定胜率、不同provider对战或独立全图观战通过。

Checkpoint（进行中，不是完整胜负统计）：
{
  "timing": {
    "wall_s": 300.4063491090001,
    "game_s": 306.5625,
    "game_wall_ratio": 1.0204927456069386,
    "speed_check": "ok",
    "observations": 3003,
    "skipped_observation_loops": 3865,
    "control_hz": 9.996459824856714,
    "display_fps": "native DXVK HUD; not API polling rate"
  },
  "plans": [
    49,
    51
  ],
  "calls": [
    51,
    51
  ],
  "model_errors": [
    2,
    0
  ],
  "failed_tasks": [
    0,
    0
  ],
  "engine_errors": 6
}

### 启动
```bash
./run_sc2.sh .venv311/bin/python native_llm.py --seconds 600
./run_sc2.sh .venv311/bin/python native_llm.py --p1 mock --p2 mock --delay 12 --seconds 200 --verify-chain
```
默认10分钟墙钟上限；双方provider/model/key可通过以上每方变量在.env设置。
支持技能/兵种仍以当前Terran执行器为准；两个窗口都是玩家视角，有各自迷雾。


## 【2026-10-07 战斗动作拒绝判别】错误码触发条件已复现，历史逐条归因仍有边界

- 用户追问此前8次拒绝原因；8次是运行中的计数。最终600秒墙钟日志共20次：
  Error 12次，NotSupported 8次。此前所指的前8次为Error 5次、NotSupported 3次。
- 模型计划拒绝属于我们自己的TaskQueue协议校验：非训练任务count必须1，
  attack_nearest不接受unit参数。实际回复有supply_depot count=2、attack_nearest
  args.unit=Marine/count=18。不是SC2禁止建两个补给站，也不是模型服务拒绝请求。
- 新增probe_native_action_errors.py：原生5.0.15.97579、GE-Proton11-7、realtime=True，
  不调用LLM；debug创建/杀死单位，只用于验证错误码，不计自然对局/胜率。
- logs/native_action_error_probe.log/.json与native_action_error_fog_probe.log/.json实测：
  1. 存活Marine攻击可见存活目标 → Success；正常移动 → Success。
  2. 攻击已死亡目标/不存在的tag → Error。
  3. 已死亡己方单位接收移动/攻击 → Error。
  4. 攻击存活但离开视野的目标 → NotSupported。目标由P2观察独立确认存活（145HP）。
     同一目标可见时攻击Success；离开视野后按tag攻击NotSupported。
  5. 向目标位置发attack-move → Success（fixture使用P2确认位置，仅作判别；
     不能在生产中偷读敌方位置。实际追击只能使用己方最后看见的位置）。
  6. 混合三条动作返回Success/Error/Success，当前逐动作匹配在该fixture成立。
- 因此实时局中观察到单位和命令到达引擎之间的死亡/失去视野，是可实测触发的原因。
  当前控制约10Hz，期间引擎持续推进。不能通过切换realtime=False掩盖这一竞态。
- 历史日志只存执行单位tag/ability/结果，未存攻击目标tag和精确发送时刻状态；
  所以不能声称前8次已逐条证明分别是哪一个目标死亡或离开视野，也未宣称已修复。
- 附加录像探查logs/native_error_tracker_diagnostic.json使用显式标记的97563 tracker
  decoder解析97579录像（本地缺少精确版本），并尝试API tag减2**32的对应关系；
  它仅提供线索，未作精确版本兼容性/映射验证，不作为历史逐条根因的最终证据。
- 本轮没有改变生产控制策略；下一步修复应先补命令目标、观察/发送game_loop、
  己方单位及目标前后状态的关联日志，再实测处理过期目标；不能静默吞掉失败。


## 【2026-10-07 结构化 LLM 反馈闭环】已接入，含真实 DeepSeek 发送验证

### 先判别
- logs/feedback_before_probe.log：改动前捕获Commander实际传给client.chat的请求，
  证明引擎错误未传入，计划错误被12条普通日志挤出recent_events；不是只靠读代码推断。

### 当前实现
- arena/feedback.py：每方独立JSONL反馈记录。事件编号、请求编号、请求排队/开始/完成、
  原始回复、反馈交付记录均持久保存；日志位置在Commander.stats.feedback.journal。
- feedback.events独立于recent_events；最多每批24条、约24000字符（单条超出则完整单独发），
  超出的事件保留，pending_total/deferred_count可见，不截断丢失事件。
- 只有client.chat成功返回，才确认本次请求携带的事件已交付；网络失败保留，下一次正常
  规划请求再次携带。请求进行期间新增的反馈不会被旧请求一起清除。没有额外隐藏网络重试。
  交付仅表示API成功返回，不证明模型理解/遵循；JSON错误另生成model_response_invalid。
- 覆盖模型请求失败、非法JSON、整个计划拒绝、过期计划、任务入队/去重/等待/完成/失败、
  引擎动作拒绝。非法计划一次列出所有校验问题，含tasks索引、字段、实际值、允许值/限制；
  计划仍原子拒绝。已结束任务的反馈独立保存，不因离开active_tasks而丢失。
- RawArena/NativeArena附带失败动作的ability、执行单位、目标tag/位置、任务/反射来源、
  前后game_loop、发送墙钟时间与本方观察；仅使用该玩家视野，不偷读对方数据。
  原生同步结果可准确对应批次动作；观察异步错误只按unit/ability匹配候选，标明关联未经证明。
  多候选/缺上下文显式显示。action级结果不冒充每单位独立执行结果。
- 引擎未解释原因时cause=unknown；观察不到目标不能擅自改写为“已经死亡”。
  已死亡标记只依据同份本方raw dead_units。只说明脚本将继续处理，不能把日志记录当已恢复。
- native_llm.py在每次step后立即收集反馈（包括最后一帧）；原生文字显示engine rejects及
  feedback pending/delivered；最终报告保留每方反馈路径/计数。原有任务协议和realtime=True保留。
- 关闭时记录未交付数量；未交付内容留在JSONL。尚未实现进程重启后自动恢复上一局待发队列。

### 验证与边界
- logs/feedback_all_tests.log：32项通过（原22项+10项反馈测试）。包括分页全量、不被日志挤出、
  网络失败保留、新事件不被旧请求确认、完整校验问题、JSON/过期计划、失败任务、动作候选、
  本方可见性、真实请求payload与保存的审计内容一致。属于fixture，不冒充自然战斗实测。
- verify_feedback_live.py / logs/feedback_live.log / logs/feedback_live_result.json：
  Windows5.0.15.97579 + GE-Proton11-7、realtime=True、debug单位受控场景。
  原生引擎实际返回NotSupported（存活目标进迷雾）和Error（已死亡己方单位移动），
  经生产NativeArena/Commander反馈路径，**一次真实DeepSeek/deepseek-chat请求携带两条反馈**，
  API约0.96秒返回合法计划，反馈交付计数2。完整请求与原始回复在对应JSONL。
  模型回复reason提及dead Marine/stale target是模型自己的表述，不能当新增根因证据。
- 此验证只运行约3.63秒墙钟，游戏/墙钟1.021，speed_check仍为warming_up；
  不据此宣称长时间稳定速度，不是胜率/自然对局或显示FPS测试。
- 验证取得两条错误的交付证明后退出。新接受计划产生的8条入队/状态反馈尚未发送，
  最终pending=8，已如实保存在报告和日志中，不声称退出时队列为空。
- 真实实验之后仅补充association/result_scope说明，32项测试再次通过；未重跑真实场景。
- 本轮新增的是反馈，不是战斗竞态修复，也没有宣称所有动作拒绝已消除。


## 【2026-10-07 用户观看 DeepSeek vs 内置 Medium Bot】已启动

- 先跑probe_native_bot.py：logs/native_bot_probe.log/.json（结果文件native_bot_probe_result.json）。
  原生单API玩家+内置Medium Terran创建/加入成功，SCV 8→9；30.076墙钟/30.313游戏，比例1.00787。
  这是无LLM最小判别实验；日志沿用了部分旧Observer字段名，但player_id=1，明确是玩家视角。
- NativeArena新增difficulty参数，可启动一个API玩家对内置Terran电脑；默认仍支持双LLM。
  native_llm.py新增--bot，禁止与--p2同时指定。双方模式复用同一个任务/反射/反馈路径。
- logs/native_bot_regression.log：原32项测试通过；这些并非双原生引擎模式重跑。
- 用户要求的一场已运行：
  ./run_sc2.sh .venv311/bin/python native_llm.py --p1 deepseek --bot medium --seconds 1800 --output logs/native_deepseek_vs_medium.json
- logs/native_deepseek_vs_medium.log/.json记录进行中状态，完成/中断后自动保存同名.SC2Replay。
  真实DeepSeek已返回并接受计划；开局仍有明确标记的scripted opening，不假称全部由模型生成。
- logs/native_deepseek_vs_medium.png已检查：桌面左上显示原生SC2窗口，DeepSeek/Medium Bot状态可见。
  当前是DeepSeek玩家视角/正常迷雾，可人工移动镜头；没有独立全图观察者。
- realtime=True，单局最多30分钟墙钟，胜负由SC2原生结果决定；启动时尚无终局结果，不计胜率。


## 【2026-10-07 当前对局 LLM 正文面板】已打开

- 用户只要求正文。新增watch_llm.py，读取当前对局JSONL的request_finished.response，
  不展示提示词、反馈输入或独立思维链；正文用textContent呈现，避免把模型输出作为HTML执行。
- 只监听127.0.0.1:8766；每秒检查新回复，按请求编号去重、显示历史正文与游戏时间/请求耗时。
  当前模型不是流式调用，因此一条回复完成后整体出现，不声称逐token显示。
- 入口：./run_sc2.sh .venv311/bin/python watch_llm.py --report logs/native_deepseek_vs_medium.json --port 8766
- logs/llm_output_viewer.log为服务记录。实际读取当前对局正文成功，并用Codex内置浏览器打开
  http://127.0.0.1:8766；已从52条自动刷新到53条，浏览器截图已检查，标签标记为保留。
- 未重启游戏，未改变DeepSeek调用。面板为独立只读进程；读入失败/不完整日志尾行会明确提示。


## 【2026-10-07 用户要求停止当前局并关闭战斗反射】已完成

- 对当前native_llm.py进程51850发送SIGTERM正常退出；结果标记interrupted，不计输赢。
  日志/报告logs/native_deepseek_vs_medium.log/.json，录像同名.SC2Replay（30307字节）。
  native-match.lock已释放，未启动新局。原生进程由既有关闭路径清理。
- 这局旧策略累计撤退705、防守229、采矿15条命令；这是下发次数，不是独立战斗事件。
- logs/disable_reflex_before.log先做判别：受伤Marine遇敌时旧反射发MOVE并预留该单位，
  同帧任务队列attack_nearest无可用军队，证明它会抢占进攻控制。
- ReflexNet/Commander默认关闭combat_reflex：军队不再进入脚本自动撤退/防守逻辑，
  不被反射层预留。保留空闲工人采矿、施工工人保护和任务队列。
  LLM下发attack_nearest/move_out后交给SC2原生战斗行为；不承诺所有战术行为都最优。
- 模型system/control_policy、结构化反馈、stats及原生UI都明确Combat reflex OFF，
  避免模型继续假设脚本会自动撤退/防守。攻击任务完成说明改为“已下发attack-move”，
  不再谎称局部反射继续接管。采矿动作来源单独标识worker_gather。
- 旧反射算法仅保留为显式combat_enabled=True/combat_reflex=True调用；历史受控反射测试
  明确启用它。所有正常入口默认False，未新增会悄悄开启的环境变量或回退。
- logs/disable_reflex_tests.log：35项fixture测试通过，含受伤遇敌时无脚本撤退且进攻任务
  可执行、关闭状态不受遗留撤退计时影响、采矿/施工保护、真实client调用边界传入OFF信息。
  本轮按用户要求不开新局，所以没有把这些fixture称为关闭后自然对局实测。


## 【2026-10-07 刚才观战卡顿的只读排查】发现显示缓冲重复重建及实时时间漂移

- 未重启游戏、未修改渲染设置。先读本局native_deepseek_vs_medium.json、反馈JSONL及DXVK日志。
- logs/native_stutter_analysis.json：前60秒累计游戏/墙钟约0.981；墙钟90.68秒时已DRIFT(0.898)。
  最后采样505.66墙钟/413.39游戏=0.8175；最慢约10秒采样区间仅推进0.587倍游戏时间。
  控制观察频率从约10Hz降至累计8.47Hz；这不是显示FPS，缺少逐帧frame-time，不能混用。
- 原native-runtime/logs/SC2_x64_d3d9.log已复制保存为logs/native_deepseek_vs_medium_d3d9.log，
  避免后续启动覆盖。SHA256=b2f61d87988f1de4b95303feee1da35102ab576b25025b3bd5ce65f13e8215d7。
  其修改时间07:47:14，最终对局报告07:47:21，之后未再启动SC2，属于刚才停止的那局。
- DXVK日志3584次“Got VK_SUBOPTIMAL_KHR, recreating swapchain”；缓冲尺寸依次1600x900、
  3631x2042，之后3578x2012重复3585条Buffer size；不是持续多种窗口尺寸变化的正常记录。
  最终像素量约为初始5倍。日志确认RX9070XT/Vulkan/DXVK，未回退OSMesa。
- Vulkan官方说明SUBOPTIMAL表示交换链与显示表面属性不再完全匹配，但仍可显示：
  https://docs.vulkan.org/refpages/latest/refpages/source/VK_KHR_swapchain.html
- 首要嫌疑是窗口放大后Proton/DXVK/窗口系统的交换链适配问题及反复重建；
  尚无固定窗口与放大窗口的A/B、逐帧耗时/GPU占用，不能宣布唯一根因已证实或已经修好。
- 本局70次LLM请求耗时0.562..1.656秒，中位1.035秒，运行在后台线程；
  没有把这约1秒响应延迟当作游戏主循环阻塞的证据。
- 先前汇报约0.98x仅对应开局50秒，不能代表后续整局；DRIFT在日志/UI出现，先前未及时指出。
- 下一步判别应固定显示尺寸/焦点，比较交换链重建计数和frame-time，同时保持realtime=True；
  用户刚要求关游戏，本轮只排查现有证据，没有擅自再开局。


## 【2026-10-07 LLM 自定义组合技能】真实 DeepSeek 定义、原生引擎执行、分页结果反馈均通过

### 先判别底层能力，再接实现
- probe_skill_primitives.py / logs/skill_primitives.log/.json：Windows 5.0.15.97579、GE-Proton11-7，
  realtime=True、headlessNoRender，debug 注入 Marine/Tank 与升级，仅用于受控验证。
- 研究前 Marine 没有兴奋剂能力，debug upgrade 后出现 ability380，发通用3675 Success，观察buff27。
- Tank MOVE3794 后排队 Siege388 均 Success；变为类型32，距目标2.471，故移动到位使用3单位容差。
- 原生 SCV Build319 后 queue_command=True 的 Gather3666 均 Success；补给站progress=1之后，
  SCV 当前第一条命令才变为295并指向该矿。排在建造后面的采矿命令不能提前算完成。
- 判别轮30.840秒墙钟/30.268秒游戏=0.98145×。无窗口，不是显示FPS或胜率测试。

### 实现与协议
- arena/composite_skills.py：LLM可定义任意合法名称的有限步骤技能，再在tasks中调用；
  内置原语build/gather/move/attack_move/stim/siege/unsiege/wait(enemy_near)，没有执行任意Python。
- 定义由模型提供，逐玩家、本局独立存储；每次请求传skill_library。定义保存在反馈JSONL，不自动跨局加载。
- 单次最多8个定义、每方最多24个定义、每个最多12步；定义最长300游戏秒、步骤最长180游戏秒。
  原子校验定义和调用后才入库/入队；未知字段、非法队列搭配、覆盖内置名、改写运行中定义都明确拒绝。
- select选定同一批单位并保留占用；建造选址与资源判断由脚本负责。原有宏命令/空闲采矿不抢占。
  同帧组合建造与旧建造共用矿物预算和位置预留，不能把同一份矿发给两个建筑。
- build→gather和move→siege可用queue:true原生Shift队列；其他步骤等观察完成再发下一步。
- 完成判据：新建筑progress=1；SCV当前采矿命令目标正确；移动距目标<=3；Marine有buff27；
  坦克类型正确且变形命令388/390已结束；attack_move匹配目标位置的攻击命令或已到目标附近。
  攻击任务完成不代表击杀或胜利，也不把旧目的地的攻击命令当作新指令成功。
- 每步开始/完成、单位占用、定义、失败均反馈；完成附实际buff/类型/坐标/orders/目标证据。
  引擎拒绝反馈新增queue_command及composite_skill的task_id/step/request_id上下文。
- 缺单位/科技/矿物/可见目标显式等待，有游戏时间上限；失去选定单位、动作拒绝、超时显式失败。
  已下单技能失败会请求STOP清原生剩余队列；日志只宣称请求取消，不能把未观察确认说成取消成功。
- 生产Commander默认接入，无需新开关；战斗反射仍OFF，未恢复自动撤退/防守。LLM正文上限800→2400 tokens。
- NativeArena新增可选render=False用于本轮无窗口验证，正常生产入口仍默认原生窗口。

### 验证证据与失败记录
- logs/composite_skills_tests.log：48项fixture通过（原35项+13项组合技能测试）。覆盖原子拒绝、
  定义复用/玩家隔离、原生queue标志、建造后当前采矿、到位与架起分别确认、缺科技等待、
  buff确认后攻击、单位预留、防止旧攻击误判、拒绝/死亡/超时、Commander实际请求携带技能库。
- 首次受控脚本把interval设99999但last_request仍为-999，导致首次请求没有触发，100秒超时。
  logs/composite_skills_live_initial_interval.log/.json保留失败证据，不能算真实模型验证。
  Commander首次请求现在使用负无穷哨兵，新增实际client边界测试包含超长interval。
- 第一轮真实成功：logs/composite_skills_live_first_success.log/.json/.SC2Replay，3个技能完成、0拒绝；
  33.243墙钟/32.634游戏=0.98168×。退出时9条待反馈，不能声称全部交付。
  复核发现坦克类型变化时仍可能带388变形命令，最终判定收紧到变形命令也结束。
- 最终verify_composite_skills_live.py / logs/composite_skills_live.log/.json/.SC2Replay：
  使用真实DeepSeek/deepseek-chat，模型在约1.291秒生成并调用：
  depot_then_mine（建补给站→原生排队采矿）、marine_stim_rally（兴奋剂→攻击移动到集结点）、
  tank_siege_rally（移动到集结点→原生排队架起）。这些名字和步骤来自实际回复，不是预装技能。
- 最终3/3技能、6/6步骤完成，模型错误0、引擎拒绝0；33.973游戏秒/34.640墙钟秒=0.980751×；
  控制观察10.017Hz，未测显示FPS。无窗口报告沿用display_fps提示文字，该字段不是测量值。
- 共3次真实模型调用（约1.291/1.266/1.308秒）；生产反馈路径分两次送完全部工作流反馈，
  6个step_completed事件17/21/26/28/30/32都出现在feedback_delivered中。
  最终delivered34、pending1；唯一未交付事件是最后一次回复产生的plan_wait，不是技能结果。
- logs/composite_skills_evidence.json汇总实际6步证据、3次正文与交付核对；原始逐请求JSONL：
  logs/composite_skills_feedback/p1-v5ckckbd.jsonl。所有验证进程已退出，原观战局未重开。

### 能力边界
- 本轮注入了Marine/Tank及升级，不是自然对局、完整科技链或胜率证明；未修复上一轮窗口卡顿。
- 坦克生产与兴奋剂研究尚未自动化；自然局没有相应前提时技能会明确等待/超时。
- enemy_near条件、缺科技/单位死亡等异常分支主要由fixture覆盖；不宣称所有原语/组合均做过自然实战。
- 新技能可组合当前原语；新兵种、新API能力仍需实现原语，任意代码、无限循环和递归不在本轮协议内。


## 【2026-10-07 主动投降技能】原生后端已接入

### 先行判别
- probe_surrender.py / logs/surrender_probe.log/.json，原生5.0.15.97579、GE-Proton11-7、realtime=True、无窗口，无debug单位和模型调用。
- 对内置Bot时P1.leave返回正常响应，客户端状态in_game→launched；随后observe/save_replay均被PySC2状态检查拒绝。
- 双API时P1.leave后，P2观察到原生结果P1 Defeat / P2 Victory，并保存10115字节终局录像。
- 因而单API客户端必须退出前保存录像，不能退出后从该客户端继续轮询或宣称拿到引擎终局结果。

### 实现
- arena/match_control.py新增投降计划校验与模型说明。格式：
  {"tasks":[{"skill":"surrender","args":{"reason":"根据己方观察给出的认输理由"}}]}。
- reason非空、<=500字符，count省略或1；禁止混合其他任务/定义技能/指定对手/嵌入组合步骤。surrender成为保留名称，不能用自定义技能覆盖。
- Commander仅在支持后端公布surrender_available；模型明确调用后，记录请求编号/理由，暂停单位动作与新规划。
  不设自动投降阈值，不恢复用户已关闭的战斗反射，不把模型API错误当作败局。
- NativeArena在step发动作前处理第一份已接受的终局请求；先保存录像再leave，且不对已离开的客户端observe/save。
- 双API：用另一玩家的观察核对正式胜负，保存其终局录像，result_source=engine_player_result。
- 单API对Bot：退出响应确认后，按主动认输比赛规则记本方Defeat、对方Victory，
  result_source=acknowledged_concession_rule，engine_results=null，replay_scope=before_leave。日志明确此边界。
- 退出前录像保存失败则不leave、反馈并继续；leave响应异常/不确定时明确报错，不编造比赛结果或自动重试。
  对手终局录像失败时保留退出前副本并写警告；观察不到引擎结果时来源明确标为认输规则，不静默当引擎结果。
  如果观察到的原生结果与认输预期矛盾，报告result_mismatch，不能覆盖成预期胜负。
- native_llm.py最终结果保存surrender记录和录像范围。成功结束时原活跃任务标为cancelled；终局事件不计入engine action errors。
- 旧Linux RawArena未接入，模型输入和非法调用均明确说明不可用。没有修改/升级本地pysc2。

### 测试与证据
- logs/surrender_tests.log：62项fixture通过（原48项+14项投降测试）。包括格式/原子拒绝、禁覆盖、禁指定他方、
  先录像后退出、P2索引、未知退出不造结果、保存失败继续、终局录像失败可见、引擎结果矛盾、后端能力声明、
  真实事件结构进入Commander，以及native_llm入口写出胜负/原因/录像/任务取消的fixture验证。
- 初次真实集成发现surrender_completed的game_loop与FeedbackJournal.add参数冲突：引擎已退出，反馈写入TypeError。
  原始失败logs/surrender_live_feedback_collision.log/.json。字段改为requested_game_loop；fixture改用真实后端生成的事件结构防止遗漏。
- verify_surrender_live.py / logs/surrender_live.log/.json最终两个受控场景通过：
  1. 真实DeepSeek/deepseek-chat按明确工程验证指令返回surrender，并注明controlled test。经生产Commander→NativeArena退出确认，
     按主动认输规则P1负/P2胜；保存logs/surrender_real_deepseek_vs_bot.SC2Replay，9290字节，退出前录像。
  2. 双API中P2由明确脚本测试规划器主动认输；另一API客户端返回正式P1 Victory / P2 Defeat，
     保存logs/surrender_scripted_p2_vs_api.SC2Replay，10136字节，终局录像。没有把该脚本说成真实LLM。
- 两种场景模型/控制错误均0，完成事件在各方JSONL中保存；比赛结束后不额外调用模型，终局待交付反馈仍标为pending。
  JSONL位置列于logs/surrender_live.json的commanders.feedback.journal。
- 录像由原生接口返回，检查了文件存在、大小与MPQ头，本轮未重新播放录像验证画面。
- 单API测试1.507秒墙钟，最后可用退出前观察推进0.670游戏秒，ratio0.444、warming_up；
  双API0.958秒墙钟/0.982游戏秒，ratio1.025、warming_up。这是短时退出测试，不是实时速度或显示FPS评测。
- 只证明真实模型可使用接口与正常结束链路；没有验证LLM自主判断“何时值得投降”的策略质量，不能算自然对局胜率。
- 所有测试已结束、native-match.lock释放；原正文服务仍关闭，未启动新的观战局。


## 【2026-10-07 Python 战术运行接口】已实现，受控原生验证通过

用户要求：自由 Python 战术脚本，但 LLM 必须持续做决策；脚本负责分配小队的局部操作和撤退。本轮完成运行接口，未接入 OpenCode 会话/赛前准备计时/模型自动写代码。

### 先做的判别实验

`probe_python_skill_worker.py`，原始 `logs/python_skill_worker_probe.log`、`logs/python_skill_worker_probe.json`：只挂载解释器/系统库、清空环境、独立网络/PID空间，stdlib运算9.0、假密钥不可见、项目文件不可见；启动0.0158墙钟秒；死循环于0.1513秒终止。这只证明所测隔离前提，不是完整游戏验收。

### 实现与验证边界

- `arena/python_skills.py` 管目录/冻结源码、异步进程、局部上下文、动作权限与时效、任务生命周期；`arena/python_worker.py` 加载单文件、执行tick、返回异常traceback。
- `TaskQueue` 接入 Python 任务、单位锁定、显式取消；`Commander` 给真实模型完整目录/参数、控制策略和失败反馈，关闭时清理技能进程。原有模型异步调用和每6游戏秒规划策略不变。
- `native_llm.py` 新增各方 `--p1-skills/--p2-skills`。Python库在启动引擎前读入，P1/P2独立。未给目录就不启用；不会静默回退。
- tick只允许给本任务单位下本清单授权命令，不能调用macro或原始引擎；目标限指挥官指定目标或局部20范围坐标/当前可见目标。程序拥有state，不能借ctx修改主机mission。
- 异常/超时/崩溃/输出过量/越权/过期结果/丢单位/动作拒绝均明确失败并请求Stop。终态的Stop只记录已请求，未声称引擎确认。脚本done明确标记objective未验证。
- 示例 `cautious_push` 是项目人工提供脚本，价格为引擎矿+气元数据，未包含克制/安全路径/科技成本模型，不代表能获胜。

### 回归及失败修复

`logs/python_skill_tests.log`：78项通过，含原有62项和16项Python新增测试。新增覆盖实际bwrap执行、stdlib/环境/目录隔离、traceback、tick/导入死循环、输出洪泛、异常退出stderr、取消时进程退出、计划原子性、源码冻结、单位权限/目标/能力检查、过期响应、玩家实例状态隔离、取消重派、Commander目录可见。首轮暴露“stderr在结果之后未完全排空可能漏过64KiB限制”，已修复并保留对应回归用例；这些fixture本身不是SC2游戏证明。

### 真实模型 + 原生引擎证据

- 脚本 `verify_python_skills_live.py`；`logs/python_skills_live.log`、`.json`、`.SC2Replay`。
- 第一次布置场景用了本机协议不存在的 `debug.invulnerable`，在技能启动前失败；原始日志/报告保存为 `logs/python_skills_live_setup_failed.log/.json`。查询本机 `DebugGameState` 后改为实际的 `debug.god`，没有升级pysc2/协议。
- 成功场景：引擎5.0.15.97579，realtime=True，headless，正常迷雾；显式注入3个本方Marine、12个敌方Marine、god调试模式。真实DeepSeek-chat调用预载cautious_push（不是模型写出的源码）。
- 第1次真实计划0.622s，调用3人小队推进。距基地平均17.209后注入优势敌军；首次撤退观察17.575，后续14.060，实际位置接近基地超过3单位。此次撤退发生于第2次规划人为增加的5s延迟期间；该次总响应5.920s。脚本共完成76次tick，发出3组行为动作，未每帧刷同一命令。
- 第3次真实计划0.658s，看见retreat事件后调用cancel_python_task，任务取消、worker关闭并请求Stop。
- 场景游戏时间13.258929s / 墙钟13.510391s（约0.981x）；引擎整体计时13.348214s/13.624259s=0.979739x，speed_check=ok。API观测/控制10.0556Hz，**不能当显示FPS**。0次引擎动作拒绝。
- 完整模型请求/正文、反馈ACK、动作、观察、哈希见 `logs/python_skills_feedback/p1-1xvbwsma.jsonl`。
- 这证明真实模型调用→隔离脚本→原生移动/撤退→模型取消通路和实时推进；没有验证自然局胜率、脚本自动生成、所有动作的真实战斗表现或OpenCode双模型准备流程。

复测：先归档同名输出，再通过 `./run_sc2.sh .venv311/bin/python -m unittest test_realtime_runtime test_feedback test_native_config test_composite_skills test_surrender test_python_skills` 和 `./run_sc2.sh .venv311/bin/python -u verify_python_skills_live.py`。后者会调用真实付费模型并启动不显示窗口的原生引擎。


## 【2026-10-07 OpenCode Go 双模型启动入口】实现完成，DeepSeek地区设置仍阻塞真实双模型开赛

用户选择 DeepSeek V4.1 Flash 与 GLM Flash。本机只登录opencode-go，其目录对应 `deepseek-v4.1-flash` 与 `glm-5.3-flash`；以本机实际目录/已验证身份为准，没有替换为旧deepseek-chat或glm-4-flash。

### 先验证前提与真实阻碍

- 本机OpenCode1.18.35。直接 `opencode models` 因HOME日志不可写失败；改用项目内独立XDG数据/缓存/配置/状态目录，没有修改HOME配置。只复制已有Go登录，私有目录700/auth600，测试后删除auth副本。
- 官方Server文档 https://opencode.ai/docs/server/ ，实际schema读取本机 `/doc`：支持 `/session`、`/session/:id/message`、`/session/status`、`/session/:id/abort`，消息模型参数为providerID/modelID。会话全工具deny。
- `logs/opencode_model_probe.json`：GLM-5.3-Flash用真实Go返回合法tasks=[]；DeepSeek返回HTTP400，上游原文“This Go model requires Global regions. Select Global in your workspace's Privacy settings to use it.”。最早探测OpenCode自行重试约70秒，新适配器监测retry立即abort并保留原错误。
- 用户已明确选择继续Go并自行修改Global，不授权/不执行换官方API key方案。`logs/opencode_check.log`及最后`logs/opencode_check_after_setup.log`仍被同一地区设置拦住，因此没有真实DeepSeek准备或双模型对局。

### 新增实现

- `arena/opencode_bridge.py`：私有本机OpenCode服务、两方目录和会话、正文提取、实际模型身份/完成状态核验、超时/上游retry显式中止。密钥不写报告，临时登录副本退出删除。服务采用已有OpenCode认证，不直接对Go网关编造请求。
- `arena/opencode_prepare.py`：给模型公共Python合同，让它返回实际源码文件与策略；路径/大小/manifest严格检查，真实隔离worker运行quiet/enemy_near/low_health三份合成观察，错误送回同会话修正。最多4轮和每方总准备期限，默认300秒，可提前通过。
- `opencode_match.py`：模型连通预检→双方独立准备→两方全部通过后进入 `native_llm.main`；默认不开确定性OPENING，模型在游戏中持续发任务。任何前置失败不启动引擎、不静默切模型。
- `native_llm.py`允许明确注入两个OpenCode客户端和Python目录，旧入口make_player默认行为保留；`test_opencode_bridge`对生产入口验证两方实际调用独立客户端，禁止旧provider回退。
- `--check-only`只调用连通检查；`--prepare-only`生成校验技能但不启动游戏；正常默认最多1800墙钟秒、Acropolis、两个原生玩家窗口、正常迷雾、realtime=True。
- 每次独立 `opencode-runtime/match-*` 目录，p1/p2/opencode.jsonl保存请求/正文/异常，attempt-*保存模型原始源码，preparation.json写验证范围，run.json写总体状态，match.json/SC2Replay沿用原生报告。

### 实测与边界

- `logs/opencode_bridge_tests_current.log`：最终83项通过（旧78 + 新5）。新测试包含模型身份/正文/截断检查、上游retry原错误与abort、文件路径穿越原子拒绝、真实隔离worker合成检查、双OpenCode客户端注入原生入口的fixture。fixture最终Tie是合成后端返回，绝不是实测比赛结果。
- `verify_opencode_prepare.py` 只用选定GLM做准备流程实验，不是替代对手。第一次默认推理100秒仍无正文，明确终止；`logs/opencode_glm_prepare_default_timeout.log/.json`保留。数据库仅统计到持续reasoning而没有assistant正文，不能说是工具死锁。
- 在独立服务配置reasoningEffort=low，并把准备范围收紧为一个实用小技能。参考OpenCode options文档 https://opencode.ai/docs/models/ 及模型低推理支持；没有关闭GLM推理，也没有修改用户原配置。
- `logs/opencode_glm_prepare.log/.json`：真实GLM第1版越过局部20单位坐标界限，被明确拒绝；第2版用objective/retreat目标修正，通过3份合成观察。87.1006墙钟秒、2次生成，技能marine_skirmish，源码SHA256=99c1fb56977757e11dfefe65e58c8ca8e340f4d1555ee1424496f68f7401cc74；三轮worker响应0.144/0.120/0.111ms。这是合成状态上的执行响应，非对局帧率。
- 实际模型源码/准备记录在 `opencode-runtime/glm-prep-validation-vsk_k6zb/p1/attempt-2/`；每次生成正文和修正请求在该p1/opencode.jsonl。
- 已证明GLM通过真实OpenCode生成→收到错误→自修→隔离运行通路。没有证明该技能自然对局效果、DeepSeek实际生成、两模型共同比赛或胜率；最后仍缺Go Global设置放行。所有本轮服务在退出清理，未开SC2窗口。


## 【2026-10-07 用户要求替换受Global限制的选手】默认Qwen3.8 Flash对GLM

- 用户明确要求“换一个没有global的限制的”，替代先前继续DeepSeek并改Global的选择。本轮保留已有Go认证与Privacy设置，只替换P1。
- 先运行 `opencode_match.py --p1 opencode-go/qwen3.8-flash --check-only`，没有先改默认值或启动SC2。
- `logs/opencode_qwen_replacement_check.log`、`opencode-runtime/match-rih_elgk/run.json`：真实Qwen3.8 Flash返回合法tasks=[]，3.6686墙钟秒；真实GLM-5.3-Flash同样返回合法JSON，2.6424秒；两者模型身份核验通过，status=connectivity_passed，未出现Global地区拒绝。每方原始请求/正文在该运行目录p1/p2/opencode.jsonl。
- 判别通过后更新 `arena/opencode_bridge.py` 的DEFAULT_MODELS及手册/交接文件。默认启动命令不变：`./run_sc2.sh .venv311/bin/python opencode_match.py`。
- 结论限定当前账号/Go设置下的真实连接测试；没有宣称所有地区永久可用。未执行Qwen技能准备或完整双模型比赛；原有GLM准备验证不等于Qwen验证。服务退出，临时认证副本已删除，没有修改HOME、Privacy或U盘。

## 2026-10-07：Qwen 对 GLM 原生观战已启动

- 用户明确要求开始对局。真实 OpenCode Go Qwen3.8 Flash / GLM-5.3-Flash 分别编写 sustained_skirmish / marine_skirmish，通过隔离 Python 合成观察检查；原始准备位于 `opencode-runtime/match-shhfuq2p`，Qwen 55.788s/1轮，GLM 104.182s/4轮。
- 首次双窗口启动 150 秒未连上 websocket，失败可见；`logs/opencode_watch_match.log` 保留完整失败。随后相同 P1 参数的单窗口探针 27 秒端口连通，`logs/native_engine_start_probe.log`、`logs/native_engine_start_wine.log`。未确认首次失败根因，未宣称已修复启动可靠性。
- 显式恢复脚本 `logs/resume_prepared_watch.py` 校验原技能清单/源码哈希并重跑隔离检查，在新的独立 OpenCode 会话中分别回灌本方已通过源码/战略且获真实模型确认，没有代写/重新生成或换模型。运行目录 `opencode-runtime/watch-recovery-qzjd49ed`，原生目录 `native-runtime/match-wa8qqxuq`。
- `logs/opencode_watch_recovery.log` 已记录引擎 5.0.15.97579 READY、双方真实计划被 Commander 接收并发出建造/造工人指令。前20.045s墙钟推进20.446s游戏，比例1.02005，控制10.028Hz（不是显示FPS），引擎拒绝0。全局战斗反射OFF，各方Python技能已注册；尚未据此证明技能自然战斗效果或完整对局胜率。
- 以 realtime=True、两个1600x900原生GPU玩家窗口/正常迷雾运行；没有第三方全图观察者。最多运行1800秒；本记录写入时仍运行，结果/录像由该目录match.json及match.SC2Replay落盘。启动截面证据 `logs/opencode_watch_started.json`。原生渲染日志确认双1600x900 swapchain，未重新测显示FPS。

## 2026-10-07：自主 Python 调试与后台修复验收

- 按用户指令结束旧 Qwen/GLM 观战局，`opencode-runtime/watch-recovery-qzjd49ed/match.json` outcome=interrupted，录像 `match.SC2Replay` 已保存。
- 先行真实 OpenCode MCP 工具判别：`logs/opencode_lab_probe.log`、`opencode_lab_probe2.log` 的运行中配置未让工具可见；启动时配置探针 `logs/opencode_lab_probe3.log` 实际 tools/call echo 并返回预设 token，证据 `opencode-runtime/lab-probe-8jg7s3mz/tool_called.json`。无模型文字模拟通过。
- 每位选手独立开发/战斗会话、独立 OpenCode 服务、独立 MCP 技能目录。实际工具读写 skills/tests；标准库 unittest 在隔离 Python 里运行，输出与退出码完整记录；自测和宿主检查绑定当前源码+测试哈希。战中开发会话不抢占战略请求，主循环仅接收已完成修复结果，旧任务源码快照不变。
- 初次真实准备 `opencode-runtime/match-fuuk35um`：双方从零写自己的源码/测试。GLM 自测17项首次3失败，后续自己修改至全过且 host validate 通过；最终摘要带 prose 导致旧严格完成摘要 JSON 检查失败。Qwen草稿保留，因另一方失败整体退出；没有开赛。`logs/autonomous_lab_prepare.log` 保留全过程。
- 后续 `logs/autonomous_lab_prepare_continue.log` 模型目录加载超时，`...continue2.log` 服务启动20秒超时；并非技能验证通过。改为显式可见60秒启动等待，45秒目录查询，局部status轮询超时只做可见诊断，真实请求绝对期限不变。未改供应商/模型或原用户配置。
- 初次真实修复试验 `logs/skill_repair_live_first_failed.log/.json`：GLM 已通过本地测试/host检查，但本地服务状态查询与abort超时，修复明确失败、技能保持阻止新调用，没有冒充安装成功。
- **真实修复通过**：`logs/skill_repair_live.log/.json/.SC2Replay`，运行 `opencode-runtime/repair-live-yisflyf5`。受控 NativeArena/realtime=True/headless、注入1 Marine和无敌模式；fixture技能故意包含undefined_phase。真实GLM下令后触发NameError，开发会话使用工具修改源码、写测试、执行隔离自测+宿主检查+真实失败观察重放，新SHA安装；随后真实战斗GLM以planner任务#2重新派遣，新版本执行至少3次。无宿主自动派兵。
- 上述受控场景游戏66.473s/墙钟67.748s；整段引擎比例0.98148，控制10.005Hz（不是显示帧率），engine_errors=0，accepted_plans=9。旧hash14d2f09e…，新hasha7de42dd…。这是完整修复链路验证，不是自然对局胜率/任意bug可修复证明。
- fixture回归 `logs/lab_full_regression_final.log` 93项通过；随后增加status轮询延迟测试及单技能/修复预算细化，补充检查见 `logs/lab_final_focused_tests.log`。不以fixture替代上面的真实链路证据。

### 自主调试验收完成与新观战局

- `logs/autonomous_lab_prepare_continue3.log` / `opencode-runtime/match-sqzk74f4/run.json`：显式接续双方自身草稿，新开发会话实际读文件、运行测试与validate，再完成验收。Qwen skirmish_push 14/14自编测试通过，GLM marine_guard 17/17通过；宿主独立检查全过。续跑准备耗时64.503s/18.755s，不包含之前从零编写/失败轮次，不能冒充总准备时间。所有源码修改均由相应模型通过工具完成。
- 生产启动命令 `./run_sc2.sh .venv311/bin/python -u opencode_match.py --prepared-from opencode-runtime/match-sqzk74f4 --seconds 1800`，精确复用已验收源码和测试，模型身份/哈希校验且重跑检查。
- **当前新对局已启动并在记录时运行**：Qwen3.8 Flash vs GLM-5.3-Flash，`opencode-runtime/match-bkvz5ycy`，日志 `logs/autonomous_lab_watch.log`，原生 `native-runtime/match-g75p976k`。双GPU玩家窗口1600x900、正常迷雾、realtime=True、全局reflex OFF、后台修复默认开启。双方真实计划均已被接收；20.070秒墙钟推进20.446秒游戏，比例1.01878，0模型错误/0引擎拒绝（启动截面，不保证后续始终0）。`logs/autonomous_lab_watch_started.json`保留最近启动截面。
- 最新补充 `logs/lab_final_focused_tests.log` 16项通过（与93项全套有重叠，不相加）；包括独立隔离、超时、源码哈希变更阻止验收、记录目标消失重放、格式归一化可见性、后台非阻塞与旧版本冻结、模型重新派遣边界、status查询短暂超时不错误中止成功请求。
- 自然局尚未结束，不能报告胜率或宣称所有技能bug都能修复；战中修复真实链路证据限上述受控NameError场景。

## 2026-10-07 11:00：用户要求重新开局

旧局 `match-bkvz5ycy` 已正常中断并保存 match.SC2Replay。新局 Qwen3.8 Flash vs GLM-5.3-Flash 已进入原生实时游戏，目录 `opencode-runtime/match-o_egaz3l`，日志 `logs/opencode_restart_20261007_110053.log`。复用 `match-sqzk74f4` 通过自主调试的精确源码及测试，重验通过；新模型会话、游戏状态、任务队列。后台修复开启，全局战斗反射OFF。20.094秒墙钟推进20.446秒游戏（1.01753×）；双方接受1/3次计划，当前0规划错误/0引擎拒绝。此为启动截面，不是整局结果。


## 2026-10-07：OpenCode 战斗请求减负、软/硬超时与迟到复核

用户批准三项改动。本轮未停止/重启 `match-o_egaz3l`；记录时仍 running，现有进程继续原规则。下一次正常启动 `opencode_match.py` 才使用新机制。未改变 realtime=True、全局反射OFF、引擎、模型身份或原用户配置。

### 先行判别
- `logs/battle_timeout_before.json`：旧适配器缩短预算的可控延迟探针确认会中止；35游戏秒的仍可训练工人计划被旧Commander整批丢弃。人工时序，非实际服务延迟/引擎验证。
- 本机只读对比 `logs/battle_context_size.json`：系统提示6149→2667字符（-56.63%）；同一历史首请求离线精简，P1载荷3327→3046，P2 2584→2303字符。未据此宣称模型延迟按同比例下降，亦非新比赛请求测量。

### 实现
- `arena/opencode_bridge.py`：只在战斗模式每次新建session，审计 old/new ID，无旧会话隐式回退。开发/准备历史不变。22s仅记录response_slow和可见提示，60s中止；以请求线程真实完成时刻判断是否越过硬期限，防止慢status轮询后误收超时回复。原有错误/abort确认/关闭失败处理保留。
- `arena/battle_plans.py` + Commander：精简协议，当前观察/任务/目录/修复状态显式携带，准备策略1200字符、上次已接受理由600字符，截断有元数据。重复日志移除，完整结构化反馈保留。默认8事件/8000字符分页，最老事件+失败优先，大事件完整发送并标记超预算；ACK只覆盖实际发送ID。没有删减源码修复通道的观察与错误。
- 请求时保存本方只读观察和技能版本。计划年龄>30游戏秒启动保守复核，>70秒一律拒绝；校验生产/施工人口变化、生产者/原工人、补给余量、可用部队/血量、可见敌军构成/位置、Python版本和修复阻止状态。迟到投降、新JSON定义和JSON工作流要求新决策。整批拒绝有具体原因并立即重新请求，不擅自改数量或部分派遣；通过后仍经原TaskQueue完整校验。
- 原生overlay显示等待秒数、SLOW和硬期限；历史错误标注Last error，不将慢响应算作失败。

### 回归和真实模型探针
- `logs/battle_timeout_focused.log` 29项通过；`logs/battle_timeout_regression.log` 项目全套107项通过；新增 `logs/battle_timeout_version_checks.log` 两项通过（Python版本更换/修复阻止、原取消任务失效）。不同轮有重叠，不将29+107相加。
- 先尝试真实服务回放本地对局数据，被自动审批拒绝（具体记录/技能信息外发未被视为明确授权），未执行。改为 `verify_battle_latency.py` 手写假想资源/人口/空技能库输入，无读取/发送本地对局记录或技能源码，获准执行。真实调用OpenCode1.18.35和原有Go模型，未启动SC2。
- `logs/battle_latency_synthetic_live.json`，目录 `opencode-runtime/battle-latency-dmfl0ez_`：GLM两次3.9613s/3.4689s返回，通过生产TaskQueue协议校验（未执行游戏动作）；两个会话ID不同。使用token总量2767/2885，第二轮显式加入任务/理由，不能据此量化真实对局的总token降幅。
- **Qwen探针失败被保留**：22.085s记录response_slow，60s仍未完成正文而硬中止，abort_acknowledged=true。`logs/battle_qwen_timeout_metadata.json` 仅统计OpenCode本地数据库消息元数据：1段reasoning/9795字符、无text段、最终MessageAbortedError，没有读取/展示推理正文。无status_poll_delayed。证明本轮在生成推理但未交付正文，不能将此说成网络错误或已根治；总体probe passed=false。
- 没有改换供应商、关闭推理开关、增加重试掩盖失败，未以静态夹具宣称自然对局/胜率验证；未测显示FPS，本轮不属于渲染性能优化。新的超时处理只避免22s过早中止，不能保证慢模型在60s内完成。


## 当前状态：用户已停止对局（2026-10-07）

`match-o_egaz3l` 已按用户要求通过 SIGINT 正常退出，结果 `interrupted`。录像 `opencode-runtime/match-o_egaz3l/match.SC2Replay` 已保存（50528字节），本局模型服务退出、临时认证副本清理；不要把下方历史“running”记录当作仍运行，也不要自动重开。



## 当前状态：复盘后重赛已启动（2026-10-07）

用户明确要求原 Qwen/GLM 检查自己的脚本、允许修复Python后再次对战。当前新局 `opencode-runtime/match-fllgcyro`，日志 `logs/opencode_review_rematch_20261007.log`，原生窗口 `native-runtime/match-uxu1lft0`。realtime=True、全局反射OFF、后台修复ON，新的精简请求/22秒软期限/60秒硬期限已启用。记录墙钟230.2s时双方已接受计划[0, 23]、规划错误[4, 7]，时间比例1.02052。Qwen实战起步仍受超时/JSON错误影响，不能声称开赛后始终正常。旧局 `match-o_egaz3l` 保持停止且已归档。

双方通过工具读取本方复盘及源码，各补充测试，Qwen17项/GLM20项和宿主检查通过；最终技能源码hash均保留上一局最后版本，没有为了展示“修复”而强制改源码。真实战斗调用演练GLM第1次通过，Qwen先JSON失败、再60s超时、第3次返回合法完整args通过。演练仅证明合成观察下调用格式，不等于自然对局成功；实际运行进展以本局日志为准。

新增复盘入口 `opencode_match.py --review-from <已停止目录>`，校验模型身份、复制本方最新技能/测试/必要fixture，提供本方完整错误记录；不交换对方源码或私有决策。开发模型明确被授权实际修改Python、补回归；require_review_read要求真实工具读过复盘，accept仍强制当前哈希自测/宿主检查通过。之后真实battle session生成Python任务，用生产TaskQueue校验，失败原样反馈，3次不通过则不开赛。原有--prepared-from/--drafts-from路径保留，三种来源模式互斥。说明已更新AI_SKILL_MANUAL。

`logs/match_review_tests.log` 14项本地检查通过，覆盖按选手隔离、错误身份/仍运行来源拒绝、实际读取门槛、错误参数反馈后再校验、演练失败禁止宣称通过，以及原有技能实验室/后台修复fixture。真实模型工具审计分别在新局p1/p2/lab/audit.jsonl，准备报告与run.json保留自测次数、源码hash和调用演练所有失败；`logs/review_rematch_started.json`保存该次启动观察。未验证整局胜率，未测原生显示FPS；control_hz约10不等于FPS。

## 2026-10-07：带指导的模型自主修复与赛前资格检查

用户授权原 Qwen3.8 Flash / GLM-5.3-Flash 修复自己的 Python，并允许宿主提供指导。旧 `match-fllgcyro` 实际已到1800秒上限结束并保存录像；以下新增准备/检查失败不会启动引擎。

- `match-hn4hvt1m` / `logs/guided_repair_prepare.log`：双方实际读取本方31/54条完整错误/修订记录，补充测试。先前17/20项扩展为23/24项；源码保留旧局最终版本，不冒称此轮双方都改了源码。
- `logs/in_range_target_race_probe.json`：隔离运行双方真实源码，输入3个Marine和射程内敌军，执行快照敌军消失。Qwen attack_move 通过，GLM attack_unit 对失效tag失败。非引擎实验。已加入宿主独立检查和可读取的 `fixtures/execution_race.json`，不能由选手旧fixture覆盖。
- `match-so7ln9uw` / `logs/guided_repair_rematch.log`：模型自行读写/测试，GLM实际修复射程内敌军仍可能消失的问题，源码SHA从46a3cddf…变为3fd685ef…；Qwen已有实现通过，不强制改源码。最终自编测试24/25项及宿主检查通过。实际工具审计在各自 `lab/audit.jsonl`。此轮资格检查拒绝GLM虚构build_barracks、Qwen给move_out错误传参，因此未开赛。
- OpenCode实际StructuredOutput最小探针见 `logs/structured_battle_probe*.log/.json`。仅允许格式化工具，其他战斗工具仍deny；读取实际版本 `info.structured`，缺失明确报错，不回退纯文本。Qwen强制工具与原thinking配置探针API400；仅战斗请求改用明确的 `arena_fast` variant（thinking disabled）后两模型实际工具回复各约4秒。同一Go provider/model，没有换模型；开发/修复配置保留。不是严格配对延迟A/B，也不保证所有请求低于4秒。
- 输出schema按当前可调用技能和参数生成，生产TaskQueue仍做独立校验。错误由模型改正，宿主不替它改任务。JSON自定义技能同轮定义/调用仍支持。
- `--qualify` 强制实际Python调用演练+连续五个不同合成观察下的真实模型决策，每次≤22秒并通过生产协议校验。失败完整反馈，最多3轮，每轮必须重新连续通过五次；不能累计零散成功绕过检查。它不执行游戏动作，不能声称自然战斗通过。战中依然22秒提示、60秒硬超时。
- `match-t0a5txlm` / `logs/guided_repair_qualified_match.log`：GLM调用演练通过；Qwen错误将演练当实战自行改人数/任务，3次拒绝后正确停止，未启动游戏。将演练目的提到系统指令、明确只在练习中覆盖战略选择，继续验证，未放宽验收。
- 本地回归 `logs/guided_repair_final_regression.log` 118项通过；属于fixture/协议/隔离测试，不是胜率或原生显示FPS证据。


## 当前状态：指导修复并通过资格检查的新局已启动（2026-10-07）

用户要求原模型自行修复后重赛，并允许提供指导。当前运行 `opencode-runtime/match-vm6o1ldo`，日志 `logs/guided_repair_qualified_match2.log`，原生窗口 `native-runtime/match-3av_j5vt`。旧 `match-fllgcyro` 已1800秒到时结束，不再运行。保持realtime=True、全局战斗反射OFF、后台模型自修复ON，未启用8766浏览器服务。

GLM根据宿主复现的“射程内敌军执行时消失”问题，实际自行编辑Python，将失效tag攻击改为attack_move；新SHA3fd685ef…。Qwen当前a0b71bcf…已通过同一检查，保留源码并补测试。两方自编测试24/25项及宿主独立检查通过，精确源码来源 `match-so7ln9uw`。宿主没有替选手编辑技能。前几次错误演练/资格检查均明确停止且未开游戏，详情见STATUS。

两方实际Python调用练习和连续五次真实模型决策均通过：Qwen 2.450–3.267s、GLM 1.381–4.565s（合成观察+生产校验，不是游戏动作验收）。开赛后50.257秒墙钟推进51.250秒游戏、比例1.01977；Qwen接受8/GLM6次真实计划，完成4/2项任务，0引擎拒绝。GLM曾将tasks写成字符串，生产校验明确拒绝并反馈，下一次真实回复纠正为数组并继续；不能宣称所有格式错误已消失。证据 `logs/guided_repair_watch_started.json` 及主日志。尚未验证整局胜率，没有重新测显示FPS。

战斗请求改用实际OpenCode StructuredOutput，仅该格式工具允许，缺失结构结果报错，无纯文本回退。Qwen仅战斗使用arena_fast（thinking disabled），同一Go模型；开发/修复仍保留推理。准备复用会复制必要旧fixture，但当前宿主execution_race.json不被覆盖。`--qualify` 开启连续五次真实决策门槛，失败反馈后必须整轮重做；运行中仍22秒软提示、60秒硬截止。本轮本地回归118项通过（fixture，不冒充自然局）。



## 当前状态：原生窗口显示模型决策正文，新局已启动（2026-10-07）

用户选择结束旧局并重开使叠字生效。旧 `match-vm6o1ldo` 已SIGINT正常结束，outcome=interrupted，录像16139字节。当前新局 `opencode-runtime/match-07ipg3rk`，原生日志 `native-runtime/match-qynqlhwe`，主日志 `logs/native_reason_overlay_watch.log`。复用双方已经测试的同一Python版本，重跑自测/宿主检查/实际派遣练习和连续五次资格检查后开赛；realtime=True，全局战斗反射OFF。

原生窗口的LLM THINKING/ACTIVE下方新增LLM says：模型最近一次已接受计划的reason原文、游戏时间和距今秒数。等待新回复时保留，拒绝/过期计划不覆盖；空正文明确提示，最多3行，超长显式[...]。不读取内部推理、不增加LLM调用。实现为Commander.last_decision + arena/native_overlay.py + native_llm.py。

`logs/native_reason_overlay_regression.log` 52项现有相关回归通过，另验证实际回复reason存在及DebugText protobuf传输、中英文换行/截断。新局READY，30.099秒墙钟推进30.714秒游戏，双方已接受5/4次计划且last_decision包含真实正文；GLM有1次规划错误，未隐藏。`logs/native_reason_overlay_started.json` 保留后续实际运行截面及对应显示文本。验证范围是原生运行/叠字调用与模型数据，未成功获取桌面截图，不声称中文字体像素效果已验收，亦未测显示FPS或整局胜率。



### 2026-10-07：观战正文改为简体中文，当前局直接生效

用户要求两模型输出中文。在 `match-07ipg3rk` 每方独立 battle/developer 目录写入中文语言AGENTS.md；原OpenCode服务的后续请求实际读取生效，未重启游戏，未改动API/技能标识符。两方last_decision已有被生产校验接受的中文reason，证据 `logs/native_chinese_output_verified.json`，部署记录 `logs/native_chinese_instructions.json`。这是模型自己的中文回复，非观战层机器翻译。公开自然语言包括reason/strategy/修复解释；原始工具错误和协议字段保持原文。

后续启动默认中文：battle_plans.LANGUAGE_RULES接入紧凑/普通Commander与开发修复提示；结构化输出reason必填非空、100字以内。当前进程通过本方AGENTS语言指导生效，下一局加载更新后的schema。只证明已观测回复符合中文要求，无法保证模型以后每次遵从；规划错误仍原样拒绝/反馈。


## 当前状态：独立中文输出窗口，新局运行中（2026-10-07）

用户要求撤掉游戏内LLM正文，改为我们自己的双模型输出窗口，并同意做好后重开。当前 `opencode-runtime/match-cqqhx8ds`，主日志 `logs/dual_output_window_watch.log`，原生游戏 `native-runtime/match-jkouzs4x`，输出窗口日志 `<本局>/output-window.log`。双方真实中文计划已接收；70.187秒墙钟推进71.696秒游戏，1.02151×，双方接受11/7、规划错误1/3（启动截面，不隐瞒错误）。证据 `logs/dual_output_window_started.json` 保存后续截面。保持realtime=True与全局反射OFF。

新增 `llm_output_window.py`：独立pygame桌面窗口「星际 AI 对战 · 双方输出」，双栏正文/指令摘要、完整JSON切换、错误筛选与滚轮历史。使用系统Noto CJK字体，实际渲染截图已检查中文正常；`logs/dual_output_window_preview.png`、`logs/dual_output_window_errors.png`。只读取本局公开回复和feedback journal，不读内部推理，不额外调用模型；关闭窗口不终止游戏。半行JSONL等待写完、解析/读取错误/过期状态可见，不把回复或任务派遣等同于任务完成。展示每0.5秒刷新，状态采样10秒；缓存文本换行，离屏绘制平均6.60ms，仅窗口渲染指标，不能冒充游戏FPS。

`opencode_match.py` 完成验收后自动启动窗口（`--no-output-window`显式关闭）；native_llm移除正文叠字，保留运行状态。`logs/dual_output_window_regression.log` 19项相关现有测试通过；另实测增量日志不重复、半行等待、拒绝状态关联与渲染，完整证据 `logs/dual_output_window_render_check.json`。

新局明确复用 `match-vm6o1ldo` 的完整验收版本，因为旧局战中修复草稿已改变且未完成验收。再次自测24/25项、宿主检查、真实派遣/五次连续决策通过；GLM第一轮因tasks字符串被拒绝，反馈后第二整轮通过；失败没有跳过。

旧 `match-07ipg3rk` 用户要求停止：SIGINT已进入KeyboardInterrupt，但主进程随后在清理中退出（exec143），未完成最终报告/录像。游戏进程已退出，6个仅属该局的残留模型/MCP服务已终止，临时auth副本清除。run/match报告显式加shutdown_recovery并标interrupted，保留原始样本；没有伪造录像。证据 `logs/dual_output_shutdown_recovery.json` 和旧主日志末尾。该清理中断根因未判明，不宣称已修复。



## 当前状态：Qwen 对 GPT-6 Luna 新局已启动（2026-10-07）

用户明确要求将GLM替换为GPT-6 Luna，保留Qwen。当前默认模型与运行阵容为 `opencode-go/qwen3.8-flash` / `opencode-go/gpt-6-luna`；没有使用gpt-5.6-luna或其他供应商替代。真实结构化调用探针 `logs/gpt6_luna_connectivity.log/.json` 3.364秒，返回身份与生产计划校验均通过，沿用已有Go登录/地区设置。

新局 `opencode-runtime/match-gg7rlqak`，主日志 `logs/qwen_luna_watch.log`，原生 `native-runtime/match-6a86sa45`。独立中文输出窗口已自动启动，右栏按身份显示gpt-6-luna；保持realtime=True、游戏内无正文叠字、全局战斗反射OFF与后台修复ON。前10.034秒墙钟推进10.223秒游戏，比例1.01884，双方各接受2次真实计划，启动截面规划错误0/0，非整局保证。最新截面 `logs/qwen_luna_started.json`；尚未测完整胜率和原生显示FPS。

准备目录 `match-n6myyne2`，`logs/qwen_luna_prepare.log`。新增--p1-prepared-from/--p2-prepared-from可按选手单独复用精确验收源，与全局来源互斥；原身份/哈希/重验门槛保留。本次Qwen复用match-vm6o1ldo（24项自测），GPT-6 Luna从空lab自行使用工具写steady_marine源码/manifest/6项测试。初次2项失败后真实修改代码和测试，第二次6/6及宿主检查通过，准备墙钟40.392秒；SHA7e68635756ecd757303fa57cd16c09cd0dbda3ef43f4c5b48ae07f523d94918b。不是把GLM旧代码换标签。

实际派遣演练Luna第一次多带skills字段被拒绝、第二次通过；五决策资格检查Luna首轮通过1.471–1.874秒。Qwen首轮兵营count=2非法，被反馈后第二整轮通过2.309–3.671秒。完整失败保留。新局精确复用刚验收的双方技能，重跑源码/测试检查，没有重复调用已通过的五决策资格流程。14项既有相关回归通过，日志 `logs/qwen_luna_selection_checks.log`；自然对局表现不能由合成资格数据推断。

旧 `match-cqqhx8ds` 已SIGINT正常退出、报告interrupted，录像21136字节，旧输出窗口已关闭；其后台修复草稿未拿来冒充已验收源码。手册默认阵容已更新。



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



## 2026-10-07：Luna 进攻目标误指己方基地，执行层修复与真实移动验证

用户观察到 Luna 反复说推进但部队不出门。原局 `match-_ig80d8w` 的 P2 确实调用 steady_marine/attack_nearest，不是只有正文。`logs/luna_home_target_before.json` 抽取原始 `logs/feedback/p2-aoei49av.jsonl`：任务12在149.33秒启动，objective和retreat都为(33.5,138.5)，到253.53秒截止时两名Marine仍仅距家2.95/2.82格。P1目标却正确。旧局后由Luna自己调用surrender结束，正式结果P1胜/P2负，录像29620字节；这个宿主缺陷使对局不公平，不能用来比较模型强弱。

先对未修改引擎做真实双API最小判别：`logs/enemy_start_before_probe.log/.json`，Native5.0.15、realtime=True、无模型/单位动作。P1的GameInfo候选点仅包含P2出生点，P2的GameInfo候选点仅包含P1出生点；旧RawArena/NativeArena只读controller[0]并共用列表，P2解析必然返回自己。日志保留正常关闭后所属launcher超时终止的记录，不隐藏。

修正 `arena/raw_engine.py`、`arena/native_engine.py`：每个API玩家使用自己的GameInfo候选点，共用configure_start_locations做数量/地图范围校验，排除自己的出生点；无有效点明确中止，禁止借用其他玩家私有观察或悄悄回退。开局打印双方map-target，`native_llm.py`报告保存map_targets。没有改模型战术源码，没有启用战斗反射，没有升级pysc2。

`logs/enemy_start_regression.log` 62项相关回归通过，新增8项覆盖真实双视角数据、双方出生互换、单API对Bot、不能读取对方私有home、缺失/仅己方/坏数据拒绝、宏命令与Python目标解析。它们是fixture，不是自然对局。

`verify_enemy_start_live.py` + `logs/enemy_start_after_live.log/.json/.SC2Replay` 使用同一旧局已冻结Qwen a0b71bcf…/Luna e81a957c…源码，真实双玩家realtime引擎。每方注入4个Marine，3个归原Python技能、1个归attack_nearest宏；没有LLM调用、无无敌/去雾。这次出生点顺序与旧局相反，两方仍解析正确。10.409秒墙钟推进10.625秒游戏，双方全部测试兵离家至少35.39/35.22格，向敌方接近至少21.71/21.60格，真实订单目标与目标基地一致，0引擎拒绝；不是自然胜率或原生显示FPS验证。

旧局另有50ms Python worker超时/规划格式错误，不能用本修复宣称它们一起解决。Luna该局真实修复已自动发布到持久库版本3456655165dbe81433ba407b22530bef7b245bb70618c928f2de3b78625b79c3，源码5e0082a5…；此后正常新局仍用最新持久版本，不强行回滚至受控实验所用旧源码。用户已授权验证后重开，启动日志为 `logs/enemy_start_fixed_watch.log`，启动结果待追加。


### 出生点修复后的正常观战局已启动

当前 `match-3d9uz_j7`（原生`match-dea1h2up`），Qwen3.8 Flash 对 GPT-6 Luna；双方从持久库自动加载并重验。Luna真实调用演练与五次资格首轮通过；Qwen前两轮格式错误全部记录/反馈，第三轮连续五次通过，未跳过检查。`logs/enemy_start_fixed_watch.log`为主日志。正常模式未注入单位、未脚本固定开局，realtime=True、全局战斗反射OFF，独立中文输出窗口运行。

启动截面墙钟50.159秒、游戏51.205秒、比例1.02086；双方接受[8, 9]次计划，规划错误[0, 0]，引擎拒绝0。新局报告明确P2 home=(33.5,138.5)、enemy_start=(142.5,33.5)。`logs/enemy_start_fixed_started.json`保存精确启动数据和继承版本。此时自然局仍在经济开局，尚未把受控移动测试说成新自然局的战术胜负验证。


## 【2026-10-07 三族生产、科技、经济与模型知识】

### 范围与实际状态

用户选择此次覆盖人族、虫族、神族。扩展生产执行层、Python战术权限及准备/战斗知识，并非只给模型一份名词清单。普通多人对战范围，不包含战役、合作指挥官和MOD。当前原生5.0.15.97579目录：

| 种族 | 单位/库存/召唤产品 | 建筑/附属建筑 | 科研 | 免费可逆形态 |
|---|---:|---:|---:|---:|
| 人族 | 18 | 22 | 32 | 24 |
| 虫族 | 20 | 20 | 30 | 28 |
| 神族 | 20 | 16 | 27 | 6 |

这些是目录条目，不能直接说成58种常规战斗兵，也不是所有89项科研实测通过。每项有中文用途、生产者、费用及前置；敌方当前可见单位也有用途说明。科技宏等待资源和前置，不自动补建或决定战略。新接口在下一场加载生效。

旧自然局 `opencode-runtime/match-3d9uz_j7/match.json` 记录P1 Victory/P2 Defeat，墙钟840.372、游戏857.679秒，1.02059倍；本次开发前已自然结束。这是旧接口局，不能当作三族扩展的胜率证据。本次没有开启新的正常观战局；验证结束后 `native-runtime/native-match.lock` 已可独占获取。保持realtime=True、反射OFF、原生项目副本，不改U盘、不升级pysc2。

### 先判别的真实引擎前提

- `logs/multirace_linux_premise2.log/.json`：Linux三族实际气矿能力320/1154/882，气体0→4。第一轮仪器不完善的失败保留为 `multirace_linux_premise.*`。
- `logs/native_query_discriminator.*`：资源debug命令到下一份观察才生效；初次能力查询不能用来断言能力永久缺失。`multirace_data_probe.*` 的过早资源断言失败保留，导出的元数据本身可读取。
- `logs/multirace_engine_data.pb/.json`：原生5.0.15.97579数据；`multirace_macro_live.json` 中metadata_after_startup核对开局前后值一致。原生开始8工人、CC费用300，与Linux4.10不同；没有用网上固定数字覆盖引擎。
- `logs/baneling_native_discriminator.*` 和 `baneling_native_bound.*`：4121/80两条控制命令明确NotSupported；真实4124成功产生Baneling。这个判别实验本来就含2次预期拒绝，不计为0错误。参考映射4121在原生对应Observer变形，采用名称身份绑定并输出ability_identity_binding。
- `logs/addon_native_discriminator.*`：旧Factory(152.5,31.5)附属地块被占，2×2占地无效；联合搜索得到71个合法位置，新Factory(142.5,41.5)。附属自身placement查询仍返回无可用点，但实际quick建造成功，0动作拒绝，证明不能以该查询作为唯一附属建造门槛。
- `logs/ammo_archon_native.*`：单单位HT/DT能力查询没有1766，两名圣堂同一raw1766却成功合成Archon。该fixture注入GhostAcademy缺自然前置，Nuke710遭1次拒绝，这不证明核弹接口失效。随后自然生产前置链的edge子项成功。Carrier库存不完整暴露为单位数；发射后才观察到部分Interceptor。

### 实现路径

`arena/tech_recipe_map.json` 来自MIT开源reference/python-sc2固定commit 81d66110cb0aa57cc7c2895dad207775e496fbd1；保留source.json URL/哈希和LICENSE，只用受限AST提取字面映射，未安装/执行下载代码。`arena/tech_tree.py` 将映射绑定实际引擎，元数据缺失/能力修正可见。`arena/native_catalog.pb` 仅作赛前知识快照；每场游戏重新读取live数据。

`arena/macro.py` 统一生产、研究、建筑、免费形态、采气与扩张。单位/升级实际观察后完成，保留资源预留和单位所有权；有限重试仅用于明确可恢复原因。SCV可续建；附属堵塞可升起搬迁；反应堆支持第二槽；虫族按消耗来源、幼虫和双单位批次计费；神族供能、折跃和两圣堂合体均区分。`raw_engine/native_engine`提供三族设置、upgrade_names、气矿/能量/载员等字段；中立矿点保留地图快照供扩张使用，不读取对方隐私观察。

`tasks/reflex`保留气矿工人分配，工人进入气矿不能被误补；已在采气的工人也受保护，防止建造抢人导致NotSupported。gather将过饱和矿区工人转到缺工基地。`commander/battle_plans`提供本族严格schema与中文知识、矿区饱和度、科技和可见敌人用途；未知产品/跨种族任务整批拒绝。

Python新增本族/混编选择器及cast，禁止越权、超出局部范围、通过法术绕过付费生产科研。运输内成员由embarked保留归属，失踪不等同死亡；army排除库存/短命召唤等；停止技能不向建筑乱发Stop。准备实验室按种族生成观察、提供GAME_KNOWLEDGE，并拒绝无法分配本族单位的技能。准备支持1..4技能；修复不能同时改动不相关技能以造成持久库与游戏内版本漂移。非人族持久库按race隔离，人族旧库保留。

### 成功证据与前提

以下真实原生均realtime=True。经济/科技链使用debug给予矿气并加快建造，**没有tech_tree跳过前置**；模型未参与这些确定序列。

| 证据文件（均另有.log） | 有效片段 | 完成步骤 | 墙钟秒 | 游戏秒 | 比例 | 动作拒绝 |
|---|---|---:|---:|---:|---:|---:|
| multirace_macro_live.json | 人族经济 | 8 | 31.563 | 30.982 | 0.98161 | 0 |
| 同上 | 虫族经济 | 9 | 38.180 | 37.500 | 0.98219 | 0 |
| 同上 | 神族经济 | 10 | 36.478 | 35.804 | 0.98151 | 0 |
| multirace_advanced_live.json | **仅人族** | 40 | 98.932 | 97.098 | 0.98147 | 0 |
| multirace_advanced_second.json | **仅虫族** | 53 | 113.149 | 111.027 | 0.98124 | 0 |
| multirace_advanced_protoss_final.json | 神族 | 41 | 133.664 | 131.161 | 0.98127 | 0 |

三族经济链均实际造气矿、分配3人、观察瓦斯收入增加、生产耗气单位、研究及扩张完成。高级链覆盖坦克/雷神/战巡/幽灵等人族生产，虫穴→主巢→巨型尖塔和潜伏者/巢虫领主/雷兽等变异，神族机械/空军/圣堂合体等；各自测试部分研究与形态。40/53/41是含重复人口和工人的步骤数，不是不同兵种数。两份旧advanced文件还保留其他种族失败片段，不宣称整份旧实验全部通过。

`logs/multirace_edges_final.json/.log`：12个宏步骤全部completed，包括反应堆第二槽、注入阻挡后的Factory迁移并造TechLab、Tank、GhostAcademy、Nuke与Interceptor补货。核弹由本方Ghost核打击可用确认；航母来自debug注入，只验证机库接口，不假称人族自然造航母。库存完成事件明确exact_quantity_observable=false，航母还标hangar_capacity_inference=true。**整脚本随后因拥挤基地中的Blink落点距目标约0.9格，超过测试0.5格阈值而失败**，没有整轮通过或虚构缺失的最终错误总数。

`logs/multirace_cast_final.json/.log`：独立原生受控测试通过，注入Stalker/Marine/Medivac及升级。实际隔离Python发Blink1442，loop119到124从(134.5,35.5)到(131.5,35.5)，3格位移、目标误差0且进入冷却；实际载入Marine后API passenger与Pythonembarked对应，队员不再假死亡；0动作拒绝。这证明局部施法和运输观察，不是自然研究或完整空投战术验收。

### 真实模型指令演练

`logs/multirace_models.log/.json`，真实Qwen3.8 Flash和GPT-6 Luna，各测试三族，共6项最终通过。使用合成world、生产StructuredOutput/schema/TaskQueue，要求计划包括采气、开矿、耗气兵和研究。Qwen人族5.482s、虫族6.011s首过；神族前2次纠错后4.314s通过。Luna人族3.219s、虫族8.234s、神族2.062s均首过。这里只证明模型可按这些新接口发合规计划，不代表它在自然局自主判断时机正确或保证胜率；真实失败与反馈没有删除。

### 回归与失败保留

- `logs/multirace_final_checks2.log`：全套151项，17.067秒，OK。涵盖目录/种族隔离、原子计划拒绝、不同产品去重、采气工人暂隐与死亡替换、消耗差价、能力身份、Archon双单位、库存语义、法术权限、实验室/隔离/持久库/修复/投降等。是本地fixture回归，不等于151项原生比赛。
- 先前 `multirace_final_checks.log` 149项通过；新增运输/错误种族两项检查时，`multirace_last_boundaries.log` 的测试替身漏了worker.future字段而报错，补齐替身后完成最终151项，未添加生产静默回退。早期 `multirace_regression_first.log` 因旧fixture缺start_location_report报错，保留并显式更新fixture；中间144/148次结果不能和151相加。
- `multirace_macro_initial.*`：初版气矿补人过量、迷雾矿点丢失导致扩张无候选；均先观察再修复。
- `multirace_advanced_initial.*`：气矿工人被建造抢走、毒爆旧ID、神族供能失败；`multirace_advanced_second.*`：人族附属查询误判、神族水晶过密；`multirace_advanced_live.*` 神族Archon单体查询失败；后续各自成功证据如表，不抹去这些失败。
- `multirace_models_initial.*` / `multirace_models_second.*`保留前几轮模型漏项/付费变异接口用错。生产SYSTEM和schema补强之后重新实际请求，不把宿主代改JSON说成模型自纠。
- 早期 `multirace_edges.log` 因重启/重定向发生交错，不作完整审计依据；`multirace_edges_ammo_premise.json`保留初次库存不能观察完成的判别。最终edge与cast各使用独立新文件，原生锁保证游戏未并发。

### 使用与剩余边界

完整转发手册 `SC2_GAME_KNOWLEDGE.md` 从同一模型目录生成；`AI_SKILL_MANUAL.md`更新宏能力、种族启动、运输/施法合同。复现入口示例：

```bash
./run_sc2.sh .venv311/bin/python opencode_match.py --p1-race zerg --p2-race protoss --qualify
./run_sc2.sh .venv311/bin/python render_game_knowledge.py
./run_sc2.sh .venv311/bin/python -m unittest discover -v
```

参考日志和验证脚本默认有固定输出路径，复测前需归档旧文件，不覆盖原始失败。新普通比赛尚未运行；没有完整新胜率、未穷举89科研和所有法术、未测显示FPS，不能把约10Hz观察轮询率当帧率。原生受控结果说明执行链可用，复杂战略和新的三族Python战术仍须选手自行准备、验收并在自然局验证。聚合证据为 `logs/multirace_final_summary.json`。


## 【2026-10-07 Kimi指定选手、共享参考与实际观战】

用户指定Kimi K3，授权另选对手并参考Luna/Qwen已有Python。读取本机当前OpenCode Go目录，Kimi K3、Qwen3.8 Max实际可用；`logs/kimi_qwen_max_connectivity.log` / `opencode-runtime/match-toe5bpo8/run.json`真实连接5.465/4.134秒通过。没有依据型号名称断言本项目“最强”或宣称游戏胜率。

### 参考共享

增加 `arena/skill_references.py` 和 `opencode_match.py --reference-model`（重复指定）。从持久库固定、完整性验证作者版本，仅向双方实验室共享源码、manifest、自测、策略与SHA，不复制认证或私有推理；不预填新选手skills。只读 `REFERENCE_GUIDE.md` 和 `fixtures/skill_reference_1/2.json` 可通过lab_read_file读取，写入工具拒绝修改。准备提示要求读两份资料，自行借鉴、编写及验收；run.json.references和持久发布origin保留出处。

本轮参考：Luna版本3fed10f47249bf1fcc1143360fd22cbfc34a5cf7546e81084c77e7b007e633ac（steady_marine源码7ac4fe2e…），Qwen Flash版本6e14def1a105bc25a004f4d9a939499a322fbc3e104b0aab850db81edc28cf52（skirmish_push源码a0b71bcf…）。实际空实验室读/写拒绝判别 `logs/kimi_qwen_reference_evidence.json`，原stdout在kimi_qwen_reference_premise.log；24项相关回归 `kimi_qwen_reference_checks.log`通过。没有给旧作者源码更换模型标签当作新模型创作。

### 最初候选Qwen Max没有通过派遣

`opencode-runtime/match-2j6nyp43`，`logs/kimi_qwen_max_reference_watch.log`：Kimi和Qwen Max均实际用工具读取两份参考（audit.jsonl）；Kimi179.327秒准备，10项自测和宿主通过，source哈希89796c908175725c99785a6944455a0eb0ef33a6b53165e65c1885794e53ce7c；其持久版本31724444e1145147ce015929e9af22bc20514b31758ad5101aa8689116edd51d。Qwen Max先两次300秒超时，第三轮完成，总822.949秒，11项自测和宿主通过，marine_push源码ec2ab7fe975256855b6a1905a6ee71d715839e3d72a1f43e07a4c99bf34b60ed，持久版本c979b61c6f3879bf843e4191e8f6727cf8a4df5757e9ce84121f08cf4ce73924。

源码通过不代表会调用：Qwen Max派遣演练三次reason说marine_push、tasks却为train_worker；明确失败，游戏未启动。读取本地/provider元数据发现Qwen Max走Anthropic SDK，公开low变体为effort=low，而通用代码仅设reasoningEffort=low。桥接改为实际low变体并校验存在，同时保持模型ID。`logs/qwen_max_effort_probe3.json`：带33604字符参考材料的短代码请求8.156秒返回；这不等价于完整开发任务，不能据此证明300秒超时全由参数导致。probe1/2因新目录被OpenCode判为ENOENT而HTTP500，日志保留，第三次使用已存在开发目录成功。

`logs/kimi_qwen_max_reference_watch2.log` / `match-mqbu9u_1`复用双方原样验收技能、显式Qwen low后再测：它又3次把派遣任务写成no_op/train_unit，故仍不开游戏。原因未彻底判定，不能简单归因Python源码，也不能称设置low已修复派遣。向用户明确告知后，对手改选GPT-6 Luna，Qwen Max失败和源码未删除。`qwen_max_variant_checks2.log`桥接/三族22项回归通过，含准备/战斗均发送low变体；第一轮误写不存在test_battle_plans模块的导入失败保留在qwen_max_variant_checks.log，不冒充通过。

### 实际启动的Kimi对Luna

当前 `opencode-runtime/match-ffaa512v`，`logs/kimi_luna_reference_watch.log`，原生 `native-runtime/match-s4iglwvz`。Kimi加载刚完成的自身参考改编版本；Luna加载其原本已验收/实战修复版本，两方重新自测及宿主检查通过。Kimi派遣首轮多了顶层字段被拒绝，第二轮成功；Luna首轮成功。五次连续真实决策两方整轮通过：Kimi2.97–7.32秒、Luna1.72–1.94秒。准备材料仍向双方开发/修复实验室开放，但Luna这次加载现成版本，没有新的研究参考/编辑工具会话；不声称它本轮实际读过Qwen参考。

两个原生玩家窗口和独立中文输出窗口由生产入口启动，输出日志 `<run>/output-window.log` 加载Noto CJK成功。wmctrl/xwininfo本机不可用，未做桌面窗口枚举或像素截图验收；游戏启动和双API观察、中文窗口子进程启动有日志，不编造显示FPS。

`logs/kimi_luna_reference_started.json`：realtime=True、正常迷雾、全局反射OFF、无debug资源/单位/科技注入，无脚本固定开局。启动截面墙钟103.5705秒/游戏101.4286秒，比例0.979319（速度检查OK）；Kimi/Luna实际接收计划14/17，规划错误0/0、引擎拒绝0/0，已采瓦斯112/176。Luna已安排expand等待资源。这里只证明启动并实际运营，非完整赛果或保证后续不报错。当前正常比赛仍留给用户观看，没有等待结束或自动停止。


## 结束记录（2026-10-07：用户已停止Kimi/Luna对局）

用户看到OpenCode Go的5小时额度耗尽后，明确要求关闭。`opencode-runtime/match-ffaa512v`已通过SIGINT正常进入清理，run.status和match.outcome均为interrupted；独立输出窗口已关闭。录像 `match.SC2Replay` 保存24283字节，本局游戏/模型/输出窗口相关进程已退出，原生锁释放，双方临时认证副本已清除。证据 `logs/kimi_luna_stop_request.json`、`logs/kimi_luna_stopped.json`。没有启用余额扣费、没有安排自动重开；下方运行状态为历史记录。



## 最新状态（2026-10-07：当前 Codex 对 DeepSeek 官方 V4.1 Flash 已完成一次实测）

用户要求当前助手亲自参赛，不使用GPT API key，并指定官方付费DeepSeek-V4.1-Flash。新增 `session_match.py`、`arena/external_planner.py`、`arena/deepseek_planner.py`。P1为当前会话逐次读己方文件邮箱并提交真实计划，P2用DEEPSEEK_API_KEY直连官方 `/v1/chat/completions`，准确model为 `deepseek-flash`，与官方模型列表name=DeepSeek-V4.1-Flash核对；low推理、JSON输出，仅记录公开正文，无OpenCode/无模型替换。不是把Codex订阅变成隐藏API；会话不持续工作就没有新决策。

自然原生实时对局 `session-runtime/codex-vs-deepseek-flash-20261007`，原生 `native-runtime/match-8svew76h`，主日志 `logs/codex_deepseek_flash_watch.log`。1200.066墙钟秒推进1224.777游戏秒（1.02059×），到预设20分钟墙钟上限正常结束，outcome=`time limit`，**没有引擎胜负判定**。终场P1人口111/130、2基地、22SCV；P2人口7/88、0基地、1SCV，P1明显占优但不可写成正式获胜。录像58117字节。双方引擎拒绝0，未测显示FPS。

P1接受28条当前会话实际计划；28次请求超过25秒，成功回复平均17.464秒。P2接受137条计划、2次格式/参数拒绝，成功回复平均7.900秒。全部日志保留，无静默代打。整局任务失败P1=8、P2=6（不是最终Commander有界历史中的4/0）；P1有单位损失、移动/等待超时、误选架起坦克等，后续确实提交过修订JSON技能。双方本入口都没有预装Python技能或Python后台修复，使用相同宏与JSON组合技能接口；既有OpenCode Python/持久库入口未改。本场尚非正式公平胜率基准，双方响应期限25/40秒不同，当前会话工具延迟显著。

所有比赛/模型/原生及本场输出窗口进程已退出，邮箱closed，原生锁释放；无继续计费请求。证据 `logs/codex_deepseek_final.json`、`logs/codex_deepseek_cleanup.json`。这次没有让比赛在助手最终回复后假装继续自动决策。

前提：`logs/codex_external_premise.json`是合成Commander非阻塞邮箱判别，非游戏；`logs/deepseek_official_models.json`真实模型列表；`logs/deepseek_flash_premise.json`小请求5.855秒；`logs/deepseek_flash_full_plan.json`完整科技目录合成局势真实API回复2.002秒、执行层验收通过，不冒充原生开局。`logs/external_session_checks.log`64项相关回归通过；补充边界后`logs/external_session_final_checks2.log`8项通过（与64重叠，不能相加）。第一版补充测试文件有引号语法错误，原失败`external_session_final_checks.log`保留，已修正。

后续优先：当前会话25秒回应窗口造成一半请求超时，不宜直接当无人值守比赛入口。增大timeout还需同步处理Commander默认30游戏秒旧计划限制，不能只改一个常数。移动任务多单位严格到位判据、局势缺乏单位位置/路径进度、任务反馈积压值得独立判别后修复，不能凭这场直接归因。手册新增当前会话参赛章节。CLI observe会等待至少15秒有效期的新请求；submit严格匹配ID/期限、拒绝重复提交；120秒无成功回应会显式停止比赛，旧任务只在等待期继续。



## 最新状态（2026-10-07：超时、单工人施工、防守集结、侦察记忆）

已按用户四点请求实现，下一场自动生效；realtime=True、全局战斗反射OFF保持。当前无新观战局，受控验证均关闭，原生锁释放；本轮没有付费模型调用。汇总 `logs/runtime_optimization_summary.json`，清理 `logs/runtime_optimization_cleanup.json`。

1. **超时**：当前会话邮箱改为按需请求快照，只有 `observe` 请求后才开始25秒回复计时；读取前不制造周期性超时，提交ID/期限校验与120秒离线停局保留。DeepSeek增加慢请求状态及响应耗时/输入大小/公开usage元数据，不更换模型、不暴露推理。原来三族宏超过30游戏秒一律丢弃，现对生产数量、生产者、结构/科技损失、敌情、补给、研究与采气意图做保守复验；形态/新JSON技能仍需新计划，绝对70秒上限保留。5次缩时邮箱验收零超时，但**不是新整局真实模型超时率**。真实引擎故意延迟35墙钟秒的计划被复验接受，实际造出2Marine，0拒绝，游戏/墙钟0.98143。原生证据 `logs/late_macro_native.json/.log`，缩时 `logs/mailbox_pacing_after.json`。
2. **单SCV施工**：原生订单319是坐标目标，target_unit_tag=0。旧代码因此误派第二个SCV（原生前提 `logs/runtime_optimization_native_before.*`）。现在保留唯一owner，按位置订单/进度判断；活工人中断由本人恢复，明确死亡才换人，暂时不可见不假定死亡。原生连续5秒施工多派0次，死亡后只派1名替补，最终建成。
3. **产能集结**：`arena/rally.py` 使用各自玩家的GameInfo地形/地面路径及引擎可达查询，主矿坡口内侧→暴露分矿入口，威胁/阻挡明确等待。只设置产能建筑UNIT rally，不召回已有兵、不改工人采矿点；请求后读取raw.rally_targets确认，失败有记录和有限重试。人族新Marine分别抵达主矿/二矿点，老Marine留在原点；虫族Zergling和神族Zealot抵达各自坡口，虫族worker矿点仍在。覆盖三族常规产能类型，但原生代表线不是每种产能逐项验证；warp-in/移动单位变异位置与已有战术任务仍由模型/脚本控制。
4. **侦察记忆**：`arena/intelligence.py` 每次己方观察更新，独立于LLM请求频率；短暂看到的敌兵/建筑、最后位置/时间、明确死亡事件保留到下一次决策。`intelligence`直接进入每次prompt，不排在任务反馈后；未返回请求的旧revision不会吞掉新事件。历史记忆明确标为last_seen/old_unconfirmed，不作当前可见目标、不读取对方私有观察。受控模型忙碌期间Observer看到Medivac后死亡，下一次prompt当前敌军为空但历史情报和死亡事件仍存在；实际死亡事件30.67游戏秒，下一次请求33.6秒，约2.93秒。正在处理的API请求无法中途追加prompt，下一次请求优先带新情报。

原生综合 `logs/runtime_optimization_native_after.json/.log`：0动作拒绝，62.323墙钟秒推进61.161游戏秒（0.98136倍）。三族补充 `logs/rally_races_native.json/.log`：Zerg/Protoss0拒绝、1.02310倍。176项相关回归 `logs/runtime_optimization_final_checks.log`通过（18.655秒），包括隔离Python、修复、持久库。均为受控debug资源/单位/部分快速建造或本地fixture，**不是完整自然对局/胜率/显示FPS**；约10Hz是观察频率，不是画面帧率。

失败/边界保留：`runtime_optimization_native_attempt1.*`施工子项已通过，但最初侦察布置没形成稳定可见目标而超时，后改飞行侦察/运输单位才整体验证通过；未据此隐藏生产错误。`late_macro_native_attempt1.*`测试准备漏了兵营的补给站前置，等待超时；补齐前置重测通过。综合after原始报告里scouting.death_game_s误命名为等待视野消失的时间，实际死亡以death_alert.game_s为准；汇总已明确更正，验证脚本未来输出分开两个时间，原日志保留。主矿没有可识别坡道时会明确标main_ground_approach；寻路不可达/能力缺失不会假称集结成功。



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



## 等待开赛（2026-10-07：Kimi K3 对 DeepSeek V4.1 Flash，双方 OpenCode Go）

用户指定这两个模型并要求给双方现有最好的技能。选用当前已验收且有实战记录的Kimi `stim_push`（含推进/兴奋剂/局部撤退与滞后/订单去重，不宣称胜率最强）；原作者和版本保留。双方相同源码及测试已预填到 `opencode-runtime/shared-kimi-deepseek-dqlfcajm`，源码digest相同，宿主重跑自测和隔离检查都通过，但**两位选手尚未审阅/正式采用，不冒称模型已准备完成**。`arena/lab_prepare.py`现提示这是用户共享种子、要求读取来源、检查源码并真实测试，保留作者归属；16项相关检查通过。

Kimi真实连接5.595秒通过，DeepSeek `opencode-go/deepseek-v4.1-flash`被服务端拒绝，原因Go需要Global。失败检查 `opencode-runtime/match-m09sm0g0`、`logs/kimi_deepseek_go_connectivity.log`；服务已清理，无游戏启动。用户正在按指引调整Global；浏览器实际看到Personal工作区Settings→Privacy→Regions选中Europe & United States，Global未选。已打开Privacy页面供用户操作，未替用户修改。准备证据及精确待启动参数在 `logs/kimi_deepseek_ready.json`；用户改好后复测并用 `--drafts-from` 加 `--reference-model opencode-go/kimi-k3 --qualify` 启动模型检查/演练及原生观战。不要误用DeepSeek官方key、静默换模型或未确认地区改变便循环付费重试。


## 当前运行（2026-10-07：Kimi K3 对 DeepSeek V4.1 Flash，双方 OpenCode Go）

用户已把工作区Regions改为Global；准确模型 `opencode-go/kimi-k3` 与 `opencode-go/deepseek-v4.1-flash` 的真实连接均成功，未使用DeepSeek官方key或替换型号。正常观战对局 `opencode-runtime/match-glwspbli`，原生 `native-runtime/match-9no79iws`；日志 `logs/kimi_deepseek_go_watch.log`，启动证据 `logs/kimi_deepseek_go_started.json`。两个原生GPU玩家窗口和独立中文正文窗口已启动，后者Noto CJK加载成功。保持realtime=True、正常迷雾、全局战斗反射OFF、后台Python修复/持久技能库ON，无固定脚本开局/无debug资源单位注入。对局留给用户观看，最长1800墙钟秒；未等待终局，不能写胜负/胜率或显示FPS。

用户要求现有最好技能，双方从同一份已验收Kimi stim_push源码与测试起步；基于功能/验收/实战记录选取，未宣称统计最强。两位实际通过实验室工具读取源码、跑测试和宿主检查。Kimi原样保留，10项自测通过，准备26.16秒；DeepSeek发现运输内成员不应误判小队损失，自己改源码并增加3项回归，先有1项失败，随后自己修复，最终13项全过，准备97.08秒。各自改进均已持久化，保留共享来源；最终源码不相同（共同起点，允许自主修复）。Kimi技能sha=89796c908175725c99785a6944455a0eb0ef33a6b53165e65c1885794e53ce7c；DeepSeek=a1b66fd78b595a4494912d534d174b4e2c9679a73d26909ada68c59ec1623524。

真实Python派遣演练Kimi首轮通过；DeepSeek首轮多了顶层字段被拒绝，收到反馈后第二轮通过。连续5个合成局势真实模型计划两方均通过：Kimi3.59–6.62秒，DeepSeek7.44–10.76秒。属于派遣/响应能力检查，不是原生微操全部通过。开始游戏后双方已接收实际战略计划并执行采矿、造工人/补给；启动截面实时速率与错误数以started.json为准。首个10秒截面双方规划/引擎错误0，不能保证后续永远不报错。

上方共享种子已经被本场采用/改进。未来新局正常使用持久库最新版本，勿再拿旧 `--drafts-from shared-kimi-deepseek-dqlfcajm` 覆盖DeepSeek的运输修复。下文“等待Global”“没有运行对局”为历史状态。



## 最新状态（2026-10-07：用户因 OpenCode 周额度耗尽关闭对局）

`match-glwspbli` 已按用户要求中止，双方原生游戏及独立正文窗口退出，原生锁释放；`run.json` 为 interrupted。证据 `logs/kimi_deepseek_go_stopped.json`，原始错误在 `logs/kimi_deepseek_go_watch.log`：weekly usage limit reached。没有重新开局或新增验证API请求，不据中止局宣称胜负。下文“当前运行”为历史记录。

用户另要求垂直同步+60 FPS上限。最小检查证实实际SC2为DXVK D3D9（`native-runtime/logs/SC2_x64_d3d9.log`），原present mode为IMMEDIATE。`arena/native_runtime.py` 启动时追加 `d3d9.presentInterval=1;d3d9.maxFrameRate=60;dxgi.syncInterval=1;dxgi.maxFrameRate=60` 到DXVK_CONFIG，保留其他继承选项并打印NATIVE_DISPLAY。DXGI选项兼容日后渲染器改变；不改realtime/游戏推进/API轮询。依据DXVK官方配置 https://github.com/doitsujin/dxvk/blob/master/dxvk.conf ，本机DXVK DLL含这些配置项。`logs/native_display_60_config_check.json` 仅验证启动环境传递/覆盖与可见日志（启动子进程mock），没有重新启动游戏、没有实测新FPS。下一场需核验DXVK effective configuration、present mode及HUD，不能提前宣称已稳定60帧。用户U盘、显示器刷新率及全局配置未改。



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



## 当前运行（2026-10-07：官方 DeepSeek V4.1 Flash 自我对战）

用户要求双方都用其DeepSeek官方key。已启动 `official-runtime/match-ff_2t4h4/run.py`（进程会话45417），真实模型`deepseek-flash`，端点`https://api.deepseek.com/v1`，两侧同一DEEPSEEK_API_KEY、独立client/观察/状态；没有OpenCode Go消费、无mock。免费GET models鉴权成功后直接开局，没有额外生成连通性探针。双方沿用DeepSeek已有Go作者库版本68a5e4ff7765…，各自复制冻结并本地重跑自测/宿主检查；不改作者来源，不假称审计问题已修复。本局未接后台源码改写服务（已向用户说明），宏/任务执行仍由LLM决策，全局战斗反射OFF、realtime=True、无固定脚本开局、无debug注入。最长1800墙钟秒或自然结束，保留运行供用户观看。

日志`official-runtime/match-ff_2t4h4/match.log`，周期状态`match.json`，启动证据`started.json`；原生引擎`native-runtime/match-fu9gsh0y`，独立中文输出窗口PID295189，日志output-window.log。真实双方计划已接收，初30墙钟秒各5计划、规划/引擎错误0，游戏/墙钟1.0213×；不是全局无错误或终局证明。当前状态入口`logs/deepseek_official_duel_current.json`。

60FPS/VSync新配置首次在原生游戏验证：DXVK effective configuration显示d3d9.presentInterval=1/maxFrameRate=60，present mode=FIFO；游戏59秒时截图P1 HUD59.9fps、P2 59.8fps，`window-b.png`/`window-a.png`，配置日志另存display-startup-d3d9.log。这是两张瞬时显示样本，不能当持续稳定帧率/与旧图的严格A/B。native_llm.py共享凭据提示改为识别provider，直接官方client不再被误称OpenCode登录。



## 已实现待下一场启用（2026-10-07：主指挥 + 独立防守副官）

用户同意实现，但明确选择“保留当前局，下一场启用”。未中止旧局；旧官方对局 match-ff_2t4h4 已自行结束（P1 Defeat / P2 Victory），未自动开新观战局。旧文档“当前运行”为历史。

新增 `arena/defense.py` 和可复用 `official_duel.py`。下一场用 `./run_sc2.sh .venv311/bin/python official_duel.py`，默认官方 deepseek-flash 双方各一名主指挥（low、25游戏秒）+独立防守副官（thinking disabled、384输出tokens、3游戏秒最短间隔、单在途、I/O timeout8秒，回复超过6游戏/墙钟秒拒绝）。局部守备脚本先响应基地警报，模型随后选接战/守位/撤后；无警报不轮询付费模型。实时推进/正常迷雾/60帧配置不变，旧全局战斗 ReflexNet 仍OFF。独立正文窗口已显示副官及公开错误。

冲突 Python 任务整体暂停、关闭旧worker并丢弃迟到动作，冻结期限；交还后保留state，用新worker和新观察继续。缺员明确要求重新派遣而非自动修代码。旧 Stop 和主指挥过期战术不能覆盖副官。只借用基地附近45格可用战斗单位，通常4–12、整队最多32；不借工人、不偷生产/JSON工作流、不指挥架起或埋地单位移动；当前集中防守一个基地。旧技能审计问题没有一并修复。详情 AI_SKILL_MANUAL.md 末节。

证据目录 `../_probe/defense_20261007/`：before.json 合成判别证明原有忙碌主模型无回防动作；checks_final.log 179项通过（19.358秒），包括原宏/三族/Python隔离/修复/持久库回归。isolated_resume.json 为真实隔离worker+合成观察，旧PID303376→新PID303380，state ticks1→2。native.json/native.log 为真实SC2 5.0.15受控无画面debug注入测试：主模型仍等待，首次可见警报同轮发回防，4次固定测试副官回复、0引擎拒绝，18.084墙钟秒/18.438游戏秒=1.01957倍；全体Marine最大位移8.23格。native_owned.* 补充单独记录实际分配单位的位移。不是自然比赛胜率，不是实际DeepSeek延迟或画面FPS。viewer.png 为离屏中文窗口预览。prepare.log 已成功本地恢复双方同版持久技能，prepare-only运行目录match-r__5nmsb；0付费请求，未切持久库HEAD。

回归初次 checks.log 的179项中仅撤退滞后测试失败：3秒经22.4Hz取整不足3秒，未产生请求；将采样点改为3.1秒后全过。未放宽生产限流。无静默回退；模型/API/格式/过期失败都有独立事件。本轮没有新增付费API请求，真实非思考副官响应速度与自然防守胜率需下一局记录。

补充验收：native_owned.json 单独确认4个实际被接管Marine分别移动7.73/8.72/7.04/7.68格，0引擎拒绝，游戏/墙钟1.01956倍；无画面引擎已关闭，native-match锁释放。最后增加“无可分配机动部队”显式事件和状态，checks_no_units.log 21项通过（与179项重叠，不相加）。汇总 logs/defense_adjutant_summary.json。

## 对局时限更新（2026-10-07）

用户要求每局上限90分钟。official_duel.py、opencode_match.py、native_llm.py、session_match.py 的默认 --seconds 统一为5400（开局完成后的墙钟秒）；自然胜负仍提前结束，技能/API请求/赛前准备超时不受此改动影响。原生对局报告新增 max_wall_seconds 记录实际参数。AI_SKILL_MANUAL 的启动示例已同步。静态编译及提取实际参数定义后的默认解析检查通过，证据 logs/match_duration_90min_check.json；没有启动游戏或调用API。旧对局归档脚本/历史日志保持原始值。

## 当前对局（2026-10-07：官方 DeepSeek 双方 + 防守副官，90分钟）

用户明确要求启动，已运行 official_duel.py --seconds 5400。运行目录 match-k9q3snxs，launcher PID 306613、exec session23400；原生目录 match-j0jo0eui，日志 /home/tyrion/Projects/wolf/sc2-arena/logs/official_defense_watch-x4pmumq7.log，独立中文正文窗口 PID 306756。双方直接用 DEEPSEEK_API_KEY、https://api.deepseek.com/v1、模型deepseek-flash；无OpenCode消费。各自主指挥low、防守副官thinking disabled，后者只在基地警报触发；实时、正常迷雾、60fps/VSync配置及90分钟上限保留。沿用同一已验收持久技能版本68a5e4ff7765…，未声称修复旧技能审计问题，未启用付费后台源码改写。

启动证据 logs/deepseek_official_defense_started.json 及运行目录started.json：双方已接收真实主指挥计划，规划/引擎错误均0；防守副官已启用，启动截面尚未遇敌所以请求0，不冒称真实防守LLM时延已测。运行留给用户观看；本轮未等待终局。实时结果应读当前match.json，run.json在过程中仍标starting_native_game。

启动进程核验补充：沙箱PID空间对宿主launcher/viewer查询会返回不存在，不代表进程已退出。已在宿主空间复核PID306613/306756均存活，started.json中存活字段已更正并标注process_check_scope；真实对局状态持续更新。


## 最新修复（2026-10-07：普通作战与副官补齐兴奋剂/坦克形态）

原观战局 match-k9q3snxs 已自然结束，P1 Defeat/P2 Victory；游戏1238.04秒，墙钟1213.08秒。未重开观战局。下一次 official_duel.py 默认 combat_micro=True，可 --without-combat-micro 显式关闭；其他 native_llm.py 调用需加 --combat-micro。realtime=True、正常迷雾、90分钟/60FPS配置、旧全局反射OFF保持，本轮付费模型调用0。

先验诊断 ../_probe/combat_micro_20261007/before.json：真实日志 logs/feedback/p1-btusgvlm.jsonl、p2-ukcli9uw.jsonl。P1无stim/morph计划；P2末期20辆移动形态坦克，没有架起计划，但曾在game732.23秒（12:12）实际给8Marine发stim，后续观察7个存活成员buff27，不能说从未打针。P2四次stim_push派遣分别有无可见敌人、50ms worker超时、成员损失、50ms超时，源码控制路径不稳定；主要使用attack_nearest/move_out，而这两种基础命令与旧副官均只会移动/攻击/停止。合成最小判别使用已研究stim的Marine+Tank，旧副官仅发两个3674攻击命令。

新增 arena/combat_micro.py，只服务有明确move_out/attack_nearest目标或副官授权的部队，不创造战略目标、不因伤兵全队撤退、不接管活跃Python/JSON/形态任务。Marine/Marauder研究完成、健康且近敌时个体打针；坦克遇地面目标/到达部署点架起，继续推进或副官撤后时先收起再走，失联有5秒滞后。先查真实可用能力，确认buff24/27或类型改变且变形订单消失，失败/不可用/交接未确认均有公开事件。主/副模型prompt明确能力与边界，独立中文窗口显示请求与实际结果。没有修改旧持久技能版本或宣称其全部问题已修。

最终证据 logs/combat_micro_summary.json；项目外 ../_probe/combat_micro_20261007/native.json、native.log、native-feedback/p1-fbs3fdxc.jsonl。真实原生SC2 5.0.15.97579、无画面、debug注入单位/升级、固定测试指挥、正常迷雾、realtime=True：5次能力指令，4次实际确认（Marine27、Marauder24、坦克架起、完全架起后收到主指挥move_out并收起），1次副官交还控制权前的收起未确认并明确记录；引擎拒绝0，能力失败0。坦克收起后前进3.183格，18.75游戏秒/18.370墙钟秒=1.02066倍。不是自然完整比赛/胜率/画面FPS/真实API延迟测试。checks_final.log：202项回归全过（18.309秒），含23项新增微操、原副官/三族/宏/反馈/Python隔离/修复/持久库。viewer_events.json仅为生产Feed读取真实10条微操事件，未声称新截图验收。cleanup.json在宿主确认SC2进程0、原生锁释放。

失败证据保留：native_attempt1为debug注入后立即读取尚未出现单位，探针改为等实际观察；native_defense_phase是旧“类型变化就确认”的较弱验证；native_attempt3发现3秒确认窗偏短，真实变形订单约3.17秒才结束，生产改为坦克6游戏秒/兴奋剂3游戏秒并加长变形回归。没有删失败或只看类型就宣称完整变形成功。自定义Python的50ms超时、成员损失及原技能审计问题仍独立待办。


## 当前观战局（2026-10-07：官方DeepSeek双方，新微操已启用）

用户要求修改后重开，已启动 official_duel.py --seconds 5400。运行目录 match-wt_2tct6，launcher PID314471、exec session99302，原生 match-eu599bhh，游戏PID314767/314773，中文窗口PID314613。日志 logs/official_micro_watch-3wztt6fk.log，启动证据 logs/deepseek_official_micro_started.json 和该run/started.json。双方直接官方deepseek-flash、DEEPSEEK_API_KEY，无OpenCode生成请求；同版已验收技能正常恢复。保持realtime=True、正常迷雾、60fps/VSync配置、90分钟上限、无固定脚本开局/无debug注入。双方combat_micro与defense实际开启，原全局反射OFF，后台源码改写未启用。

启动截面双方已接收真实计划 [5, 5]，规划错误 [0, 0]，引擎拒绝0；游戏/墙钟1.01007倍。仍是早期开局，尚不宣称自然比赛的兴奋剂/坦克实效或胜率。本轮不等待终局，保留双方原生窗口和中文输出让用户观看；实时状态看本run/match.json。宿主核验launcher/viewer及两个SC2实际命令行存活；SC2的comm为Play Main Threa，首次按sc2_x64进程名匹配为空不能当作没有游戏，已用/proc/cmdline纠正。无额外生成连通性测试。


## 当前状态（2026-10-07：控制进程中断，旧状态误显示运行中已修）

match-wt_2tct6 在最后实际回复game1038.705秒（17:18.7）附近收到SIGTERM；原始 logs/official_micro_watch-3wztt6fk.log 第一异常在arena.step/observe，第二次SIGTERM又打断Commander.stop/Defense.close。native_llm旧finally因而跳过P2停止、引擎关闭、录像与match.json收尾；official_duel的run.json已interrupted/KeyboardInterrupt: stop signal，match.json仍是game1030.8秒的running。不能称为模型卡住、额度耗尽或90分钟到时；信号发送者未记录，来源未知，不能将修复状态保存漏洞说成已根治外部终止。

宿主PID核验 ../_probe/match_stale_20261007/before.json：launcher314471消失，中文窗口314613和SC2进程314767/314773仍存活；项目锁已释放，因此以后不能只据锁空闲宣称无残留游戏。SC2 comm为Play Main Threa，须按实际/proc命令行匹配，不能按SC2_x64简称判断。当前未关残留进程、未重开、未继续调用API。原始match.json保留为本run/match_before_interrupt_status_repair.json，已按run日志+宿主事实纠正本run/match.json为interrupted，附来源与纠正时间，窗口显示停止。未伪造终局或保存成功的录像。

修改：arena/lifecycle.py提供原子JSON写入、首次信号中断/后续信号仅记录（清理不中途重入）、信号名/时间记录但sender仍未知。native_llm在清理前先保存终止状态，各清理步骤独立留错并继续，末尾再次保存、恢复原信号处理器；official_duel的run.json也原子写。输出窗口交叉核对run.json终态，25秒过期运行报告显示状态失联，不继续称实时；自然胜负字典也修正为可渲染，避免旧版.get(dict)异常。现有窗口读取已纠正的match.json即可显示停止，新版完整读状态机制下次加载生效。

最小判别先执行旧生产native_llm+假引擎/假client、两次真实self SIGTERM，复现仅stop_0且saved_outcome=running（reproduce_before.json）；不是自然SC2实验。修后checks_final.log共72项通过/3.056秒，其中真实两次SIGTERM验证终态先落盘、双方清理及引擎关闭均到达；另覆盖清理异常、原子写失败、失联/终局显示及副官/微操/投降回归。stopped_viewer.png为新窗口离屏读取本场真实日志的截图，已检查显示“对局已停止”与原始错误。汇总 logs/match_stale_diagnosis.json；所有本轮验证无付费API、无新游戏。

附本场实际微操结果（截至日志停止，不是胜率）：P1确认{'stim': 89, 'siege': 17, 'unsiege': 14}；P2确认{'stim': 61, 'siege': 13, 'unsiege': 10}，均来自combat_micro_confirmed原始事件。本场已有真实坦克形态与兴奋剂效果，但未自然结束，不记胜负。


## 当前观战局（2026-10-07：关闭残留后独立后台重开）

用户明确要求关旧局并开新局。旧match-wt_2tct6已通过API请求退出并对仍存活的精确旧游戏PID发送SIGTERM，旧游戏314767/314773和输出314613均退出；无广泛kill、用户U盘未改。关闭前成功保存68932字节 interrupted_before_close.SC2Replay，仅为孤立旧局关闭前录像，不算自然终局。清理证据 logs/official_micro_stopped_by_user.json。

新局 match-7i4qyg4y，原生 match-onkqe8tq；official_duel.py --seconds 5400，独立后台PID328415/session328415，输出窗口PID328557，游戏PID[328732, 328738]。不再依赖长时间exec会话，stdin为DEVNULL、stdout/stderr写 /home/tyrion/Projects/wolf/sc2-arena/logs/official_detached_watch-wiket9r_.log。先以3秒无API短进程确认原启动命令退出后独立会话仍完成，证据 ../_probe/match_stale_20261007/detach_check.json；新局宿主核验父进程已非短启动命令。之前SIGTERM发送者仍未知，不能据此宣称所有外部终止已根治。

双方仍官方 deepseek-flash / DEEPSEEK_API_KEY，防守副官+兴奋剂/坦克微操启用，旧全局反射OFF，正常迷雾/realtimeTrue/90分钟/60FPS配置、无固定脚本开局或debug注入，加载上次修复的原子终态保存与重复信号清理；无OpenCode调用，无付费源码修复。开局截面双方接受计划[4, 4]，规划错误[0, 0]，引擎拒绝0，游戏/墙钟1.01989倍；未等待终局，继续供用户观看。logs/deepseek_official_detached_started.json 和本run/started.json保留证据，实时看match.json，当前指针 logs/deepseek_official_duel_current.json。开启的是双玩家视角原生窗口，尚未实现第三观察者。


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


## 当前观战局（2026-10-08：官方DeepSeek双方，副官关闭，最慢速度）

用户要求开局，双方沿用官方deepseek-flash、https://api.deepseek.com/v1、DEEPSEEK_API_KEY；无OpenCode生成调用。运行 match-uj0ytzbm，原生 match-gyrqnnzw；独立后台launcher 55998/session同PID、中文输出窗口 56140、原生游戏 [56381, 56387]。命令 official_duel.py --seconds 5400 --game-speed slower --without-defense；防守副官不存在、不产生副官请求。仍保留combat_micro（明确进攻任务中的兴奋剂/坦克形态）、轨道能力、持久Python技能；旧全局战斗反射OFF。正常迷雾/realtimeTrue/60FPS与VSync配置/90分钟墙钟上限。无固定脚本开局或debug注入。保持本局供用户观看，未等待终局。

最慢档新增为slower（Galaxy c_gameSpeedSlower），AcropolisSlower.SC2Map单改MapScript.galaxy，原图和USB未动。先项目外原生无模型判别：20.0497墙钟秒191loops=9.5263loops/s，证据 ../_probe/slower_watch_20261008/speed_slower.json；探针复用旧faster期望所以该探针report的speed_check=DRIFT，是预期比较结果，并非把旧速度标签当新配置。生产随后接入9.6loops/s期望、地图hash检查、启动8秒双方逐一速度校验与最慢中文标签。正式本局校验双方9.59165loops/s；最新窗口110.57墙钟秒平均9.5230loops/s，speed_check=ok。此为模拟速度，不是画面FPS测量。

开局已接收双方真实计划[8, 8]，规划错误[0, 0]，引擎拒绝0，仅是启动截面；未宣称自然全局无错/胜率。19项速度/生命周期/配置回归通过（checks.log），未额外花费模型连通性测试。官方launcher报告/日志明确defense=OFF与game_speed=slower，避免“thinking disabled”误表示副官关闭。底层支持slower/normal/faster；通用CLI默认normal和official默认带副官未全局更改，本次及后续按用户最新偏好明确传上述参数。

启动证据 logs/deepseek_slower_no_defense_started.json、本run/started.json；原始日志 /home/tyrion/Projects/wolf/sc2-arena/logs/official_slower_no_defense-i0sm2hmp.log；实时状态看本run/match.json，当前指针logs/deepseek_official_duel_current.json。副本构建器支持--speed slower并读回对比只有脚本改变；数据hash与判别探针完全一致。双原生玩家窗口及独立中文输出已启动，仍不是第三观察者席位。


## 更新（2026-10-08：模型开局手册、扩张预算与运营反馈）

新增 arena/economy.py，模型收到本族经济/限时压制/防守三种条件式开局；没有自动固定开局。macro_plan(count=1) 明确 opening、target_bases、worker_target、expand_by_game_s、reserve_expansion、reserve_for_s(30..180)、exempt_products(<=8)、reason，全部必填，保持到模型修改；每回复最多一个，整条计划校验失败不部分应用。数据在 economy_report 中每轮独立于历史窗口/反馈分页发送。旧无tech_tree后端明确不支持、system不诱导调用；赛前实验室GAME_KNOWLEDGE和中文观战窗口也接入。文档SC2_GAME_KNOWLEDGE.md及AI_SKILL_MANUAL.md末尾说明合同和证据边界。

预留仅服务显式expand任务，采用本场tech_tree配方造价；第一个未发出的expand优先于可选支出，宏/JSON组合生产只能使用未预留的矿气。低于阶段工人数目标的工人、<=4余量的补给、模型指定产品可例外使用资金，故不保证任何时候都最快开矿。没有expand不存钱且可见告警；配方费用不唯一明确报告unsupported；预算到期、任务失败/取消、基地数量开工目标达成/发单后释放，未静默回退。允许cancel_expansion撤回未发出的扩张；已发出请求明确拒绝取消（不能虚称原生建筑被取消）。解除预算不撤销已有订单；续期需模型重新发计划。过期预算仍保留目标和旧expand任务。改计划在队列满时仍允许，迟到预算/取消指令需新观察而拒绝复用。

记录初次观察工人/经济、当前饱和度与目标进度、第二座基地首次观察开工/完工、采样卡人口/空闲工人秒/空闲基地秒/训练基地秒、请求支出分类、每30游戏秒检查点。观测间隔>2游戏秒部分计为未采样；虫族幼虫利用率为null，发单成本可包含拒绝/重试而非已扣款，两个基地不代表两个独立有效矿区。内存检查点20条/prompt6条，支出明细12条。玩家之间实例独立，固定手册在缓存前缀、动态经济报告在后缀。只因模型已有明确进攻任务的微操/副官设置未改。

先做旧队列最小判别，../_probe/macro_opening_20261008/before.json；后续同一受控场景comparison.json：300矿无预算先Marine能力560、二矿等钱；显式预算后先CC能力318。不是原生等额资源对抗。prompt_sample.json为生产payload格式的合成样本，progress_fixture.json为合成观察里程碑，并非原生建造时长。

最终 checks_latest.log：217项回归全通过，7.840秒（25项新增运营检查加既有系统；中间checks.log216项/8.025秒、checks_final.log66项为重叠，不相加）。覆盖同资金优先级、实时配方变400的适应、跨观察存钱、到期/取消/失败、必要生产例外、目标/饱和度/采样空档、schema/实际Commander请求和中文Feed、旧接口兼容，以及三族、Python隔离、微操、副官、轨道、生命周期等。无付费API测试。

旧观战 match-uj0ytzbm 在开发过程中自然结束（P1 Defeat/P2 Victory），未主动终止或自动重开。宿主确认原生锁和进程均释放后进行单玩家原生判别：native-runtime/match-lrpr6s1j、5.0.15.97579、Slower/realtimeTrue/normalfog/无画面。未注入前初始观察game8.5s仍8SCV、原始food_workers=8、实时CC配方300矿；用户告知今天更新为12工人，但本项目API运行环境本次没有观察到12，差异原因未定位，不能宣称已同步最新版。代码不写死8或12，也不把缓存造价当本场造价。

随后受控debug资源及友方SCV侦察视野（无敌方/全图揭示）后，用生产TaskQueue和显式宏计划实际建起第二座CC，观察progress=0.010625，0引擎拒绝；只验证开工，没等待建成，不是AI自然选开局/自主侦察/胜率。native_probe.json/log及native_start.json保留全部原始证据。测试结束关闭引擎，cleanup.json宿主进程0、锁空闲。0新增模型API测试；未修改USB、未升级pysc2/原生引擎/地图或重开付费对局。下一次启动加载新机制。

汇总 logs/macro_opening_summary.json；所有本轮探针在项目外 ../_probe/macro_opening_20261008/。


## 当前观战局（2026-10-08：官方DeepSeek TvZ，最慢速度，副官关闭）

用户要求启动人族对虫族供观看。已启动 official-runtime/match-krctza57，原生 native-runtime/match-kwvxhu_f；独立后台launcher/session65483，中文输出窗口65555，原生游戏PID65822/65828。宿主逐一确认存活。日志 logs/official_tvz_watch-gw33lms0.log，启动证据 logs/deepseek_official_tvz_started.json 和该run/started.json；当前指针logs/deepseek_official_duel_current.json。保留供观看，未等待终局/重启。

P1 Terran / P2 Zerg，双方直连官方deepseek-flash、同DEEPSEEK_API_KEY、https://api.deepseek.com/v1，各自独立上下文。realtimeTrue、Slower、normalfog、90分钟墙钟、60FPS/VSync配置；防守副官OFF、旧全局反射OFF、combat_microON（适用于明确任务的人族生化/坦克）。无debug资源或单位注入、无固定脚本开局。新开局手册/macro_plan/预算/运营反馈已实际出现在双方请求，两边均已选择economic计划；只证明请求/计划接通，不保证策略优良或完整比赛已验证。

official_duel.py新增--p1-race/--p2-race并明确传给native_llm。启动前读取持久库确认全部是Marine脚本；P1复用已验收DeepSeek来源的stim_push版本68a5e4...，P2明确使用本族宏指令、科技/经济/作战接口，不加载人族专用Python，没有虫族自定义微操库，不是静默回退。report记录player_skill_modes，P2prepared_strategy也明确能力边界，使用本族opening_playbook。先prepare-only的match-x0waw_kl本地恢复/隔离验收通过，0模型请求/无游戏；正式局重新验收后才发车。没有修改持久库版本或伪造模型技能作者。

启动截面 accepted_plans=[12,14]、planner_errors=[0,0]、引擎拒绝0；task failed=[0,2]，不是全程零错。P2初始train_worker/drone和build_supply/overlord都发生“macro order was not observed within 8 seconds”，原始logs/feedback/p2-vfvb0rzc.jsonl保留macro_failed及task_status。随后真实观察Drone从8到12、Overlord从1到3，说明生产实际发生；确认路径问题尚未排查修复，不应把错误说成引擎拒绝或生产完全失效，也不能假称已修。已向用户明确说明。本场正常执行继续供观看；尚无自然胜负、完整造兵链/虫族全部技能验证。

原生初始观察仍双方8工人，本场未尝试升级安装或修改地图开局。最后速度窗口约9.53loops/s、游戏/墙钟0.4255，期望9.6/0.42857，speed_check=ok；这是模拟速度不是显示FPS。观看仍是两个原生玩家视角窗口和中文正文窗口，不是第三观察席位。
