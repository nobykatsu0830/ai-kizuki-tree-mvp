# AI気づきツリー MVP

公式LINE/Slack/Discord/WhatsAppから届く参加者の感想を、掲載OK後すぐに公開ページへ反映する最小プロトタイプです。問題がある投稿だけ、事務局があとから非公開にします。

## 起動

```bash
cd /Users/noby/product/ai-kizuki-tree-mvp
python3 app.py
```

ブラウザで開く：

- 公開ページ: http://127.0.0.1:8787/
- 投稿デモ: http://127.0.0.1:8787/submit
- 事務局公開管理: http://127.0.0.1:8787/admin
- 3D気づき宇宙: http://127.0.0.1:8787/cosmos
- 原液から教材化（旧Factory）: http://127.0.0.1:8787/factory
- AI編集者モード: http://127.0.0.1:8787/weekly
- 原液登録・ローカルパイプライン: http://127.0.0.1:8787/admin/recordings
- フォローアップ候補: http://127.0.0.1:8787/admin/followup-suggestions
- フォローアップ登録: http://127.0.0.1:8787/admin/followups
- 公開用Obsidian Vault書き出し: http://127.0.0.1:8787/admin/obsidian-vault
- 星座JSON: http://127.0.0.1:8787/api/constellations

## いま実装済み

- 公開ページ
- 投稿フォーム（LINE webhookの代替デモ）
- 事務局公開管理画面（問題投稿の非公開）
- SQLite保存
- 親感想＋返信1階層
- 簡易タグ付け
- LINE本番Webhook受信
- LINE署名検証（`.env` に `LINE_CHANNEL_SECRET` がある場合）
- LINE自動返信：掲載同意クイックリプライ
- 掲載同意フロー：名前あり/匿名/掲載しない
- 3D気づき宇宙：公開データを球体上に表示
- LINEから特定の気づきへ返信：`返信:気づきID 返信本文` 形式で親子関係を保持
- Media Factory / 原液から教材化：Nobyの音声文字起こし・ライブメモ・走り書きを保存し、ローカルルールで教材化メモを生成
- 教材化メモ：要約、参加者への3つの問い、LINE短文案、音声投稿案、次回ライブへの問い、タグ候補
- AI編集者モード：公開中の声をタグ別にまとめ、頻出テーマ、深い振り返り候補、次回ライブへ返す問いを表示
- Phase 1 DB基盤：`spaces` / `source_recordings` / `derived_contents` / `constellations` / `constellation_stars` / `followups` / `reflux_notifications`
- 既存の `reflections` を「星」として扱う拡張列：`space_id` / `star_kind` / `visibility` / `embedding_json` / `constellation_id`
- `worldview.yaml` による世界観語彙（宇宙・星・星座・星雲・推し等）の外部化
- `pipeline/` 配下の標準ライブラリのみのCLI：文字起こしプレースホルダ、整文、ダイジェスト、要約、ローカル埋め込み、星座化、週次レポート、フォローアップ候補
- `/admin/recordings` で原液を登録し、同名 `.txt` または `.txt/.md` パスをローカルに読み込んで派生コンテンツを作成
- `/api/constellations` で星座と星のJSONを返す
- `/admin/followup-suggestions` はAI自動返信ではなく、推しが声で応える星座トップ3の選定支援だけを行う
- `/admin/obsidian-vault` で、Noby個人のSecond Brainとは別の公開用Obsidian保管庫へ公開中の星・星座・タグを書き出す

## 気づきの宇宙ループとしての現在地

このMVPでは、次の流れをローカル環境だけで確認できます。

1. Nobyの声・ライブ・メモの原液を `/admin/recordings` に登録する（音声と同名の `.txt`、または `.txt/.md` パスを使う）
2. `/admin/recordings` の「ローカルパイプライン実行」で `transcript_raw` → `transcript_clean` → `digest` → `summary` を作る
3. 参加者がLINEまたは投稿デモから振り返りを送る
4. LINE上で「名前ありでOK」または「匿名ならOK」が選ばれると、公開ページと `/cosmos` に自動反映される
5. 問題がある投稿だけ、事務局が `/admin` で非公開にする
6. `python3 pipeline/constellate.py` で星を星座化する
7. `python3 pipeline/weekly_report.py` で `outputs/weekly_reports/` に週次星座レポートを生成する
8. `/admin/followup-suggestions` で、推しが声で応える候補を確認する

旧 `/factory` と `/weekly` も残しています。すばやく貼り付け教材化・週次集計を見るための互換ページです。

### Phase 1 CLI

```bash
# 原液登録はUIまたはPythonから source_recordings に登録後、recording idを指定
python3 pipeline/transcribe.py --recording-id <recording_id>
python3 pipeline/clean.py --recording-id <recording_id>
python3 pipeline/digest.py --recording-id <recording_id>
python3 pipeline/summarize.py --recording-id <recording_id>

# 公開中の星にローカル埋め込みプレースホルダを付け、星座化する
python3 pipeline/embed_stars.py
python3 pipeline/constellate.py

# 週次星座レポートとフォローアップ候補
python3 pipeline/weekly_report.py
python3 pipeline/suggest_followup.py

# Noby個人のSecond Brainとは別の公開用Obsidian Vaultへ書き出し
python3 pipeline/export_obsidian.py --vault /Users/noby/product/kizuki-universe-public-vault
```

制限：外部文字起こし、実埋め込みAPI、LINE一斉配信、Cloudflare Tunnel固定URLはまだ接続していません。ローカルPhase 1では、テキストファイルを原液として読み、Markdown/SQLite/管理画面で循環を確認します。

## 公開サイトの自動更新

公開URL：

```text
https://nobykatsu0830.github.io/kizuki-universe/
```

公開サイトはGitHub Pages上の静的サイトですが、LINEで掲載OKが選ばれた時、または事務局管理画面で非公開操作をした時に、アプリが自動で以下を行います。

1. 公開中データから公開用HTMLを書き出し
2. `index.html` / `cosmos.html` / `questions.html` を更新
3. GitHub Pagesリポジトリへcommit/push
4. 数十秒後に公開サイトへ反映

公開ページ：

- トップ/読むページ: `https://nobykatsu0830.github.io/kizuki-universe/`
- 3D気づき宇宙: `https://nobykatsu0830.github.io/kizuki-universe/cosmos.html`
- 問いのページ: `https://nobykatsu0830.github.io/kizuki-universe/questions.html`

自動公開を一時停止したい場合は、環境変数で次を設定します。

```bash
KIZUKI_AUTO_PUBLISH=0
```

## LINEから特定の気づきへ返信する

公開ページまたは3D気づき宇宙に表示される気づきIDを使って、公式LINEに次の形式で送ります。

```text
返信:気づきID ここに返信本文を書く
```

例：

```text
返信:9f607d246d81 私も待つ時間にそわそわします
```

送信後は通常投稿と同じく、LINE上で「名前ありでOK / 匿名ならOK / 掲載しない」を選びます。名前あり/匿名を選んだ場合、すぐに親コメントへの返信として公開されます。

## 次に接続するもの

### LINE Harness / LINE Messaging API

実際のLINE webhookを `/api/line-webhook` に接続し、受信JSONを以下に変換する。

```json
{
  "source": "line",
  "external_user_id": "LINE user id",
  "display_name": "LINE表示名または匿名",
  "body": "参加者の感想",
  "parent_id": "返信先の気づきカードID（任意）"
}
```

### Slack

Slack Events APIまたはSlash commandから同じ形式で保存する。

### Discord

Discord Botで指定チャンネル/スレッドの投稿を拾い、同じ形式で保存する。

### WhatsApp

Twilio/360dialog/WATI/Bird等のWebhookから同じ形式で保存する。

## 本番化メモ

本番では、以下を追加する。

- ログイン付き管理画面
- 投稿者の掲載同意：名前あり/匿名/掲載しない
- 個人情報検知
- AIタグ付け・要約・関連づけの高度化
- 週次まとめ生成の精度向上
- Obsidian/Markdown書き出し
- Cloudflare Workers/D1 または Supabase/Vercel への移行
