"""Offline checks of harness diagnostics; never runs guest operations."""
import contextlib
import io
import unittest
from unittest.mock import patch

import sshid_incident as incident


class Diagnostics(unittest.TestCase):
    def rendered(self, error):
        out = io.StringIO()
        try:
            raise error
        except Exception as caught:
            with contextlib.redirect_stdout(out):
                incident.report_exception(caught)
        return out.getvalue()

    def test_command_failure(self):
        with patch.object(incident.subprocess, 'run') as command:
            command.return_value.returncode = 1
            try:
                incident.run('systemctl', 'reload', 'ssh.service')
            except incident.HarnessError as error:
                text = self.rendered(error)
            else:
                self.fail('missing harness failure')
        self.assertIn('FAIL: HARNESS', text)
        self.assertIn('systemctl reload ssh.service rc=1', text)
        self.assertIn('location=sshid_incident.py:', text)

    def test_untrusted_exception(self):
        text = self.rendered(RuntimeError('PRIVATE_DIAGNOSTIC_CANARY'))
        self.assertNotIn('PRIVATE_DIAGNOSTIC_CANARY', text)
        self.assertIn('type=RuntimeError message=<redacted>', text)

    def test_os_error_excludes_path(self):
        text = self.rendered(FileNotFoundError(2, 'missing', '/PRIVATE_DIAGNOSTIC_CANARY'))
        self.assertNotIn('PRIVATE_DIAGNOSTIC_CANARY', text)
        self.assertIn('No such file', text)

    def test_failed_measurement_can_be_collected(self):
        with patch.object(incident.subprocess, 'run') as command:
            command.return_value.returncode = 1
            self.assertEqual(incident.run('systemctl', 'reload', 'ssh.service', check=False).returncode, 1)

    def test_apt_observation_starts_after_exit_and_only_reads_state(self):
        from unittest.mock import Mock, call
        apt = Mock()
        apt.wait.return_value = 0
        unit_states = ['inactive', 'inactive', 'active', 'inactive',
                       'active', 'active', 'active', 'active']
        out = io.StringIO()
        with patch.object(incident.time, 'monotonic', side_effect=[0, 0, 0, 5, 15]), \
                patch.object(incident.threading, 'Event') as event, \
                patch.object(incident, 'state', side_effect=unit_states) as state, \
                patch.object(incident, 'run') as commands, contextlib.redirect_stdout(out):
            rc, observations = incident.observe_apt(apt)
        apt.wait.assert_called_once_with(timeout=300)
        self.assertEqual(event.return_value.wait.call_args_list, [call(0), call(5), call(10), call(15)])
        self.assertEqual(state.call_args_list, [call('ssh.service'), call('ssh.socket')] * 4)
        commands.assert_not_called()
        self.assertEqual(rc, 0)
        self.assertEqual(observations[0], ('inactive', 'inactive'))
        for seconds in (0, 5, 15, 30):
            self.assertIn(f'DIAG: REAL-APT: after-apt-seconds={seconds} ', out.getvalue())


if __name__ == '__main__':
    unittest.main()
