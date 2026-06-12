## Variant: 3D気づき地球 / 3D気づき宇宙

### Design stance
Google Earthのように、気づきが平面ではなく球体の表面に浮かぶ。ユーザーはドラッグで回し、ズームし、星を選んで探索する。

### Key choices
- Layout: Canvasによる3D球面投影 + HTMLラベル + 詳細パネル
- Interaction: ドラッグ回転、ホイール/ボタン拡大縮小、自動回転、テーマフィルタ、星クリック
- Visual: 奥にある星は薄く、手前の星が立ち上がる。球体の緯線/経線でGoogle Earth的な奥行きを出す
- Implementation: 外部ライブラリなし。Canvasと自前の3D投影で軽量に実装

### Trade-offs
- Strong at: 2Dのっぺり感を解消、商品デモとして強い、探索感が出る
- Weak at: 本番ではスマホ操作、星が多い時のクラスタリング、アクセシビリティ調整が必要

### Best for
- 気づき宇宙の本命方向を検証するための次段階プロトタイプ
