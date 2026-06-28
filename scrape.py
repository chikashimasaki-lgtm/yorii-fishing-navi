#!/usr/bin/env python3
"""
寄居フィッシング ブログ(ameblo.jp/minmin0507) から
最新の放流情報・将来の休業日・営業情報・放流魚サイズ・料金を抽出して data.json を生成する。

GitHub Actions から定期実行する想定。ブラウザはCORSで直接ブログを叩けないため、
サーバー側(Actions)で取得→同一オリジンのJSONとして配信する。
"""
import re
import json
import html as ht
import urllib.request
from datetime import datetime, timezone, timedelta

BASE = "https://ameblo.jp/minmin0507"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
JST = timezone(timedelta(hours=9))
Z2H = str.maketrans("０１２３４５６７８９：～", "0123456789:~")


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read().decode("utf-8", "replace")


def list_entries():
    """最近記事を新しい順に [(id, title), ...] で返す"""
    h = fetch(BASE + "/entrylist.html")
    seen, out = set(), []
    for eid, title in re.findall(r'/entry-(\d+)\.html"[^>]*>([^<]{3,80})', h):
        if eid in seen:
            continue
        seen.add(eid)
        out.append((eid, ht.unescape(title).strip()))
    return out


def entry(eid):
    """記事の (投稿日YYYY-MM-DD, 本文プレーンテキスト) を返す"""
    h = fetch(f"{BASE}/entry-{eid}.html")
    dm = re.search(r'"datePublished":"(\d{4}-\d{2}-\d{2})', h)
    date = dm.group(1) if dm else None
    hh = (h.replace("\\u003C", "<").replace("\\u003E", ">")
            .replace("\\n", "\n").replace("\\/", "/"))
    # script/style の中身（改行・句点の無い長大なJS/CSS）を先に除去（本文への混入を防ぐ）
    hh = re.sub(r"<script[^>]*>.*?</script>", " ", hh, flags=re.S | re.I)
    hh = re.sub(r"<style[^>]*>.*?</style>", " ", hh, flags=re.S | re.I)
    plain = re.sub(r"<[^>]+>", " ", hh)
    plain = re.sub(r"[ \t　]+", " ", ht.unescape(plain))
    s = plain.find("毎日特典付き")            # 本文の定型開始
    body = plain[s:] if s >= 0 else plain
    for end in ("いいね！した", "コメント(", "リブログ", "アメンバー", "フォロー"):
        e = body.find(end, 200)
        if e > 0:
            body = body[:e]
            break
    return date, body.translate(Z2H)


def infer_year(month, day, now):
    y = now.year
    try:
        d = datetime(y, int(month), int(day), tzinfo=JST)
    except ValueError:
        return y
    if (now - d).days > 180:
        y += 1
    return y


def future_closures(text, now):
    """臨時休業の日付のうち、今日以降のものだけを返す"""
    iso_list, raw_list = [], []
    for mb in re.finditer(r"臨時休業(.{0,140})", text, re.S):
        for mm, dd in re.findall(r"(\d{1,2})月(\d{1,2})日", mb.group(1)):
            y = infer_year(mm, dd, now)
            iso = f"{y:04d}-{int(mm):02d}-{int(dd):02d}"
            d = datetime(y, int(mm), int(dd), tzinfo=JST).date()
            if d >= now.date() and iso not in iso_list:
                iso_list.append(iso)
                raw_list.append(f"{int(mm)}月{int(dd)}日")
    return iso_list, raw_list


def main():
    now = datetime.now(JST)
    data = {
        "source": BASE + "/",
        "fetched_at": now.isoformat(timespec="seconds"),
        "closures": [], "closures_raw": [],
        "nighter": None, "stocking": [], "pricing": [],
        "latest_stocking": None,
        "hours": {"open": 7, "close": 16, "sat_close": 21},  # 平日7-16/土ナイター21時
        "year_inferred": True,
    }
    try:
        entries = list_entries()[:6]
        bodies = []
        for eid, title in entries:
            try:
                date, body = entry(eid)
                bodies.append({"id": eid, "title": title, "date": date, "body": body})
            except Exception:
                continue

        # --- 最新の放流情報（放流を含む最新記事） ---
        for b in bodies:
            if "放流" in b["body"] or "放流" in b["title"]:
                sents = re.split(r"[。\n]", b["body"])
                hits = [s.strip() for s in sents if "放流" in s and len(s.strip()) <= 60]
                text = "。".join(hits[:3]).strip()
                if not text:
                    text = b["title"]
                data["latest_stocking"] = {
                    "date": b["date"], "title": b["title"],
                    "url": f"{BASE}/entry-{b['id']}.html", "text": text}
                break

        # --- 将来の休業日（複数記事を横断して収集） ---
        allbody = "\n".join(b["body"] for b in bodies)
        data["closures"], data["closures_raw"] = future_closures(allbody, now)

        # --- 日付別の実営業時間（「N月N日(曜日) 朝H時~H時まで」を抽出。新しい記事優先） ---
        hbd = {}
        for b in bodies:   # bodiesは新しい順
            for mm, dd, h1, h2 in re.findall(
                    r"(\d{1,2})月(\d{1,2})日\s*\([^)]*\)\s*朝?\s*(\d{1,2})時\s*~\s*(\d{1,2})時",
                    b["body"]):
                y = infer_year(mm, dd, now)
                iso = f"{y:04d}-{int(mm):02d}-{int(dd):02d}"
                if iso not in hbd:
                    hbd[iso] = [int(h1), int(h2)]
        data["hours_by_date"] = hbd

        # --- 放流魚サイズ・ナイター・料金（記載のある記事から） ---
        for b in bodies:
            t = b["body"]
            if not data["stocking"]:
                for sp in ("ライギョ", "ナマズ"):
                    m = re.search(sp + r"\s*(\d{1,3})\s*cm\s*~\s*(\d{1,3})\s*cm", t)
                    if m:
                        data["stocking"].append(
                            {"species": sp, "size": f"{int(m.group(1))}cm〜{int(m.group(2))}cm"})
            if not data["nighter"]:
                mn = re.search(r"(土曜[^。\n]{0,20}ナイター[^。\n]{0,20}?(\d{1,2})時[^。\n]{0,8})", t)
                if mn:
                    data["nighter"] = re.sub(r"\s+", " ", mn.group(1)).strip()[:60]
            if not data["pricing"]:
                seen_plan = set()
                for hours, yen in re.findall(r"(\d{1,2})\s*時間\s*(\d{3,5})\s*円", t):
                    plan = f"{int(hours)}時間"
                    if plan in seen_plan:
                        continue
                    seen_plan.add(plan)
                    data["pricing"].append({"plan": plan, "yen": int(yen)})

        data["ok"] = True
    except Exception as e:
        data["ok"] = False
        data["error"] = str(e)

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
