"""Chinese strategy reference supplied to both battle decisions and developer labs."""
from arena.tech_tree import ROLES
ECONOMY={
 'general':'矿物与瓦斯是不同资源。采气先造在喷泉上的气矿，再显式 gather_gas 分配工人；只有气矿不会自动采气。一般每口气矿3工人，按当前战术调整0..3。不要因早期瓦斯为0就永远只造矿兵。',
 'workers':'持续生产工人，同时看每个基地和气矿的 assigned/ideal_harvesters；不要把所有工人塞在主矿。采矿工人能提高后续收入，过量采气会损失矿物收入。',
 'expand':'expand 在可观察矿区建新基地，需要资源、地形、视野和防守。完成后继续生产工人；gather 将过饱和矿区的多余工人转移到缺工分矿。新基地本身不等于额外收入。',
 'supply':'预留人口。build_supply 对人族造补给站、神族造水晶塔、虫族造王虫；供给建造需要时间。人口上限200。',
 'tech':'科技前置不会凭空出现：明确安排建筑、附属建筑、采气、升级和部队。宏任务会等待缺失前置，不会代替你决定科研路线。不要重复提交相同活动任务。升级只有出现在 world.upgrades 才能认为完成。',
 'combat':'根据侦察到的地面/空中、轻甲/重甲、隐形、范围伤害选择组合；混合输出、治疗、侦测和控制。单一枪兵可能适合一段时机，不应成为接口限制。侦测单位通常输出弱，需要保护。'}
RACES={
 'terran':'SCV建造期间持续工作，可用新SCV接手未完成建筑；气矿为refinery。兵营→重工厂→星港，科技实验室解锁高级兵，反应堆增加基础单位并行产能；二者占同一附属位置。轨道指挥可用矿骡和扫描，行星要塞偏防御。生化适合机动和医疗运输机配合；坦克架起后射程/范围压制但不能移动；机械化通常更耗瓦斯。升级步兵攻防与兴奋剂/战斗盾牌，或机械/飞船攻防，按主力选择。',
 'zerg':'幼虫生产工人、王虫和多数兵种；建筑消耗工蜂，记得补工人。女王可注卵、铺菌毯和治疗。建筑多依赖菌毯，气矿为extractor。孵化场→虫穴→主巢解锁科技；变异会消耗原单位，例如蟑螂→火蟑螂、刺蛇→潜伏者、腐化者→巢虫领主。不要把总兵种价值当变异付款。王虫提供人口，监察王虫提供侦测。经济与兵力共享幼虫，要按威胁取舍。',
 'protoss':'探机开启折跃后可继续采矿；多数建筑和传送门折跃点需要水晶塔/相位棱镜供能，基地和同化炉例外。气矿为assimilator。控制核心、暮光议会、机械台/机场分科技路线；折跃门科技完成后用morph_unit转换传送门，折跃受供能与冷却限制。高阶圣堂/黑暗圣堂两名合成执政官。观察者侦测、运输棱镜机动、哨兵和施法单位需要微操，不只是攻击移动。'}
EFFECTS={
 'stimpack':'解锁陆战队员/掠夺者兴奋剂，消耗生命换取短时攻速和移速；研究后仍要施放，配合治疗。',
 'combat_shield':'提高陆战队员生命上限。','concussive_shells':'掠夺者攻击可减速符合条件的目标。',
 'hi_sec_auto_tracking':'提高人族防御建筑的射程。','terran_structure_armor':'提高建筑护甲。',
 'lock_on':'旋风锁定相关强化；具体效果服从当前地图版本。','drilling_claws':'强化寡妇雷钻地。',
 'infernal_preigniter':'提高恶火/恶蝠对轻甲威胁。','smart_servos':'加快机械单位模式转换。',
 'weapon_refit':'战列巡航舰大和炮相关科技。','advanced_ballistics':'解放者对地模式射程强化。',
 'medivac_caduceus_reactor':'医疗运输机能量/治疗相关强化，依当前版本。','personal_cloaking':'幽灵隐形。',
 'cloaking_field':'女妖隐形。','hyperflight_rotors':'女妖移动强化。','interference_matrix':'渡鸦干扰矩阵科技。','corvid_reactor':'渡鸦能量相关强化。',
 'centrifical_hooks':'毒爆虫机动强化。','burrow':'解锁虫族埋地；埋地不自动等于能攻击。',
 'pneumatized_carapace':'王虫移动速度强化。','grooved_spines':'刺蛇射程强化。','muscular_augments':'刺蛇移动强化。',
 'frenzy':'刺蛇狂暴/机动技能相关科技；以当前引擎可用能力为准。','neural_parasite':'感染虫神经寄生。',
 'adaptive_talons':'潜伏者钻地强化。','lurker_range':'潜伏者射程强化。','glial_reconstitution':'蟑螂移动强化。',
 'tunneling_claws':'蟑螂埋地移动相关强化。','adrenal_glands':'跳虫攻击速度强化。','metabolic_boost':'跳虫移动速度强化。',
 'anabolic_synthesis':'雷兽机动强化。','chitinous_plating':'雷兽护甲强化。',
 'warp_gate_research':'解锁折跃门转换；仍需要转换、供能和冷却。','shadow_strike':'黑暗圣堂武士暗影步。',
 'anion_pulse_crystals':'凤凰射程强化。','tempest_ground_attack_upgrade':'风暴战舰对建筑伤害强化。',
 'void_ray_speed_upgrade':'虚空辉光舰移动强化。','extended_thermal_lance':'巨像射程强化。',
 'gravitic_drive':'运输棱镜移动强化。','gravitic_booster':'观察者移动强化。','psi_storm':'高阶圣堂武士灵能风暴；研究后需施法。',
 'resonating_glaives':'使徒攻速强化。','blink':'追猎者闪烁。','charge':'狂热者冲锋。'}
def effect(name):
 if name in EFFECTS:return EFFECTS[name]
 if 'weapons_level' in name:return '提升名称指定兵种类别的武器伤害；高等级需要前一级与科技前置。'
 if 'armors_level' in name:return '提升名称指定兵种类别的护甲；高等级需要前一级与科技前置。'
 if 'shields_level' in name:return '提高神族护盾护甲；高等级需要前一级与科技前置。'
 return '引擎列出的科技；详细效果未编写，请勿凭名称假设已解锁能力。'
def model_knowledge(tree,w):
 from arena.economy import OPENINGS
 from arena.technology import playbook,report
 return dict(technology_playbook=playbook(tree,w.race),technology_report=report(tree,w),economy=ECONOMY,race_notes=RACES[w.race],opening_playbook=OPENINGS[w.race],
  visible_enemy_roles={tree.names[u.type_id]:ROLES.get(str(u.type_id),{}).get('role','建筑或未收录形态；参照侦察') for u in w.enemy_units},
  principle='你决定经济、科技、阵容和作战时机；宿主仅执行已派遣任务和局部技能。')

from functools import lru_cache
@lru_cache(maxsize=1)
def cached_tree():
 from pathlib import Path
 from s2clientprotocol import sc2api_pb2
 from arena.tech_tree import TechTree
 data=sc2api_pb2.ResponseData();data.ParseFromString(Path(__file__).with_name('native_catalog.pb').read_bytes())
 return TechTree(data,'native 5.0.15.97579 verified metadata snapshot; not a live availability check')

BUILDINGS={
 'command_center':'生产SCV、交矿与开矿；可升级轨道指挥或行星要塞。','orbital_command':'轨道指挥，矿骡采矿和扫描侦测。','planetary_fortress':'基地防御升级，偏地面防守。',
 'supply_depot':'提供人口，可降下让路。','barracks':'人族步兵生产。','factory':'地面机械生产。','starport':'人族空军生产。',
 'refinery':'建在瓦斯喷泉，需派SCV采气。','engineering_bay':'步兵攻防和建筑防御科技。','armory':'机械/飞船攻防及高级机械前置。','fusion_core':'战列巡航舰等高级空军科技。','ghost_academy':'幽灵科技与核弹。',
 'bunker':'载入步兵进行阵地防御；空地堡没有步兵火力。','missile_turret':'防空和侦测隐形。','sensor_tower':'预警，不能代替侦测隐形。','auto_turret':'渡鸦临时炮塔。',
 'hatchery':'虫族交矿、幼虫、女王与分矿。','lair':'二本科技，保留孵化场功能。','hive':'三本科技，保留基地功能。','extractor':'建在喷泉，需派工蜂采气。',
 'spawning_pool':'跳虫、女王和基础虫族科技前置。','evolution_chamber':'地面攻防。','roach_warren':'蟑螂生产与升级。','baneling_nest':'毒爆虫变异和升级。','hydralisk_den':'刺蛇与升级。','lurker_den':'潜伏者变异和升级。',
 'infestation_pit':'感染虫、虫群宿主、主巢前置。','spire':'飞龙、腐化者及空军攻防。','greater_spire':'巢虫领主变异与空军升级。','ultralisk_cavern':'雷兽及升级。',
 'spine_crawler':'对地静态防御，可拔起移动，扎根需合法地形。','spore_crawler':'防空和侦测，可拔起移动。','nydus_network':'虫道运输网络。','nydus_canal':'虫道出口，配合装载/卸载。','creep_tumor':'扩展菌毯。','creep_tumor_queen':'女王铺设的菌毯肿瘤。',
 'nexus':'生产探机、交矿、分矿与能量技能。','pylon':'提供人口及供能，保护关键科技的供能。','assimilator':'建在喷泉，需派探机采气。','gateway':'基础地面部队生产，可研究后转换折跃门。','cybernetics_core':'追猎者等单位、折跃门、空军攻防。','forge':'地面攻防与护盾科技、防御炮台前置。',
 'twilight_council':'冲锋、闪烁、使徒强化及圣堂科技前置。','templar_archive':'高阶圣堂与灵能风暴。','dark_shrine':'黑暗圣堂与暗影步。','robotics_facility':'机械地面、运输、侦测单位。','robotics_bay':'巨像、干扰者与机械升级。','stargate':'空军生产。','fleet_beacon':'高级空军及科技。','photon_cannon':'对空对地防御和侦测，需供能。','shield_battery':'护盾恢复，需要能量和供能。','stasis_trap':'先知的停滞陷阱。'}
def building_role(name):
 if name.endswith('_tech_lab'):return '挂接科技实验室：解锁对应生产建筑高级单位和科研；必须附在正确建筑上。'
 if name.endswith('_reactor'):return '反应堆：提高对应基础单位生产并行容量；不解锁科技实验室单位。'
 return BUILDINGS.get(name,'当前引擎生产/科技节点；用途未收录。')
