#!/bin/bash
# 朝のGO — 夜間に準備した「照らし合う宇宙」を本番に灯すスクリプト
# 使い方:  cd ~/product/ai-kizuki-tree-mvp && bash 朝のGO.sh
set -e
cd "$(dirname "$0")"

echo "════════════════════════════════════════════"
echo " 気づきの宇宙 — 朝のGO"
echo "════════════════════════════════════════════"

# 0) Supabaseが復元済みかを先に確認（休止中なら本番は起動できません）
echo ""
echo "STEP 0: Supabaseの状態確認"
echo "  もし未復元なら → https://supabase.com/dashboard/project/kcqkazrjgbvrspfbawob"
echo "  を開いて「Restore project」を押してから、このスクリプトを再実行してください。"
echo ""

# 1) 本番へ反映（mainへpush → Renderが自動デプロイ）
echo "STEP 1: git push origin main（Render自動デプロイ発火）"
git push origin main

# 2) 本番の起動を待つ（Render無料枠はビルド+起動に2〜5分）
echo "STEP 2: 本番 /health の復帰待ち（最大10分）"
for i in $(seq 1 40); do
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 20 https://kizuki-universe.onrender.com/health || true)
  echo "  試行 $i/40: HTTP $code"
  if [ "$code" = "200" ]; then echo "  ✦ 本番が灯りました"; break; fi
  sleep 15
done

# 3) 本番データに光の糸と問いを編む（初回の編み込み）
echo "STEP 3: 本番データへの編み込みバッチ（codex使用・数分）"
python3 batch_classify.py

# 4) 毎晩03:30の自動編み込みを登録（既存のcronは保持）
echo "STEP 4: 毎晩の編み込みcronを登録"
if crontab -l 2>/dev/null | grep -q "batch_classify.py"; then
  echo "  すでに登録済み — スキップ"
else
  (crontab -l 2>/dev/null; echo "30 3 * * * cd $HOME/product/ai-kizuki-tree-mvp && /usr/bin/env python3 batch_classify.py >> outputs/batch_classify.log 2>&1") | crontab -
  echo "  登録しました（毎日03:30）"
fi
mkdir -p outputs

# 5) 最終確認
echo "STEP 5: 本番ページの確認"
curl -s https://kizuki-universe.onrender.com/ | grep -o "星々から生まれた問い\|響き合っています\|光の糸" | sort | uniq -c || true
echo ""
echo "✦ 完了。 https://kizuki-universe.onrender.com/ を開いて、宇宙を旅してください。"
