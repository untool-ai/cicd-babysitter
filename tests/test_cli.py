import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from src.cli import main, ledger_report


class CLITests(unittest.TestCase):
    def test_verified_actions_have_separate_metrics_denominator(self):
        report = ledger_report({'events': [{'event_json': '{"conclusion":"failure"}', 'decision_json': '{}', 'received_at': None}],
                                'actions': [{'state': state} for state in ('verified_success', 'verified_failure', 'uncertain')], 'chain_valid': True})
        self.assertEqual(report['record_count'], 1)
        self.assertEqual(report['reliability']['failures'], 1)
        self.assertEqual(report['verified_remediation']['known_samples'], 2)
        self.assertEqual(report['verified_remediation']['unknown_samples'], 1)
        self.assertEqual(report['verified_remediation']['success_rate'], .5)
        self.assertEqual(report['remediation_latency']['unknown_samples'], 3)

    def test_export_report_and_runbook_without_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            config=Path(directory)/'config.json'; config.write_text('{}')
            for command in (['export'],['report'],['report','--runbook']):
                output=io.StringIO()
                with contextlib.redirect_stdout(output):
                    code=main(['--config',str(config),'--database',str(Path(directory)/'ledger.db'),*command])
                self.assertEqual(code,0,output.getvalue())
                self.assertTrue(output.getvalue())


if __name__=='__main__': unittest.main()
