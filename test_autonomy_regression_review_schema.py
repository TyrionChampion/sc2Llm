"""Reviews need the same provider-side JSON constraint as battle decisions."""
import json
from pathlib import Path
import unittest
import test_chatgpt_planner


class ReviewSchema(unittest.TestCase):
    def test_official_review_cli_receives_bounded_complete_output_schema(self):
        fixture=test_chatgpt_planner.ChatGPTPlannerTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        client=fixture.planner('review');client.response_kind='review'
        client.chat('Return a complete review object.','{}')
        received=json.loads((client.directory/'request-0001/received.json').read_text())
        args=received['args'];self.assertIn('--output-schema',args)
        schema=json.loads(Path(args[args.index('--output-schema')+1]).read_text())
        self.assertEqual(set(schema['required']),{'tasks','reason','category','strategy','findings','gap'})
        self.assertEqual(schema['properties']['tasks']['maxItems'],0)
        self.assertEqual(schema['properties']['strategy']['maxLength'],1800)
        self.assertFalse(schema['additionalProperties'])


if __name__=='__main__':unittest.main()
