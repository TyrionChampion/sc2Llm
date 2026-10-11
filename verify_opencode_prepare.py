"""Real GLM/OpenCode preparation validation only; no game or substitute opponent."""
import json,tempfile
from pathlib import Path
from arena.opencode_bridge import OpenCodeServer,OpenCodePlanner
from arena.opencode_prepare import prepare_player

def main():
    model='opencode-go/glm-5.3-flash'
    root=Path(tempfile.mkdtemp(prefix='glm-prep-validation-',dir='opencode-runtime')).resolve();root.chmod(0o700)
    server=OpenCodeServer(root/'server',[model]);planner=None;report={'model':model,'status':'starting','root':str(root)}
    try:
        server.start();planner=OpenCodePlanner(server,model,0,root/'p1')
        catalog,prepared=prepare_player(planner,root/'p1',180)
        report.update(status='passed',preparation=prepared)
        print('OPENCODE_GLM_PREPARE_OK',json.dumps(report,ensure_ascii=False),flush=True)
    except BaseException as error:
        report.update(status='failed',error=type(error).__name__+': '+str(error));raise
    finally:
        if planner:planner.close()
        server.close();Path('logs/opencode_glm_prepare.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
