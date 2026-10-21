import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.workers import task_assignment as worker


class AssignmentWorkerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stop = asyncio.Event()
        self.connection = MagicMock()
        for method in ("scalar", "execute", "commit", "close", "invalidate"):
            setattr(self.connection, method, AsyncMock())
        self.connection.scalar.return_value = True
        self.engine = SimpleNamespace(connect=AsyncMock(return_value=self.connection), dispose=AsyncMock())
        self.settings = SimpleNamespace(slack_signing_secret="test", gemini_api_key="test", gemini_model="test")
        self.patches = [
            patch.object(worker, "engine", self.engine),
            patch.object(worker, "get_settings", return_value=self.settings),
        ]
        self.calls = {}
        for name in ("process_next_event", "expire_assignments", "select_next_assignment", "dispatch_next_assignment"):
            mock = AsyncMock(return_value=False)
            self.calls[name] = mock
            self.patches.append(patch.object(worker, name, mock))
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    async def _stop_after_iteration(self, event, timeout):
        event.set()

    async def test_standby_waits_for_leader_before_processing(self):
        self.connection.scalar.side_effect = [False, True]

        async def wait(event, timeout):
            if timeout == 1:
                event.set()

        with patch.object(worker, "_wait_for_stop", side_effect=wait):
            await worker.run(self.stop)
        self.assertEqual(self.connection.scalar.await_count, 2)
        self.calls["dispatch_next_assignment"].assert_awaited_once()
        self.assertIn("pg_advisory_unlock", str(self.connection.execute.call_args.args[0]))
        self.connection.close.assert_awaited_once()
        self.engine.dispose.assert_awaited_once()

    async def test_stopping_a_standby_does_not_process_or_unlock(self):
        self.connection.scalar.return_value = False
        with patch.object(worker, "_wait_for_stop", side_effect=self._stop_after_iteration):
            await worker.run(self.stop)
        self.calls["process_next_event"].assert_not_awaited()
        self.connection.execute.assert_not_awaited()
        self.connection.close.assert_awaited_once()

    async def test_shutdown_during_callbacks_stops_new_dispatches(self):
        async def callback():
            self.stop.set()
            return False

        self.calls["process_next_event"].side_effect = callback
        await worker.run(self.stop)
        self.calls["select_next_assignment"].assert_not_awaited()
        self.calls["dispatch_next_assignment"].assert_not_awaited()
        self.connection.close.assert_awaited_once()

    async def test_lost_lock_connection_exits_and_is_not_returned_to_pool(self):
        self.connection.execute.side_effect = RuntimeError("connection lost")
        with self.assertRaises(RuntimeError):
            await worker.run(self.stop)
        self.calls["process_next_event"].assert_not_awaited()
        self.connection.invalidate.assert_awaited_once()
        self.engine.dispose.assert_awaited_once()

    async def test_configuration_errors_do_not_start_processing(self):
        for field in ("slack_signing_secret", "gemini_api_key", "gemini_model"):
            original = getattr(self.settings, field)
            setattr(self.settings, field, None)
            with self.assertRaises(RuntimeError):
                await worker.run(self.stop)
            setattr(self.settings, field, original)
        self.engine.connect.assert_not_awaited()

    async def test_failed_database_connect_disposes_engine(self):
        self.engine.connect.side_effect = RuntimeError("database unavailable")
        with self.assertRaises(RuntimeError):
            await worker.run(self.stop)
        self.engine.dispose.assert_awaited_once()
