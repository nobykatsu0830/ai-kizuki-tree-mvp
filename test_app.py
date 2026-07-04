import os
import json
import tempfile
import unittest
from pathlib import Path

import app
import batch_classify
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

    def test_parse_tag_json_extracts_array_from_messy_text(self):
        self.assertEqual(app._parse_tag_json('テーマは ["待つ","安心"] です'), ["待つ", "安心"])
        self.assertEqual(app._parse_tag_json('```json\n["笑い"]\n```'), ["笑い"])
        self.assertEqual(app._parse_tag_json("not json"), [])
        # 重複除去と最大3個
        self.assertEqual(app._parse_tag_json('["a","a","b","c","d"]'), ["a", "b", "c"])

    def test_llm_infer_tags_returns_none_without_api_key(self):
        original = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            self.assertIsNone(app.llm_infer_tags("笑った", ["笑い"]))
        finally:
            if original is not None:
                os.environ["ANTHROPIC_API_KEY"] = original

    def test_resolve_tags_falls_back_and_grows_theme_vocabulary(self):
        original = os.environ.pop("ANTHROPIC_API_KEY", None)
        original_db_path = app.DB_PATH
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                app.DB_PATH = Path(tmpdir) / "kizuki_tree.sqlite3"
                app.init_db()
                app.insert_reflection("web", "テスト", "ジブリッシュで笑ったら肩の力が抜けた", status="approved")
                with app.db() as conn:
                    names = pipeline_common.active_theme_names(conn)
                # キーワードフォールバックでも、付いたテーマが語彙表に蓄積される
                self.assertTrue(set(names) & {"笑い", "ジブリッシュ", "身体感覚"})
        finally:
            app.DB_PATH = original_db_path
            if original is not None:
                os.environ["ANTHROPIC_API_KEY"] = original

    def test_ensure_theme_is_idempotent_and_skips_unclassified(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_db_path = app.DB_PATH
            app.DB_PATH = Path(tmpdir) / "kizuki_tree.sqlite3"
            try:
                app.init_db()
                with app.db() as conn:
                    pipeline_common.ensure_theme(conn, "新しいテーマ")
                    pipeline_common.ensure_theme(conn, "新しいテーマ")
                    pipeline_common.ensure_theme(conn, "未分類")
                    pipeline_common.ensure_theme(conn, "")
                    names = pipeline_common.active_theme_names(conn)
                self.assertEqual(names.count("新しいテーマ"), 1)
                self.assertNotIn("未分類", names)
            finally:
                app.DB_PATH = original_db_path

    def test_init_db_seeds_themes_from_existing_reflection_tags(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_db_path = app.DB_PATH
            app.DB_PATH = Path(tmpdir) / "kizuki_tree.sqlite3"
            try:
                app.init_db()
                with app.db() as conn:
                    conn.execute(
                        "INSERT INTO reflections (id, source, display_name, body, tags, status, created_at, space_id, star_kind, visibility) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?)",
                        ("seedrow", "web", "X", "本文", json.dumps(["継承テーマ", "未分類"], ensure_ascii=False),
                         "approved", app.now_iso(), pipeline_common.default_space_id(), "insight", "universe"),
                    )
                    conn.execute("DELETE FROM themes")
                # 再初期化でテーマ語彙が既存タグから取り込まれる
                app.init_db()
                with app.db() as conn:
                    names = pipeline_common.active_theme_names(conn)
                self.assertIn("継承テーマ", names)
                self.assertNotIn("未分類", names)
            finally:
                app.DB_PATH = original_db_path

    def test_batch_clean_themes_dedupes_caps_and_handles_empty(self):
        self.assertEqual(batch_classify.clean_themes(["待つ", "待つ", "安心"]), ["待つ", "安心"])
        self.assertEqual(batch_classify.clean_themes(["a", "b", "c", "d"]), ["a", "b", "c"])
        self.assertEqual(batch_classify.clean_themes(["未分類", ""]), ["未分類"])
        self.assertEqual(batch_classify.clean_themes([]), ["未分類"])

    def test_batch_parse_result_json_handles_noise(self):
        self.assertEqual(
            batch_classify._parse_result_json('説明文 {"results":[{"id":"x","themes":["笑い"]}]} 末尾'),
            {"results": [{"id": "x", "themes": ["笑い"]}]},
        )
        self.assertEqual(batch_classify._parse_result_json("これはJSONではない"), {"results": []})

    def test_batch_build_prompt_lists_existing_themes_and_posts(self):
        rows = [{"id": "r1", "body": "待つことが苦手だと気づいた"}]
        prompt = batch_classify.build_prompt(["待つ", "安心"], rows)
        self.assertIn("既存のテーマ: 待つ、安心", prompt)
        self.assertIn("id: r1", prompt)
        self.assertIn("待つことが苦手", prompt)

    def test_batch_apply_classification_updates_tags_themes_and_marks_done(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "kizuki_tree.sqlite3"
            pipeline_common.init_db(db_path)
            with pipeline_common.connect(db_path) as conn:
                conn.execute(
                    "INSERT INTO reflections (id, source, display_name, body, tags, status, created_at, space_id, star_kind, visibility) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    ("b1", "line", "Aさん", "ジブリッシュで笑った", json.dumps(["未分類"], ensure_ascii=False),
                     "approved", pipeline_common.now_iso(), pipeline_common.default_space_id(), "insight", "universe"),
                )
            # codexが返したと想定した結果を適用
            with pipeline_common.connect(db_path) as conn:
                updated = batch_classify.apply_classification(conn, [{"id": "b1", "themes": ["笑い", "解放感"]}])
            self.assertEqual(updated, 1)
            with pipeline_common.connect(db_path) as conn:
                row = conn.execute("SELECT tags, themed_at FROM reflections WHERE id='b1'").fetchone()
                names = pipeline_common.active_theme_names(conn)
            self.assertEqual(json.loads(row["tags"]), ["笑い", "解放感"])
            self.assertTrue(row["themed_at"])  # 処理済みの印
            self.assertIn("解放感", names)  # 新テーマが語彙に創発

    def test_resolve_space_path_parses_slug(self):
        self.assertEqual(app.resolve_space_path("/s/demo/cosmos"), ("demo", "/cosmos"))
        self.assertEqual(app.resolve_space_path("/s/demo"), ("demo", "/"))
        self.assertEqual(app.resolve_space_path("/s/demo/"), ("demo", "/"))
        # /s/ プレフィックスなしはデフォルト宇宙、パスはそのまま
        self.assertEqual(app.resolve_space_path("/cosmos"), (pipeline_common.DEFAULT_SPACE_ID, "/cosmos"))
        self.assertEqual(app.resolve_space_path("/"), (pipeline_common.DEFAULT_SPACE_ID, "/"))

    def test_space_base_prefix(self):
        try:
            pipeline_common.set_current_space(pipeline_common.DEFAULT_SPACE_ID, pipeline_common.load_worldview())
            self.assertEqual(app.space_base(), "")  # デフォルト宇宙は接頭辞なし
            pipeline_common.set_current_space("demo", {"space_id": "demo"})
            self.assertEqual(app.space_base(), "/s/demo")
        finally:
            pipeline_common.clear_current_space()

    def test_spaces_are_isolated_in_queries(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_db_path = app.DB_PATH
            app.DB_PATH = Path(tmpdir) / "k.sqlite3"
            try:
                app.init_db()
                with app.db() as conn:
                    pipeline_common.create_space(conn, "demo", "デモ宇宙", {"terms": {"universe": "デモ宇宙"}})
                # noby宇宙に星
                pipeline_common.set_current_space("noby-universe", pipeline_common.load_worldview())
                app.insert_reflection("web", "ノビー", "noby星", status="approved")
                pipeline_common.clear_current_space()
                # demo宇宙に星
                with app.db() as conn:
                    wv = pipeline_common.load_worldview_for_space(conn, "demo")
                pipeline_common.set_current_space("demo", wv)
                app.insert_reflection("web", "太郎", "demo星", status="approved")
                demo_rows = [r["body"] for r in app.rows("approved")]
                pipeline_common.clear_current_space()
                pipeline_common.set_current_space("noby-universe", pipeline_common.load_worldview())
                noby_rows = [r["body"] for r in app.rows("approved")]
                pipeline_common.clear_current_space()
                self.assertEqual(demo_rows, ["demo星"])
                self.assertEqual(noby_rows, ["noby星"])
            finally:
                pipeline_common.clear_current_space()
                app.DB_PATH = original_db_path

    def test_unknown_space_worldview_falls_back_to_default(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "k.sqlite3"
            pipeline_common.init_db(db_path)
            with pipeline_common.connect(db_path) as conn:
                wv = pipeline_common.load_worldview_for_space(conn, "noby-universe")
            self.assertEqual(wv["space_id"], "noby-universe")
            self.assertTrue(wv["terms"]["universe"])

    def test_rename_theme_replaces_tags_everywhere(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "k.sqlite3"
            pipeline_common.init_db(db_path)
            with pipeline_common.connect(db_path) as conn:
                conn.execute(
                    "INSERT INTO reflections (id, source, display_name, body, tags, status, created_at, space_id, star_kind, visibility) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    ("r1", "web", "X", "本文", json.dumps(["待つ", "安心"], ensure_ascii=False),
                     "approved", pipeline_common.now_iso(), pipeline_common.default_space_id(), "insight", "universe"),
                )
                pipeline_common.ensure_theme(conn, "待つ")
            with pipeline_common.connect(db_path) as conn:
                pipeline_common.rename_theme(conn, "待つ", "待つこと")
            with pipeline_common.connect(db_path) as conn:
                row = conn.execute("SELECT tags FROM reflections WHERE id='r1'").fetchone()
                names = pipeline_common.active_theme_names(conn)
            self.assertEqual(json.loads(row["tags"]), ["待つこと", "安心"])
            self.assertIn("待つこと", names)
            self.assertNotIn("待つ", names)

    def test_merge_theme_dedupes_and_hides_source(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "k.sqlite3"
            pipeline_common.init_db(db_path)
            with pipeline_common.connect(db_path) as conn:
                conn.execute(
                    "INSERT INTO reflections (id, source, display_name, body, tags, status, created_at, space_id, star_kind, visibility) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    ("r1", "web", "X", "本文", json.dumps(["笑い", "ユーモア"], ensure_ascii=False),
                     "approved", pipeline_common.now_iso(), pipeline_common.default_space_id(), "insight", "universe"),
                )
            with pipeline_common.connect(db_path) as conn:
                pipeline_common.merge_theme(conn, "ユーモア", "笑い")
            with pipeline_common.connect(db_path) as conn:
                row = conn.execute("SELECT tags FROM reflections WHERE id='r1'").fetchone()
                hidden = pipeline_common.hidden_theme_names(conn)
                active = pipeline_common.active_theme_names(conn)
            self.assertEqual(json.loads(row["tags"]), ["笑い"])  # 重複排除
            self.assertIn("ユーモア", hidden)  # 統合元は非表示で履歴に残る
            self.assertNotIn("ユーモア", active)

    def test_set_theme_status_hides_from_public(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "k.sqlite3"
            pipeline_common.init_db(db_path)
            with pipeline_common.connect(db_path) as conn:
                pipeline_common.ensure_theme(conn, "未分類っぽいテーマ")
                pipeline_common.set_theme_status(conn, "未分類っぽいテーマ", "hidden")
            with pipeline_common.connect(db_path) as conn:
                self.assertIn("未分類っぽいテーマ", pipeline_common.hidden_theme_names(conn))

    def test_cosmos_nodes_filters_hidden_themes(self):
        rows = [{"id": "n1", "parent_id": None, "display_name": "A", "body": "本文",
                 "tags": json.dumps(["笑い", "ボツ"], ensure_ascii=False), "source": "line"}]
        nodes = app.cosmos_nodes(rows, hidden={"ボツ"})
        self.assertEqual(nodes[0]["tags"], ["笑い"])

    def test_themes_admin_page_renders(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_db_path = app.DB_PATH
            app.DB_PATH = Path(tmpdir) / "k.sqlite3"
            try:
                app.init_db()
                app.insert_reflection("web", "X", "ジブリッシュで笑った", status="approved")
                html = app.themes_admin_page().decode("utf-8")
                self.assertIn("テーマを整える", html)
                self.assertIn("/admin/themes", html)
            finally:
                app.DB_PATH = original_db_path

    def test_batch_pending_rows_excludes_already_themed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "kizuki_tree.sqlite3"
            pipeline_common.init_db(db_path)
            with pipeline_common.connect(db_path) as conn:
                for rid, themed in (("p1", None), ("p2", pipeline_common.now_iso())):
                    conn.execute(
                        "INSERT INTO reflections (id, source, display_name, body, tags, status, created_at, space_id, star_kind, visibility, themed_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (rid, "web", "X", "本文", "[]", "approved", pipeline_common.now_iso(),
                         pipeline_common.default_space_id(), "insight", "universe", themed),
                    )
            with pipeline_common.connect(db_path) as conn:
                ids = [r["id"] for r in batch_classify.pending_rows(conn, 10)]
            self.assertIn("p1", ids)
            self.assertNotIn("p2", ids)  # 既に分類済みは対象外

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


class RelayEventsTest(unittest.TestCase):
    def _insert(self, conn, rid, name, body, tags, created_at=None, space_id=None):
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
                created_at or pipeline_common.now_iso(),
                space_id or pipeline_common.default_space_id(),
                pipeline_common.infer_star_kind(body),
            ),
        )

    def test_constellate_records_born_event(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "k.sqlite3"
            pipeline_common.init_db(db_path)
            with pipeline_common.connect(db_path) as conn:
                self._insert(conn, "s1", "Aさん", "待つことが苦手でした。", ["待つ"])
                self._insert(conn, "s2", "Bさん", "待つ時間にそわそわしました。", ["待つ"])
                pipeline_common.constellate_stars(conn)
                feed = pipeline_common.relay_feed(conn)
        born = [e for e in feed if e["kind"] == "constellation_born"]
        self.assertTrue(born)
        self.assertTrue(born[0]["constellation_name"].endswith("の星座"))
        self.assertIn("Aさん", born[0]["star_who"])
        self.assertEqual(born[0]["detail"]["star_count"], 2)

    def test_constellate_records_grew_event(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "k.sqlite3"
            pipeline_common.init_db(db_path)
            with pipeline_common.connect(db_path) as conn:
                self._insert(conn, "s1", "Aさん", "待つことが苦手でした。", ["待つ"])
                self._insert(conn, "s2", "Bさん", "待つ時間にそわそわしました。", ["待つ"])
                pipeline_common.constellate_stars(conn)
                self._insert(conn, "s3", "Cさん", "待つ練習を始めました。", ["待つ"])
                pipeline_common.constellate_stars(conn)
                feed = pipeline_common.relay_feed(conn)
        grew = [e for e in feed if e["kind"] == "constellation_grew"]
        self.assertTrue(grew)
        self.assertIn("Cさん", grew[0]["star_who"])

    def test_all_time_connects_old_stars(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "k.sqlite3"
            pipeline_common.init_db(db_path)
            with pipeline_common.connect(db_path) as conn:
                self._insert(conn, "old", "古株さん", "笑いで安心しました。", ["安心"], created_at="2026-01-05T00:00:00+00:00")
                self._insert(conn, "new", "新人さん", "今日も安心が戻りました。", ["安心"])
                created = pipeline_common.constellate_stars(conn, all_time=True)
                star_ids = {
                    row["star_id"]
                    for row in conn.execute("SELECT star_id FROM constellation_stars")
                }
        self.assertTrue(created)
        self.assertIn("old", star_ids)
        self.assertIn("new", star_ids)

    def test_relay_feed_orders_newest_first(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "k.sqlite3"
            pipeline_common.init_db(db_path)
            with pipeline_common.connect(db_path) as conn:
                conn.execute(
                    "INSERT INTO relay_events (id, space_id, kind, constellation_id, constellation_name, star_id, star_who, detail_json, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    ("e1", pipeline_common.default_space_id(), "constellation_born", "c1", "古い星座", None, "X", "{}", "2026-01-01T00:00:00+00:00"),
                )
                conn.execute(
                    "INSERT INTO relay_events (id, space_id, kind, constellation_id, constellation_name, star_id, star_who, detail_json, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    ("e2", pipeline_common.default_space_id(), "constellation_born", "c2", "新しい星座", None, "Y", "{}", "2026-02-01T00:00:00+00:00"),
                )
                feed = pipeline_common.relay_feed(conn)
        self.assertEqual(feed[0]["constellation_name"], "新しい星座")

    def test_relay_events_isolated_by_space(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "k.sqlite3"
            pipeline_common.init_db(db_path)
            with pipeline_common.connect(db_path) as conn:
                pipeline_common.create_space(conn, "other-space", "別の宇宙", {})
                pipeline_common.record_relay_event(conn, pipeline_common.default_space_id(), "constellation_born", "c1", "自分の星座", [], 2)
                pipeline_common.record_relay_event(conn, "other-space", "constellation_born", "c2", "他人の星座", [], 2)
                mine = pipeline_common.relay_feed(conn, space_id=pipeline_common.default_space_id())
                theirs = pipeline_common.relay_feed(conn, space_id="other-space")
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0]["constellation_name"], "自分の星座")
        self.assertEqual(len(theirs), 1)
        self.assertEqual(theirs[0]["constellation_name"], "他人の星座")


class MultiTenantAdminTest(unittest.TestCase):
    def _rec(self, conn, rid, space_id):
        conn.execute(
            "INSERT INTO source_recordings (id, space_id, title, audio_path, recorded_at, kind, status, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (rid, space_id, f"rec {rid}", f"/x/{rid}.m4a", pipeline_common.now_iso(), "live", "raw", pipeline_common.now_iso()),
        )

    def test_space_admin_token_hashed_and_isolated(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "k.sqlite3"
            pipeline_common.init_db(db_path)
            with pipeline_common.connect(db_path) as conn:
                pipeline_common.create_space(conn, "space-a", "A")
                pipeline_common.create_space(conn, "space-b", "B")
                pipeline_common.set_space_admin_token(conn, "space-a", "alpha-pass")
                pipeline_common.set_space_admin_token(conn, "space-b", "beta-pass")
                ha = pipeline_common.get_space_admin_hash(conn, "space-a")
                hb = pipeline_common.get_space_admin_hash(conn, "space-b")
        # 正しいパスのハッシュと一致、他スペースのパスでは不一致、平文は保存されない
        self.assertEqual(ha, pipeline_common.hash_admin_token("alpha-pass"))
        self.assertNotEqual(ha, pipeline_common.hash_admin_token("beta-pass"))
        self.assertNotEqual(ha, hb)
        self.assertNotIn("alpha-pass", ha)

    def test_space_admin_unset_and_clear_returns_none(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "k.sqlite3"
            pipeline_common.init_db(db_path)
            with pipeline_common.connect(db_path) as conn:
                pipeline_common.create_space(conn, "space-x", "X")
                unset = pipeline_common.get_space_admin_hash(conn, "space-x")
                pipeline_common.set_space_admin_token(conn, "space-x", "p")
                pipeline_common.set_space_admin_token(conn, "space-x", "")  # 解除
                cleared = pipeline_common.get_space_admin_hash(conn, "space-x")
        self.assertIsNone(unset)
        self.assertIsNone(cleared)

    def test_admin_recording_rows_scoped_to_current_space(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "k.sqlite3"
            pipeline_common.init_db(db_path)
            app.DB_PATH = db_path
            with pipeline_common.connect(db_path) as conn:
                pipeline_common.create_space(conn, "space-a", "A")
                pipeline_common.create_space(conn, "space-b", "B")
                self._rec(conn, "ra1", "space-a")
                self._rec(conn, "ra2", "space-a")
                self._rec(conn, "rb1", "space-b")
            pipeline_common.set_current_space("space-a", pipeline_common.DEFAULT_WORLDVIEW)
            rows = app.source_recording_rows()
            pipeline_common.clear_current_space()
        ids = {r["id"] for r in rows}
        self.assertEqual(ids, {"ra1", "ra2"})  # 他スペースのrb1は混ざらない

    def test_admin_constellation_rows_scoped_to_current_space(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "k.sqlite3"
            pipeline_common.init_db(db_path)
            app.DB_PATH = db_path
            with pipeline_common.connect(db_path) as conn:
                pipeline_common.create_space(conn, "space-a", "A")
                pipeline_common.create_space(conn, "space-b", "B")
                pipeline_common.upsert_constellation(conn, "Aの星座", "s", "q", "2026-06-15", space_id="space-a")
                pipeline_common.upsert_constellation(conn, "Bの星座", "s", "q", "2026-06-15", space_id="space-b")
            pipeline_common.set_current_space("space-a", pipeline_common.DEFAULT_WORLDVIEW)
            names = {c["name"] for c in app.constellation_rows()}
            pipeline_common.clear_current_space()
        self.assertEqual(names, {"Aの星座"})  # 他スペースのBの星座は混ざらない


class ResonanceAndQuestionsTest(unittest.TestCase):
    """光の糸（意味リンク）・星々から生まれた問い・星詳細ページの循環。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._original_db_path = app.DB_PATH
        self._original_api_key = os.environ.pop("ANTHROPIC_API_KEY", None)
        app.DB_PATH = Path(self._tmpdir.name) / "kizuki_tree.sqlite3"
        app.init_db()

    def tearDown(self):
        app.DB_PATH = self._original_db_path
        if self._original_api_key is not None:
            os.environ["ANTHROPIC_API_KEY"] = self._original_api_key
        pipeline_common.clear_current_space()
        self._tmpdir.cleanup()

    def _add_star(self, rid, body, name="参加者", status="approved", visibility="universe", space_id=None, tags=("笑い",)):
        with app.db() as conn:
            conn.execute(
                "INSERT INTO reflections (id, source, display_name, body, tags, status, created_at, space_id, star_kind, visibility) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (rid, "web", name, body, json.dumps(list(tags), ensure_ascii=False),
                 status, pipeline_common.now_iso(), space_id or pipeline_common.default_space_id(), "insight", visibility),
            )

    def test_star_page_shows_resonating_stars_with_reason(self):
        self._add_star("st1", "待つことが苦手だと気づいた", name="Aさん")
        self._add_star("st2", "焦りの奥に願いがあった", name="Bさん")
        with app.db() as conn:
            pipeline_common.upsert_star_link(conn, "st1", "st2", "どちらも「待つ」ことへの気づき")
        page = app.star_page("st1").decode("utf-8")
        self.assertIn("待つことが苦手", page)
        self.assertIn("響き合う", page)
        self.assertIn("Bさん", page)
        self.assertIn("どちらも「待つ」ことへの気づき", page)
        self.assertIn('/star/st2', page)

    def test_star_page_hides_unlisted_and_unknown_stars(self):
        self._add_star("st3", "承認待ちの星", status="pending")
        self.assertIsNone(app.star_page("st3"))
        self.assertIsNone(app.star_page("no-such-star"))

    def test_star_page_promises_weaving_when_no_links(self):
        self._add_star("st4", "まだひとりぼっちの星")
        page = app.star_page("st4").decode("utf-8")
        self.assertIn("まだ糸は張られていません", page)
        self.assertIn("宇宙の織り手", page)

    def test_star_page_excludes_partner_hidden_after_weaving(self):
        self._add_star("st5", "残る星")
        self._add_star("st6", "あとで非公開になる星", name="Cさん")
        with app.db() as conn:
            pipeline_common.upsert_star_link(conn, "st5", "st6", "理由")
            conn.execute("UPDATE reflections SET status='rejected' WHERE id='st6'")
        page = app.star_page("st5").decode("utf-8")
        self.assertNotIn("Cさん", page)
        self.assertIn("まだ糸は張られていません", page)

    def test_submit_with_question_links_star_and_records_relay(self):
        self._add_star("q-src1", "素材の星")
        with app.db() as conn:
            qid = pipeline_common.create_emergent_question(conn, "テストの問いですか。", "", ["q-src1"])
        rid = app.insert_reflection("web", "回答者", "問いへの応答です", status="approved", question_id=qid)
        with app.db() as conn:
            row = conn.execute("SELECT question_id FROM reflections WHERE id=?", (rid,)).fetchone()
            relays = conn.execute("SELECT kind FROM relay_events WHERE kind='question_answered'").fetchall()
        self.assertEqual(row["question_id"], qid)
        self.assertEqual(len(relays), 1)
        page = app.star_page(rid).decode("utf-8")
        self.assertIn("この星が応えた問い", page)
        self.assertIn("テストの問いですか。", page)

    def test_submit_ignores_question_from_other_space(self):
        with app.db() as conn:
            pipeline_common.create_space(conn, "other-uni", "他の宇宙")
            other_qid = pipeline_common.create_emergent_question(conn, "他宇宙の問い。", "", [], space_id="other-uni")
        rid = app.insert_reflection("web", "回答者", "クロステナント防止テスト", status="approved", question_id=other_qid)
        with app.db() as conn:
            row = conn.execute("SELECT question_id FROM reflections WHERE id=?", (rid,)).fetchone()
        self.assertIsNone(row["question_id"])

    def test_public_page_shows_emergent_questions_and_theme_filter(self):
        self._add_star("pp1", "笑いで軽くなった", name="Aさん", tags=("笑い",))
        self._add_star("pp2", "身体がゆるんだ", name="Bさん", tags=("身体感覚",))
        with app.db() as conn:
            pipeline_common.create_emergent_question(conn, "笑いのあと、何が残りますか。", "", ["pp1", "pp2"])
        page = app.public_page().decode("utf-8")
        self.assertIn("星々から生まれた問い", page)
        self.assertIn("笑いのあと、何が残りますか。", page)
        self.assertIn("あなたの星を灯す", page)
        self.assertIn("Aさん・Bさん", page)  # 問いの出自（還流の可視化）
        self.assertIn("テーマでたどる", page)
        filtered = app.public_page(theme="身体感覚").decode("utf-8")
        self.assertIn("身体がゆるんだ", filtered)
        self.assertNotIn("笑いで軽くなった", filtered)
        unknown = app.public_page(theme="存在しないテーマ").decode("utf-8")
        self.assertIn("笑いで軽くなった", unknown)  # 不明テーマは全件表示に戻す

    def test_public_page_shows_resonance_line_and_star_links(self):
        self._add_star("pr1", "星その1", name="Aさん")
        self._add_star("pr2", "星その2", name="Bさん")
        with app.db() as conn:
            pipeline_common.upsert_star_link(conn, "pr1", "pr2", "理由の一行")
        page = app.public_page().decode("utf-8")
        self.assertIn("響き合っています", page)
        self.assertIn('/star/pr1', page)

    def test_relay_feed_wording_for_new_kinds(self):
        sid = pipeline_common.default_space_id()
        with app.db() as conn:
            pipeline_common.record_relay(conn, sid, "link_woven", {"link_count": 3, "pairs": []})
            pipeline_common.record_relay(conn, sid, "question_born", {"question": "生まれた問い？", "source_whos": ["Aさん"]}, star_who="Aさん")
            pipeline_common.record_relay(conn, sid, "question_answered", {"question": "生まれた問い？", "who": "Bさん"}, star_who="Bさん")
        page = app.public_page().decode("utf-8")
        self.assertIn("光の糸が張られました", page)
        self.assertIn("問いが生まれました", page)
        self.assertIn("問いに応えました", page)

    def test_cosmos_page_embeds_links_and_escapes(self):
        self._add_star("cs1", "宇宙の星1")
        self._add_star("cs2", "宇宙の星2")
        with app.db() as conn:
            pipeline_common.upsert_star_link(conn, "cs1", "cs2", "静かな理由</script>")
        page = app.cosmos_page().decode("utf-8")
        self.assertIn("starLinks=", page)
        self.assertIn("cs1", page)
        self.assertNotIn("理由</script>", page)  # JSON埋め込みで</がエスケープされる
        self.assertIn("drawThread", page)
        self.assertIn("flyTo", page)

    def test_staticize_replaces_new_routes(self):
        html_text = app.staticize_html(
            b'<a href="/star/abc123">x</a><a href="/submit?question_id=q1">y</a><a href="/?theme=%E7%AC%91%E3%81%84">z</a>'
        )
        self.assertNotIn('href="/star/', html_text)
        self.assertNotIn("question_id=", html_text)
        self.assertNotIn('href="/?theme=', html_text)

    def test_submit_page_renders_question_context(self):
        with app.db() as conn:
            qid = pipeline_common.create_emergent_question(conn, "フォームに出る問い。", "", [])
        page = app.submit_page(question_id=qid).decode("utf-8")
        self.assertIn("この問いに応える", page)
        self.assertIn("フォームに出る問い。", page)
        page2 = app.submit_page(question_id="unknown-q").decode("utf-8")
        self.assertNotIn("この問いに応える", page2)  # 不明な問いは通常フォーム


class JoinPasswordTest(unittest.TestCase):
    """投稿パスワード（合言葉）のDAL層。未設定=誰でも投稿可の後方互換を含む。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._original_db_path = app.DB_PATH
        app.DB_PATH = Path(self._tmpdir.name) / "kizuki_tree.sqlite3"
        app.init_db()

    def tearDown(self):
        app.DB_PATH = self._original_db_path
        pipeline_common.clear_current_space()
        self._tmpdir.cleanup()

    def test_unset_join_password_returns_none(self):
        with app.db() as conn:
            self.assertIsNone(pipeline_common.get_space_join_hash(conn, pipeline_common.default_space_id()))

    def test_set_and_verify_join_password_hash(self):
        sid = pipeline_common.default_space_id()
        with app.db() as conn:
            pipeline_common.set_space_join_password(conn, sid, "ことのは")
            h = pipeline_common.get_space_join_hash(conn, sid)
        self.assertEqual(h, pipeline_common.hash_admin_token("ことのは"))
        self.assertNotEqual(h, pipeline_common.hash_admin_token("ちがうことば"))

    def test_clearing_join_password_reopens_submission(self):
        sid = pipeline_common.default_space_id()
        with app.db() as conn:
            pipeline_common.set_space_join_password(conn, sid, "secret")
            self.assertIsNotNone(pipeline_common.get_space_join_hash(conn, sid))
            pipeline_common.set_space_join_password(conn, sid, "")
            self.assertIsNone(pipeline_common.get_space_join_hash(conn, sid))

    def test_join_password_is_per_space(self):
        with app.db() as conn:
            pipeline_common.create_space(conn, "other-uni", "他の宇宙")
            pipeline_common.set_space_join_password(conn, "other-uni", "よそのことば")
            self.assertIsNone(pipeline_common.get_space_join_hash(conn, pipeline_common.default_space_id()))
            self.assertIsNotNone(pipeline_common.get_space_join_hash(conn, "other-uni"))

    def test_join_gate_page_renders_form_and_error(self):
        page = app.join_gate_page(next_path="/submit").decode("utf-8")
        self.assertIn("合言葉", page)
        self.assertIn('action="/join"', page)
        error_page = app.join_gate_page(next_path="/submit", error=True).decode("utf-8")
        self.assertIn("合言葉が違うようです", error_page)


if __name__ == "__main__":
    unittest.main()
