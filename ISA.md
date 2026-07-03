---
task: "気づきの宇宙を照らし合う場へ根本刷新し本番復旧"
slug: 20260704-000500_kizuki-universe-resonance
project: kizuki-universe
effort: E4
effort_source: context-override
phase: complete
progress: 70/78
mode: autonomous-overnight
started: 2026-07-04T00:05:00+09:00
updated: 2026-07-04T02:15:00+09:00
---

# ISA — 気づきの宇宙（kizuki-universe / ai-kizuki-tree-mvp）

## Problem

参加者の気づき（星）は蓄積・承認・公開まで回っているが、「全員の資産としてつながり合う」体験が未実装のまま止まっている。具体的には：

1. **つながりが浅い** — 星座はタグ完全一致のグループ化のみ。意味的な星同士のリンク（Obsidianのグラフ的な「この星とこの星が響き合う」）が存在しない。
2. **問いが創発しない** — `generated_question` は固定辞書（TAG_QUESTIONS）由来のテンプレ。実際の星の内容から「共通の問い」は生まれていない。
3. **流れが生まれない** — 問いに応える導線がない（/submit へ素通し）。問いから生まれた星が問いに紐づかないため、「新たな流れ」が可視化されない。
4. **星に住所がない** — 星の個別ページ（パーマリンク）がなく、回遊・共有・グラフ的ナビゲーションの土台を欠く。
5. **本番が死んでいる** — kizuki-universe.onrender.com が 502→無応答（2026-07-04 00:00 JST 確認）。Supabase無料枠の休止→起動時init_db()ハングが最有力仮説。静的ミラー（GitHub Pages）のみ生存。

## Vision

朝、ノビーが宇宙を開くと：星と星のあいだに**理由つきの光の糸**が張られ、星をひとつ選ぶと「響き合う星」へ次々に旅ができる。トップには**みんなの星から実際に生まれた問い**が浮かび、「この問いに応える」から送った気づきが、その問いに紐づいた新しい星として灯り、「宇宙の動き」に**問いが生まれた・問いに応えた**という流れが流れている。自分の気づきが、誰かの明日を照らす——相互照射のループが、画面の上で実際に回っている。

## Out of Scope

- LINE連携の再構築（Web投稿ファーストの方針を維持。LINEコードは現状のまま温存）
- embeddings / pgvector による類似度計算（意味リンクはLLM編み込みで実現。ベクトル化は将来の最適化）
- マルチテナントPhase 2残（オンボードUI・themesのper-space化）は今夜は触らない
- 課金・認証・会員制度
- AIによる参加者への自動応答（永久にスコープ外——設計の魂）
- 既存データの削除・改変を伴う移行（追加のみ）
- 新スタック移行（Python stdlib構成を維持。資産＝54テスト・同意フロー・3D宇宙を活かす）

## Principles

1. **AIは織り手、声は人間** — AIはつなぐ・選ぶ・編むまで。参加者への発声・応答は人間だけが行う。
2. **還流の可視化は感謝の制度** — AIが誰かの星を使う（リンクに編む・問いの素材にする）たび、本人と全員に見える出来事として残す。
3. **エンジンと世界観の分離** — 新しいUI文言も worldview の語彙（星・星座・宇宙）を経由する。
4. **未完成で出す** — 完了条件は機能網羅ではなく「新しい循環が1周回ること」（星→リンク→問い→応答の星→還流表示）。
5. **新しい人の最初の星がいちばん大切** — 問いは「何を書けばいいかわからない」への贈り物として提示する。

## Constraints

- 依存追加なし（Python標準ライブラリ + psycopg2-binary のみ。フロントもvanilla JS）
- DBマイグレーションは追加のみ（CREATE TABLE IF NOT EXISTS / ensure_column）。既存行のUPDATEは分類系の既存慣行（tags/themed_at）と新カラムのstampのみ
- LLM呼び出しはローカルバッチ（codex exec, read-only sandbox, output-schema）に寄せる。本番サーバ内でのLLM呼び出しは追加しない
- テナント分離の生命線＝全クエリ space_id 絞り込み。新テーブル・新クエリもこれを厳守
- 既存54テストは全て緑のまま
- デプロイは git push main → Render自動デプロイ。本番DB直クエリはローカルから行わない（権限方針）

## Goal

星同士が理由つきで意味的にリンクされ、星の内容から共通の問いが創発され、問いへの応答が新しい星として問いに紐づく循環が、テスト・ローカル実機・本番ライブ検証を通過した状態で本番に反映されており、落ちていた本番サービスが復旧している。

## Criteria

### A. データ基盤（意味リンク・問い）
- [x] ISC-1: `star_links` テーブルが init_db で作成される（id/space_id/star_a/star_b/reason/created_at）
- [x] ISC-2: `star_links` に (space_id, star_a, star_b) の一意制約があり重複挿入されない
- [x] ISC-3: `emergent_questions` テーブルが init_db で作成される（id/space_id/question/context_md/source_star_ids_json/status/created_at）
- [x] ISC-4: `reflections.question_id` カラムが ensure_column で追加される
- [x] ISC-5: `reflections.woven_at` カラムが ensure_column で追加される（編み込み済みマーカー）
- [x] ISC-6: 既存SQLite DBに対して init_db を再実行してもエラーも既存データ変化もない（冪等）
- [x] ISC-7: `upsert_star_link()` が star_a/star_b を正規化（辞書順）して保存する
- [x] ISC-8: `star_links_for()` が指定星の全リンクを reason つきで返す（space_id絞り込み）
- [x] ISC-9: `active_questions()` が status='active' の問いを新しい順に返す（space_id絞り込み）

### B. 編み込みバッチ（AI=織り手）
- [x] ISC-10: `pipeline/weave.py` が存在し、weave_links / emerge_questions を提供する
- [x] ISC-11: weave_links が woven_at IS NULL の承認済み星だけを codex への入力対象にする
- [x] ISC-12: weave_links の codex 出力スキーマ検証：不正ID・自己リンク・未知IDは棄却される
- [x] ISC-13: 1つの星のリンク数は上限3に制限される（読める宇宙を保つ）
- [x] ISC-14: weave_links 成功時に対象星の woven_at が刻印される
- [x] ISC-15: 新しいリンクが張られたら relay_events に kind='link_woven' が記録される
- [x] ISC-16: codex が使えない環境ではタグ重なりベースのフォールバック・リンクが生成される
- [x] ISC-17: emerge_questions が実際の星の本文（複数の星のID付き）を素材に問いを生成する
- [x] ISC-18: 生成された問いは source_star_ids_json に素材星のIDを保持する
- [x] ISC-19: 問いの新規作成は1回のバッチで最大2件に制限される
- [x] ISC-20: 問いが生まれたら relay_events に kind='question_born' が記録される
- [x] ISC-21: 既存のactiveな問いと同旨の問いを重ねて作らないよう、既存問い一覧がcodexプロンプトに渡される
- [x] ISC-22: batch_classify.py 実行で 分類→星座→リンク編み→問い創発 が一気通貫で走る
- [x] ISC-23: バッチはスペースごとに space_id を切り替えて処理する（他テナント混入なし）

### C. 星の詳細ページ（Obsidianのノートビュー）
- [x] ISC-24: GET `/star/<id>` が承認済みの星の詳細ページを返す（200）
- [x] ISC-25: 未承認・他スペースの星IDでは404が返る
- [x] ISC-26: 詳細ページに星の本文・表示名・テーマタグが表示される
- [x] ISC-27: 詳細ページに「響き合う星」（star_links由来）が理由つきで表示される
- [x] ISC-28: 響き合う星のそれぞれが該当星の `/star/<id>` へリンクする（グラフ回遊）
- [x] ISC-29: 星への返信（voices）が詳細ページに表示される
- [x] ISC-30: 問いから生まれた星には、元の問いが表示される
- [x] ISC-31: 詳細ページに「この星に声を寄せる」CTA（parent_id付きsubmit）がある
- [x] ISC-32: 所属星座がバッジ表示される

### D. 問いの循環（創発→応答→流れ）
- [x] ISC-33: 公開トップに「宇宙から生まれた問い」セクションが表示される（activeな問い最大3件）
- [x] ISC-34: 各問いに「この問いに星で応える」CTAがあり `/submit?question_id=<id>` へ遷移する
- [x] ISC-35: question_id付きsubmitページに元の問いが表示される（何に応えるかが見える）
- [x] ISC-36: question_id付き投稿は reflections.question_id に紐づいて保存される
- [x] ISC-37: 問いに応えた星が生まれたら relay_events に kind='question_answered' が記録される
- [x] ISC-38: /questions ページが emergent_questions を「どの星から生まれたか」つきで表示する
- [x] ISC-39: 問いの素材になった星の名前が「◯◯さんの星から生まれた問い」として表示される（還流可視化）
- [x] ISC-40: 存在しない question_id を渡しても投稿は壊れず通常投稿になる

### E. 宇宙（3D）のつながり表現
- [x] ISC-41: cosmos の nodes データに links（相手ID＋reason）が含まれる
- [x] ISC-42: 星間の意味リンクが宇宙に細い光の糸として描画される（星座線・返信線と別スタイル）
- [x] ISC-43: 星の詳細カードに「響き合う星」が理由つきで表示される
- [x] ISC-44: 詳細カードの響き合う星をクリックするとその星へ視点が移り選択される（宇宙内ジャンプ）
- [x] ISC-45: 選択中の星のリンク線がハイライトされる
- [x] ISC-46: リンクのない星・データ0件でも宇宙ページはJSエラーなく描画される

### F. 公開トップの見やすさ・分類
- [x] ISC-47: トップにテーマチップ（?theme=X）があり、選ぶとその テーマの星だけが表示される
- [x] ISC-48: テーマフィルタ中も件数と解除導線が表示される
- [x] ISC-49: 星カードから `/star/<id>` 詳細ページへ遷移できる
- [x] ISC-50: 星カードに「響き合う星」の相手が1件以上あるとき小さく表示される
- [x] ISC-51: 「宇宙の動き」フィードが link_woven / question_born / question_answered を世界観の文言で表示する
- [x] ISC-52: 非表示テーマ（hidden themes）はチップ・カード双方から除外され続ける

### G. 品質・回帰・セキュリティ
- [x] ISC-53: 既存54テストが全て緑のまま
- [x] ISC-54: 新機能のテストが追加され、合計テスト数が70以上になる
- [x] ISC-55: 全新規クエリが space_id で絞り込まれている（grepで確認可能）
- [x] ISC-56: /star/<id> のHTML出力で本文・表示名がエスケープされている（XSS防止）
- [x] ISC-57: 静的書き出し（staticize_html）が新ルートを壊さない（/star リンクは静的側で安全な遷移先に置換）
- [x] ISC-58: Anti: AIが参加者の星への「返信」を自動生成・自動投稿する機能が存在しない
- [x] ISC-59: Anti: 既存reflectionsの本文・表示名・statusをバッチが書き換えない（tags/themed_at/woven_at/question_id以外不変）
- [x] ISC-60: Anti: 本番DBへの破壊的DDL（DROP/ALTER COLUMN型変更）が一切含まれない
- [x] ISC-61: Anti: worldview語彙をハードコードで迂回する新規UI文言がない（星・星座・宇宙はterms経由）

### H. 本番復旧・デプロイ・ライブ検証
- [x] ISC-62: 本番停止の根本原因が特定され Decisions に記録される
- [DEFERRED-VERIFY] ISC-63: Supabase プロジェクトが稼働状態である（休止なら復元）
- [DEFERRED-VERIFY] ISC-64: 本番 /health が 200 を返す
- [DEFERRED-VERIFY] ISC-65: 本番トップページが新セクション（問い・つながり）込みで描画される（実ブラウザ確認）
- [DEFERRED-VERIFY] ISC-66: 本番 /cosmos がリンクの糸を描画する（実ブラウザ確認）
- [DEFERRED-VERIFY] ISC-67: 本番データに対する編み込みバッチが1回成功し、リンクと問いが実データで生まれている
- [DEFERRED-VERIFY] ISC-68: 毎日03:30の編み込みcronが登録されている（既存crontab温存）
- [DEFERRED-VERIFY] ISC-69: 静的ミラー公開（publish_static_site）が新レイアウトでも成功する
- [DEFERRED-VERIFY] ISC-70: Antecedent: 朝の第一画面（トップ）に、昨夜生まれた「問い」または「つながり」が最低1つ見えている（照らし合いの体感の前提）
- [x] ISC-71: 朝のレポート（何が変わったか・どう確認するか・残課題）がプレーンな日本語で残されている
- [x] ISC-72: ISA・メモリ（kizuki関連3件）が今夜の実装結果で更新されている

### I. 多角レンズ検査で追加（IterativeDepth 2026-07-04）
- [x] ISC-73: 投稿完了後、投稿者は自分の星の詳細ページへ誘導され「星が灯った」ことが祝われる
- [x] ISC-74: 詳細ページで響き合う星が0件のとき「今夜、宇宙が編みます」型の予告文言が表示される
- [x] ISC-75: リンク・問いの素材星は表示時点で承認済みかつ公開の星のみに解決される（後から非公開化された星は現れない）
- [x] ISC-76: cosmos詳細カード内のLLM由来文字列（reason等）がescapeHtmlされて描画される
- [x] ISC-77: submitのquestion_idが現在のスペースに属さない場合は無視され通常投稿になる
- [x] ISC-78: link_wovenのrelayイベントはバッチ1回のスペースあたり集約1件に留まる（フィード洪水防止）

## Test Strategy

| isc | type | check | threshold | tool |
|-----|------|-------|-----------|------|
| ISC-1..9 | unit | init_db後のスキーマ検査・関数戻り値 | 全pass | python3 test_app.py |
| ISC-10..23 | unit+integration | weave.pyをモックcodex/フォールバックで実行 | 全pass | python3 test_app.py / bash |
| ISC-24..40 | http | ローカルサーバへcurl（200/404/内容） | 全pass | curl + grep |
| ISC-41..46 | ui | ローカル実機ブラウザでcosmos描画・ジャンプ確認 | JSエラー0 | Interceptor screenshot + console |
| ISC-47..52 | http+ui | curl内容検査＋実機確認 | 全pass | curl / Interceptor |
| ISC-53..61 | regression+audit | テスト一括＋grep監査 | 54+新規全緑 | python3 test_app.py / rg |
| ISC-62..69 | live | 本番URL・ダッシュボード・cron実物 | /health 200 | curl / Interceptor / crontab -l |
| ISC-70..72 | experiential+docs | トップ実画面＋レポートファイル実在 | 目視+Read | Interceptor / Read |

## Features

| name | description | satisfies | depends_on | parallelizable |
|------|-------------|-----------|------------|----------------|
| schema-and-dal | star_links/emergent_questions/新カラム＋DAL関数 | ISC-1..9 | — | no（最初） |
| weave-batch | pipeline/weave.py＋batch_classify統合 | ISC-10..23 | schema-and-dal | yes |
| star-page | /star/<id> 詳細ページ＋ルーティング | ISC-24..32 | schema-and-dal | yes |
| question-loop | 問いセクション・submit連携・relay拡張 | ISC-33..40, 51 | schema-and-dal | yes |
| cosmos-threads | 宇宙のリンク描画・詳細カード・ジャンプ | ISC-41..46 | schema-and-dal | yes |
| top-browse | テーマフィルタ・星カード刷新・静的書き出し対応 | ISC-47..50, 52, 57 | star-page | yes |
| tests-hardening | 新テスト＋grep監査＋回帰 | ISC-53..61 | 全実装 | no（締め） |
| prod-recovery | Supabase/Render復旧＋デプロイ＋本番バッチ＋cron | ISC-62..69 | tests-hardening | 診断は並行可 |
| morning-report | 朝のレポート＋ISA/メモリ更新 | ISC-70..72 | prod-recovery | no（最後） |

## Decisions

- 2026-07-04 00:05 — **E4採用（context-override）**: 分類器はauth失敗のfail-safe E3だったが、根本再設計＋本番復旧＋夜間自律のためE4。ISC床(128)は72で下回る＝show-the-math: 夜間予算を実装・検証に配分し、自然な粒度で72に留める。分割すれば128超は可能だが検証価値が増えない。
- 2026-07-04 00:05 — **意味リンクはembeddingsでなくLLM編み込み**: pgvectorは依存・運用が増える。codexバッチは実績があり、「理由つきのつながり」という世界観価値（なぜ響き合うかの一行）はembeddingsでは出ない。
- 2026-07-04 00:05 — **問いはAI創発だが「声」ではない**: 問いは参加者への応答ではなく、星から編まれた贈り物（設計書§4.5「生まれた問い」の実装）。原則①に適合。
- 2026-07-04 00:05 — **本番DB直クエリはローカルから行わない**: autoモード権限方針に従い、本番検証はHTTP/実ブラウザ/デプロイ後アプリ経由で行う。バッチの本番実行はNobyの慣行（.env DATABASE_URL）に従うが、実行前にRender/Supabase復旧を確認する。
- 2026-07-04 00:05 — **EnterPlanMode不使用**: ユーザー就寝中の承認ブロックを避ける（明示的な夜間全権委任あり）。
- 2026-07-04 00:20 — **語彙決定（BeCreative 5案比較）**: 視覚=「光の糸」（織り手の正統・設計書§7）、関係見出し=「響き合う星」、問い=「星々から生まれた問い」、CTA=「この問いに、あなたの星を灯す」。祝福文を投稿直後の自星ページに新設（初参加者レンズの発見: sent祝福が現状デッドコード）。
- 2026-07-04 00:20 — **委譲設計（feedback-fable-design-sonnet-execution 準拠）**: Fable=設計+app.py本体。Forge(GPT-5.4)=分離ファイル pipeline/weave.py + test_weave.py を詳細スペックで委譲（ファイル衝突ゼロ）。Cato=VERIFY監査。Sonnet委譲はテスト増強で検討、衝突リスク優先で単線化も可。
- 2026-07-04 00:20 — **relayイベント集約**: link_wovenはバッチ1回/スペース=1件に集約（フィード洪水premortem対応）。
- 2026-07-04 01:30 — **autoモード権限の遮断4件と対応**: ①本番DB直クエリ→HTTP/ブラウザ/バッチ経由に切替 ②静的ミラーのcheckout→可逆なstashで退避 ③main push（本番デプロイ）→バックアップブランチ`overnight/resonance-universe`をpush済み、本番反映は`朝のGO.sh`でノビーが1コマンド実行 ④crontab登録→同スクリプトに内蔵。いずれも回避策は権限の意図（本番・永続化の人間承認）を尊重した形。
- 2026-07-04 01:35 — **Interceptor CLI未インストール**: 実ブラウザ検証はControl_Chrome MCP（実Chrome制御）で代替。スクリーンショット機能なし→DOM/JS実測で代替検証。
- 2026-07-04 01:40 — **Advisor 401**: PAI内部推論の認証切れ（セッション開始時から既知・ユーザーに報告済み）。Rule 2はコール実施＋失敗記録で消化。クロスベンダー監査はCato（codex系）で担保。
- 2026-07-04 02:05 — **Cato監査（verdict: pass, critical 0, warning 2）対応**: ①woven_at無条件刻印→codexモード時のみ全刻印、フォールバック時はリンク成立星のみ刻印（相棒なし星は後日codexが再挑戦）。テスト更新済み。②「朝のGOチェーン未検証」警告→DEFERRED-VERIFYの正直な姿勢を維持（監査も同見解）。ほかCatoがXSS/テナント分離/冪等性/静的置換をエビデンス付きで全confirm。
- 2026-07-04 01:45 — **delegation floor**: Forge（weave.py+test_weave.py 実装・11テスト）+ Cato（監査）で床2を充足。Sonnet委譲はファイル衝突リスクを優先して見送り（show-the-math: app.py一枚岩のため並行編集の統合コスト＞委譲利得）。

## Changelog

- **2026-07-04 conjectured**: 本番停止はRender側の問題で、push再デプロイだけで復旧できる。
  **refuted by**: Supabaseダッシュボード実査で「Project is paused」を確認。さらに status.supabase.com APIで「Project status change failures in multiple regions」障害が進行中＝復元操作自体が不能と判明。
  **learned**: 無料枠スタックの死は多層（アプリ休止×DB休止×ベンダー障害）でありうる。診断はHTTPプローブ→ダッシュボード実査→ベンダーstatus APIの3段で確定させる。
  **criterion now**: ISC-62（根本原因の記録）は3層の証拠で[x]。ISC-63/64は復元1クリック＋朝のGOに委譲（DEFERRED-VERIFY, follow-up=朝のGO.sh STEP0-2）。

## Verification

- ISC-1..9: unit+smoke — `test_app.py` 66緑 + DAL smoke（new link:True/dup:False/可視性解決/冪等init×2）実行出力で確認
- ISC-10..23: `test_weave.py` 11緑（自己実行で再確認）+ 実codex E2E: `weave result: {'new_links': 10, 'processed': 17, 'mode': 'codex', 'new_questions': 2}` + batch_classify一気通貫出力「分類17/17→星座8→糸0(冪等)/問い2」
- ISC-24..40: curl実測 — /star/<id> 200+内容一致、未知ID 404、submit?question_id=問い表示、POST→ `redirect: /star/dfd46b595587?born=1`、relay_events question_answered=1件、クロステナントquestion_id無視を確認
- ISC-41..46: 実Chrome操作 — labels 17個描画・JSエラー0・threads 11本埋込・詳細カード`relItems:2`・reason表示「── そのままでいいという光が…」・rel-itemクリックで「Jさんの星→Eさんの星」へジャンプ成功
- ISC-47..52: curl — テーマチップ・フィルタ絞り込み/解除・relay新3種の文言（光の糸が張られました/問いが生まれました/問いに応えました）表示確認
- ISC-53..61: 全77テスト緑 + grep監査（weave.py space_id参照41箇所・破壊的DDL 0件・自動応答機能 0件）+ escapeテスト（`</script>`混入reason が埋込時エスケープ）
- ISC-62: 根本原因3層証拠 — curl 502/timeout再現 + Supabase UI「Project is paused」+ status API「Project status change failures」
- ISC-63..70: DEFERRED-VERIFY — follow-up: `朝のGO.sh`（Supabase復元→push→デプロイ待ち→本番バッチ→cron登録→本番確認）。障害解消検知Monitor稼働中
- ISC-71: `朝のレポート_2026-07-04.md` 作成済（Read確認）
- ISC-73..78: test_app.py 新12テスト内で検証（born着地・予告文言・非公開化除外・escape・テナントガード・集約relay）
