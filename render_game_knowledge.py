"""Render the distributable handbook from the same engine-bound catalogue as the models."""
import json
from pathlib import Path
from arena.knowledge import cached_tree, ECONOMY, RACES
from arena.python_skills import KINDS

INTRO = """# 星际争霸 II 三族指挥与技能手册

范围：普通多人对战的人族、虫族、神族，不含战役、合作指挥官及自定义 MOD。以下目录来自已读取的原生 5.0.15.97579 元数据快照；每场游戏仍重新读取引擎并查询可用能力。目录存在不等于任意时刻可执行，也不代表每项均已实战验收。

## 宏任务与经济操作

LLM 决定经济、科技、阵容及作战时机；脚本选择单位、查询合法位置、等待资源和前置，观察实际完成。没有自动替模型选择科技路线。

| 指令 | 参数及语义 |
|---|---|
| train_worker | count=1..20，生产本族工人 |
| build_supply | 本族人口单位/建筑，外层 count=1 |
| train_unit | args.unit 使用本族目录名，count=1..20 |
| build_structure | args.structure 使用本族目录名，可选 base=home/expansion/观察里的 site_N，外层 count=1 |
| research | args.upgrade 使用目录名，外层 count=1；看到升级完成才算成功 |
| morph_unit | args.unit 使用可切换形态名；可选 target=home/rally/enemy_start/nearest_enemy；count=1..20 |
| gather_gas | args.workers_per_refinery=0..3，每口已建成气矿持续维持此分配；0 表示释放回矿 |
| expand | 在已知矿区建新基地；可选 args.base=expansion/观察里的 site_N |
| gather | 将过饱和矿区的多余工人分配到缺工基地 |

一般 args={}。生产与形态切换的 count 是期望产品数量，跳虫等按实际批次生产，可能超过奇数请求。建筑、科研、采气、开矿等外层 count 必须为1。计划中的不同产品可并存，同一活跃意图会合并。等待不会自动补建缺失前置；模型需显式安排它们。

**付费变异使用生产接口**：火蟑螂、潜伏者、巢虫领主、监察王虫用 train_unit；虫穴、主巢、巨型尖塔、轨道指挥用 build_structure。morph_unit 只用于目录里的免费可逆形态，例如架起/解架、埋地/出土、升降及折跃门转换。

核弹 nuke 和拦截机 interceptor 是补充库存任务，count 只能为1。核弹通过本方幽灵出现核打击能力来确认，缺少幽灵时会明确等待。航母机库数量在当前观察中不能直接完整读取；满库存由无异常状态、空闲航母的能力查询推断，并明确记录推断依据，不报告虚构的精确数量。

## Python 局部战术

准备1..4个技能，manifest.unit 使用本族单位名，或 army/worker。army 包含常规作战与支援单位，排除工人、建筑、王虫、幼虫、核弹、机库拦截机和短命召唤物等；这些单位可用各自具体选择器。错误种族的选择器在准备验收时拒绝。

manifest.actions 可声明 cast。动作形如 {"action":"cast","unit_tags":[tag],"ability":"ctx.available_spells中实际名称","target":本地tag或[x,y]}。无目标法术省略 target，其他目标类型严格按 available_spells 填写。禁止以 cast 绕过付费生产和科研；实际下令前再次检查单位归属、局部范围、能量/冷却/能力。每个 tick 每单位最多1条指令，战术目标限分配小队20格内。

ctx.units 包含当前可见的已分配单位，并带 energy/energy_max、cargo_taken/cargo_max、passengers、buffs 和 orders；ctx.available_spells 是按当前能力查询过滤的名称、ID与目标类型。装入运输机的队员移入 ctx.embarked（tag/transport_tag/pos），不会直接判为死亡；需等待卸载后再命令该队员。运输机可作为局部观察的中心，但未分配的运输机仍不能下令。任务结束/失败对仍可观察到的非建筑队员请求 Stop；这不是引擎停止确认。

兴奋剂支持陆战队员和掠夺者，研究及生命值必须满足。坦克需先解架才能前进。法术源码由模型编写和隔离测试；已有的 JSON 顺序工作流仍使用原来的有限选择器，三族宏与 Python 才是本次扩展接口。完整 Python 合同见 AI_SKILL_MANUAL.md 第0节。
"""

def render():
    tree=cached_tree();parts=[INTRO,'## 经济与阵容知识','\n\n'.join(ECONOMY.values()),
        '完整 Python 选择器：'+', '.join(sorted(KINDS))]
    for race,label in [('terran','人族'),('zerg','虫族'),('protoss','神族')]:
        c=tree.public(race,include_spells=True)
        parts.extend(['## '+label+'（'+race+'）',RACES[race]])
        for section,key in [('单位与库存','units'),('建筑与附属建筑','structures')]:
            parts.append('### '+section)
            for e in c[key]:
                parts.append('- **'+e['name']+'**：'+e['role']+'\n  配方：`'+json.dumps(e['recipes'],ensure_ascii=False,separators=(',',':'))+'`')
                if e.get('spells'):parts[-1]+='\n  局部能力候选（实时可用看 ctx.available_spells）：'+', '.join(s['name'] for s in e['spells'])
        parts.append('### 辅助单位与形态用途')
        parts.extend('- **'+e['name']+'**：'+e['role'] for e in c['auxiliary_units'])
        parts.append('### 科研')
        for e in c['research']:
            parts.append('- **'+e['name']+'**：'+e['effect']+'\n  配方：`'+json.dumps({k:v for k,v in e.items() if k not in ('name','effect')},ensure_ascii=False,separators=(',',':'))+'`')
        parts.extend(['### 可切换形态',', '.join(c['forms'])])
    parts.append("""## 数据来源与验证边界

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
""")
    return '\n\n'.join(parts)+'\n'

if __name__=='__main__':
    target=Path(__file__).with_name('SC2_GAME_KNOWLEDGE.md');target.write_text(render());print(str(target),target.stat().st_size,'bytes')
