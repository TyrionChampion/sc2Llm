"""Run TestCase suites only; exclude standalone live game/model probes."""
import ast
from pathlib import Path
import sys
import unittest


def main():
    root = Path(__file__).resolve().parent
    modules = []
    for path in sorted(root.glob('test_*.py')):
        tree = ast.parse(path.read_text(), filename=str(path))
        if any(isinstance(node, ast.ClassDef) and any(
            isinstance(base, ast.Attribute) and base.attr == 'TestCase'
            for base in node.bases) for node in tree.body):
            modules.append(path.stem)
    suite = unittest.defaultTestLoader.loadTestsFromNames(modules)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(main())
