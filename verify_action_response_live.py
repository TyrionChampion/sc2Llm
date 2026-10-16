"""Zero-LLM negative control for immediate SC2 action-response feedback."""
import argparse
import json
from pathlib import Path
from absl import flags
from arena.raw_engine import RawArena,command,TRAIN_SCV


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--label',required=True)
    args=p.parse_args();flags.FLAGS(['native-response-control'])
    folder=Path('logs/action-response-'+args.label);folder.mkdir(exist_ok=False)
    result=dict(model_calls=0,debug_fixture=False,intentionally_invalid_action=True,passed=False)
    arena=None
    try:
        arena=RawArena(render=False,races=['protoss','terran'],game_speed='slower')
        nexus=arena.initial[0].my(59)[0]
        # A Protoss Nexus cannot train an SCV. This raw negative control bypasses
        # planner validation deliberately; do not classify it as a battle fault.
        action=command(TRAIN_SCV,[nexus.tag]);context=dict(source='intentional_native_response_control',task_id=None)
        arena.register_action_context(0,[(action.SerializeToString(),context)])
        after=arena.step([[action],[]])[0]
        events=[e for e in after.feedback if e['kind']=='engine_action_rejected']
        result.update(response_results=list(arena.controllers[0].response.result),
            combined_errors=list(after.errors),feedback=events,engine_action_errors=arena.action_errors)
        # Observation protobufs are not JSON serializable; store fixed numeric fields.
        result['observation_errors']=[dict(tag=e.unit_tag,ability=e.ability_id,result=e.result)
                                      for e in arena.proto(0).action_errors]
        result['passed']=(bool(events) and events[0]['origin']=='action_response'
            and events[0]['actions'][0]['context']==context and arena.action_errors==1)
        arena.save_replay(folder/'control.SC2Replay')
    except Exception as error:result['error']=type(error).__name__+': '+str(error)
    finally:
        if arena:arena.close()
        (folder/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        print(json.dumps(result,ensure_ascii=False),flush=True)
    return 0 if result['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
