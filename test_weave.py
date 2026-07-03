import json
import os
import tempfile
import unittest
from pathlib import Path

from pipeline import common as pc
from pipeline import weave


class WeaveTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "kizuki_tree.sqlite3"
        pc.init_db(self.db_path)
        self.original_no_codex = os.environ.get("KIZUKI_WEAVE_NO_CODEX")
        self.original_codex_model = os.environ.get("KIZUKI_CODEX_MODEL")
        os.environ["KIZUKI_WEAVE_NO_CODEX"] = "1"
        self._orig_run_codex = weave._run_codex
        self._orig_codex_available = weave.codex_available

    def tearDown(self):
        weave._run_codex = self._orig_run_codex
        weave.codex_available = self._orig_codex_available
        if self.original_no_codex is None:
            os.environ.pop("KIZUKI_WEAVE_NO_CODEX", None)
        else:
            os.environ["KIZUKI_WEAVE_NO_CODEX"] = self.original_no_codex
        if self.original_codex_model is None:
            os.environ.pop("KIZUKI_CODEX_MODEL", None)
        else:
            os.environ["KIZUKI_CODEX_MODEL"] = self.original_codex_model
        self.tmpdir.cleanup()

    def _insert_star(
        self,
        conn,
        sid,
        display_name,
        body,
        tags,
        space_id=None,
        created_at=None,
        status="approved",
        visibility="universe",
    ):
        star_id = sid or f"star{abs(hash((display_name, body, created_at))) % 100000}"
        conn.execute(
            "INSERT INTO reflections (id, source, display_name, body, tags, status, created_at, space_id, star_kind, visibility) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                star_id,
                "line",
                display_name,
                body,
                json.dumps(tags, ensure_ascii=False),
                status,
                created_at or pc.now_iso(),
                space_id or pc.default_space_id(),
                "insight",
                visibility,
            ),
        )
        return star_id

    def test_fallback_creates_link_and_stamps_woven_at(self):
        with pc.connect(self.db_path) as conn:
            a = self._insert_star(conn, "s1", "Aさん", "笑いで緊張がほどけた。", ["笑い"], created_at="2026-07-04T00:00:01+00:00")
            b = self._insert_star(conn, "s2", "Bさん", "笑いのあとに安心した。", ["笑い", "安心"], created_at="2026-07-04T00:00:02+00:00")

            result = weave.weave_links(conn, pc.default_space_id())

            self.assertEqual(result, {"new_links": 1, "processed": 2, "mode": "fallback"})
            link = conn.execute(
                "SELECT star_a, star_b, reason FROM star_links WHERE space_id=?",
                (pc.default_space_id(),),
            ).fetchone()
            self.assertEqual({link["star_a"], link["star_b"]}, {a, b})
            self.assertIn("笑い", link["reason"])
            rows = conn.execute(
                "SELECT id, woven_at, tags FROM reflections WHERE id IN (?, ?) ORDER BY id ASC",
                (a, b),
            ).fetchall()
            self.assertTrue(all(row["woven_at"] for row in rows))
            self.assertEqual(json.loads(rows[0]["tags"]), ["笑い"])
            self.assertEqual(json.loads(rows[1]["tags"]), ["笑い", "安心"])

    def test_no_tag_overlap_creates_no_link_and_leaves_unwoven_for_codex_retry(self):
        with pc.connect(self.db_path) as conn:
            a = self._insert_star(conn, "n1", "Aさん", "身体のこわばりに気づいた。", ["身体感覚"], created_at="2026-07-04T00:00:01+00:00")
            b = self._insert_star(conn, "n2", "Bさん", "問いが残った。", ["問い"], created_at="2026-07-04T00:00:02+00:00")

            result = weave.weave_links(conn, pc.default_space_id())

            self.assertEqual(result, {"new_links": 0, "processed": 2, "mode": "fallback"})
            self.assertEqual(
                conn.execute("SELECT COUNT(*) AS count FROM star_links WHERE space_id=?", (pc.default_space_id(),)).fetchone()["count"],
                0,
            )
            # フォールバック夜に相棒が見つからなかった星は未編みのまま残る
            # （後日codexが再挑戦できる — 無条件刻印は糸の成長上限になる）
            rows = conn.execute(
                "SELECT woven_at FROM reflections WHERE id IN (?, ?)",
                (a, b),
            ).fetchall()
            self.assertTrue(all(row["woven_at"] is None for row in rows))
            self.assertEqual(
                conn.execute("SELECT COUNT(*) AS count FROM relay_events WHERE space_id=?", (pc.default_space_id(),)).fetchone()["count"],
                0,
            )

    def test_max_links_per_star_is_respected(self):
        with pc.connect(self.db_path) as conn:
            star_ids = []
            for index in range(5):
                star_ids.append(
                    self._insert_star(
                        conn,
                        f"cap{index}",
                        f"{index}さん",
                        f"みんなで笑った気づき {index}",
                        ["笑い"],
                        created_at=f"2026-07-04T00:00:0{index}+00:00",
                    )
                )

            result = weave.weave_links(conn, pc.default_space_id())

            self.assertEqual(result["mode"], "fallback")
            self.assertGreaterEqual(result["new_links"], 1)
            for star_id in star_ids:
                self.assertLessEqual(weave.link_count(conn, pc.default_space_id(), star_id), 3)

    def test_link_woven_relay_is_aggregated_once_per_run(self):
        with pc.connect(self.db_path) as conn:
            self._insert_star(conn, "r1", "Aさん", "笑いが戻った。", ["笑い"], created_at="2026-07-04T00:00:01+00:00")
            self._insert_star(conn, "r2", "Bさん", "笑いで場がほどけた。", ["笑い"], created_at="2026-07-04T00:00:02+00:00")

            weave.weave_links(conn, pc.default_space_id())

            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) AS count FROM relay_events WHERE space_id=? AND kind='link_woven'",
                    (pc.default_space_id(),),
                ).fetchone()["count"],
                1,
            )

    def test_second_run_with_no_unwoven_stars_adds_no_event(self):
        with pc.connect(self.db_path) as conn:
            self._insert_star(conn, "u1", "Aさん", "笑いが残った。", ["笑い"], created_at="2026-07-04T00:00:01+00:00")
            self._insert_star(conn, "u2", "Bさん", "笑いが広がった。", ["笑い"], created_at="2026-07-04T00:00:02+00:00")

            first = weave.weave_links(conn, pc.default_space_id())
            before = conn.execute("SELECT COUNT(*) AS count FROM relay_events").fetchone()["count"]
            second = weave.weave_links(conn, pc.default_space_id())
            after = conn.execute("SELECT COUNT(*) AS count FROM relay_events").fetchone()["count"]

            self.assertEqual(first["new_links"], 1)
            self.assertEqual(second, {"new_links": 0, "processed": 0, "mode": "none"})
            self.assertEqual(before, after)

    def test_space_isolation_ignores_other_space_stars(self):
        with pc.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO spaces (id, name, worldview_path, worldview_json, created_at) VALUES (?,?,?,?,?)",
                ("other-universe", "別宇宙", "", "{}", pc.now_iso()),
            )
            other = self._insert_star(
                conn,
                "other1",
                "別宇宙さん",
                "笑いの星です。",
                ["笑い"],
                space_id="other-universe",
                created_at="2026-07-04T00:00:00+00:00",
            )
            a = self._insert_star(conn, "iso1", "Aさん", "笑いが戻った。", ["笑い"], created_at="2026-07-04T00:00:01+00:00")
            b = self._insert_star(conn, "iso2", "Bさん", "笑いが広がった。", ["笑い"], created_at="2026-07-04T00:00:02+00:00")

            result = weave.weave_links(conn, pc.default_space_id())

            self.assertEqual(result["new_links"], 1)
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) AS count FROM star_links WHERE star_a=? OR star_b=?",
                    (other, other),
                ).fetchone()["count"],
                0,
            )
            other_row = conn.execute("SELECT woven_at FROM reflections WHERE id=?", (other,)).fetchone()
            self.assertIsNone(other_row["woven_at"])
            linked_ids = {
                row["star_a"]
                for row in conn.execute("SELECT star_a, star_b FROM star_links WHERE space_id=?", (pc.default_space_id(),))
            } | {
                row["star_b"]
                for row in conn.execute("SELECT star_a, star_b FROM star_links WHERE space_id=?", (pc.default_space_id(),))
            }
            self.assertEqual(linked_ids, {a, b})

    def test_emerge_questions_returns_no_codex_when_disabled(self):
        with pc.connect(self.db_path) as conn:
            self._insert_star(conn, "q1", "Aさん", "笑いのあとに静けさが残った。", ["笑い"])
            self._insert_star(conn, "q2", "Bさん", "その静けさを味わった。", ["笑い"])

            result = weave.emerge_questions(conn, pc.default_space_id())

            self.assertEqual(result, {"new_questions": 0, "skipped": "no-codex"})

    def test_emerge_questions_inserts_only_non_duplicate_valid_candidates(self):
        with pc.connect(self.db_path) as conn:
            id1 = self._insert_star(conn, "e1", "Aさん", "笑ったあとに身体がゆるんだ。", ["笑い"], created_at="2026-07-04T00:00:01+00:00")
            id2 = self._insert_star(conn, "e2", "Bさん", "安心が広がって息が深くなった。", ["安心"], created_at="2026-07-04T00:00:02+00:00")
            existing_text = "笑ったあとに戻ってくる静けさは、私たちに何を知らせているのでしょうか。"
            pc.create_emergent_question(conn, existing_text, "", [id1, id2], space_id=pc.default_space_id())
            original_count = len(pc.active_questions(conn, space_id=pc.default_space_id(), limit=20))

            def fake_available():
                return True

            def fake_run_codex(prompt, schema, timeout=300):
                return {
                    "questions": [
                        {
                            "question": "身体がゆるんだ瞬間に見えてくる安心は、次の一歩をどこへ導いてくれるのでしょうか。",
                            "context": "笑いと安心のあいだで生まれた問いです。",
                            "star_ids": [id1, id2],
                        },
                        {
                            "question": existing_text,
                            "context": "",
                            "star_ids": [id1, id2],
                        },
                    ]
                }

            weave.codex_available = fake_available
            weave._run_codex = fake_run_codex

            result = weave.emerge_questions(conn, pc.default_space_id())

            self.assertEqual(result, {"new_questions": 1})
            questions = pc.active_questions(conn, space_id=pc.default_space_id(), limit=20)
            self.assertEqual(len(questions), original_count + 1)
            inserted = next(q for q in questions if q["question"] != existing_text)
            self.assertEqual(inserted["question"], "身体がゆるんだ瞬間に見えてくる安心は、次の一歩をどこへ導いてくれるのでしょうか。")
            self.assertEqual([star["id"] for star in inserted["source_stars"]], [id1, id2])
            relay = conn.execute(
                "SELECT kind, star_who FROM relay_events WHERE kind='question_born' ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            self.assertEqual(relay["kind"], "question_born")
            self.assertEqual(relay["star_who"], "Aさん・Bさん")

    def test_weave_links_codex_rejects_invalid_and_self_links(self):
        with pc.connect(self.db_path) as conn:
            id1 = self._insert_star(conn, "c1", "Aさん", "笑いが戻った。", ["笑い"], created_at="2026-07-04T00:00:01+00:00")
            id2 = self._insert_star(conn, "c2", "Bさん", "笑いが広がった。", ["笑い"], created_at="2026-07-04T00:00:02+00:00")
            id3 = self._insert_star(conn, "c3", "Cさん", "笑いが深まった。", ["笑い"], created_at="2026-07-04T00:00:03+00:00")

            def fake_available():
                return True

            def fake_run_codex(prompt, schema, timeout=300):
                return {
                    "links": [
                        {"a": "missing", "b": id1, "reason": "見えない星です。"},
                        {"a": id2, "b": id2, "reason": "自分自身へのリンクです。"},
                        {"a": id1, "b": id3, "reason": "笑いの余韻が静かに響き合っています。"},
                    ]
                }

            weave.codex_available = fake_available
            weave._run_codex = fake_run_codex

            result = weave.weave_links(conn, pc.default_space_id())

            self.assertEqual(result, {"new_links": 1, "processed": 3, "mode": "codex"})
            rows = conn.execute(
                "SELECT star_a, star_b, reason FROM star_links WHERE space_id=? ORDER BY created_at ASC",
                (pc.default_space_id(),),
            ).fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual({rows[0]["star_a"], rows[0]["star_b"]}, {id1, id3})
            self.assertIn("響き合っています", rows[0]["reason"])

    def test_weave_links_falls_back_when_codex_times_out(self):
        with pc.connect(self.db_path) as conn:
            self._insert_star(conn, "t1", "Aさん", "笑いで肩がほどけた。", ["笑い"], created_at="2026-07-04T00:00:01+00:00")
            self._insert_star(conn, "t2", "Bさん", "笑いのあとに安心した。", ["笑い"], created_at="2026-07-04T00:00:02+00:00")

            def fake_available():
                return True

            def fake_run_codex(prompt, schema, timeout=300):
                raise subprocess.TimeoutExpired(cmd="codex", timeout=timeout)

            import subprocess

            weave.codex_available = fake_available
            weave._run_codex = fake_run_codex

            result = weave.weave_links(conn, pc.default_space_id())

            self.assertEqual(result, {"new_links": 1, "processed": 2, "mode": "fallback"})
            self.assertEqual(
                conn.execute("SELECT COUNT(*) AS count FROM star_links WHERE space_id=?", (pc.default_space_id(),)).fetchone()["count"],
                1,
            )

    def test_weave_links_falls_back_when_codex_returns_empty_parse(self):
        with pc.connect(self.db_path) as conn:
            self._insert_star(conn, "p1", "Aさん", "笑いで肩がほどけた。", ["笑い"], created_at="2026-07-04T00:00:01+00:00")
            self._insert_star(conn, "p2", "Bさん", "笑いのあとに安心した。", ["笑い"], created_at="2026-07-04T00:00:02+00:00")

            weave.codex_available = lambda: True
            weave._run_codex = lambda prompt, schema, timeout=300: {}

            result = weave.weave_links(conn, pc.default_space_id())

            self.assertEqual(result, {"new_links": 1, "processed": 2, "mode": "fallback"})
            self.assertEqual(
                conn.execute("SELECT COUNT(*) AS count FROM star_links WHERE space_id=?", (pc.default_space_id(),)).fetchone()["count"],
                1,
            )


if __name__ == "__main__":
    unittest.main()
