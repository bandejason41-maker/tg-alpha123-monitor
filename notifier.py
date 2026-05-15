"""
Telegram 公开频道监听器
抓取 t.me/s/<channel> 的网页,关键词过滤,通过 Gmail SMTP 发邮件
状态(已通知的消息 ID)通过 GitHub Actions Cache 保存
"""

import os
import re
import sys
import json
import smtplib
import requests
from email.mime.text import MIMEText
from email.header import Header
from email.utils import formataddr
from bs4 import BeautifulSoup

# ==================== 配置 ====================
CHANNEL = "alpha123cn"
KEYWORDS = ["即将开始", "开抢预警", "新空投", "空投"]
STATE_FILE = "seen_ids.json"
MAX_KEEP_IDS = 200  # 最多保留多少个已通知 ID

# 从环境变量读取邮箱配置
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465
SMTP_USER = os.environ["SMTP_USER"]     # 你的 Gmail 地址
SMTP_PASS = os.environ["SMTP_PASS"]     # 应用专用密码
MAIL_TO = os.environ["MAIL_TO"]         # 收件邮箱(QQ 邮箱或 Gmail)


# ==================== 抓取频道 ====================
def fetch_channel(channel: str):
    url = f"https://t.me/s/{channel}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/120.0 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    return r.text


def parse_messages(html: str, channel: str):
    soup = BeautifulSoup(html, "html.parser")
    wraps = soup.select("div.tgme_widget_message_wrap")
    messages = []
    for w in wraps:
        msg_div = w.select_one("div.tgme_widget_message")
        if not msg_div:
            continue

        # 消息 ID(data-post 形如 "channel/123")
        data_post = msg_div.get("data-post", "")
        m = re.search(r"/(\d+)$", data_post)
        if not m:
            continue
        msg_id = m.group(1)

        # 文本
        text_div = w.select_one("div.tgme_widget_message_text")
        text = text_div.get_text("\n", strip=True) if text_div else ""

        # 标题取第一行,正文全部
        first_line = text.split("\n", 1)[0] if text else ""

        # 时间
        time_tag = w.select_one("time")
        pub_date = time_tag.get("datetime") if time_tag else ""

        # 链接
        link = f"https://t.me/{channel}/{msg_id}"

        messages.append({
            "id": msg_id,
            "title": first_line[:80] or "(无标题)",
            "text": text or "(无文本内容,可能是图片/视频)",
            "link": link,
            "pub_date": pub_date,
        })
    return messages


# ==================== 状态管理 ====================
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()


def save_state(seen_ids):
    # 只保留最新的 MAX_KEEP_IDS 个(避免文件越来越大)
    ids_list = sorted(seen_ids, key=lambda x: int(x), reverse=True)[:MAX_KEEP_IDS]
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(ids_list, f, ensure_ascii=False)


# ==================== 过滤 ====================
def match_keywords(msg):
    blob = f"{msg['title']} {msg['text']}"
    return any(kw in blob for kw in KEYWORDS)


# ==================== 发邮件 ====================
def send_email(msg):
    subject = f"🚀 空投预警:{msg['title']}"

    body = f"""🔥 检测到新空投消息

{'='*44}

{msg['title']}

{msg['text']}

{'='*44}

🔗 链接:{msg['link']}
🕐 时间:{msg['pub_date']}

来自 Telegram 频道:Alpha123通知
"""

    mime = MIMEText(body, "plain", "utf-8")
    mime["From"] = formataddr((str(Header("Alpha123 Monitor", "utf-8")), SMTP_USER))
    mime["To"] = MAIL_TO
    mime["Subject"] = Header(subject, "utf-8")

    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30) as s:
        s.login(SMTP_USER, SMTP_PASS)
        s.sendmail(SMTP_USER, [MAIL_TO], mime.as_string())

    print(f"[OK] 已发送邮件: id={msg['id']} title={msg['title'][:30]}")


# ==================== 主流程 ====================
def main():
    print(f"开始抓取频道: {CHANNEL}")
    try:
        html = fetch_channel(CHANNEL)
    except Exception as e:
        print(f"[ERROR] 抓取失败: {e}")
        sys.exit(1)

    messages = parse_messages(html, CHANNEL)
    print(f"抓到 {len(messages)} 条消息")
    if not messages:
        print("没有消息,退出")
        return

    seen = load_state()
    print(f"已通知过 {len(seen)} 条")

    new_msgs = []
    for m in messages:
        if not match_keywords(m):
            continue
        if m["id"] in seen:
            continue
        new_msgs.append(m)

    # 首次运行(seen 为空)时,不发邮件,只把当前所有标记为已通知
    if not seen:
        print("[首次运行] 不发邮件,只记录当前消息 ID 作为基线")
        for m in messages:
            seen.add(m["id"])
        save_state(seen)
        return

    print(f"匹配 + 未通知过的: {len(new_msgs)} 条")
    if not new_msgs:
        print("没有新匹配消息,退出")
        return

    # 按消息 ID 升序发(旧的先发)
    new_msgs.sort(key=lambda x: int(x["id"]))
    for m in new_msgs:
        try:
            send_email(m)
            seen.add(m["id"])
        except Exception as e:
            print(f"[ERROR] 发邮件失败 id={m['id']}: {e}")
            # 失败也标记为已通知,避免下次重复尝试(可改为不标记以便重试)
            seen.add(m["id"])

    save_state(seen)
    print("Done")


if __name__ == "__main__":
    main()
