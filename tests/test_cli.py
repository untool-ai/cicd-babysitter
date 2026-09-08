import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from src.cli import main


class CLITests(unittest.TestCase):
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
