# 标准库
import asyncio
import json
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

# 第三方库
import jinja2
from curl_cffi.requests import AsyncSession
from playwright.async_api import async_playwright

# AstrBot
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register, StarTools
from astrbot.api import logger

@register("aicu_analysis", "Huahuatgc", "AICU B站评论查询", "2.7.1", "https://github.com/Huahuatgc/astrbot_plugin_aicu")
class AicuAnalysisPlugin(Star):
    # ================= 配置常量 =================
    AICU_BILI_API_URL = "https://worker.aicu.cc/api/bili/space"
    AICU_MARK_API_URL = "https://api.aicu.cc/api/v3/user/getusermark"
    AICU_REPLY_API_URL = "https://api.aicu.cc/api/v3/search/getreply"
    
    DEFAULT_REPLY_PAGE_SIZE = 100  # 默认抓取评论数
    DEFAULT_AVATAR_URL = "https://i0.hdslb.com/bfs/face/member/noface.jpg"

    # 请求头常量
    DEFAULT_HEADERS = {
        'User-Agent': "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
        'accept-language': "zh-CN,zh;q=0.9",
        'cache-control': "no-cache",
        'origin': "https://www.aicu.cc",
        'referer': "https://www.aicu.cc/",
        'pragma': "no-cache",
        'priority': "u=1, i",
        'sec-ch-ua': "\"Chromium\";v=\"140\", \"Not=A?Brand\";v=\"24\", \"Google Chrome\";v=\"140\"",
        'sec-ch-ua-mobile': "?0",
        'sec-ch-ua-platform': "\"Windows\"",
        'sec-fetch-dest': "empty",
        'sec-fetch-mode': "cors",
        'sec-fetch-site': "same-site",
    }
    
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config
        self._browser = None
        self._playwright = None
        
        # 1. 使用框架提供的标准数据目录
        self.data_dir = StarTools.get_data_dir("aicu_analysis")
        self.output_dir = self.data_dir / "temp"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # 2. 模板文件依然在插件源码目录
        self.plugin_dir = Path(__file__).parent
    
    async def _get_browser(self):
        """
        获取或创建浏览器实例 (修复资源泄漏问题)
        """
        if self._browser is None:
            # 启动 Playwright 服务
            self._playwright = await async_playwright().start()
            try:
                # 尝试以正常方式启动
                try:
                    self._browser = await self._playwright.chromium.launch(headless=True)
                except Exception:
                    logger.warning("[AICU] 无法正常启动浏览器，尝试使用无沙箱模式")
                    self._browser = await self._playwright.chromium.launch(headless=True, args=['--no-sandbox'])
            except Exception as e:
                # 如果浏览器启动失败，必须关闭 playwright 服务，防止僵尸进程
                logger.error(f"[AICU] 启动浏览器严重失败: {e}")
                await self._playwright.stop()
                self._playwright = None
                raise e
        return self._browser
    
    async def _close_browser(self):
        """关闭浏览器实例"""
        if self._browser:
            await self._browser.close()
            self._browser = None
        
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
    
    async def on_plugin_load(self):
        logger.info("[AICU] 插件加载完成")
    
    async def on_plugin_unload(self):
        await self._close_browser()
        logger.info("[AICU] 插件卸载，浏览器资源已清理")

    # ================= 1. 异步请求封装 =================
    async def _make_request(self, url: str, params: dict, cookie_override: str = None):
        """
        异步通用请求 (修复 JSON 解析阻塞事件循环问题)
        """
        headers = self.DEFAULT_HEADERS.copy()

        if cookie_override is not None:
            if cookie_override: headers['cookie'] = cookie_override
        elif self.config.get("cookie"):
            headers['cookie'] = self.config.get("cookie")

        async with AsyncSession() as session:
            try:
                logger.debug(f"[AICU] Fetching: {url}")
                response = await session.get(url, params=params, headers=headers, timeout=20)
                
                if response.status_code != 200:
                    logger.warning(f"[AICU] 请求返回非200状态码: {response.status_code} | URL: {url}")
                    return None
                
                # 将同步的 JSON 解析移入线程池，避免阻塞事件循环
                loop = asyncio.get_running_loop()
                return await loop.run_in_executor(None, response.json)

            except Exception as e:
                logger.error(f"[AICU] 网络请求异常: {e}")
                return None

    # ================= 2. 抓取逻辑 =================
    async def _fetch_all_data(self, uid: str, page_size: int):
        """并发获取所有用户数据"""
        task_bili = self._make_request(self.AICU_BILI_API_URL, {'mid': uid})
        task_mark = self._make_request(self.AICU_MARK_API_URL, {'uid': uid})
        
        # 评论接口先尝试带 Cookie
        reply_data = await self._make_request(
            self.AICU_REPLY_API_URL, 
            {'uid': uid, 'pn': "1", 'ps': str(page_size), 'mode': "0", 'keyword': ""}
        )
        
        # 重试逻辑：如果不带 Cookie 重试
        if not reply_data or not reply_data.get('data'):
             logger.info("[AICU] 评论获取失败，尝试不带 Cookie 重试...")
             reply_data = await self._make_request(
                self.AICU_REPLY_API_URL, 
                {'uid': uid, 'pn': "1", 'ps': str(page_size), 'mode': "0", 'keyword': ""},
                cookie_override="" 
             )
        
        bili_data, mark_data = await asyncio.gather(task_bili, task_mark)
        return bili_data, mark_data, reply_data

    # ================= 3. 数据解析 =================
    def _parse_profile(self, bili_raw, uid):
        profile = {
            "name": f"UID:{uid}", "avatar": self.DEFAULT_AVATAR_URL,
            "sign": "", "level": 0, "vip_label": "", "fans": 0, "following": 0
        }
        
        if not bili_raw or bili_raw.get('code') != 0:
            return profile

        data = bili_raw.get('data', {})
        card = data.get('card', {})
        
        if card:
            profile["name"] = card.get('name', uid)
            profile["avatar"] = card.get('face', profile["avatar"])
            profile["sign"] = card.get('sign', "")
            profile["fans"] = card.get('fans', 0)
            profile["following"] = card.get('friend', 0)
            profile["level"] = card.get('level_info', {}).get('current_level', 0)
            vip = card.get('vip', {})
            if vip.get('label', {}).get('text'):
                profile["vip_label"] = vip.get('label', {}).get('text')
        
        return profile

    def _parse_device(self, mark_raw):
        device_name = "未知设备"
        history_names = []
        
        if mark_raw and mark_raw.get('code') == 0:
            m_data = mark_raw.get('data', {})
            devices = m_data.get('device', [])
            if devices:
                device_name = devices[0].get('name') or devices[0].get('type')
            history_names = m_data.get('hname', [])
        elif not self.config.get("cookie"):
            device_name = "需配置Cookie"
            
        return device_name, history_names

    def _parse_replies(self, reply_raw):
        """解析评论列表 (修复嵌套逻辑可读性)"""
        replies = []
        if reply_raw and reply_raw.get('code') == 0:
             data_block = reply_raw.get('data', {})
             
             # 优化：处理 AICU API 数据结构不一致的问题 (data.replies 或 data.data.replies)
             if 'replies' not in data_block and isinstance(data_block.get('data'), dict):
                 data_block = data_block['data']
                 
             replies = data_block.get('replies', []) or []

        formatted_replies = []
        hours = []
        lengths = []

        for i, r in enumerate(replies):
            ts = r.get('time', 0)
            dt = datetime.fromtimestamp(ts)
            msg = r.get('message', '')
            hours.append(dt.strftime("%H"))
            lengths.append(len(msg))
            formatted_replies.append({
                "index": i + 1,
                "message": msg,
                "readable_time": dt.strftime('%Y-%m-%d %H:%M'),
                "rank": r.get('rank', 0),
                "timestamp": ts
            })

        hour_counts = Counter(hours)
        top_hours = dict(hour_counts.most_common(5))
        max_hour_count = max(hour_counts.values()) if hour_counts else 0
        
        most_common_hour = hour_counts.most_common(1)
        active_hour = most_common_hour[0][0] if most_common_hour else "N/A"
        avg_len = round(sum(lengths) / len(lengths), 1) if lengths else 0

        return {
            "list": formatted_replies,
            "count": len(formatted_replies),
            "stats": {
                "active_hour": active_hour,
                "hour_dist": top_hours,
                "max_hour_count": max_hour_count,
                "avg_length": avg_len
            }
        }

    # ================= 4. 图片渲染 =================
    async def _render_image(self, render_data):
        template_path = self.plugin_dir / "template.html"
        if not template_path.exists():
            raise FileNotFoundError("找不到 template.html 文件")

        with open(template_path, "r", encoding="utf-8") as f:
            template_str = f.read()
        
        template = jinja2.Template(template_str)
        html_content = template.render(**render_data)
        
        file_name = f"aicu_{render_data['uid']}_{int(time.time())}.png"
        file_path = self.output_dir / file_name
        
        try:
            browser = await self._get_browser()
            page = await browser.new_page(viewport={'width': 600, 'height': 800}, device_scale_factor=2)
            
            try:
                await page.set_content(html_content, wait_until='networkidle')
                try:
                    await page.locator(".container").screenshot(path=str(file_path))
                except Exception as e:
                    logger.warning(f"局部截图失败，尝试全页截图: {e}")
                    await page.screenshot(path=str(file_path), full_page=True)
            finally:
                await page.close()
        except Exception as e:
            logger.error(f"渲染过程发生严重错误: {e}")
            raise e
            
        return str(file_path)

    # ================= 5. 指令入口 =================
    @filter.command("uid")
    async def analyze_uid(self, event: AstrMessageEvent, uid: str):
        """查询 AICU 用户画像"""
        if not uid.isdigit():
            yield event.plain_result("❌ 请输入纯数字 UID")
            return

        yield event.plain_result(f"🔍 正在获取 UID: {uid} 的数据...")

        try:
            # 使用常量代替硬编码
            bili_raw, mark_raw, reply_raw = await self._fetch_all_data(uid, self.DEFAULT_REPLY_PAGE_SIZE)
            
            if not bili_raw and not reply_raw:
                yield event.plain_result(f"❌ 数据获取失败。请检查配置中的 Cookie 是否正确。")
                return

            profile = self._parse_profile(bili_raw, uid)
            device_name, history_names = self._parse_device(mark_raw)
            reply_data = self._parse_replies(reply_raw)

            render_data = {
                "uid": uid,
                "profile": profile,
                "device_name": device_name,
                "history_names": history_names[:10],
                "total_count": reply_data["count"],
                "avg_length": reply_data["stats"]["avg_length"],
                "active_hour": reply_data["stats"]["active_hour"],
                "hour_dist": reply_data["stats"]["hour_dist"],
                "max_hour_count": reply_data["stats"]["max_hour_count"],
                "replies": reply_data["list"],
                "generate_time": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }

            img_path = await self._render_image(render_data)
            yield event.image_result(img_path)

        except Exception as e:
            logger.error(f"插件处理失败: {e}", exc_info=True)
            yield event.plain_result(f"❌ 插件运行错误，请查看后台日志。")