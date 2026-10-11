"""Trusted entry for model-authored unittest suites, inside a networkless sandbox."""
import resource,sys,unittest,json
for k,v in ((resource.RLIMIT_AS,256*1024*1024),(resource.RLIMIT_CPU,3),(resource.RLIMIT_FSIZE,1024*1024),(resource.RLIMIT_NOFILE,32),(resource.RLIMIT_NPROC,16),(resource.RLIMIT_CORE,0)):
    resource.setrlimit(k,(v,v))
sys.path[:0]=['/lab/skills','/lab/tests','/lab']
suite=unittest.defaultTestLoader.discover('/lab/tests',pattern='test_*.py')
count=suite.countTestCases()
if count==0:raise RuntimeError('No unittest test cases discovered; write tests/test_*.py with TestCase methods')
result=unittest.TextTestRunner(verbosity=2).run(suite)
print(json.dumps(dict(tests_run=result.testsRun,failures=len(result.failures),errors=len(result.errors))),flush=True)
sys.exit(0 if result.wasSuccessful() else 1)
