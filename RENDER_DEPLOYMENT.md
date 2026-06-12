# Render デプロイ手順

気づきの宇宙（AI気づきツリー MVP）を Render へ永続デプロイするための手順です。

## 概要

- **デプロイ先**: Render.com
- **デプロイ方法**: GitHub 連携（自動デプロイ）
- **URL**: `https://kizuki-universe-[ランダム].onrender.com` のような固定 URL が発行される
- **LINE Webhook**: この URL を LINE Developers に設定する

## ステップ 1: Render.com でアカウント作成 / ログイン

1. https://render.com にアクセス
2. GitHub でサインアップ / ログイン
3. 承認

## ステップ 2: 新規サービス作成

1. ダッシュボード右上 **「+ New」** → **「Web Service」**
2. **「Connect a repository」**
3. GitHub リポジトリを検索 → `ai-kizuki-tree-mvp` を選択
4. **「Connect」**

## ステップ 3: 設定

| 項目 | 値 |
|------|-----|
| **Name** | `kizuki-universe` |
| **Environment** | Python 3 |
| **Build Command** | `pip install -r requirements.txt` （または空白で OK） |
| **Start Command** | `python3 app.py` |
| **Instance Type** | Free（無料） |

## ステップ 4: 環境変数を設定

**「Environment」セクション** → **「+ Add Environment Variable」**

以下を追加：

```
LINE_CHANNEL_SECRET = [.env から値をコピー]
LINE_CHANNEL_ACCESS_TOKEN = [.env から値をコピー]
LINE_CHANNEL_ID = 2010336023
```

※値は `.env` ファイルから確認してください（Telegram には貼らない）

## ステップ 5: デプロイ

1. **「Create Web Service」** ボタンを押す
2. デプロイが始まる（2〜5分）
3. **「Live」** になったら完了

## ステップ 6: 本番 URL を確認

デプロイ完了後、以下の URL でサーバーが起動しています：

```
https://kizuki-universe-xxxx.onrender.com/
```

（xxxx の部分は Render が自動生成）

## ステップ 7: LINE Developers で Webhook URL を更新

1. LINE Developers コンソール → 対象チャネル → **「Messaging API設定」**
2. **「Webhook URL」** を以下に変更：

```
https://kizuki-universe-xxxx.onrender.com/webhook/line
```

3. **「検証」** ボタンを押す → **「成功」** が出ればOK

## ステップ 8: テスト

LINE 公式アカウントにメッセージを送信 → 確認表示が出る → 選択 → 公開ページに反映

## トラブルシューティング

### デプロイが失敗する
- **ログを確認**: ダッシュボード → **「Logs」** タブで詳細を見る
- **Python 依存関係**: `requirements.txt` が必要な場合は、以下を作成：
  ```bash
  pip freeze > requirements.txt
  ```

### LINE Webhook の検証が失敗する
- **URL が正しいか確認**: `https://kizuki-universe-xxxx.onrender.com/webhook/line`
- **環境変数が設定されているか確認**: Render ダッシュボード → 「Environment」
- **Free インスタンスは 15 分の非使用で停止**: メッセージ送信時にサーバーが起動するまで数秒待つ

### メッセージが反映されない
- **Render ダッシュボード → Logs** でエラーを確認
- **LINE Developers** で Webhook URL が正しく設定されているか確認
- **新しいメッセージを送信**: 古いメッセージ（Webhook URL 変更前のもの）は反映されません

## 次のステップ

デプロイ完了後、以下を実装予定：
- LINE プロフィール取得で表示名を自動入力
- 「掲載OK / 匿名OK / 掲載しない」確認フロー
- 返信先カードを LINE から指定できる導線

---

**質問・詰まったことがあれば、ノビーさんに報告してください。**
