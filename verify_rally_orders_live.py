"""原生验证:显式每建筑集结点是否真的生效,且不被自动集结点覆盖。

这是判别实验的"真实环境"半边:test_rally_orders.py 是受控 fixture,只能证明代码路径;
这里在真实 SC2 引擎里核对 raw.rally_targets,回答三个问题:

1. 引擎接受对指定产能建筑下发的 GeneralRallyUnits(命令不带坐标)-> 该建筑的
   rally_targets 是否变成指定点(用 DefensiveRally 的同一判据:<1 tile)。
2. 指定点与自动集结点(主矿坡口/战术锚点)不同时,显式点是否保持、不被改回。
3. 未指定的另一个产能建筑是否仍由自动逻辑管理(即显式订单不是全局开关)。

不调用任何模型。结果写入 logs/rally_orders_native.json。
"""
import json
import math
import time
import traceback
from pathlib import Path

from absl import app

from arena.commander import Commander
from arena.native_engine import NativeArena
from arena.rally import PRODUCERS, producer_inventory


class Idle:
    def ready_for_request(self):return False
    def chat(self,*args):raise AssertionError('fixture must never call a model')


def main(argv):
    report=dict(fixture=True,model_calls=0,checks={},notes=[])
    commander=None;arena=None
    destination=Path('logs/rally_orders_native.json')
    def save():destination.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    try:
        with NativeArena(names=('RallyOrder Zerg fixture','Idle Terran fixture'),races=['zerg','terran'],render=False) as arena:
            report.update(version=arena.version,native_directory=str(arena.directory))
            worlds=arena.step([[],[]])
            commander=Commander(arena,0,Idle(),opening=False,tactics_enabled=True,
                                feedback_dir='logs/rally-orders-feedback')
            def tick():
                nonlocal worlds
                batch=commander.tick(worlds[0])
                worlds=arena.step([batch,[]]);commander.ingest_feedback(worlds[0])
                return batch
            def until(predicate,seconds=10):
                end=time.monotonic()+seconds
                while time.monotonic()<end:
                    tick()
                    if predicate():return True
                return False
            # 让虫族孵化场自然出现(不伪造 unit,只推进真实游戏)
            until(lambda:any(u.type_id in PRODUCERS for u in worlds[0].my_units),30)
            producers=[u for u in worlds[0].my_units if u.type_id in PRODUCERS and u.done]
            assert producers,('no native producer observed',worlds[0].summary())
            report['producers']=[dict(tag=u.tag,type_id=u.type_id,pos=[u.x,u.y]) for u in producers]
            inventory,labels=producer_inventory(arena,worlds[0],0)
            report['inventory']=inventory
            assert inventory,('empty producer inventory',report['producers'])
            target_label=inventory[0]['label']
            # 与自动集结点不同的显式点:主矿往己方后方退 10 格,确保不是自动 anchor
            grid=commander.rally.grid
            assert grid is not None,'defensive rally has no static grid; cannot pick an explicit point'
            home=arena.homes[0]
            explicit=grid.nearest((home[0]-10,home[1]))
            explicit=(explicit[0]+.5,explicit[1]+.5)
            report['explicit_point']=list(explicit)
            commander.tasks.enqueue([{'skill':'rally_units','args':{
                'producers':[target_label],'target':[home[0]-10,home[1]],
                'duration_s':600,'reason':'原生验证:显式指定集结点'}}],worlds[0].seconds)
            # 引擎按格心夹取坐标(nearest 把浮点截断成格号),所以判据用订单解析出的实际点,
            # 而不是夹取前的估算点;两者可能相差一格以上。
            until(lambda:bool(commander.tasks.rally_orders()),5)
            order=commander.tasks.rally_orders()
            actual=tuple(order[0].program.point) if order and order[0].program.point else None
            report['requested_point']=list(explicit)
            report['applied_point']=list(actual) if actual else None
            assert actual is not None,('order did not resolve a point',report)
            ok=until(lambda:any(u.tag==inventory[0]['tag'] and any(
                math.dist(t[:2],actual)<1 for t in u.rally_targets) for u in worlds[0].my_units),20)
            program=order[0].program if order else None
            report['order_public']=program.public() if program else None
            report['raw_rally_targets']={u.tag:[list(t[:2]) for t in u.rally_targets]
                                        for u in worlds[0].my_units if u.tag==inventory[0]['tag']}
            report['checks']['explicit_target_confirmed']=bool(ok)
            # 自动逻辑是否覆盖显式点:再跑 6 游戏秒,显式点必须保持
            if ok:
                kept=until(lambda:any(any(math.dist(t[:2],actual)<1 for t in u.rally_targets)
                                      for u in worlds[0].my_units if u.tag==inventory[0]['tag']),8)
                report['checks']['explicit_target_survives_automatic_rally']=bool(kept)
                report['automatic_state']=commander.rally.public()
            report['action_errors']=arena.action_errors
            report['passed']=all(report['checks'].values()) and arena.action_errors==0
    except BaseException as error:
        report.update(error=type(error).__name__+': '+str(error),traceback=traceback.format_exc())
        raise
    finally:
        if commander:commander.stop()
        report['cleanup_complete']=arena is not None and arena.closed
        save()
        print('RALLY_ORDERS_NATIVE',json.dumps(report,ensure_ascii=False),flush=True)


if __name__=='__main__':app.run(main)
