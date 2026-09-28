import base64
from collections import Counter
from datetime import datetime, timezone
import os
import re
import time
from google import genai
from google.genai import errors
from google.cloud import bigquery
import markdown
import requests

# 1. 認証と基本設定
REPO_OWNER = "mbook03"
REPO_NAME = "python-"
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
BQ_PROJECT = "haruya1"

headers = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github.v3+json",
}

now_utc = datetime.now(timezone.utc)
date_str = now_utc.strftime("%Y%m%d")
html_file_name = f"posts/post_{date_str}.html"

# --- 安全ガード：すでに手動で保存・編集されたファイルがあるか確認 ---
check_url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents/{html_file_name}"
existing_file_res = requests.get(check_url, headers=headers)

if existing_file_res.status_code == 200:
    print(f"🛡️ 保護発動: {html_file_name} はすでに存在するため、AIによる上書きをスキップします。")
    # 既存の手直し済みHTMLを取得して維持
    existing_content = base64.b64decode(existing_file_res.json()["content"]).decode("utf-8")
    single_page_html = existing_content
    # 本文部分を抽出（index.htmlに反映するため）
    body_match = re.search(r'<div class="container">(.*?)</div>', existing_content, re.DOTALL)
    body_html = body_match.group(1) if body_match else existing_content
else:
    print(f"📝 本日分の記事が見つからないため、AIで新規作成します...")
    # 2. BigQueryから過去実績を取得
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
        top_post = df_top.iloc[0] if len(df_top) > 0 else None
    except Exception as e:
        print(f"BigQueryクエリ警告: {e}")
        top_post = None

    if top_post is None:
        top_post = {
            "title": "データ分析レポート",
            "content": "データ 経済 マネー 米国 インフラ データセンター 電力",
            "char_count": 3000,
        }

    # 3. キーワード抽出
    text = str(top_post["content"])
    keywords = re.findall(r"[\u4e00-\u9fa5]{2,}|[ァ-ヴー]{2,}|[A-Za-z]{3,}", text)
    stopwords = {"データ", "レポート", "分析", "これ", "それ", "ため", "よう", "こと"}
    filtered_words = [w for w in keywords if w not in stopwords]
    top_keywords = [w[0] for w in Counter(filtered_words).most_common(5)]
    if not top_keywords:
        top_keywords = ["経済", "テクノロジー", "インフラ"]

    # 4. Gemini API による執筆（混雑時のモデル自動フォールバック対応）
    ai_client = genai.Client(api_key=GEMINI_API_KEY)
    prompt = f"""
    あなたはWebマーケティングとSEOに精通した経済・テックブロガーです。
    以下のキーワードを踏まえ、約2,500〜3,000字程度のブログ記事本文（Markdown形式）を執筆してください。
    注目キーワード: {', '.join(top_keywords)}
    【構成ルール】
    - 1行目に「# 記事タイトル」を記載
    - H2, H3見出しを用いて論理的に解説
    """

    candidate_models = ["gemini-3.8-flash", "gemini-3-flash", "gemini-2.5-flash"]
    article_md = None
    for model_name in candidate_models:
        for attempt in range(1, 4):
            try:
                res = ai_client.models.generate_content(model=model_name, contents=prompt)
                article_md = res.text
                break
            except Exception:
                time.sleep(attempt * 5)
        if article_md:
            break

    if not article_md:
        raise RuntimeError("全モデルで生成に失敗しました。")

    # タイトル抽出
    title = "新着記事"
    for line in article_md.splitlines():
        if line.startswith("# "):
            title = line.replace("# ", "").strip()
            break

    # 5. 単体HTML生成
    body_html = markdown.markdown(article_md, extensions=["extra", "codehilite"])
    single_page_html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <title>{title}｜AI日記</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: sans-serif; background-color: #fbfbf8; color: #333; line-height: 1.9; }}
    .container {{ max-width: 860px; margin: 40px auto; padding: 40px; background: #fff; border-radius: 6px; border: 1px solid #eaeaea; }}
    .back-nav a {{ color: #0066cc; text-decoration: none; font-size: 14px; font-weight: bold; }}
    .article-date {{ font-size: 13px; color: #888; font-weight: bold; margin-bottom: 8px; }}
    h1 {{ font-size: 24px; margin-bottom: 20px; line-height: 1.4; border-bottom: 1px solid #eee; padding-bottom: 14px; }}
    h2 {{ font-size: 20px; margin: 34px 0 14px; border-left: 4px solid #c94a29; padding-left: 12px; }}
    p {{ margin-bottom: 16px; font-size: 15px; }}
  </style>
</head>
<body>
  <div class="container">
    <div class="back-nav"><a href="../index.html">← 日記一覧へ戻る</a></div>
    <div class="article-date">{now_utc.strftime('%m月%d日')}</div>
    {body_html}
  </div>
</body>
</html>"""

    # 新規作成時のみコミット
    put_url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents/{html_file_name}"
    put_payload = {
        "message": f"feat: auto publish {html_file_name}",
        "content": base64.b64encode(single_page_html.encode("utf-8")).decode("utf-8"),
    }
    requests.put(put_url, headers=headers, json=put_payload)

# --- 6. index.html のサイドバーと最新表示を安全に再構築 ---
posts_api_url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents/posts"
posts_res = requests.get(posts_api_url, headers=headers)

post_links_html = ""
if posts_res.status_code == 200:
    files = posts_res.json()
    html_files = [f for f in files if f["name"].endswith(".html")]
    html_files.sort(key=lambda x: x["name"], reverse=True)
    for f in html_files:
        name = f["name"]
        date_match = re.search(r"(\d{4})(\d{2})(\d{2})", name)
        disp_date = f"{date_match.group(2)}月{date_match.group(3)}日" if date_match else name
        post_links_html += f'<li><a href="posts/{name}">📄 {disp_date} の日記</a></li>\n'

index_html_content = f"""<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AI日記｜Ad Meliora</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: sans-serif; background-color: #fbfbf8; color: #333; line-height: 1.8; }}
    header {{ background: #fff; border-bottom: 2px solid #eaeaea; padding: 16px 30px; }}
    .site-brand {{ font-size: 20px; font-weight: bold; color: #222; text-decoration: none; }}
    .container {{ max-width: 1000px; margin: 30px auto; display: flex; gap: 30px; padding: 0 20px; }}
    .sidebar {{ width: 280px; background: #fff; padding: 20px; border: 1px solid #eaeaea; border-radius: 6px; height: fit-content; }}
    .sidebar h2 {{ font-size: 16px; margin-bottom: 12px; border-bottom: 2px solid #c94a29; padding-bottom: 6px; }}
    .sidebar ul {{ list-style: none; }}
    .sidebar li {{ margin-bottom: 10px; }}
    .sidebar a {{ color: #0066cc; text-decoration: none; font-size: 14px; }}
    .main-content {{ flex: 1; background: #fff; padding: 30px; border: 1px solid #eaeaea; border-radius: 6px; }}
    .latest-badge {{ display: inline-block; background: #c94a29; color: #fff; font-size: 12px; padding: 2px 8px; border-radius: 3px; margin-bottom: 12px; }}
  </style>
</head>
<body>
  <header><a href="index.html" class="site-brand">AI日記｜Ad Meliora</a></header>
  <div class="container">
    <aside class="sidebar">
      <h2>過去の日記一覧</h2>
      <ul>{post_links_html}</ul>
    </aside>
    <main class="main-content">
      <span class="latest-badge">最新記事</span>
      {body_html}
    </main>
  </div>
</body>
</html>"""

idx_url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents/index.html"
idx_get = requests.get(idx_url, headers=headers)
idx_payload = {
    "message": "feat: update index.html safely",
    "content": base64.b64encode(index_html_content.encode("utf-8")).decode("utf-8"),
}
if idx_get.status_code == 200:
    idx_payload["sha"] = idx_get.json()["sha"]
requests.put(idx_url, headers=headers, json=idx_payload)
print("🎉 完了：手動編集した文章を保護したまま更新しました。")
