import base64
from collections import Counter
from datetime import datetime, timezone
import os
import re
import time
from google import genai
from google.cloud import bigquery
import markdown
import requests

REPO_OWNER = "mbook03"
REPO_NAME = "python-"
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
BQ_PROJECT = "haruya1"

headers = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github.v3+json",
}

# --- 1. BigQueryから直近GA4実績とキーワード取得 ---
bq_client = bigquery.Client(project=BQ_PROJECT)
query = """
WITH page_views AS (
  SELECT
    (SELECT value.string_value FROM UNNEST(event_params) WHERE key = 'page_location') AS full_url,
    COUNT(1) AS pv_count
  FROM
    `haruya1.analytics_553895213.events_*`
  WHERE
    event_name = 'page_view'
  GROUP BY
    full_url
)
SELECT
  p.title, p.content, p.char_count, COALESCE(pv.pv_count, 0) AS pv_count
FROM
  `haruya1.test_dataset.github_posts` p
LEFT JOIN
  page_views pv
ON
  pv.full_url LIKE CONCAT('%', p.file_path)
  OR (p.file_path = 'index.html' AND (pv.full_url LIKE '%/python-/' OR pv.full_url LIKE '%/python-/index.html'))
WHERE
  p.file_path != 'README.md'
ORDER BY
  pv_count DESC
LIMIT 1
"""
try:
    df_top = bq_client.query(query).to_dataframe()
    top_post = (
        df_top.iloc[0]
        if len(df_top) > 0
        else {
            "title": "データ分析",
            "content": "データ 経済 マネー 米国 インフラ",
            "char_count": 3000,
        }
    )
except Exception as e:
    print(f"BigQueryクエリ警告: {e}")
    top_post = {
        "title": "データ分析",
        "content": "データ 経済 マネー 米国 インフラ",
        "char_count": 3000,
    }

# キーワード抽出
text = str(top_post["content"])
keywords = re.findall(r"[\u4e00-\u9fa5]{2,}|[ァ-ヴー]{2,}|[A-Za-z]{3,}", text)
stopwords = {"データ", "レポート", "分析", "これ", "それ", "ため", "よう", "こと"}
filtered_words = [w for w in keywords if w not in stopwords]
top_keywords = [w[0] for w in Counter(filtered_words).most_common(5)]
if not top_keywords:
    top_keywords = ["経済", "テクノロジー", "マネー"]

# --- 2. Gemini API で新着記事を執筆（複数モデル切り替え＋指数バックオフ） ---
ai_client = genai.Client(api_key=GEMINI_API_KEY)
prompt = f"""
あなたはWebマーケティングとSEOに精通した経済・テックブロガーです。
以下のキーワードと傾向を踏まえ、約2,500字程度のブログ記事本文（Markdown形式）を執筆してください。

注目キーワード: {', '.join(top_keywords)}
文字数目安: 約2,500〜3,000字

【構成ルール】
- 1行目に「# 記事タイトル」を記載
- H2, H3見出しを用いて論理的に解説
- 読者を飽きさせないように適宜リストや強調を使用
"""

candidate_models = ["gemini-3.8-flash", "gemini-3-flash", "gemini-2.5-flash"]
article_md = None

for m in candidate_models:
    print(f"=== モデル '{m}' を検証中 ===")
    for attempt in range(1, 4):
        try:
            print(f"モデル '{m}' 呼び出し試行 {attempt}/3...")
            res = ai_client.models.generate_content(model=m, contents=prompt)
            if res and res.text:
                article_md = res.text
                print(f"✅ 記事生成成功！（使用モデル: {m}）")
                break
        except Exception as e:
            print(f"モデル '{m}' 試行 {attempt} 失敗: {e}")
            time.sleep(10 * attempt)
    if article_md:
        break

if not article_md:
    raise RuntimeError(
        "全候補モデルにおいて一時負荷（503）のため記事生成に失敗しました。"
    )

# --- 3. Markdown記事をリポジトリへ新規コミット ---
now_utc = datetime.now(timezone.utc)
date_str = now_utc.strftime("%Y%m%d")
file_name = f"post_{date_str}.md"

file_url = (
    f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents/{file_name}"
)
get_res = requests.get(file_url, headers=headers)
payload = {
    "message": f"feat: daily auto post {file_name}",
    "content": base64.b64encode(article_md.encode("utf-8")).decode("utf-8"),
    "branch": "main",
}
if get_res.status_code == 200:
    payload["sha"] = get_res.json()["sha"]

put_res = requests.put(file_url, headers=headers, json=payload)
print(f"記事コミット結果ステータス: {put_res.status_code}")
