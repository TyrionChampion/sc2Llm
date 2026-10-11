"""Bounded, observation-only technology advice; never issues orders or claims live availability."""
from collections import Counter
import re

# Unit names and API research identifiers come from the bound engine catalogue.
FOCUS = {
 'metabolic_boost':('狗速','zergling','以跳虫侦察、包夹或防守时尽早考虑；移动升级不是攻速升级。'),
 'centrifical_hooks':('毒爆虫速度','baneling','准备使用毒爆时提前考虑；先满足虫穴与毒爆巢前置，帮助接近和包夹。'),
 'adrenal_glands':('跳虫攻速','zergling','后期继续以跳虫为主力时考虑；与狗速分开研究。'),
 'glial_reconstitution':('蟑螂速度','roach','蟑螂主力的调动、追击和撤退；先满足虫穴前置。'),
 'tunneling_claws':('蟑螂埋地移动','roach','需配合埋地科技和明确的钻地/出土控制；有侦测时不能假设安全。'),
 'grooved_spines':('刺蛇射程','hydralisk','刺蛇阵容提高输出距离；仍需前排与集结。'),
 'muscular_augments':('刺蛇速度','hydralisk','刺蛇主力的移动升级；不能代替避开范围伤害。'),
 'frenzy':('刺蛇主动机动能力','hydralisk','研究效果和可施放能力以本场数据为准；若需要主动施放，另派Python技能。'),
 'adaptive_talons':('潜伏者钻地强化','lurker','配合明确的钻地/出土命令；研究本身不会替你部署潜伏者。'),
 'lurker_range':('潜伏者射程','lurker','潜伏者主力提高阵地压制距离。'),
 'pneumatized_carapace':('王虫速度','overlord overseer','需要侦察与调动时考虑；提高机动不代表无敌或全图视野。'),
 'burrow':('埋地','zergling roach baneling infestor','研究后仍需morph_unit或已验收Python控制；多数埋地单位不能攻击。'),
 'neural_parasite':('神经寄生','infestor','解锁后需要施法与保护施法者，不是被动伤害升级。'),
 'chitinous_plating':('雷兽护甲','ultralisk','雷兽作为前排时考虑。'),
 'anabolic_synthesis':('雷兽速度','ultralisk','雷兽主力提高接敌能力；仍需路线与阵型。'),
 'lock_on':('旋风锁定相关强化','cyclone','版本相关科技，具体作用服从本场目录；不能凭旧版本印象假设收益。'),
 'medivac_caduceus_reactor':('医疗运输机相关强化','medivac','能量/治疗相关强化按当前版本，仍需保护运输机和实际治疗。'),
 'corvid_reactor':('渡鸦能量相关强化','raven','按当前引擎版本与施法需求选择。'),
 'hi_sec_auto_tracking':('防御建筑射程','missile_turret planetary_fortress auto_turret','采用相关静态防守时考虑；不代替机动部队。'),
 'terran_structure_armor':('建筑护甲','bunker missile_turret planetary_fortress','静态防守体系按需强化。'),
 'stimpack':('兴奋剂','marine marauder','生化主力关键科技；完成后需实际施放并配合治疗。'),
 'combat_shield':('战斗盾牌','marine','陆战队主力提高生存；不会赋予神族护盾。'),
 'concussive_shells':('震撼弹','marauder','掠夺者减速强化，配合生化拉扯。'),
 'infernal_preigniter':('蓝火','hellion hellbat','恶火/恶蝠对轻甲强化。'),
 'drilling_claws':('寡妇雷钻地强化','widow_mine','配合部署与重新定位，研究不会自动钻地。'),
 'smart_servos':('机械变形强化','hellion hellbat thor viking_fighter viking_assault','变形单位阵容按需研究；不是所有机械单位通用移速。'),
 'cloaking_field':('女妖隐形','banshee','完成后仍要开启隐形并看能量，敌方侦测可揭示。'),
 'hyperflight_rotors':('女妖速度','banshee','女妖机动阵容。'),
 'personal_cloaking':('幽灵隐形','ghost','完成后仍需施放、能量和避开侦测。'),
 'weapon_refit':('大和炮','battlecruiser','完成后需指定有效目标施放；不是自动普攻。'),
 'advanced_ballistics':('解放者射程','liberator','配合对地模式部署；研究不会自动架设。'),
 'interference_matrix':('干扰矩阵','raven','研究后需主动施法，合法目标以引擎为准。'),
 'warp_gate_research':('折跃门','zealot stalker adept sentry high_templar dark_templar','完成后用morph_unit转换传送门，再考虑供能与冷却。'),
 'charge':('冲锋','zealot','狂热者接敌的核心科技；不解决独自冲进敌阵的问题。'),
 'blink':('闪烁','stalker','完成后需战术脚本选择局部落点、保留撤退路径。'),
 'resonating_glaives':('使徒攻速','adept','使徒阵容按需考虑。'),
 'psi_storm':('灵能风暴','high_templar','完成后需脚本处理目标、能量与重复施放，不能只攻击移动。'),
 'shadow_strike':('暗影步','dark_templar','主动机动能力，具体可用性看本场能力目录。'),
 'extended_thermal_lance':('巨像射程','colossus','巨像主力远程输出强化。'),
 'gravitic_drive':('棱镜速度','warp_prism','运输与多线调动，另需装卸控制。'),
 'gravitic_booster':('观察者速度','observer','侦察与侦测调动，注意保护。'),
 'anion_pulse_crystals':('凤凰射程','phoenix','凤凰阵容按需考虑。'),
 'void_ray_speed_upgrade':('虚空舰速度','void_ray','虚空舰机动阵容。'),
 'tempest_ground_attack_upgrade':('风暴舰对建筑强化','tempest','主要用于攻坚，不要误当对所有目标通用增伤。'),
}
GROUPS={
 'zerg_melee_weapons': 'zergling baneling ultralisk',
 'zerg_missile_weapons':'roach ravager hydralisk lurker',
 'zerg_ground_armors':'zergling baneling roach ravager hydralisk lurker ultralisk queen infestor',
 'zerg_flyer_weapons':'mutalisk corruptor brood_lord',
 'zerg_flyer_armors':'mutalisk corruptor brood_lord viper',
 'terran_infantry_weapons':'marine marauder reaper ghost',
 'terran_infantry_armors':'marine marauder reaper ghost',
 'terran_vehicle_weapons':'siege_tank hellion hellbat cyclone thor',
 'terran_ship_weapons':'viking_fighter banshee liberator battlecruiser',
 'terran_vehicle_and_ship_armors':'siege_tank hellion hellbat cyclone thor widow_mine viking_fighter banshee liberator battlecruiser medivac raven',
 'protoss_ground_weapons':'zealot stalker adept sentry dark_templar archon immortal colossus',
 'protoss_ground_armors':'zealot stalker adept sentry high_templar dark_templar archon immortal colossus disruptor',
 'protoss_air_weapons':'phoenix void_ray carrier tempest',
 'protoss_air_armors':'phoenix void_ray carrier tempest oracle',
 'protoss_shields':'zealot stalker adept sentry high_templar dark_templar archon immortal colossus disruptor phoenix void_ray carrier tempest',
}
RULES=[
 '研究用research args.upgrade精确标识符；不必为狗速/毒爆速度编写Python，也不是morph_unit。',
 '升级效果分为被动强化与解锁主动能力；研究完成不等于已经施法、变形或部署。',
 '按照当前阵容和下一阶段计划选科技；不是逐项把所有升级研究完，紧急防守/人口/工人/扩张可优先。',
 'technology_report只根据己方观察检查。提议前检查active_tasks，避免重复等待任务；能力查询、控制权和预留资金由执行器最终判定。',
 '没有瓦斯：显式造本族气矿并gather_gas；没有前置：显式build_structure或research，不要只重复提交卡住的升级。',
 '只有world.upgrades包含升级才算完成；产房订单消失可能是取消或建筑被毁，不是假定研究成功。',
]
ABILITY_NOTES={
 'zerg':[
  '女王注卵、菌毯、治疗是主动能力，不属于research。需已登记Python cast技能；缺少时仅在live_skill_development.enabled=true时请求develop_skill。注卵选queen_inject验收。',
  '潜伏者需要钻地才能输出；用tech_tree.forms中的形态，或明确派遣Python部署脚本。',
  '毒爆爆炸属于战斗控制；升级速度不会自动选择值得交换的敌军。研究/控兵由不同层执行。',
  '火蟑螂胆汁、感染虫真菌/神经寄生、飞蛇拉人/遮蔽云等需局部施法脚本，不能将攻击移动当成已施放。',
  '监察王虫可侦测，王虫不能代替侦测；升级王虫速度不等于获得侦测。'],
 'terran':[
  '轨道升级用build_structure orbital_command；矿骡/扫描/补给分别用call_mule/scan/supply_drop。',
  '兴奋剂和坦克形态可由已启用combat_micro在显式作战任务内处理；自定义Python任务需自己处理，不要假设全局自动接管。',
  '运输装卸、幽灵EMP、渡鸦技能、女妖隐形、大和炮等需已验收Python cast控制，只有完成相关研究不够。'],
 'protoss':[
  '闪烁、风暴、力场、守护者之盾、时空加速和运输装卸需要主动能力接口/已验收Python脚本；以可调用目录和引擎能力为准。',
  '折跃门科技完成后仍要morph_unit转换；必须有供能、冷却与合法折跃位置。'],
}

def family(name):
    return re.sub(r'_level[123]$','',name)

def guidance(name):
    if name in FOCUS:
        label,units,note=FOCUS[name];return dict(label=label,benefits=units.split(),when=note)
    group=family(name)
    if group in GROUPS:
        return dict(label=name,benefits=GROUPS[group].split(),
            when='主力对应类别攻防；先一级再二级三级，按本场前置与阵容安排。')
    return dict(label=name,benefits=[],when='用途未编写，不凭名称猜测效果；参照当前引擎目录。')

def playbook(tree,race):
    entries=[e for e in tree.research.values() if e['race']==race]
    return dict(rules=RULES,ability_notes=ABILITY_NOTES[race],
        upgrades=[dict(upgrade=e['name'],**guidance(e['name'])) for e in entries],
        scope='conditional guidance, not an automatic build order')

def report(tree,w,limit=12):
    # Match alternate forms through engine aliases, but report each actual unit once.
    observed=[]
    for u in w.my_units:
        actual=tree.names.get(u.type_id,str(u.type_id));names={actual}
        meta=tree.types.get(u.type_id)
        if meta is not None:
            names.update(tree.names[a] for a in ({meta.unit_alias}|set(meta.tech_alias)) if a in tree.names)
        observed.append((actual,names))
    rows=[];completed=[];in_progress=[]
    for e in tree.research.values():
        if e['race']!=w.race:continue
        name=e['name'];hint=guidance(name)
        beneficiaries=dict(Counter(actual for actual,names in observed if names.intersection(hint['benefits'])))
        if e['id'] in w.upgrades:
            if beneficiaries:completed.append(name)
            continue
        researching=[u.tag for u in w.my_units for r in e['recipes']
            if u.type_id==r['producer'] and any(tree.canonical_ability(a)==tree.canonical_ability(r['ability']) for a in u.orders)]
        if not beneficiaries and not researching:continue
        alternatives=[]
        for r in e['recipes']:
            blockers=[]
            producers=[u for u in w.my_units if u.type_id==r['producer'] and u.done]
            if not producers:blockers.append('need completed '+tree.names[r['producer']])
            if r.get('required_building') and not any(u.done and tree.matches(u.type_id,r['required_building']) for u in w.my_units):
                blockers.append('need completed '+tree.names[r['required_building']])
            if r.get('required_upgrade') and r['required_upgrade'] not in w.upgrades:
                blockers.append('need upgrade '+tree.upgrade_names[r['required_upgrade']])
            if producers and all(u.orders for u in producers):blockers.append('producer busy')
            if r.get('requires_power') and producers and not any(u.powered for u in producers):blockers.append('producer unpowered')
            alternatives.append(dict(producer=tree.names[r['producer']],blockers=blockers))
        best=min(alternatives,key=lambda a:len(a['blockers']))
        lacking=dict(minerals=max(0,e['minerals']-w.minerals),gas=max(0,e['gas']-w.vespene))
        state='researching' if researching else 'needs_prerequisites_or_idle_producer' if best['blockers'] else 'needs_resources' if any(lacking.values()) else 'observed_prerequisites_ready'
        if researching:in_progress.append(name)
        row=dict(upgrade=name,label=hint['label'],beneficiaries=beneficiaries,status=state,
            recipes=alternatives,resource_shortfall=lacking,
            command=dict(skill='research',args=dict(upgrade=name)))
        # Specific unit tech before generic levelled upgrades; suppress higher-level proposals until prior level.
        blocked_prior=all(any(b.startswith('need upgrade ') for b in a['blockers']) for a in alternatives)
        priority=(0 if researching else 2 if blocked_prior else 1,
                  0 if name in FOCUS else 1,-sum(beneficiaries.values()),name)
        rows.append((priority,row))
    rows.sort(key=lambda r:r[0])
    return dict(game_s=round(w.seconds,1),candidates=[r for _,r in rows[:limit]],
        omitted=max(0,len(rows)-limit),in_progress=sorted(set(in_progress)),
        relevant_completed=sorted(completed),
        evidence='Owned observations only; no live ability query. Check active_tasks before enqueue. Ready means observed prerequisites only, not engine acceptance or unreserved funds.')
