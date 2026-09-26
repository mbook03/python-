import os
import re
import base64
from datetime import datetime, timezone
from collections import Counter
import markdown
import requests
from google import genai
from google.cloud import bigquery

REPO_OWNER = "mbook03"
REPO_NAME = "python-"
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
BQ_PROJECT = "haruya1"

headers = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github.v3+json",
}

# ---------------------------------------------------------
# 1. BigQueryから過去実績と注目キーワードを取得
# ---------------------------------------------------------
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
    top_post = {"title": "経済インフラ分析", "content": "データ 経済 マネー 米国 インフラ データセンター 電力", "char_count": 3000}

text = str(top_post["content"])
keywords = re.findall(r"[\u4e00-\u9fa5]{2,}|[ァ-ヴー]{2,}|[A-Za-z]{3,}", text)
stopwords = {"データ", "レポート", "分析", "これ", "それ", "ため", "よう", "こと", "日本"}
filtered_words = [w for w in keywords if w not in stopwords]
top_keywords = [w[0] for w in Counter(filtered_words).most_common(5)]
if not top_keywords:
    top_keywords = ["経済", "インフラ", "データセンター"]

print(f"抽出キーワード: {', '.join(top_keywords)}")

# ---------------------------------------------------------
# 2. Gemini API で本日の記事を生成
# ---------------------------------------------------------
ai_client = genai.Client(api_key=GEMINI_API_KEY)
prompt = f"""
あなたはWebマーケティングと経済・インフラ分析に精通したプロフェッショナルブロガーです。
以下のキーワードと傾向を踏まえ、約2,500〜3,000字程度のブログ記事本文（Markdown形式）を執筆してください。

注目キーワード: {', '.join(top_keywords)}
文字数目安: 約2,500〜3,000字

【構成ルール】
- 1行目に「# 記事タイトル」を記載
- H2, H3見出しを用いて論理的・構造的に解説
- 重要な数字やポイントは箇条書きや強調（**太字**）を使用
- 結びとして将来の展望や課題を整理
"""

res = ai_client.models.generate_content(
    model="gemini-2.5-flash",
    contents=prompt
)
article_md = res.text

# タイトル抽出
title = "新着経済レポート"
for line in article_md.splitlines():
    if line.startswith("# "):
        title = line.replace("# ", "").strip()
        break

# MarkdownをHTMLに変換
content_html = markdown.markdown(article_md, extensions=["extra", "codehilite"])

# ---------------------------------------------------------
# 3. 記事を単体HTML（posts/post_YYYYMMDD.html）として保存
# ---------------------------------------------------------
now_utc = datetime.now(timezone.utc)
date_str = now_utc.strftime("%Y%m%d")
display_date = now_utc.strftime("%m月%d日")
post_file_path = f"posts/post_{date_str}.html"

post_full_html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}｜AI日記</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Hiragino Kaku Gothic ProN", "Yu Gothic", sans-serif;
      background-color: #fbfbf8;
      color: #333;
      line-height: 1.9;
    }}
    .container {{
      max-width: 860px;
      margin: 40px auto;
      background: #fff;
      padding: 40px;
      border-radius: 6px;
      border: 1px solid #eaeaea;
    }}
    .back-nav {{ margin-bottom: 20px; }}
    .back-nav a {{ color: #0066cc; text-decoration: none; font-size: 14px; font-weight: bold; }}
    .article-date {{ font-size: 13px; color: #888; font-weight: bold; margin-bottom: 8px; }}
    h1 {{ font-size: 24px; margin-bottom: 20px; line-height: 1.4; color: #111; border-bottom: 1px solid #eee; padding-bottom: 14px; }}
    h2 {{ font-size: 20px; margin: 34px 0 14px; border-left: 4px solid #c94a29; padding-left: 12px; }}
    h3 {{ font-size: 16px; margin: 24px 0 10px; color: #333; }}
    p {{ margin-bottom: 16px; font-size: 15px; }}
    ul, ol {{ margin: 16px 0 16px 24px; font-size: 15px; }}
    li {{ margin-bottom: 6px; }}
  </style>
</head>
<body>
  <div class="container">
    <div class="back-nav"><a href="../index.html">← 日記一覧へ戻る</a></div>
    <div class="article-date">{display_date}</div>
    {content_html}
  </div>
</body>
</html>
"""

# GitHub APIで posts/ にプッシュ
def push_to_github(path, content_str, commit_msg):
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents/{path}"
    get_res = requests.get(url, headers=headers)
    payload = {
        "message": commit_msg,
        "content": base64.b64encode(content_str.encode("utf-8")).decode("utf-8"),
    }
    if get_res.status_code == 200:
        payload["sha"] = get_res.json()["sha"]
    put_res = requests.put(url, headers=headers, json=payload)
    print(f"{path} コミット結果: {put_res.status_code}")

push_to_github(post_file_path, post_full_html, f"feat: create {post_file_path}")

# ---------------------------------------------------------
# 4. posts/ フォルダ内の全記事を取得して index.html を自動再構築
# ---------------------------------------------------------
posts_api_url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents/posts"
posts_res = requests.get(posts_api_url, headers=headers)
all_post_files = []
if posts_res.status_code == 200:
    for item in posts_res.json():
        if item["name"].endswith(".html"):
            all_post_files.append(item["name"])
all_post_files.sort(reverse=True)

# サイドバーリンク一覧を生成
sidebar_items = []
for fname in all_post_files:
    d_match = re.search(r"post_(\d{4})(\d{2})(\d{2})\.html", fname)
    if d_match:
        m, d = d_match.group(2), d_match.group(3)
        label = f"{m}月{d}日の記事"
    else:
        label = fname
    sidebar_items.append(f"""
      <div class="post-item">
        <a href="./posts/{fname}" class="sidebar-link">📄 {label}</a>
      </div>
    """)
sidebar_html = "\n".join(sidebar_items)

# トップページ用 index.html テンプレート
new_index_html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AI日記｜Ad Meliora - AIと世界を、よりよく。</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Hiragino Kaku Gothic ProN", "Yu Gothic", sans-serif;
      background-color: #fbfbf8;
      color: #333;
      line-height: 1.9;
    }}
    header {{
      background: #fff;
      border-bottom: 2px solid #eaeaea;
      padding: 16px 30px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      position: sticky;
      top: 0;
      z-index: 100;
    }}
    .site-brand {{ display: flex; flex-direction: column; text-decoration: none; }}
    .site-title {{ font-size: 20px; font-weight: bold; color: #222; }}
    .site-tagline {{ font-size: 11px; color: #777; margin-top: 2px; }}
    .container {{
      max-width: 1100px;
      margin: 30px auto;
      padding: 0 20px;
      display: flex;
      gap: 40px;
    }}
    .sidebar {{ width: 280px; flex-shrink: 0; }}
    .sidebar-title {{ font-size: 14px; font-weight: bold; color: #888; border-bottom: 1px solid #ddd; padding-bottom: 8px; margin-bottom: 15px; }}
    .post-item {{ margin-bottom: 12px; }}
    .sidebar-link {{ color: #444; text-decoration: none; font-size: 14px; display: block; padding: 6px 10px; border-radius: 4px; }}
    .sidebar-link:hover {{ color: #0066cc; background: #eee; }}
    .content {{
      flex: 1;
      background: #fff;
      padding: 40px;
      border-radius: 6px;
      border: 1px solid #eaeaea;
    }}
    .article-header {{ margin-bottom: 24px; border-bottom: 1px solid #eee; padding-bottom: 14px; }}
    .date-badge {{ font-size: 13px; color: #c94a29; font-weight: bold; }}
    h1 {{ font-size: 24px; margin-bottom: 18px; color: #111; line-height: 1.4; }}
    h2 {{ font-size: 20px; margin: 34px 0 14px; border-left: 4px solid #c94a29; padding-left: 12px; }}
    h3 {{ font-size: 16px; margin: 26px 0 10px; }}
    p {{ margin-bottom: 16px; font-size: 15px; }}
    ul, ol {{ margin: 16px 0 16px 24px; font-size: 15px; }}
    li {{ margin-bottom: 6px; }}
    @media (max-width: 768px) {{
      .container {{ flex-direction: column; }}
      .sidebar {{ width: 100%; }}
      .content {{ padding: 20px; }}
    }}
  </style>
</head>
<body>
  <header>
    <a href="./index.html" class="site-brand">
      <span class="site-title">AI日記｜Ad Meliora</span>
      <span class="site-tagline">AIと世界を、よりよく。</span>
    </a>
  </header>

  <div class="container">
    <aside class="sidebar">
      <div class="sidebar-title">過去の日記・記事一覧</div>
      {sidebar_html}
    </aside>

    <main class="content">
      <div class="article-header">
        <span class="date-badge">最新記事（{display_date}）</span>
      </div>
      {content_html}
    </main>
  </div>
</body>
</html>
"""

push_to_github("index.html", new_index_html, f"chore: auto update index.html for {date_str}")
