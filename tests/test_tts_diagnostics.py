import asyncio
import json
import tempfile
import unittest
import sys
from pathlib import Path

from test_llama_tts_worker import FakeProcess, make_bundle, write_wav
from app.workers.llama_tts_worker import LlamaTTSWorkerClient
from app.workers.tts_diagnostics import TTSDiagnostics


class TTSDiagnosticTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_cancellation_cleans_up_open_diagnostic_stream(self):
        with tempfile.TemporaryDirectory() as temp:
            started = asyncio.Event()
            process = FakeProcess(blocked=True)
            process.stdout = asyncio.StreamReader()
            async def factory(*args, **kwargs):
                started.set()
                return process
            client = LlamaTTSWorkerClient(process_factory=factory)
            await client.start(make_bundle(Path(temp)))
            with self.assertLogs('app.workers.llama_tts_worker', level='INFO') as captured:
                task = asyncio.create_task(client.synthesize(text='private', voice_id='zh_female_1', language='zh'))
                await started.wait()
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            row = json.loads(captured.records[-1].getMessage().split('llama-tts diagnostic ', 1)[1])
            self.assertEqual(row['outcome'], 'cancelled')
            self.assertTrue(process.killed)
            self.assertFalse(client.running)
            self.assertFalse([t for t in asyncio.all_tasks() if t.get_name() == 'tts-diagnostic-reader'])

    async def test_start_failure_logs_errno_not_raw_error_or_command(self):
        with tempfile.TemporaryDirectory() as temp:
            async def factory(*args, **kwargs):
                raise OSError(2, '/private/secret/path')
            client = LlamaTTSWorkerClient(process_factory=factory)
            await client.start(make_bundle(Path(temp)))
            with self.assertLogs('app.workers.llama_tts_worker', level='INFO') as captured:
                with self.assertRaisesRegex(RuntimeError, 'OS error 2'):
                    await client.synthesize(text='secret dialogue', voice_id='zh_female_1', language='zh')
            text = '\n'.join(captured.output)
            self.assertNotIn('/private/secret/path', text)
            self.assertNotIn('secret dialogue', text)
            row = json.loads(captured.records[-1].getMessage().split('llama-tts diagnostic ', 1)[1])
            self.assertEqual(row['outcome'], 'start_failed')
            self.assertEqual(row['os_error'], 2)

    async def test_chunked_oversized_and_private_lines_are_discarded_not_truncated_into_metrics(self):
        diagnostic = TTSDiagnostics()
        stream = asyncio.StreamReader()
        stream.feed_data(b'x' * 10000 + b'frames generated: 99, speed: 1.00 frames/s\n')
        stream.feed_data(b'/private/path and secret dialogue\n0.01.000 I frames gener')
        stream.feed_data(b'ated: 25, speed: 12.50 frames/s\n')
        stream.feed_data(b'timings: prompt eval 0.02s + generation 1.72s + vocoder 0.15s = total 1.89s private\n')
        stream.feed_eof()
        await diagnostic.drain(stream)
        self.assertEqual(diagnostic.metrics['frames'], 25)
        self.assertEqual(diagnostic.metrics['frames_per_second'], 12.5)
        self.assertNotIn('engine_total_seconds', diagnostic.metrics)
        self.assertLess(len(json.dumps(diagnostic.metrics)), 300)

    async def test_real_noisy_child_is_drained_without_deadlock_or_raw_log_leak(self):
        with tempfile.TemporaryDirectory() as temp:
            async def factory(*args, **kwargs):
                destination = args[args.index('-o') + 1]
                script = "import sys,wave;sys.stdout.write('private-noise'*200000+'\\n');sys.stdout.flush();w=wave.open(sys.argv[1],'wb');w.setnchannels(1);w.setsampwidth(2);w.setframerate(24000);w.writeframes(b'\\0\\0'*2400);w.close();print('frames generated: 25, speed: 12.50 frames/s')"
                return await asyncio.create_subprocess_exec(sys.executable, '-c', script, destination, **kwargs)
            client = LlamaTTSWorkerClient(process_factory=factory)
            await client.start(make_bundle(Path(temp)))
            with self.assertLogs('app.workers.llama_tts_worker', level='INFO') as captured:
                await asyncio.wait_for(client.synthesize(text='test', voice_id='zh_female_1', language='zh', timeout=10), 15)
            self.assertNotIn('private-noise', '\n'.join(captured.output))
            row = json.loads(captured.records[-1].getMessage().split('llama-tts diagnostic ', 1)[1])
            self.assertEqual(row['frames'], 25)
            self.assertEqual(row['outcome'], 'succeeded')

    async def test_reader_failure_is_diagnostic_only(self):
        class BrokenStream:
            async def read(self, _size):
                raise OSError('private path')
        diagnostic = TTSDiagnostics()
        await diagnostic.drain(BrokenStream())
        self.assertEqual(diagnostic.metrics, {'last_stage': 'process_started', 'reader_error': True})

    async def test_success_logs_only_safe_context_and_numeric_engine_metrics(self):
        with tempfile.TemporaryDirectory() as temp:
            async def factory(*args, **kwargs):
                process = FakeProcess()
                process.stdout = asyncio.StreamReader()
                process.stdout.feed_data(b'private prompt /secret/path\n0.01.000 I frames generated: 85, speed: 42.39 frames/s\n')
                process.stdout.feed_data(b'0.02.000 I timings: prompt eval 0.02s + generation 1.72s + vocoder 0.15s = total 1.89s\n')
                process.stdout.feed_eof()
                write_wav(Path(args[args.index('-o') + 1]))
                return process
            client = LlamaTTSWorkerClient(process_factory=factory)
            await client.start(make_bundle(Path(temp)))
            with self.assertLogs('app.workers.llama_tts_worker', level='INFO') as captured:
                await client.synthesize(text='私密对白', voice_id='zh_female_1', language='zh')
            text = '\n'.join(captured.output)
            self.assertNotIn('私密对白', text)
            self.assertNotIn('/secret/path', text)
            self.assertNotIn(temp, text)
            rows = [json.loads(record.getMessage().split('llama-tts diagnostic ', 1)[1]) for record in captured.records]
            self.assertEqual(rows[0]['event'], 'start')
            self.assertEqual(rows[-1]['outcome'], 'succeeded')
            self.assertEqual(rows[-1]['device'], 'cuda')
            self.assertEqual(rows[-1]['characters'], 4)
            self.assertEqual(rows[-1]['frames'], 85)
            self.assertEqual(rows[-1]['generation_seconds'], 1.72)
            self.assertEqual(rows[-1]['engine_total_seconds'], 1.89)
            self.assertGreaterEqual(rows[-1]['elapsed_seconds'], 0)

    async def test_timeout_keeps_last_progress_and_reaps_reader_and_process(self):
        with tempfile.TemporaryDirectory() as temp:
            process = FakeProcess(blocked=True)
            process.stdout = asyncio.StreamReader()
            process.stdout.feed_data(b'0.01.000 I frames generated: 25, speed: 12.50 frames/s\n')
            async def factory(*args, **kwargs):
                return process
            client = LlamaTTSWorkerClient(process_factory=factory, poll_interval=.001)
            await client.start(make_bundle(Path(temp)))
            with self.assertLogs('app.workers.llama_tts_worker', level='INFO') as captured:
                with self.assertRaises(TimeoutError):
                    await client.synthesize(text='私密对白', voice_id='zh_female_1', language='zh', timeout=.01)
            row = json.loads(captured.records[-1].getMessage().split('llama-tts diagnostic ', 1)[1])
            self.assertEqual(row['outcome'], 'timeout')
            self.assertEqual(row['frames'], 25)
            self.assertEqual(row['last_stage'], 'generation')
            self.assertTrue(process.killed)
            self.assertFalse(client.running)
            self.assertFalse([task for task in asyncio.all_tasks() if task.get_name() == 'tts-diagnostic-reader'])
