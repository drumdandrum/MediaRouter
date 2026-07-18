import shutil
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from app.core.config import get_settings


class ReservationLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_data_dir = get_settings().data_dir
        cls.temp_dir = Path(tempfile.mkdtemp())
        shutil.copy2(cls.original_data_dir / "media_router.db", cls.temp_dir / "media_router.db")
        get_settings().data_dir = cls.temp_dir
        from app.services.catalog import list_items
        cls.live = list_items("channel", limit=2)
        cls.movies = list_items("movie", limit=2)
        if len(cls.live) < 2 or len(cls.movies) < 2:
            raise unittest.SkipTest("Lifecycle integration fixture needs two live and two movie catalog items")

    @classmethod
    def tearDownClass(cls):
        get_settings().data_dir = cls.original_data_dir
        shutil.rmtree(cls.temp_dir)

    def setUp(self):
        from app.services.broker import release_all_active
        release_all_active()

    def _resolve(self, item, session):
        from app.services.broker import resolve_source
        return resolve_source(item.internal_id, item.media_type, client_session=session,
            allow_reservation_reuse=True, lifecycle_enabled=True)

    def _row(self, reservation_id):
        with sqlite3.connect(self.temp_dir / "media_router.db") as conn:
            conn.row_factory = sqlite3.Row
            return conn.execute("SELECT * FROM broker_reservations WHERE reservation_id=?", (reservation_id,)).fetchone()

    def test_runtime_acquisition_is_provisional_and_reuses(self):
        first = self._resolve(self.live[0], "reuse-session")
        second = self._resolve(self.live[0], "reuse-session")
        self.assertEqual("provisional", first.reservation.lifecycle_state)
        self.assertEqual(first.reservation.reservation_id, second.reservation.reservation_id)
        self.assertEqual(2, second.reservation.request_count)

    def test_provisional_consumes_capacity(self):
        from app.services.broker import get_status
        self._resolve(self.live[0], "capacity-session")
        status = get_status()
        self.assertEqual(1, status.provisional_reservations)
        self.assertEqual(1, status.consuming_reservations)

    def test_rapid_probe_burst_does_not_promote(self):
        decision = self._resolve(self.live[0], "burst-session")
        for _ in range(4):
            decision = self._resolve(self.live[0], "burst-session")
        self.assertEqual("provisional", decision.reservation.lifecycle_state)
        self.assertEqual(0, decision.reservation.distinct_activity_count)

    def test_meaningful_later_activity_promotes_same_id(self):
        decision = self._resolve(self.live[0], "promote-session")
        old = (datetime.utcnow() - timedelta(seconds=25)).isoformat()
        with sqlite3.connect(self.temp_dir / "media_router.db") as conn:
            conn.execute("UPDATE broker_reservations SET created_at=?, first_seen_at=? WHERE reservation_id=?",
                         (old, old, decision.reservation.reservation_id))
        one = self._resolve(self.live[0], "promote-session")
        two = self._resolve(self.live[0], "promote-session")
        self.assertEqual(decision.reservation.reservation_id, two.reservation.reservation_id)
        self.assertEqual("active", two.reservation.lifecycle_state)
        self.assertIsNotNone(two.reservation.promoted_at)
        self.assertEqual(2, two.reservation.distinct_activity_count)

    def test_live_switch_supersedes_atomically(self):
        from app.services.broker import get_status
        old = self._resolve(self.live[0], "surf-session")
        new = self._resolve(self.live[1], "surf-session")
        self.assertEqual("superseded", self._row(old.reservation.reservation_id)["lifecycle_state"])
        self.assertEqual(new.reservation.reservation_id,
                         self._row(old.reservation.reservation_id)["superseded_by_reservation_id"])
        self.assertEqual(1, get_status().consuming_reservations)

    def test_different_sessions_do_not_supersede(self):
        first = self._resolve(self.live[0], "session-one")
        self._resolve(self.live[1], "session-two")
        self.assertEqual("provisional", self._row(first.reservation.reservation_id)["lifecycle_state"])

    def test_vod_switch_supersedes_only_provisional(self):
        from app.services.broker import confirm_reservation
        first = self._resolve(self.movies[0], "browse-session")
        self._resolve(self.movies[1], "browse-session")
        self.assertEqual("superseded", self._row(first.reservation.reservation_id)["lifecycle_state"])
        active = self._resolve(self.movies[0], "play-session")
        confirm_reservation(active.reservation.reservation_id)
        self._resolve(self.movies[1], "play-session")
        self.assertEqual("active", self._row(active.reservation.reservation_id)["lifecycle_state"])

    def test_terminal_heartbeat_does_not_revive(self):
        from app.services.broker import heartbeat_reservation, release_reservation
        decision = self._resolve(self.live[0], "terminal-session")
        release_reservation(decision.reservation.reservation_id)
        heartbeat = heartbeat_reservation(decision.reservation.reservation_id)
        self.assertEqual("released", heartbeat.lifecycle_state)


if __name__ == "__main__":
    unittest.main()
