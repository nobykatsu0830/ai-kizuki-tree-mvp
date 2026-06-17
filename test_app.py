import os
import json
import tempfile
import unittest
from pathlib import Path

import app
from pipeline import common as pipeline_common
from pipeline import export_obsidian


class LineWebhookHelpersTest(unittest.TestCase):
    def test_extract_line_text_messages_ignores_non_text_events(self):
        payload = {
            "events": [
                {
                    "type": "message",
                    "replyToken": "reply-1",
                    "source": {"userId": "U123"},
                    "message": {"type": "text", "text": "今日の気づきです"},
                },
                {
                    "type": "message",
                    "replyToken": "reply-2",
                    "source": {"userId": "U456"},
                    "message": {"type": "image", "id": "img"},
                },
                {"type": "follow", "replyToken": "reply-3", "source": {"userId": "U789"}},
            ]
        }

        messages = app.extract_line_text_messages(payload)

        self.assertEqual(
            messages,
            [
                {
                    "external_user_id": "U123",
                    "body": "今日の気づきです",
                    "reply_token": "reply-1",
                }
            ],
        )

    def test_build_line_reply_payload(self):
        payload = app.build_line_reply_payload("reply-1", "受け取りました")

        self.assertEqual(
            payload,
            {
                "replyToken": "reply-1",
                "messages": [{"type": "text", "text": "受け取りました"}],
            },
        )

    def test_build_consent_reply_payload_has_three_choices(self):
        payload = app.build_consent_reply_payload("reply-1")

        actions = payload["messages"][0]["quickReply"]["items"]
        self.assertEqual([a["action"]["data"] for a in actions], ["consent=name", "consent=anonymous", "consent=reject"])

    def test_extract_line_postbacks(self):
        payload = {
            "events": [
                {"type": "postback", "replyToken": "r1", "source": {"userId": "U1"}, "postback": {"data": "consent=anonymous"}},
                {"type": "message", "message": {"type": "text", "text": "ignore"}},
            ]
        }

        self.assertEqual(
            app.extract_line_postbacks(payload),
            [{"external_user_id": "U1", "reply_token": "r1", "data": "consent=anonymous"}],
        )

    def test_parse_line_reply_command_extracts_parent_and_body(self):
        parsed = app.parse_line_reply_command("返信:abc123 今日の気づきに共感しました")

        self.assertEqual(parsed, {"parent_id": "abc123", "body": "今日の気づきに共感しました"})

    def test_parse_line_reply_command_returns_none_for_normal_message(self):
        self.assertIsNone(app.parse_line_reply_command("今日の気づきです"))

    def test_apply_consent_accepts_and_publishes_immediately(self):
        original_db_path = app.DB_PATH
        with tempfile.TemporaryDirectory() as tmpdir:
            app.DB_PATH = Path(tmpdir) / "kizuki_tree.sqlite3"
            try:
                app.init_db()
                rid = app.insert_reflection(
                    "line",
                    "LINE参加者",
                    "ジブリッシュで笑ったあと、肩の力が抜けたことに気づきました。",
                    external_user_id="UIMMEDIATE",
                    status="awaiting_consent",
                )

                accepted_id = app.apply_consent("UIMMEDIATE", "anonymous")

                self.assertEqual(accepted_id, rid)
                with app.db() as conn:
                    row = conn.execute("SELECT status, display_name, approved_at FROM reflections WHERE id=?", (rid,)).fetchone()
                self.assertEqual(row["status"], "approved")
                self.assertEqual(row["display_name"], "匿名参加者")
                self.assertTrue(row["approved_at"])
            finally:
                app.DB_PATH = original_db_path

    def test_hide_reflection_removes_star_from_public_cosmos(self):
        original_db_path = app.DB_PATH
        with tempfile.TemporaryDirectory() as tmpdir:
            app.DB_PATH = Path(tmpdir) / "kizuki_tree.sqlite3"
            try:
                app.init_db()
                rid = app.insert_reflection("line", "参加者", "笑いの講座で安心が戻りました。", status="approved")

                app.hide_reflection(rid)

                self.assertFalse(any(row["id"] == rid for row in app.cosmos_rows()))
            finally:
                app.DB_PATH = original_db_path

    def test_cosmos_filter_clears_selected_detail_and_keeps_autorotation(self):
        shell = app.COSMOS_SHELL

        self.assertIn("selected=null;setActiveLabel(null);detail.classList.remove('show')", shell)
        self.assertIn("if(!drag&&!reduceMotion)rotY+=", shell)
        self.assertIn("tagEl.textContent=filter!=='all'", shell)

    def test_cosmos_shell_has_empty_state_overlay(self):
        shell = app.COSMOS_SHELL

        # 星0件のときに詩的な空状態オーバーレイを表示する
        self.assertIn('id="cosmosEmpty"', shell)
        self.assertIn("この宇宙は、まだ夜の底にある。", shell)
        self.assertIn("if(!nodes.length){document.getElementById('cosmosEmpty').classList.add('show')", shell)

    def test_cosmos_shell_caches_projection_for_performance(self):
        shell = app.COSMOS_SHELL

        # O(1)の親参照（nodeMap）と1フレーム1回の投影キャッシュを使う
        self.assertIn("const nodeMap=new Map(nodes.map(n=>[n.id,n]))", shell)
        self.assertIn("n._p=sphere(n);n._s=project(n._p)", shell)
        # キャッシュ座標を使う線描画関数に置き換わっている
        self.assertIn("function drawLineP(", shell)
        self.assertNotIn("project(sphere(a)),pb=project(sphere(b))", shell)

    def test_worldview_cta_defaults_to_internal_submit(self):
        cta = pipeline_common.worldview_cta()

        self.assertEqual(cta["join_url"], "/submit")
        self.assertTrue(cta["join_label"])
        self.assertTrue(cta["join_note"])

    def test_worldview_cta_is_overridable_for_world_expansion(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            wv_path = Path(tmpdir) / "worldview.yaml"
            wv_path.write_text(
                "space_id: demo\n"
                "cta:\n"
                "  join_label: 笑いの教養講座を見る\n"
                "  join_url: https://example.com/course\n"
                "  join_note: 世界中の気づきが、ここから星になる。\n",
                encoding="utf-8",
            )
            merged = pipeline_common.load_worldview(wv_path)
            self.assertEqual(merged["cta"]["join_url"], "https://example.com/course")
            self.assertEqual(merged["cta"]["join_label"], "笑いの教養講座を見る")

    def test_public_page_includes_closing_cta(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_db_path = app.DB_PATH
            app.DB_PATH = Path(tmpdir) / "kizuki_tree.sqlite3"
            try:
                app.init_db()
                app.insert_reflection("line", "Aさん", "待つことの気づき", status="approved")
                html = app.public_page().decode("utf-8")
                self.assertIn("joincta", html)
                self.assertIn("Join the Universe", html)
            finally:
                app.DB_PATH = original_db_path

    def test_build_consent_reply_payload_includes_parent_context_when_reply(self):
        payload = app.build_consent_reply_payload("reply-1", parent_title="Aさんの気づき")

        self.assertIn("Aさんの気づきへの返信", payload["messages"][0]["text"])

    def test_get_line_profile_name_returns_empty_without_token_or_user(self):
        original = os.environ.pop("LINE_CHANNEL_ACCESS_TOKEN", None)
        try:
            self.assertEqual(app.get_line_profile_name("U123"), "")
            self.assertEqual(app.get_line_profile_name(""), "")
        finally:
            if original is not None:
                os.environ["LINE_CHANNEL_ACCESS_TOKEN"] = original

    def test_consent_name_keeps_stored_profile_display_name(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_db_path = app.DB_PATH
            app.DB_PATH = Path(tmpdir) / "kizuki_tree.sqlite3"
            try:
                app.init_db()
                # webhookでLINEプロフィール名が保存された状態を再現
                rid = app.insert_reflection(
                    "line", "大久保信克", "今日の気づきです。",
                    external_user_id="Uabc", status="awaiting_consent",
                )
                returned = app.apply_consent("Uabc", "name")
                self.assertEqual(returned, rid)
                row = next(r for r in app.rows("approved") if r["id"] == rid)
                # 「名前ありでOK」では本名がそのまま残る
                self.assertEqual(row["display_name"], "大久保信克")
            finally:
                app.DB_PATH = original_db_path

    def test_cosmos_nodes_marks_replies_with_parent_id(self):
        rows = [
            {"id": "root1", "parent_id": None, "display_name": "Aさん", "body": "待つことの気づき", "tags": json.dumps(["待つ"], ensure_ascii=False), "source": "line"},
            {"id": "child1", "parent_id": "root1", "display_name": "Bさん", "body": "私も共感しました", "tags": json.dumps(["安心"], ensure_ascii=False), "source": "line"},
        ]

        nodes = app.cosmos_nodes(rows)

        self.assertEqual(nodes[0]["id"], "root1")
        self.assertIsNone(nodes[0]["parent_id"])
        self.assertEqual(nodes[1]["parent_id"], "root1")
        self.assertEqual(nodes[1]["reply_to"], "Aさんの気づき")

    def test_generate_material_derivatives_creates_local_outputs(self):
        derivatives = app.generate_material_derivatives(
            "待つことは何もしないことではない",
            "気づきの宇宙 第7回",
            "待つ時間に身体がそわそわしました。相手を変えようとしていた自分に気づきました。",
        )

        self.assertIn("待つ時間", derivatives["summary"])
        self.assertEqual(len(derivatives["questions"]), 3)
        self.assertIn("待つ", derivatives["tags"])
        self.assertIn("身体感覚", derivatives["tags"])
        self.assertIn("次回ライブ", derivatives["next_live_question"])
        self.assertIn("ひとこと送ってください", derivatives["participant_text_draft"])
        self.assertIn("音声でも大丈夫です", derivatives["audio_text_draft"])

    def test_weekly_insights_groups_approved_reflections_by_tag(self):
        rows = [
            {"id": "r1", "parent_id": None, "display_name": "Aさん", "body": "待つことが苦手だと気づきました。本当は安心したかったです。", "tags": json.dumps(["待つ", "安心"], ensure_ascii=False)},
            {"id": "r2", "parent_id": None, "display_name": "Bさん", "body": "身体がそわそわして、自分の焦りに気づきました。", "tags": json.dumps(["身体感覚"], ensure_ascii=False)},
            {"id": "r3", "parent_id": "r1", "display_name": "Cさん", "body": "私も待つ場面で相手を変えようとしていました。", "tags": json.dumps(["待つ"], ensure_ascii=False)},
        ]

        insights = app.weekly_insights(rows)

        self.assertEqual(insights["total"], 3)
        self.assertEqual(insights["frequent_themes"][0]["tag"], "待つ")
        self.assertEqual(insights["frequent_themes"][0]["count"], 2)
        self.assertTrue(any("次回ライブ" in q for q in insights["next_live_questions"]))
        self.assertGreaterEqual(insights["deep_candidates"][0]["score"], insights["deep_candidates"][-1]["score"])

    def test_init_db_creates_phase1_tables_and_migrates_reflection_columns(self):
        original_db_path = app.DB_PATH
        with tempfile.TemporaryDirectory() as tmpdir:
            app.DB_PATH = Path(tmpdir) / "kizuki_tree.sqlite3"
            try:
                app.init_db()
                with app.db() as conn:
                    tables = {
                        row["name"]
                        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                    }
                    reflection_columns = {
                        row["name"] for row in conn.execute("PRAGMA table_info(reflections)")
                    }
                self.assertIn("reflections", tables)
                self.assertIn("media_materials", tables)
                for table in ("spaces", "source_recordings", "derived_contents", "constellations", "constellation_stars", "followups", "reflux_notifications"):
                    self.assertIn(table, tables)
                for column in ("space_id", "star_kind", "visibility", "embedding_json", "constellation_id"):
                    self.assertIn(column, reflection_columns)
            finally:
                app.DB_PATH = original_db_path

    def test_worldview_loader_reads_simple_nested_yaml(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "worldview.yaml"
            path.write_text('space_id: test-space\nterms:\n  universe: テスト宇宙\nmessages:\n  consent_prompt: "掲載してよいですか？"\n', encoding="utf-8")

            worldview = pipeline_common.load_worldview(path)

        self.assertEqual(worldview["space_id"], "test-space")
        self.assertEqual(worldview["terms"]["universe"], "テスト宇宙")
        self.assertEqual(worldview["messages"]["consent_prompt"], "掲載してよいですか？")

    def test_pipeline_recording_loop_creates_derived_contents(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "kizuki.sqlite3"
            text_path = Path(tmpdir) / "recording.txt"
            text_path.write_text("えーと、待つ時間に身体がそわそわしました。安心したかったと気づきました。", encoding="utf-8")
            pipeline_common.init_db(db_path)
            with pipeline_common.connect(db_path) as conn:
                rid = pipeline_common.create_source_recording(conn, "テスト原液", str(text_path), "2026-06-12T09:00:00+00:00")
                results = pipeline_common.run_recording_pipeline(conn, rid)
                derived_types = {row["content_type"] for row in conn.execute("SELECT content_type FROM derived_contents")}

        self.assertEqual([r["step"] for r in results], ["transcribe", "clean", "digest", "summarize"])
        self.assertIn("transcript_raw", derived_types)
        self.assertIn("transcript_clean", derived_types)
        self.assertIn("digest", derived_types)
        self.assertIn("summary", derived_types)

    def test_constellate_weekly_report_and_followup_suggestions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "kizuki.sqlite3"
            pipeline_common.init_db(db_path)
            with pipeline_common.connect(db_path) as conn:
                for rid, name, body in (
                    ("s1", "Aさん", "待つことが苦手だと気づきました。安心したいです。"),
                    ("s2", "Bさん", "待つ時間に身体がそわそわしました。安心に戻りたいです。"),
                    ("s3", "Cさん", "待つ場面で何を見ればいいですか？"),
                ):
                    conn.execute(
                        """
                        INSERT INTO reflections
                        (id, parent_id, source, display_name, body, tags, status, created_at, space_id, star_kind, visibility)
                        VALUES (?, NULL, 'test', ?, ?, ?, 'approved', ?, ?, ?, 'universe')
                        """,
                        (
                            rid,
                            name,
                            body,
                            json.dumps(["待つ"], ensure_ascii=False),
                            pipeline_common.now_iso(),
                            pipeline_common.default_space_id(),
                            pipeline_common.infer_star_kind(body),
                        ),
                    )
                created = pipeline_common.constellate_stars(conn)
                report = pipeline_common.generate_weekly_report(conn, output_dir=Path(tmpdir) / "reports")
                suggestions = pipeline_common.suggest_followups(conn)
                embeddings = [row["embedding_json"] for row in conn.execute("SELECT embedding_json FROM reflections ORDER BY id")]
                report_exists = Path(report["path"]).exists()

        self.assertTrue(created)
        self.assertGreaterEqual(report["constellation_count"], 1)
        self.assertTrue(report_exists)
        self.assertTrue(suggestions)
        self.assertTrue(all(embeddings))
    def test_constellate_groups_secondary_tags_as_public_constellations(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "kizuki.sqlite3"
            pipeline_common.init_db(db_path)
            with pipeline_common.connect(db_path) as conn:
                for rid, name, body, tags in (
                    ("s1", "Aさん", "笑ったあとに安心が戻りました。", ["笑い", "安心"]),
                    ("s2", "Bさん", "身体がゆるんで安心しました。", ["身体感覚", "安心"]),
                ):
                    conn.execute(
                        """
                        INSERT INTO reflections
                        (id, parent_id, source, display_name, body, tags, status, created_at, space_id, star_kind, visibility)
                        VALUES (?, NULL, 'test', ?, ?, ?, 'approved', ?, ?, ?, 'universe')
                        """,
                        (
                            rid,
                            name,
                            body,
                            json.dumps(tags, ensure_ascii=False),
                            pipeline_common.now_iso(),
                            pipeline_common.default_space_id(),
                            pipeline_common.infer_star_kind(body),
                        ),
                    )
                pipeline_common.constellate_stars(conn)
                names = [row["name"] for row in conn.execute("SELECT name FROM constellations ORDER BY name")]

        self.assertIn("安心の星座", names)

    def test_export_obsidian_writes_separate_public_vault_without_second_brain_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "kizuki.sqlite3"
            vault_path = Path(tmpdir) / "kizuki-universe-vault"
            pipeline_common.init_db(db_path)
            with pipeline_common.connect(db_path) as conn:
                for rid, status, visibility, name, body in (
                    ("pub1", "approved", "universe", "匿名参加者", "待つ時間に身体がそわそわしました。安心したかったです。"),
                    ("private1", "pending", "universe", "未承認さん", "これはまだ公開しない声です。"),
                    ("self1", "approved", "self_only", "本人のみ", "これは本人だけの星です。"),
                ):
                    conn.execute(
                        """
                        INSERT INTO reflections
                        (id, parent_id, source, display_name, body, tags, status, created_at, space_id, star_kind, visibility)
                        VALUES (?, NULL, 'line', ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            rid,
                            name,
                            body,
                            json.dumps(["待つ"], ensure_ascii=False),
                            status,
                            pipeline_common.now_iso(),
                            pipeline_common.default_space_id(),
                            pipeline_common.infer_star_kind(body),
                            visibility,
                        ),
                    )
                pipeline_common.constellate_stars(conn)
                result = export_obsidian.export_public_vault(conn, vault_path)

            self.assertEqual(result["vault_path"], str(vault_path))
            self.assertIn("kizuki-universe-vault", result["vault_path"])
            self.assertNotIn("2nd-Brain", result["vault_path"])
            self.assertTrue((vault_path / "00_はじめに.md").exists())
            self.assertTrue((vault_path / "Stars" / "pub1.md").exists())
            self.assertFalse((vault_path / "Stars" / "private1.md").exists())
            self.assertFalse((vault_path / "Stars" / "self1.md").exists())
            star_text = (vault_path / "Stars" / "pub1.md").read_text(encoding="utf-8")
            self.assertIn("type: star", star_text)
            self.assertIn("待つ時間", star_text)
            self.assertIn("[[Tags/待つ]]", star_text)


if __name__ == "__main__":
    unittest.main()
