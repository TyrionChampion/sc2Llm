"""Explicit user-authorized reference sharing; never load it as another model's accepted skill."""
import hashlib
import json
from pathlib import Path
from arena.persistent_skills import PersistentSkills


def reference_materials(models,library_root):
    """Pin and verify existing Terran bundles once, before giving both players identical material."""
    library=PersistentSkills(library_root);materials=[]
    for model in dict.fromkeys(models):
        selected=library.latest(model)
        if selected is None:raise FileNotFoundError('Requested reference model has no accepted library version: '+model)
        root=Path(selected['path']);manifest=selected['manifest']
        files={str(p.relative_to(root)):p.read_text() for folder in ('skills','tests') for p in sorted((root/folder).iterdir())}
        value=dict(kind='reference_only',author_model=model,source_version=selected['version'],
            source_race='terran',strategy=manifest['strategy'],skills=manifest['skills'],files=files,
            file_sha256={n:hashlib.sha256(s.encode()).hexdigest() for n,s in files.items()},
            validation_scope='Source bundle integrity verified; formerly accepted own tests/host checks. Not a guarantee of tactical strength or current-context correctness.',
            instructions='User authorized both contestants to study these prior skills. You may adapt useful code; retain reference attribution. Write your own files/tests and pass current validation. Do not mistake Marine-specific assumptions for a universal tactic. Production, gas, expansions and research are LLM macro decisions, not Python strategy automation.')
        content=json.dumps(value,ensure_ascii=False,indent=2)
        if len(content.encode())>128000:raise ValueError('Reference exceeds lab read limit; split explicitly, do not truncate')
        materials.append(dict(model=model,version=selected['version'],path='fixtures/skill_reference_%d.json'%(len(materials)+1),content=content))
    return materials


def attach_references(lab,materials):
    result=[]
    for item in materials:
        path=lab.path(item['path']);path.write_text(item['content'])
        result.append({k:item[k] for k in ('model','version','path')})
    if result:
        (lab.root/'REFERENCE_GUIDE.md').write_text('The user authorized these shared, read-only study references. They are not your accepted skills. Read the JSON files with lab_read_file; inspect source and tests, then author/adapt, test and validate your own final version. Both contestants receive identical pinned versions.\n'+json.dumps(result,ensure_ascii=False,indent=2))
        print('[lab references] '+json.dumps(result,ensure_ascii=False),flush=True)
    return result
