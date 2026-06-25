#!/usr/bin/env python3
"""
寄居フィッシング ブログ(ameblo.jp/minmin0507) から
休業日・営業情報・放流情報を抽出して data.json を生成する。

GitHub Actions から定期実行する想定。ブラウザはCORSで直接ブログを叩けないため、
サーバー側(Actions)で取得→同一オリジンのJSONとして配信する。
"""
import re
import json
import html as ht
import urllib.request
from datetime import datetime, timezone, timedelta

URL = "https://ameblo.jp/minmin0507/"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
JST = timezone(timedelta(hours=9))

Z2H = str.maketrans("０１２３４５６７８９：～", "0123456789:~")


def fetch_html():
    req = urllib.request.Request(URL, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read().decode("utf-8", "replace")


def extract_article_text(html):
    """記事本文の領域をキーワードで特定し、タグ除去してテキスト化"""
    # エスケープ済みHTMLを通常HTMLに戻す
    h = html.replace("\\u003C", "<").replace("\\u003E", ">")
    h = h.replace("\\n", "\n").replace("\\/", "/").replace('\\"', '"')

    # 記事の開始/終了をキーワードで推定
    starts = [h.find(k) for k in ("必ず読んで", "臨時休業", "休業日", "放流魚")]
    starts = [s for s in starts if s >= 0]
    if not starts:
        body = h
    else:
        start = max(0, min(starts) - 400)
        ends = [h.find(k, min(starts)) for k in ("割引", "料金表", "お待ち", "LINE会員")]
        ends = [e for e in ends if e >= 0]
        end = (max(ends) + 200) if ends else (min(starts) + 3000)
        body = h[start:end]

    body = re.sub(r"<[^>]+>", " ", body)
    body = ht.unescape(body)
    body = re.sub(r"[ \t　]+", " ", body)
    return body.strip()


def to_iso(month, day, year):
    return f"{year:04d}-{int(month):02d}-{int(day):02d}"


def infer_year(month, day, now):
    """年なし日付に年を補完。過ぎていれば来年扱い（半年以上前なら翌年）"""
    y = now.year
    try:
        d = datetime(y, int(month), int(day), tzinfo=JST)
    except ValueError:
        return y
    if (now - d).days > 180:
        y += 1
    return y


def parse(text, now):
    t = text.translate(Z2H)
    data = {
        "source": URL,
        "fetched_at": now.isoformat(timespec="seconds"),
        "closures": [], "closures_raw": [],
        "business": [], "nighter": None,
        "stocking": [], "pricing": [],
        "year_inferred": True,
    }

    # 記事タイトル
    mt = re.search(r"(ライギョ[^！\n。]{0,30}[！!]?)", t)
    data["title"] = mt.group(1).strip() if mt else ""

    # --- 臨時休業ブロック内の日付 ---
    mb = re.search(r"臨時休業(.*?)(?:明日|営業|放流|料金|お願い|その他|$)", t, re.S)
    block = mb.group(1) if mb else ""
    for mm, dd in re.findall(r"(\d{1,2})月(\d{1,2})日", block):
        y = infer_year(mm, dd, now)
        iso = to_iso(mm, dd, y)
        if iso not in data["closures"]:
            data["closures"].append(iso)
            data["closures_raw"].append(f"{int(mm)}月{int(dd)}日")

    # --- 営業時間（「N月N日(...) 朝7時～16時」型）---
    for mm, dd, h1, h2 in re.findall(
            r"(\d{1,2})月(\d{1,2})日\s*\([^)]*\)\s*朝?\s*(\d{1,2})時\s*~\s*(\d{1,2})時", t):
        y = infer_year(mm, dd, now)
        data["business"].append(
            {"date": to_iso(mm, dd, y), "hours": f"{int(h1)}:00-{int(h2)}:00"})

    # --- ナイター ---
    mn = re.search(r"(土曜[^。\n]*ナイター[^。\n]*?(\d{1,2})時[^。\n]*)", t)
    if mn:
        data["nighter"] = re.sub(r"\s+", " ", mn.group(1)).strip()

    # --- 放流魚 ---
    for sp in ("ライギョ", "ナマズ"):
        m = re.search(sp + r"\s*(\d{1,3})\s*cm\s*~\s*(\d{1,3})\s*cm", t)
        if m:
            data["stocking"].append(
                {"species": sp, "size": f"{int(m.group(1))}cm〜{int(m.group(2))}cm"})

    # --- 料金表 ---
    for hours, yen in re.findall(r"(\d{1,2})\s*時間\s*(\d{3,5})\s*円", t):
        data["pricing"].append({"plan": f"{int(hours)}時間", "yen": int(yen)})
    md = re.search(r"1日\s*(\d{3,5})\s*円", t)
    if md:
        data["pricing"].append({"plan": "1日", "yen": int(md.group(1))})

    return data


def main():
    now = datetime.now(JST)
    try:
        html = fetch_html()
        data = parse(extract_article_text(html), now)
        data["ok"] = True
    except Exception as e:
        data = {"ok": False, "error": str(e),
                "fetched_at": now.isoformat(timespec="seconds"), "source": URL}

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
