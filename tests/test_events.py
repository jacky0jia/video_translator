import asyncio
import json
import unittest

from app.core import events


class EventQueueTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        events._task_queues.clear()

    async def test_emit_without_subscriber_does_not_create_orphan_queue(self):
        events.emit_event("idle-task", {"status": "translating"})
        self.assertNotIn("idle-task", events._task_queues)

    async def test_active_subscriber_receives_event(self):
        generator = events.event_generator("active-task")
        pending = asyncio.create_task(anext(generator))
        await asyncio.sleep(0)
        events.emit_event("active-task", {"status": "completed"})
        payload = await asyncio.wait_for(pending, 1)
        self.assertIn('"status": "completed"', payload)
        await generator.aclose()

    async def test_standalone_transcription_closes_its_event_stream(self):
        generator = events.event_generator("transcribed-task")
        pending = asyncio.create_task(anext(generator))
        await asyncio.sleep(0)
        events.emit_event("transcribed-task", {"status": "transcribed"})

        payload = await asyncio.wait_for(pending, 1)

        self.assertIn('"status": "transcribed"', payload)
        with self.assertRaises(StopAsyncIteration):
            await anext(generator)
        self.assertNotIn("transcribed-task", events._task_queues)

    async def test_terminal_event_reaches_all_subscribers(self):
        first = events.event_generator("shared-task")
        second = events.event_generator("shared-task")
        pending = [asyncio.create_task(anext(stream)) for stream in (first, second)]
        await asyncio.sleep(0)
        events.emit_event("shared-task", {"status": "transcribed"})
        payloads = await asyncio.wait_for(asyncio.gather(*pending), 1)
        self.assertTrue(all('"status": "transcribed"' in payload for payload in payloads))
        await first.aclose()
        self.assertIn("shared-task", events._task_queues)
        await second.aclose()
        self.assertNotIn("shared-task", events._task_queues)

    async def test_artifact_paths_are_browser_urls_in_live_and_initial_events(self):
        path = str(events.settings.OUTPUT_DIR / "nested" / "subtitle.json")
        data = {"status": "completed", "transcription_path": path,
                "translations": {"Chinese": path},
                "subtitle_outputs": {"srt": "/static/output/already.srt"},
                "dubbing_video_path": None}
        generator = events.event_generator("paths", initial_data=data)
        initial = await anext(generator)
        events.emit_event("paths", data)
        live = await anext(generator)
        for payload in (initial, live):
            actual = json.loads(payload.removeprefix("data: "))
            self.assertEqual(actual["transcription_path"], "/static/output/nested/subtitle.json")
            self.assertEqual(actual["translations"]["Chinese"], actual["transcription_path"])
            self.assertEqual(actual["subtitle_outputs"]["srt"], "/static/output/already.srt")
            self.assertIsNone(actual["dubbing_video_path"])
        self.assertEqual(data["transcription_path"], path)
        self.assertEqual(data["translations"]["Chinese"], path)
        await generator.aclose()


if __name__ == "__main__":
    unittest.main()
