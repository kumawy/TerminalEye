"""Native stderr is process-wide, so exercise it only in child processes."""
import json
from pathlib import Path
import subprocess
import sys
import textwrap


ROOT = Path(__file__).resolve().parents[1]


def run_child(source):
    result = subprocess.run(
        [sys.executable, '-c', textwrap.dedent(source)], cwd=ROOT,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stdout
    return result.stdout


def test_delayed_native_stderr_is_replayed_only_after_ui_finishes():
    output = run_child('''
        import os, threading, time
        from eyeterm.diagnostics import NativeDiagnostics

        with NativeDiagnostics():
            os.write(1, b'UI start\\n')
            def delayed_log():
                time.sleep(.02)
                os.write(2, b'Unexpected delayed detector warning\\n')
            worker = threading.Thread(target=delayed_log)
            worker.start()
            worker.join()
            os.write(1, b'UI finished\\n')
        os.write(2, b'Stderr restored\\n')
    ''')
    assert output == ('UI start\nUI finished\n'
                      'Unexpected delayed detector warning\nStderr restored\n')


def test_known_benign_logs_are_filtered_without_hiding_other_errors():
    output = run_child('''
        import io, os
        from eyeterm.diagnostics import NativeDiagnostics

        replay = io.StringIO()
        with NativeDiagnostics(output=replay):
            os.write(2, (
                'INFO: Created TensorFlow Lite XNNPACK delegate for CPU.\\n'
                'WARNING: All log messages before absl::InitializeLog() is called are written to STDERR\\n'
                'W0000 00:00:1761134541.101833 116168 inference_feedback_manager.cc:114] '
                'Feedback manager requires a model with a single signature inference. '
                'Disabling support for feedback tensors.\\n'
                'I0000 00:00:1761134541.101833 116168 gl_context.cc:369] '
                'GL version: 2.1 (2.1 Metal - 89.3), renderer: Apple M1\\n'
                'ERROR: GL version: unavailable\\n'
                'ERROR: Created TensorFlow Lite XNNPACK delegate for CPU.\\n'
                'OpenCV camera permission denied\\n'
            ).encode())
            assert replay.getvalue() == ''
        print(replay.getvalue(), end='')
    ''')
    assert output == ('ERROR: GL version: unavailable\n'
                      'ERROR: Created TensorFlow Lite XNNPACK delegate for CPU.\n'
                      'OpenCV camera permission denied\n')


def test_plain_known_messages_and_split_utf8_are_handled():
    output = run_child('''
        import os
        from eyeterm.diagnostics import NativeDiagnostics

        with NativeDiagnostics():
            os.write(2, b'Created TensorFlow Lite XNNPACK delegate for CPU.\\n')
            os.write(2, b'Feedback manager requires a model with a single signature inference. '
                        b'Disabling support for feedback tensors.\\n')
            os.write(2, b'All log messages before absl::InitializeLog() is called are written to STDERR\\n')
            os.write(2, b'Camera \\xe2')
            os.write(2, b'\\x80\\x94 failed')
    ''')
    assert output == 'Camera — failed'


def test_python_exception_propagates_and_fd2_is_restored():
    output = run_child('''
        import io, os
        from eyeterm.diagnostics import NativeDiagnostics

        replay = io.StringIO()
        os.set_inheritable(2, False)
        try:
            with NativeDiagnostics(output=replay):
                os.write(2, b'Camera device error\\n')
                raise ValueError('original exception')
        except ValueError as exc:
            assert str(exc) == 'original exception'
        else:
            raise AssertionError('exception was suppressed')
        assert replay.getvalue() == 'Camera device error\\n'
        assert not os.get_inheritable(2)
        os.write(2, b'Stderr after exception\\n')
    ''')
    assert output == 'Stderr after exception\n'


def test_no_newline_flood_is_bounded_and_keeps_recent_error():
    output = run_child('''
        import io, json, os, tracemalloc
        from eyeterm.diagnostics import NativeDiagnostics

        replay = io.StringIO()
        tracemalloc.start()
        with NativeDiagnostics(output=replay):
            for _ in range(1024):
                os.write(2, b'x' * 8192)
            os.write(2, b'\\nRecent camera device error\\n')
        _, peak = tracemalloc.get_traced_memory()
        text = replay.getvalue()
        assert text.endswith('\\nRecent camera device error\\n')
        assert 'truncated' in text.lower()
        print(json.dumps({'length': len(text), 'peak': peak}))
    ''')
    measurement = json.loads(output)
    assert measurement['length'] < 128 * 1024
    assert measurement['peak'] < 1024 * 1024


def test_disabled_capture_leaves_native_stderr_live():
    output = run_child('''
        import io, os
        from eyeterm.diagnostics import NativeDiagnostics

        replay = io.StringIO()
        with NativeDiagnostics(enabled=False, output=replay):
            os.write(2, b'Live native diagnostic\\n')
            os.write(1, b'UI finished\\n')
        assert replay.getvalue() == ''
    ''')
    assert output == 'Live native diagnostic\nUI finished\n'


def test_retained_native_pipe_copy_does_not_hang_cleanup():
    output = run_child('''
        import io, os, threading, time
        from eyeterm.diagnostics import NativeDiagnostics

        before = {thread.ident for thread in threading.enumerate()}
        replay = io.StringIO()
        start = time.monotonic()
        with NativeDiagnostics(output=replay):
            native_copy = os.dup(2)
            os.write(native_copy, b'Captured native error\\n')
        assert time.monotonic() - start < 1
        os.close(native_copy)
        assert replay.getvalue() == 'Captured native error\\n'
        assert {thread.ident for thread in threading.enumerate()} == before
        os.write(2, b'Cleanup finished\\n')
    ''')
    assert output == 'Cleanup finished\n'


def test_reader_start_failure_restores_fd2():
    output = run_child('''
        import os
        from unittest.mock import patch
        from eyeterm.diagnostics import NativeDiagnostics

        with patch('eyeterm.diagnostics.threading.Thread.start',
                   side_effect=RuntimeError('reader could not start')):
            try:
                with NativeDiagnostics():
                    raise AssertionError('entered after setup failure')
            except RuntimeError as exc:
                assert str(exc) == 'reader could not start'
        os.write(2, b'Stderr after setup failure\\n')
    ''')
    assert output == 'Stderr after setup failure\n'


def test_replay_failure_does_not_replace_original_python_exception():
    output = run_child('''
        import os
        from eyeterm.diagnostics import NativeDiagnostics

        class BrokenOutput:
            def write(self, text):
                raise OSError('closed output')

        try:
            with NativeDiagnostics(output=BrokenOutput()):
                os.write(2, b'Native camera error\\n')
                raise ValueError('original camera failure')
        except ValueError as exc:
            assert str(exc) == 'original camera failure'
        else:
            raise AssertionError('exception was suppressed')
        os.write(2, b'Stderr after broken replay\\n')
    ''')
    assert output == 'Stderr after broken replay\n'
