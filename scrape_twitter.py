import json
import os
import time
import httpx
from twikit import Client
from twikit.errors import Forbidden, Unauthorized, UserNotFound


async def login(client: Client, cookies: dict, username: str):
    client.set_cookies(cookies)
    print("正在验证 cookie ...")
    try:
        user = await client.get_user_by_screen_name(username)
    except httpx.TransportError:
        print("网络连接失败，无法访问 x.com（请检查网络或代理后重试）")
        raise
    except UserNotFound:
        print("用户不存在或名称有误：@" + username)
        raise
    except (Unauthorized, Forbidden):
        print("cookie 验证失败（可能已过期）")
        raise
    except Exception:
        print("验证时发生异常（可能是 x.com 改版导致解析失败）")
        raise
    print("cookie 验证成功。")
    return user


def append_tweet_to_file(tweet, json_fh):
    row = {
        "id": str(tweet.id),
        "created_at": getattr(tweet, "created_at", ""),
        "text": getattr(tweet, "text", ""),
        "user": getattr(tweet.user, "screen_name", ""),
        "reply_count": getattr(tweet, "reply_count", ""),
        "retweet_count": getattr(tweet, "retweet_count", ""),
        "like_count": getattr(tweet, "favorite_count", ""),
        "lang": getattr(tweet, "lang", ""),
    }
    json_fh.write(json.dumps(row, ensure_ascii=False) + "\n")


async def run(username: str, limit: int, wait: float,  cookies: dict ) :
    client = Client("en-US")
    user = await login(client, cookies, username)

    # 目标用户 id
    target_id = str(user.id)
    print("目标用户:", user.name, "@" + username, "| id:", target_id)

    json_path = f"{username}.jsonl"

    # 断点续抓：记录已见过的推文 id
    seen_ids = set()
    if os.path.exists(json_path):
        with open(json_path, encoding="utf-8") as f:
            for line in f:
                try:
                    seen_ids.add(json.loads(line)["id"])
                except Exception:
                    pass
        print("检测到已有数据，将跳过已抓取的", len(seen_ids), "条。")

    json_fh = open(json_path, "a", encoding="utf-8")

    try:
        # 第一种：Tweets 页签（最近推文）
        tweets = await client.get_user_tweets(target_id, "Tweets")
        total_saved = 0
        while tweets:
            for tweet in tweets:
                tid = str(tweet.id)
                if tid in seen_ids:
                    continue
                append_tweet_to_file(tweet, json_fh)
                seen_ids.add(tid)
                total_saved += 1
            print(f"推文类 {total_saved} 条...")
            json_fh.flush()

            if limit and total_saved >= limit:
                print("已达设定上限，停止。")
                break

            # 若有下一页，翻页
            if hasattr(tweets, "next_cursor") and getattr(tweets, "next_cursor", None):
                time.sleep(wait)
                tweets = await tweets.next()
            else:
                break

        #若 Tweets 页签翻完了，再补抓 Replies 页签 
        if not limit or total_saved < limit:
            print("继续抓取 Replies 页签以补全...")
            replies = await client.get_user_tweets(target_id, "Replies")
            while replies:
                for tweet in replies:
                    tid = str(tweet.id)
                    if tid in seen_ids:
                        continue
                    append_tweet_to_file(tweet, json_fh)
                    seen_ids.add(tid)
                    total_saved += 1
                print(f"回复类 {total_saved} 条...")
                json_fh.flush()
                if limit and total_saved >= limit:
                    break
                if hasattr(replies, "next_cursor") and getattr(replies, "next_cursor", None):
                    time.sleep(wait)
                    replies = await replies.next()
                else:
                    break

        print(f"完成！共抓取 {total_saved} 条推文。")
        print("JSONL:", os.path.abspath(json_path))
    finally:
        json_fh.close()

def extract_texts_to_txt(directory: str):
    for name in sorted(os.listdir(directory)):
        if not name.lower().endswith(".jsonl"):
            continue
        path = os.path.join(directory, name)
        if not os.path.isfile(path):
            continue

        out_path = os.path.join(directory, os.path.splitext(name)[0] + ".txt")
        count = 0
        try:
            with open(path, encoding="utf-8", errors="ignore") as f, \
                    open(out_path, "w", encoding="utf-8") as out_fh:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        item = json.loads(line)
                    except Exception:
                        continue  # 非 JSONL 行，跳过
                    if not isinstance(item, dict):
                        continue
                    text = item.get("text")
                    if not isinstance(text, str) or not text.strip():
                        continue
                    out_fh.write(text.rstrip() + "\n\n")
                    count += 1
        except OSError:
            continue  # 无法读取/写入的文件，跳过
    return count

