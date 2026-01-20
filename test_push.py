import requests
import json
import sys
import os
from datetime import datetime, timedelta, timezone
# ================= 配置区域 =================
# 从环境变量读取，如果没有则使用默认值（用于本地测试）
CF_ZONE_ID = os.environ.get("CF_ZONE_ID", "")
CF_API_TOKEN = os.environ.get("CF_API_TOKEN", "")
DOMAIN_NAME = "liuer.indevs.in"

CF_ACCOUNT_ID = os.environ.get("CF_ACCOUNT_ID", "")
CF_D1_DATABASE_ID = os.environ.get("CF_D1_DATABASE_ID", "")

TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")

EMOS_API_URL = "https://emos.lol/api/plug/sendTelegramMessage"
EMOS_AUTH_TOKEN = os.environ.get("EMOS_AUTH_TOKEN", "")
EMOS_TO_USER = os.environ.get("EMOS_TO_USER", "")
# ===========================================

# ===========================================

def format_number(num):
    """仿 Cloudflare 格式化数字 (k, M, B)"""
    if num is None:
        return "0"
    num = float(num)
    if num < 1000:
        return str(int(num))
    elif num < 1000000:
        return f"{num/1000:.2f}k"
    elif num < 1000000000:
        return f"{num/1000000:.2f}M"
    else:
        return f"{num/1000000000:.2f}B"

def format_bytes(size):
    """格式化流量单位"""
    power = 2**10
    n = 0
    power_labels = {0 : '', 1: 'K', 2: 'M', 3: 'G', 4: 'T'}
    while size > power:
        size /= power
        n += 1
    return f"{size:.2f} {power_labels[n]}B"

def get_cf_stats(start_dt, end_dt):
    """获取 Cloudflare 流量统计"""
    iso_start = start_dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    iso_end = end_dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    url = "https://api.cloudflare.com/client/v4/graphql"
    headers = {
        "Authorization": f"Bearer {CF_API_TOKEN}",
        "Content-Type": "application/json"
    }
    
    query = """
    query getStats($zoneTag: string, $start: Time, $end: Time) {
      viewer {
        zones(filter: { zoneTag: $zoneTag }) {
          httpRequests1hGroups(
            filter: { datetime_geq: $start, datetime_lt: $end }
            limit: 50
          ) {
            sum {
              requests
              bytes
            }
          }
        }
      }
    }
    """
    
    payload = {
        "query": query,
        "variables": {
            "zoneTag": CF_ZONE_ID,
            "start": iso_start,
            "end": iso_end
        }
    }

    try:
        resp = requests.post(url, json=payload, headers=headers)
        data = resp.json()
        
        if "errors" in data and data["errors"]:
            print(f"CF API Error: {data['errors']}")
            return None

        groups = data["data"]["viewer"]["zones"][0]["httpRequests1hGroups"]
        
        total_req = sum(item['sum']['requests'] for item in groups)
        total_bytes = sum(item['sum']['bytes'] for item in groups)
        
        return {
            "requests": total_req,
            "bytes": total_bytes
        }
    except Exception as e:
        print(f"CF Request Exception: {e}")
        return None

def get_d1_stats(date_str, table_name):
    """查询 D1 数据库指定表在特定日期的数据"""
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/d1/database/{CF_D1_DATABASE_ID}/query"
    headers = {
        "Authorization": f"Bearer {CF_API_TOKEN}",
        "Content-Type": "application/json"
    }
    
    sql = f"SELECT sum(playing_count) as pc, sum(playback_info_count) as pic FROM {table_name} WHERE date = ?"
    
    payload = {
        "sql": sql,
        "params": [date_str]
    }

    try:
        resp = requests.post(url, json=payload, headers=headers)
        data = resp.json()
        
        if not data.get("success", False):
            return {"pc": 0, "pic": 0}
        
        rows = data.get("result", [{}])[0].get("results", [])
        
        if rows and rows[0].get("pc") is not None:
            return {
                "pc": rows[0]["pc"], 
                "pic": rows[0]["pic"]
            }
        else:
            return {"pc": 0, "pic": 0}
            
    except Exception as e:
        print(f"D1 Request Exception ({table_name}): {e}")
        return {"pc": 0, "pic": 0}

def send_telegram_message(message):
    """发送 TG 消息 (HTML 模式)"""
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TG_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    try:
        requests.post(url, json=payload)
    except Exception as e:
        print(f"TG Send Error: {e}")

def send_emos_message(message):
    """发送 Emos 消息 (Markdown 模式)"""
    headers = {
        "Authorization": f"Bearer {EMOS_AUTH_TOKEN}",
        "User-Agent": "Apifox/1.0.0 (https://apifox.com)",
        "Accept": "*/*",
        "Host": "emos.lol",
        "Connection": "keep-alive"
        # requests 库在发送 data 字典时会自动处理 Content-Type，
        # 通常不需要手动设置 multipart boundary，除非 API 极其严格。
    }
    
    payload = {
        "to": "group",
        "text": message,
        "parse_mode": "Markdown",
        "destroy_second": "86400"
    }
    
    try:
        # 使用 data 参数发送 form-data
        requests.post(EMOS_API_URL, headers=headers, data=payload)
        print("✅ Emos 推送成功")
    except Exception as e:
        print(f"❌ Emos Send Error: {e}")

def main():
    # 定义北京时区
    tz_bj = timezone(timedelta(hours=8))
    
    # 检查是否有命令行参数
    if len(sys.argv) > 1:
        # --- 模式 A: 手动指定日期 (仅发送 TG) ---
        target_date_str = sys.argv[1] # 格式应该如 2025-12-03
        
        try:
            current_date_obj = datetime.strptime(target_date_str, "%Y-%m-%d").replace(tzinfo=tz_bj)
            next_date_obj = current_date_obj + timedelta(days=1)
            
            print(f"🔄 正在查询指定日期: {target_date_str} (北京时间)")
            
            stats = get_cf_stats(current_date_obj, next_date_obj)
            
            table_auto = "auto_emby_daily_stats"
            table_emos = "emos_emby_daily_stats"
            d1_auto = get_d1_stats(target_date_str, table_auto)
            d1_emos = get_d1_stats(target_date_str, table_emos)

            if stats:
                msg = f"📊 <b>Cloudflare 指定查询</b>\n"
                msg += f"域名: <code>{DOMAIN_NAME}</code>\n\n"
                msg += f"📅 <b>日期: {target_date_str}</b>\n"
                msg += f"请求数: <b>{format_number(stats['requests'])}</b>\n"
                msg += f"流量: {format_bytes(stats['bytes'])}\n\n"
                msg += f"Emos反代: 播放请求 {d1_emos['pc']} 次 | 获取播放信息 {d1_emos['pic']} 次\n"
                msg += f"Auto全自动反代: 播放请求 {d1_auto['pc']} 次 | 获取播放信息 {d1_auto['pic']} 次\n"
                msg += f"\n#Cloudflare #历史查询"
                
                print("✅ 获取成功，正在推送 Telegram...")
                send_telegram_message(msg)
            else:
                print("❌ 获取 Cloudflare 数据失败。")

        except ValueError:
            print("❌ 日期格式错误！请使用 YYYY-MM-DD 格式，例如: python3 push.py 2025-12-03")
            return

    else:
        # --- 模式 B: 默认 Crontab 模式 (昨天 + 前天) ---
        now = datetime.now(tz_bj)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        yesterday_start = today_start - timedelta(days=1)
        day_before_start = today_start - timedelta(days=2)

        date_str_yest = yesterday_start.strftime('%Y-%m-%d')
        date_str_before = day_before_start.strftime('%Y-%m-%d')

        print(f"🔄 正在执行每日例行查询: {date_str_yest} 和 {date_str_before}")

        # 1. 获取流量
        stats_yest = get_cf_stats(yesterday_start, today_start)
        stats_before = get_cf_stats(day_before_start, yesterday_start)

        if not stats_yest or not stats_before:
            print("❌ 获取流量数据失败，跳过推送")
            return

        # 2. 获取 D1
        table_auto = "auto_emby_daily_stats"
        table_emos = "emos_emby_daily_stats"

        d1_auto_yest = get_d1_stats(date_str_yest, table_auto)
        d1_emos_yest = get_d1_stats(date_str_yest, table_emos)
        d1_auto_before = get_d1_stats(date_str_before, table_auto)
        d1_emos_before = get_d1_stats(date_str_before, table_emos)

        # 3. 构建消息 (HTML版 - 用于 Telegram)
        msg_html = f"📊 <b>Cloudflare 每日报表</b>\n"
        msg_html += f"域名: <code>{DOMAIN_NAME}</code>\n\n"

        # 昨天 (HTML)
        msg_html += f"📅 <b>昨天 ({yesterday_start.strftime('%m-%d')})</b>\n"
        msg_html += f"请求数: <b>{format_number(stats_yest['requests'])}</b>\n"
        msg_html += f"流量: {format_bytes(stats_yest['bytes'])}\n\n"
        msg_html += f"Emos反代: 播放请求 {d1_emos_yest['pc']} 次 | 获取播放信息 {d1_emos_yest['pic']} 次\n"
        msg_html += f"Auto全自动反代: 播放请求 {d1_auto_yest['pc']} 次 | 获取播放信息 {d1_auto_yest['pic']} 次\n\n"

        # 前天 (HTML)
        msg_html += f"📅 <b>前天 ({day_before_start.strftime('%m-%d')})</b>\n"
        msg_html += f"请求数: <b>{format_number(stats_before['requests'])}</b>\n"
        msg_html += f"流量: {format_bytes(stats_before['bytes'])}\n\n"
        msg_html += f"Emos反代: 播放请求 {d1_emos_before['pc']} 次 | 获取播放信息 {d1_emos_before['pic']} 次\n"
        msg_html += f"Auto全自动反代: 播放请求 {d1_auto_before['pc']} 次 | 获取播放信息 {d1_auto_before['pic']} 次\n"
        msg_html += f"\n#Cloudflare #Emby #日报"

        # 4. 构建消息 (Markdown版 - 用于 Emos)
        # Markdown 语法: *加粗* `代码`
        msg_md = f"📊 *Cloudflare 每日报表*\n"
        msg_md += f"域名: `{DOMAIN_NAME}`\n\n"

        # 昨天 (Markdown)
        msg_md += f"📅 *昨天 ({yesterday_start.strftime('%m-%d')})*\n"
        msg_md += f"请求数: *{format_number(stats_yest['requests'])}*\n"
        msg_md += f"流量: {format_bytes(stats_yest['bytes'])}\n\n"
        msg_md += f"Emos反代: 播放请求 {d1_emos_yest['pc']} 次 | 获取播放信息 {d1_emos_yest['pic']} 次\n"
        msg_md += f"Auto全自动反代: 播放请求 {d1_auto_yest['pc']} 次 | 获取播放信息 {d1_auto_yest['pic']} 次\n\n"

        # 前天 (Markdown)
        msg_md += f"📅 *前天 ({day_before_start.strftime('%m-%d')})*\n"
        msg_md += f"请求数: *{format_number(stats_before['requests'])}*\n"
        msg_md += f"流量: {format_bytes(stats_before['bytes'])}\n\n"
        msg_md += f"Emos反代: 播放请求 {d1_emos_before['pc']} 次 | 获取播放信息 {d1_emos_before['pic']} 次\n"
        msg_md += f"Auto全自动反代: 播放请求 {d1_auto_before['pc']} 次 | 获取播放信息 {d1_auto_before['pic']} 次\n"
        msg_md += f"\n#Cloudflare #Emby #日报"

        print("✅ 获取成功，正在推送...")
        
        # 推送 Telegram
        send_telegram_message(msg_html)
        
        # 推送 Emos
        send_emos_message(msg_md)
        
        print("完成。")

if __name__ == "__main__":
    main()
