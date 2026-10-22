"""Bounded, numeric-only observations; never persist raw native output."""
import asyncio
import re
import time


class TTSDiagnostics:
    def __init__(self):
        self.started = time.monotonic()
        self.metrics = {"last_stage": "process_started"}

    def line(self, raw):
        text = raw.decode('ascii', errors='replace').strip()
        text = re.sub(r'^\d+(?:\.\d+)+ [IWE] ', '', text)
        match = re.fullmatch(r'frames generated: (\d{1,9}), speed: (\d{1,9}(?:\.\d{1,6})?) frames/s', text)
        if match:
            self.metrics.update(frames=int(match[1]), frames_per_second=float(match[2]), last_stage='generation')
        else:
            number = r'(\d{1,9}(?:\.\d{1,6})?)'
            match = re.fullmatch(r'timings: prompt eval ' + number + r's \+ generation ' + number + r's \+ vocoder ' + number + r's = total ' + number + 's', text)
            if not match:
                return
            self.metrics.update(zip(('prompt_seconds', 'generation_seconds', 'vocoder_seconds', 'engine_total_seconds'), map(float, match.groups())))
            self.metrics['last_stage'] = 'generation_finished'
        self.metrics['last_progress_seconds'] = round(time.monotonic() - self.started, 3)

    async def drain(self, stream):
        if stream is None:
            return
        pending = b''
        oversized = False
        try:
            while chunk := await stream.read(4096):
                # A continuously noisy child must not starve timeout/cancellation.
                await asyncio.sleep(0)
                for index, part in enumerate(chunk.split(b'\n')):
                    if index:
                        if not oversized:
                            self.line(pending)
                        pending, oversized = b'', False
                    if len(pending) + len(part) > 2048:
                        pending, oversized = b'', True
                    elif not oversized:
                        pending += part
            if pending and not oversized:
                self.line(pending)
        except Exception:
            self.metrics['reader_error'] = True

    async def finish(self, reader):
        try:
            await asyncio.wait_for(asyncio.shield(reader), .25)
        except asyncio.TimeoutError:
            self.metrics['reader_incomplete'] = True
        finally:
            if not reader.done():
                reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
