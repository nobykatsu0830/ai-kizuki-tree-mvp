# LINE接続手順（AI気づきツリー MVP）

## 重要

Channel Secret / Access Token は秘密情報です。Telegramや通常チャットには貼らないでください。
このMac上の `.env` に保存します。

## 1. このMacに秘密情報を保存

```bash
cd /Users/noby/product/ai-kizuki-tree-mvp
cp .env.example .env
open -a TextEdit .env
```

`.env` に以下を入れます。

```env
LINE_CHANNEL_SECRET=xxxxxxxx
LINE_CHANNEL_ACCESS_TOKEN=xxxxxxxx
LINE_CHANNEL_ID=xxxxxxxx
```

保存後、MVPを再起動します。

```bash
cd /Users/noby/product/ai-kizuki-tree-mvp
python3 app.py
```

## 2. Webhook URLについて

LINE DevelopersのWebhook URLには、外部からアクセスできる **HTTPS URL** が必要です。

ローカルURLは不可：

```text
http://127.0.0.1:8787/webhook/line
```

本番またはトンネル後のURLはこの形：

```text
https://xxxxx.example.com/webhook/line
```

現在の一時トンネルURL：

```text
https://describes-area-deviation-boring.trycloudflare.com/webhook/line
```

※これは検証用の一時URLです。Mac側のトンネルを止めると使えなくなります。本番では固定URLにします。

## 3. LINE Developers側の設定

LINE Developers > 対象のMessaging APIチャネル > Messaging API設定

- Webhook URL：`https://xxxxx.../webhook/line`
- Webhookの利用：オン
- 応答メッセージ：必要に応じてオフ
- あいさつメッセージ：任意

その後、`検証` ボタンを押します。

## 4. 今のMVPの受信仕様

LINEからテキストメッセージが届くと：

1. `awaiting_consent`（掲載確認待ち）として一時保存
2. LINEへクイックリプライを返す
   - 名前ありでOK
   - 匿名ならOK
   - 掲載しない
3. 名前あり/匿名が選ばれたら `approved` になり、公開サイトへ自動反映
4. 掲載しないが選ばれたら `rejected` へ移動

事務局管理画面では、問題がある公開中投稿だけをあとから非公開にします。

事務局管理画面：

```text
http://127.0.0.1:8787/admin
```

## 5. 次に実装すること

- LINEプロフィール取得で表示名を取得
- 「掲載OK / 匿名OK / 掲載しない」確認フロー
- 返信先カードをLINEから指定できる導線
- Cloudflare等への常時稼働デプロイ
