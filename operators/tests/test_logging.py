import contextlib
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import run_ansible


class LoggingTests(unittest.TestCase):
    def test_standard_output_is_streamed_and_private_diagnostic_is_retained(self):
        with tempfile.TemporaryDirectory() as tmp:
            diagnostic = Path(tmp) / 'private' / 'stage.log'
            output = io.StringIO()
            script = "import os; print('TASK [visible progress]', flush=True); print(os.environ['ANSIBLE_STDOUT_CALLBACK']); print(os.environ['ANSIBLE_DISPLAY_ARGS_TO_STDOUT'])"
            with contextlib.redirect_stdout(output):
                result = run_ansible([sys.executable, '-u', '-c', script], diagnostic, 5)
            self.assertEqual(result.returncode, 0)
            self.assertIn('TASK [visible progress]', output.getvalue())
            self.assertEqual(output.getvalue(), diagnostic.read_text())
            self.assertIn('default\nfalse\n', output.getvalue())
            self.assertEqual(os.stat(diagnostic).st_mode & 0o777, 0o600)
