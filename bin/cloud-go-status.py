"""Check the injected Go binding with one minimal request; never expose its value."""
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from arena.go_planner import GO_MODEL, GoPlanner, CredentialBindingMissing


def main():
    output = ROOT/'logs/onboarding'
    output.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix='go-readiness-',dir=output))
    client = None
    try:
        client = GoPlanner(GO_MODEL, directory)
        answer = json.loads(client.chat('Return only a JSON object containing tasks:[] and reason:"ready".', '{}'))
        if answer.get('tasks') != []:
            raise ValueError('Minimal Go response failed validation')
        print(json.dumps(dict(available=True,metadata=client.last_metadata,evidence=str(directory))))
        return 0
    except CredentialBindingMissing as error:
        print(json.dumps(dict(available=False,error=str(error),error_kind='credential_binding_missing',
                              request_sent=False,evidence=str(directory))))
        return 2
    except Exception as error:
        print(json.dumps(dict(available=False,error=str(error),evidence=str(directory))))
        return 2
    finally:
        if client:
            client.close()


if __name__=='__main__':
    raise SystemExit(main())
