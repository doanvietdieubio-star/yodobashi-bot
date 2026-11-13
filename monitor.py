#!/usr/bin/env python3
"""Theo dõi bình luận mới trên https://gamenv.net/tc/yodobashi/ (WordPress + wpDiscuz) -> Discord Webhook.

Nhận diện chi nhánh qua emoji/từ viết tắt trong nội dung, dịch ký hiệu tình trạng hàng (🍐/🐜),
và dịch nội dung Nhật -> Việt bằng deep-translator (Google Translate, miễn phí).
"""
import json
import os
import re
import sys
import time
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

try:
    from deep_translator import GoogleTranslator
except ImportError:  # vẫn chạy được nếu thiếu thư viện
    GoogleTranslator = None

# ----------------------------- CẤU HÌNH -----------------------------
TARGET_URL = os.getenv("TARGET_URL", "https://gamenv.net/tc/yodobashi/").strip()
WEBHOOK = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
SITE_COOKIE = os.getenv("SITE_COOKIE", "").strip()          # tùy chọn: "name=value; name2=value2"
SEND_ON_FIRST_RUN = os.getenv("SEND_ON_FIRST_RUN", "false").lower() == "true"
DEBUG_DUMP = os.getenv("DEBUG_DUMP", "false").lower() == "true"
IGNORE_IDS = {x.strip() for x in os.getenv("IGNORE_IDS", "").split(",") if x.strip()}
STATE_FILE = Path("state.json")
DEBUG_DIR = Path("debug")
MAX_SEEN = 1000
EMBED_COLOR = 0xE60012

TRANSLATE = os.getenv("TRANSLATE", "true").lower() == "true"
MAX_TRANSLATE_CHARS = 4500

# Từ điển chi nhánh: (danh sách emoji / từ viết tắt, tên hiển thị). Có thể thêm/sửa tùy ý.
BRANCH_KEYWORDS = [
    (["⛱️", "横ヨド"], "Yodobashi Yokohama"),
    (["🦉"], "Yodobashi Ikebukuro"),
    (["🐝"], "Yodobashi Hachioji"),
    (["🥟"], "Yodobashi Utsunomiya"),
    (["🥜"], "Yodobashi Chiba"),
    (["🦏", "🥚"], "Yodobashi Saitama Shintoshin"),
    (["🗻"], "Yodobashi Shinjuku / Kofu"),
    (["🔞"], "Yodobashi Shinjuku / Akihabara"),
    (["アキヨド"], "Yodobashi Akihabara"),
    (["梅田", "🐙"], "Yodobashi Umeda (Osaka)"),
    (["博多", "🍜"], "Yodobashi Hakata (Fukuoka)"),
    (["札幌", "⛄"], "Yodobashi Sapporo"),
    (["仙台", "🐮"], "Yodobashi Sendai"),
    (["錦糸町"], "Yodobashi Kinshicho"),
    (["上野", "🐼"], "Yodobashi Ueno"),
    (["町田"], "Yodobashi Machida"),
    (["川崎"], "Yodobashi Kawasaki"),
    (["京都", "⛩️"], "Yodobashi Kyoto"),
]
# Mở rộng nhanh qua biến môi trường: BRANCH_MAP_JSON='{"🐸": "Yodobashi Tên mới"}'
try:
    for _kw, _name in json.loads(os.getenv("BRANCH_MAP_JSON", "{}")).items():
        BRANCH_KEYWORDS.append(([_kw], _name))
except (json.JSONDecodeError, AttributeError):
    print("⚠️ BRANCH_MAP_JSON không hợp lệ, bỏ qua.")

NO_BRANCH_TEXT = "Xem trong nội dung"
# "あり" nhưng loại trừ ありがとう / ありません / ありえない / ありそう
ARI_RE = re.compile(r"あり(?!が|ませ|え|そう)")
JP_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uff66-\uff9f]")

# ----------------------------- JS TRÍCH XUẤT -----------------------------
EXTRACT_JS = r"""
() => {
  const abs = (u) => { try { return new URL(u, location.href).href; } catch (e) { return null; } };
  const IMG_EXT = /\.(jpe?g|png|gif|webp|avif|bmp)(\?|#|$)/i;
  const isAvatar = (i) => i.closest('.wpd-avatar') || /avatar|gravatar/i.test((i.className || '') + ' ' + (i.src || ''));
  const isEmoji = (i) => /emoji|smiley/i.test(i.className || '') || /s\.w\.org|\/emoji/i.test(i.src || '');
  const bigSrc = (img) => {
    const a = img.closest('a');
    if (a && a.href && IMG_EXT.test(a.href)) return a.href;
    for (const k of ['data-full', 'data-orig-file', 'data-large-file', 'data-src', 'data-lazy-src']) {
      const v = img.getAttribute(k); if (v) return abs(v);
    }
    const ss = img.getAttribute('srcset') || img.getAttribute('data-srcset');
    if (ss) {
      let best = null, bw = -1;
      ss.split(',').forEach(p => {
        const [u, w] = p.trim().split(/\s+/); const n = parseInt(w) || 0;
        if (u && n >= bw) { bw = n; best = u; }
      });
      if (best) return abs(best);
    }
    return abs(img.currentSrc || img.src);
  };

  const out = [];
  document.querySelectorAll('.wpd-comment').forEach(el => {
    const m = (el.id || '').match(/wpd-comm-(\d+)_(\d+)/);
    if (!m) return;
    const wrap = el.querySelector(':scope > .wpd-comment-wrap') || el;
    const left = wrap.querySelector('.wpd-comment-left');
    const right = wrap.querySelector('.wpd-comment-right') || wrap;
    const header = wrap.querySelector('.wpd-comment-header');

    const sticky = /stick|pinned/i.test(el.className) ||
      !!wrap.querySelector('[class*="stick"], [class*="pinned"], [class*="pin-"]');

    const badges = [];
    const seen = new Set();
    [left, header].forEach(c => {
      if (!c) return;
      c.querySelectorAll('img').forEach(i => {
        if (isAvatar(i) || isEmoji(i) || seen.has(i)) return;
        seen.add(i);
        badges.push({ kind: 'img', src: abs(i.currentSrc || i.src), alt: i.alt || '', title: i.title || i.closest('[title]')?.title || '', cls: i.className || '', text: '' });
      });
      c.querySelectorAll('[class*="badge"],[class*="rank"],[class*="store"],[class*="branch"],[class*="shop"],[class*="flag"],.wpd-comment-label,.wpd-user-icon,.wpd-author-title').forEach(e => {
        if (seen.has(e)) return;
        seen.add(e);
        const bg = getComputedStyle(e).backgroundImage;
        const bgm = bg && bg.match(/url\(["']?(.*?)["']?\)/);
        badges.push({ kind: 'el', src: bgm ? abs(bgm[1]) : '', alt: '', title: e.title || '', cls: e.className || '', text: (e.textContent || '').trim().slice(0, 80) });
      });
    });

    const textEl = right.querySelector('.wpd-comment-text');
    let text = '';
    if (textEl) {
      const c = textEl.cloneNode(true);
      c.querySelectorAll('img, script, style').forEach(x => x.remove());
      c.querySelectorAll('br').forEach(b => b.replaceWith('\n'));
      c.querySelectorAll('p, div, li').forEach(b => b.append('\n'));
      text = c.textContent.replace(/\n{3,}/g, '\n\n').trim();
    }

    const imgs = [];
    const addImg = (u) => { if (u && IMG_EXT.test(u) && !imgs.includes(u)) imgs.push(u); };
    right.querySelectorAll('.wpd-comment-text img, [class*="attach"] img, [class*="wpd-"] img').forEach(i => {
      if (isAvatar(i) || isEmoji(i) || (header && header.contains(i))) return;
      addImg(bigSrc(i));
    });
    right.querySelectorAll('.wpd-comment-text a[href], [class*="attach"] a[href]').forEach(a => addImg(a.href));

    const authorEl = wrap.querySelector('.wpd-comment-author');
    const dateEl = wrap.querySelector('.wpd-comment-date');
    const timeEl = wrap.querySelector('time');
    out.push({
      id: m[1],
      parent: m[2],
      sticky,
      author: authorEl ? authorEl.textContent.trim() : '',
      date: (dateEl && (dateEl.getAttribute('title') || '').trim()) || (timeEl && timeEl.getAttribute('datetime')) ||
            (dateEl ? dateEl.textContent.trim() : ''),
      date_text: dateEl ? dateEl.textContent.trim() : '',
      text,
      images: imgs,
      badges,
      permalink: location.href.split('#')[0] + '#comment-' + m[1],
      debug_html: (el.querySelector(':scope > .wpd-comment-wrap')?.outerHTML || el.outerHTML).slice(0, 4000),
    });
  });
  return out;
}
"""


# ----------------------------- TIỆN ÍCH -----------------------------
def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text("utf-8"))
        except json.JSONDecodeError:
            pass
    return None


def save_state(seen):
    STATE_FILE.write_text(json.dumps({"seen": seen[-MAX_SEEN:]}, ensure_ascii=False, indent=1), "utf-8")


def parse_cookie_header(header, url):
    cookies = []
    for part in header.split(";"):
        if "=" in part:
            k, v = part.strip().split("=", 1)
            cookies.append({"name": k, "value": v, "url": url})
    return cookies


def _norm(t):
    """Bỏ variation selector để ⛱ và ⛱️ (⛩ và ⛩️) khớp nhau."""
    return (t or "").replace("\ufe0f", "").replace("\ufe0e", "")


def find_branches(text):
    """Trả về chuỗi tên chi nhánh (có thể nhiều chi nhánh, theo thứ tự xuất hiện trong bình luận)."""
    t = _norm(text)
    hits = []
    for keywords, name in BRANCH_KEYWORDS:
        found = [(t.find(_norm(k)), k) for k in keywords if _norm(k) and _norm(k) in t]
        if found:
            found.sort()
            hits.append((found[0][0], name, [k for _, k in found]))
    if not hits:
        return NO_BRANCH_TEXT
    hits.sort(key=lambda x: x[0])
    return "\n".join(f"{name} ({' / '.join(kws)})" for _, name, kws in hits)


def stock_notes(text):
    """Dịch ký hiệu tình trạng hàng: 🍐 = hết hàng (nashi), 🐜 / あり = có hàng (ari)."""
    t = _norm(text)
    notes = []
    if "🍐" in t:
        notes.append("[❌ Hết hàng (🍐)]")
    if "🐜" in t:
        notes.append("[✅ Có hàng (🐜)]")
    elif ARI_RE.search(t):
        notes.append("[✅ Có hàng (あり)]")
    return " ".join(notes)


def translate_ja_vi(text):
    """Dịch Nhật -> Việt bằng Google Translate (deep-translator). Lỗi thì trả chuỗi rỗng."""
    if not TRANSLATE or GoogleTranslator is None or not text or not JP_RE.search(text):
        return ""
    src = text[:MAX_TRANSLATE_CHARS]
    for attempt in range(3):
        try:
            out = (GoogleTranslator(source="ja", target="vi").translate(src) or "").strip()
            return "" if out == text.strip() else out
        except Exception as e:  # rate limit / mạng
            print(f"⚠️ Dịch lỗi (lần {attempt + 1}): {e}")
            time.sleep(1.5 * (attempt + 1))
    return ""


def upgrade_image(ctx, url):
    """Bỏ hậu tố thumbnail WordPress (-300x200) nếu bản gốc tồn tại."""
    full = re.sub(r"-\d{2,4}x\d{2,4}(?=\.\w{3,4}(?:\?|$))", "", url)
    if full == url:
        return url
    try:
        r = ctx.request.head(full, timeout=10000)
        if r.ok and "image" in (r.headers.get("content-type") or ""):
            return full
    except Exception:
        pass
    return url


def clip(s, n):
    s = s or ""
    return s if len(s) <= n else s[: n - 1] + "…"


# ----------------------------- CÀO TRANG -----------------------------
def scrape():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
            locale="vi-VN", timezone_id="Asia/Tokyo", viewport={"width": 1366, "height": 900},
        )
        if SITE_COOKIE:
            ctx.add_cookies(parse_cookie_header(SITE_COOKIE, TARGET_URL))
        page = ctx.new_page()
        page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=60000)

        try:
            page.wait_for_selector("#wpdcom, #comments, .wpd-comment", timeout=30000, state="attached")
            page.evaluate("document.querySelector('#wpdcom, #comments')?.scrollIntoView()")
        except Exception:
            pass
        page.wait_for_timeout(3000)

        try:
            btn = page.query_selector(".wpdiscuz-sort-button[data-sorting='newest']:not(.wpdiscuz-sort-button-active)")
            if btn:
                btn.click()
                page.wait_for_timeout(2500)
        except Exception:
            pass

        for _ in range(2):
            try:
                more = page.query_selector(".wpd-load-more-submit")
                if more and more.is_visible():
                    more.click()
                    page.wait_for_timeout(2500)
                else:
                    break
            except Exception:
                break

        comments = page.evaluate(EXTRACT_JS)

        if DEBUG_DUMP or not comments:
            DEBUG_DIR.mkdir(exist_ok=True)
            (DEBUG_DIR / "page.html").write_text(page.content(), "utf-8")
            (DEBUG_DIR / "comments_raw.json").write_text(json.dumps(comments, ensure_ascii=False, indent=1), "utf-8")
            page.screenshot(path=str(DEBUG_DIR / "screenshot.png"), full_page=False)

        for c in comments:
            c["images"] = [upgrade_image(ctx, u) for u in c["images"]]
        browser.close()
        return comments


# ----------------------------- DISCORD -----------------------------
def post_discord(embeds):
    payload = {"username": "Yodobashi Monitor", "embeds": embeds}
    for _ in range(5):
        r = requests.post(WEBHOOK, json=payload, timeout=30)
        if r.status_code == 429:
            time.sleep(float(r.json().get("retry_after", 2)) + 0.5)
            continue
        if r.status_code >= 400:
            print(f"❌ Discord lỗi {r.status_code}: {r.text[:300]}")
            return False
        return True
    return False


def build_messages(c, branch, stock="", translation=""):
    link, imgs = c["permalink"], c["images"]
    fields = [
        {"name": "🏪 Chi nhánh", "value": clip(branch, 1024), "inline": True},
        {"name": "👤 Người đăng", "value": clip(c["author"] or "Ẩn danh", 1024), "inline": True},
        {"name": "🕒 Thời gian", "value": clip(c["date"] or c["date_text"] or "Không rõ", 1024), "inline": False},
    ]
    if stock:
        fields.append({"name": "📦 Tình trạng hàng", "value": clip(stock, 1024), "inline": False})
    fields.append({"name": "💬 Nội dung gốc (🇯🇵)", "value": clip(c["text"] or "(không có nội dung chữ)", 1024), "inline": False})
    if translation:
        fields.append({"name": "🌐 Bản dịch tiếng Việt (🇻🇳)", "value": clip(translation, 1024), "inline": False})
    if imgs:
        fields.append({"name": "🖼️ Hình ảnh đính kèm", "value": f"{len(imgs)} ảnh (hiển thị bên dưới)", "inline": False})
    main = {"title": "🔔 Bình luận mới - Yodobashi", "url": link, "color": EMBED_COLOR,
            "fields": fields, "footer": {"text": f"Comment #{c['id']}"}}

    # Discord gộp các embed có cùng `url` thành gallery (tối đa 4 ảnh)
    chunks = [imgs[i:i + 4] for i in range(0, len(imgs), 4)]
    msgs = []
    first = chunks[0] if chunks else []
    embeds = [main]
    if first:
        main["image"] = {"url": first[0]}
        embeds += [{"url": link, "image": {"url": u}} for u in first[1:]]
    msgs.append(embeds)
    for n, ch in enumerate(chunks[1:], start=2):
        u = f"{link}?img_group={n}"
        e = [{"title": f"🖼️ Ảnh đính kèm (phần {n}/{len(chunks)})", "url": u, "color": EMBED_COLOR,
              "image": {"url": ch[0]}}]
        e += [{"url": u, "image": {"url": x}} for x in ch[1:]]
        msgs.append(e)
    return msgs


# ----------------------------- MAIN -----------------------------
def main():
    if not WEBHOOK:
        sys.exit("Thiếu biến môi trường DISCORD_WEBHOOK_URL")

    comments = scrape()
    if not comments:
        print("⚠️ Không tìm thấy bình luận nào (có thể bị chặn bot / cần đăng nhập / selector sai). Xem artifact debug.")
        sys.exit(1)

    state = load_state()
    seen = state["seen"] if state else []
    seen_set = set(seen)

    candidates = [c for c in comments if not c["sticky"] and c["id"] not in IGNORE_IDS]
    print(f"Tổng {len(comments)} bình luận, {len(comments) - len(candidates)} bị bỏ qua (ghim/ignore).")

    if state is None and not SEND_ON_FIRST_RUN:
        save_state([c["id"] for c in sorted(candidates, key=lambda x: int(x["id"]))])
        print(f"🌱 Lần chạy đầu: đã ghi nhớ {len(candidates)} bình luận hiện có, không gửi để tránh spam.")
        return

    new = sorted((c for c in candidates if c["id"] not in seen_set), key=lambda x: int(x["id"]))
    print(f"🆕 {len(new)} bình luận mới.")

    for c in new:
        branch = find_branches(c["text"])
        stock = stock_notes(c["text"])
        translation = translate_ja_vi(c["text"])
        ok = all(post_discord(m) for m in build_messages(c, branch, stock, translation))
        if ok:
            seen.append(c["id"])
            save_state(seen)
            print(f"✅ Đã gửi #{c['id']} ({c['author']} - {branch})")
        else:
            print(f"❌ Gửi thất bại #{c['id']}, sẽ thử lại lần chạy sau.")
        time.sleep(1.2)

    if not new:
        save_state(seen)


if __name__ == "__main__":
    main()
