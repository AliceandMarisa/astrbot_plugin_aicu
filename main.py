# 标准库
import asyncio
import json
import time
import re
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


@register("aicu_analysis", "Huahuatgc", "AICU B站评论查询", "2.9.5", "https://github.com/Huahuatgc/astrbot_plugin_aicu")
class AicuAnalysisPlugin(Star):
    # ================= 配置常量 =================
    # 原有的 API
    AICU_BILI_API_URL = "https://worker.aicu.cc/api/bili/space"
    AICU_MARK_API_URL = "https://api.aicu.cc/api/v3/user/getusermark"
    AICU_REPLY_API_URL = "https://api.aicu.cc/api/v3/search/getreply"

    # 新增的弹幕 API
    AICU_DANMAKU_API_URL = "https://api.aicu.cc/api/v3/search/getvideodm"  # 视频弹幕
    AICU_LIVE_DANMAKU_API_URL = "https://api.aicu.cc/api/v3/search/getlivedm"  # 直播弹幕
    # 新增的入场信息 API
    AICU_ENTRY_API_URL = "https://ukamnads.icu/api/v2/user"  # 用户入场信息

    # 新增的粉丝牌和大航海 API
    AICU_MEDAL_API_URL = "https://workers.vrp.moe/bilibili/user-medals/{uid}"  # 粉丝牌信息
    AICU_GUARD_API_URL = "https://workers.vrp.moe/bilibili/live-guards/{uid}?p=1"  # 大航海信息

    # 新增的AI分析API
    AICU_AI_ANALYSIS_URL = "https://api.aicu.cc/ai"  # AI分析评论

    BILI_VIDEO_INFO_URL = "https://api.bilibili.com/x/web-interface/view"  # B站视频信息API
    BILI_USER_CARD_URL = "https://api.bilibili.com/x/web-interface/card"   # B站用户空间卡片API

    DEFAULT_REPLY_PAGE_SIZE = 100  # 默认抓取评论数
    DEFAULT_DANMAKU_PAGE_SIZE = 100  # 默认弹幕查询数量
    DEFAULT_ENTRY_PAGE_SIZE = 20  # 默认入场信息每页数量
    DEFAULT_AVATAR_URL = "https://i0.hdslb.com/bfs/face/member/noface.jpg"
    DEFAULT_AI_ANALYSIS_TIMEOUT = 30  # AI分析超时时间（秒）

    # 请求头常量
    DEFAULT_HEADERS = {
        'User-Agent': "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
        'accept-language': "zh-CN,zh;q=0.9",
        'cache-control': "no-cache",
        'origin': "https://www.aicu.cc",
        'referer': "https://www.aicu.cc/",
        'pragma': "no-pragma",
        'priority': "u=1, i",
        'sec-ch-ua': "\"Chromium\";v=\"140\", \"Not=A?Brand\";v=\"24\", \"Google Chrome\";v=\"140\"",
        'sec-ch-ua-mobile': "?0",
        'sec-ch-ua-platform': "\"Windows\"",
        'sec-fetch-dest': "empty",
        'sec-fetch-mode': "cors",
        'sec-fetch-site': "same-site",
    }

    # 入场信息API的特定请求头
    ENTRY_HEADERS = {
        'User-Agent': "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
        'accept': "*/*",
        'accept-encoding': "gzip, deflate, br, zstd",
        'accept-language': "zh-CN,zh;q=0.9",
        'origin': "https://laplace.live",
        'priority': "u=1, i",
        'referer': "https://laplace.live/",
        'sec-ch-ua': "\"Not A(Brand\";v=\"8\", \"Chromium\";v=\"132\", \"Google Chrome\";v=\"132\"",
        'sec-ch-ua-mobile': "?0",
        'sec-ch-ua-platform': "\"Windows\"",
        'sec-fetch-dest': "empty",
        'sec-fetch-mode': "cors",
        'sec-fetch-site': "cross-site",
    }

    # AI分析的特定请求头
    AI_ANALYSIS_HEADERS = {
        'User-Agent': "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
        'accept': "*/*",
        'accept-language': "zh-CN,zh;q=0.9",
        'origin': "https://www.aicu.cc",
        'priority': "u=1, i",
        'referer': "https://www.aicu.cc/",
        'sec-ch-ua': "\"Not A(Brand\";v=\"8\", \"Chromium\";v=\"132\", \"Google Chrome\";v=\"132\"",
        'sec-ch-ua-mobile': "?0",
        'sec-ch-ua-platform': "\"Windows\"",
        'sec-fetch-dest': "empty",
        'sec-fetch-mode': "cors",
        'sec-fetch-site': "same-site",
        'content-type': "text/plain"
    }

    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config
        self._browser = None
        self._playwright = None

        # Cloudflare 验证相关缓存
        self._aicu_cf_cookie: str | None = None
        self._aicu_cf_cookie_expires_at: float = 0.0  # 时间戳，避免过于频繁刷新

        # 使用框架提供的标准数据目录
        self.data_dir = StarTools.get_data_dir("aicu_analysis")
        self.output_dir = self.data_dir / "temp"
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # 插件源码目录
        self.plugin_dir = Path(__file__).parent

    async def _get_browser(self):
        """获取或创建浏览器实例"""
        if self._browser is None:
            self._playwright = await async_playwright().start()
            try:
                headless = self.config.get("browser_headless", True)
                launch_options = {
                    "headless": headless,
                    "args": [
                        "--disable-blink-features=AutomationControlled",
                        "--disable-dev-shm-usage",
                        "--no-sandbox",
                        "--disable-setuid-sandbox",
                    ],
                }
                try:
                    self._browser = await self._playwright.chromium.launch(**launch_options)
                except Exception:
                    logger.warning("[AICU] 无法正常启动浏览器，尝试使用无沙箱模式(简化参数)")
                    self._browser = await self._playwright.chromium.launch(
                        headless=headless,
                        args=['--no-sandbox'],
                    )
            except Exception as e:
                logger.error(f"[AICU] 启动浏览器严重失败: {e}")
                await self._playwright.stop()
                self._playwright = None
                raise e
        return self._browser

    async def _ensure_aicu_cf_cookie(self):
        """
        使用无头浏览器访问 aicu.cc 获取 Cloudflare 验证后的 Cookie，
        并缓存一段时间，供后续接口请求复用。

        注意：
        - 如果连续多次尝试仍然拿不到 Cookie，会进入冷却期，在冷却期内不再阻塞请求。
        """
        now = time.time()

        # 1. 已经有有效的 CF Cookie，直接用
        if self._aicu_cf_cookie and now < self._aicu_cf_cookie_expires_at:
            return

        # 2. 上次尝试失败后处于冷却期，也直接返回，避免每次请求都卡住
        if (not self._aicu_cf_cookie) and now < self._aicu_cf_cookie_expires_at:
            # 这里 _aicu_cf_cookie_expires_at 表示“下次再尝试过码”的时间点
            return

        browser = await self._get_browser()
        context = await browser.new_context(
            viewport={"width": 1280, "height": 720},
            user_agent=self.DEFAULT_HEADERS.get("User-Agent"),
        )
        page = await context.new_page()
        target_url = "https://www.aicu.cc/"
        logger.info(f"[AICU] 通过浏览器访问 {target_url} 以获取 Cloudflare 验证 Cookie")

        try:
            # 只等 DOMReady，timeout 缩短到 5 秒
            try:
                await page.goto(target_url, wait_until="domcontentloaded", timeout=5000)
            except Exception as e:
                logger.warning(f"[AICU] 打开 aicu.cc 时发生错误/超时(已忽略): {e}")

            cf_cookie = None
            max_wait_seconds = 5  # 最多额外等 5 秒轮询 Cookie

            for i in range(max_wait_seconds):
                cookies = await context.cookies()
                cookie_kv = []
                for c in cookies:
                    domain = c.get("domain") or ""
                    if "aicu.cc" in domain:
                        cookie_kv.append(f"{c['name']}={c['value']}")

                if cookie_kv:
                    cf_cookie = "; ".join(cookie_kv)
                    logger.info(f"[AICU] 已获取 Cloudflare 相关 Cookie (耗时约 {i+1} 秒): {cf_cookie}")
                    break

                await asyncio.sleep(1)

            if cf_cookie:
                # 成功拿到 Cookie：缓存 30 分钟，避免频繁过码
                self._aicu_cf_cookie = cf_cookie
                self._aicu_cf_cookie_expires_at = time.time() + 1800  # 30 分钟后再尝试刷新
            else:
                logger.warning("[AICU] 轮询后仍未从浏览器上下文中获取到 aicu.cc 相关 Cookie，Cloudflare 可能仍在拦截")
                # 失败：进入冷却期，避免每次请求都重复卡 5 秒
                self._aicu_cf_cookie_expires_at = time.time() + 600  # 10 分钟后再尝试一次

        except Exception as e:
            logger.error(f"[AICU] 通过浏览器获取 Cloudflare Cookie 失败: {e}", exc_info=True)
            # 发生异常也设置一个冷却期，避免不停重试
            self._aicu_cf_cookie_expires_at = time.time() + 600
        finally:
            try:
                await context.close()
            except Exception:
                pass

    async def _close_browser(self):
        """关闭浏览器实例"""
        if self._browser:
            await self._browser.close()
            self._browser = None

        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

    async def on_plugin_load(self):
        logger.info(f"[AICU] 插件加载完成，所有群聊和私聊均可使用")

    async def on_plugin_unload(self):
        await self._close_browser()
        logger.info("[AICU] 插件卸载，浏览器资源已清理")

    # ================= 新增：UID解析函数 =================
    def _extract_uid(self, uid_str: str) -> str:
        """
        从各种格式的UID字符串中提取纯数字UID

        支持的格式：
        - 纯数字：123456
        - 带UID前缀：UID:123456
        - 带uid前缀：uid:123456
        - 带UID=前缀：UID=123456
        - 带uid=前缀：uid=123456
        - 包含其他字符：UID:123456abc（会提取数字部分）
        """
        if not uid_str:
            return ""

        # 转换为小写方便处理
        uid_lower = uid_str.lower()

        # 如果包含uid:或uid=前缀，去掉前缀
        if uid_lower.startswith("uid:"):
            uid_str = uid_str[4:]  # 去掉"uid:"（4个字符）
        elif uid_lower.startswith("uid="):
            uid_str = uid_str[4:]  # 去掉"uid="（4个字符）

        # 提取所有数字字符
        digits = re.findall(r'\d+', uid_str)

        # 如果找到数字，返回第一个连续数字串
        if digits:
            return digits[0]

        # 如果没有找到数字，返回空字符串
        return ""

    def _validate_uid(self, uid: str) -> tuple[bool, str]:
        """验证UID是否有效，返回(是否有效, 错误信息)"""
        if not uid:
            return False, "❌ 请输入有效的UID"

        extracted_uid = self._extract_uid(uid)

        if not extracted_uid:
            return False, "❌ 未能在输入中找到有效的数字UID"

        if not extracted_uid.isdigit():
            return False, "❌ UID必须为纯数字"

        # 检查UID长度
        if len(extracted_uid) < 1:
            return False, "❌ UID不能为空"

        if len(extracted_uid) > 20:
            logger.warning(f"[AICU] 检测到超长UID ({len(extracted_uid)}位): {extracted_uid}")

        return True, extracted_uid

    # ================= 1. 异步请求封装 =================
    async def _make_request(self, url: str, params: dict, cookie_override: str = None, use_entry_headers: bool = False):
        """异步通用请求（对 aicu.cc 域名自动通过浏览器获取 Cloudflare Cookie）"""
        headers = self.DEFAULT_HEADERS.copy()

        if use_entry_headers:
            headers.update(self.ENTRY_HEADERS)

        # aicu.cc 域名尝试先过 Cloudflare
        if "aicu.cc" in url:
            try:
                await self._ensure_aicu_cf_cookie()
            except Exception as e:
                logger.warning(f"[AICU] 获取 Cloudflare Cookie 失败，将继续使用原始请求: {e}")

        # 组装 cookie：调用方覆盖 / 用户配置 + Cloudflare Cookie
        cookie_parts: list[str] = []

        if cookie_override is not None:
            if cookie_override:
                cookie_parts.append(cookie_override)
        elif self.config.get("cookie"):
            cookie_parts.append(self.config.get("cookie"))

        if self._aicu_cf_cookie:
            cookie_parts.append(self._aicu_cf_cookie)

        if cookie_parts:
            headers["cookie"] = "; ".join(cookie_parts)

        async with AsyncSession() as session:
            try:
                logger.debug(f"[AICU] Fetching: {url}")
                response = await session.get(url, params=params, headers=headers, timeout=30)

                if response.status_code != 200:
                    logger.warning(f"[AICU] 请求返回非200状态码: {response.status_code} | URL: {url}")
                    return None

                loop = asyncio.get_running_loop()
                return await loop.run_in_executor(None, response.json)

            except Exception as e:
                logger.error(f"[AICU] 网络请求异常: {e}")
                return None

    async def _make_ai_analysis_request(self, comments_text: str):
        """发送AI分析请求"""
        headers = self.AI_ANALYSIS_HEADERS.copy()

        # AI 分析也在 aicu.cc 域名下，需要先尝试过 Cloudflare
        try:
            await self._ensure_aicu_cf_cookie()
        except Exception as e:
            logger.warning(f"[AICU] 获取 Cloudflare Cookie 失败（AI分析），将继续使用原始请求: {e}")

        cookie_parts: list[str] = []
        if self.config.get("cookie"):
            cookie_parts.append(self.config.get("cookie"))
        if self._aicu_cf_cookie:
            cookie_parts.append(self._aicu_cf_cookie)
        if cookie_parts:
            headers["cookie"] = "; ".join(cookie_parts)

        timeout = self.config.get("ai_analysis_timeout", self.DEFAULT_AI_ANALYSIS_TIMEOUT)

        async with AsyncSession() as session:
            try:
                logger.debug(f"[AICU] 发送AI分析请求，评论长度: {len(comments_text)}")
                response = await session.post(
                    self.AICU_AI_ANALYSIS_URL,
                    data=comments_text.encode('utf-8'),
                    headers=headers,
                    timeout=timeout
                )

                if response.status_code != 200:
                    logger.warning(f"[AICU] AI分析请求返回非200状态码: {response.status_code}")
                    return None

                # 解析SSE流式响应
                analysis_result = ""
                content = response.text

                # 按行分割响应内容
                lines = content.strip().split('\n')
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue

                    # 检查是否是SSE格式
                    if line.startswith('data: '):
                        data_content = line[6:]  # 去掉"data: "前缀

                        # 检查是否结束
                        if data_content == '[DONE]':
                            break

                        try:
                            # 解析JSON
                            json_data = json.loads(data_content)
                            if 'response' in json_data:
                                analysis_result += json_data['response']
                        except json.JSONDecodeError:
                            # 如果不是有效的JSON，可能直接是文本
                            if data_content and data_content != 'null':
                                analysis_result += data_content
                    else:
                        # 如果不是SSE格式，直接添加到结果
                        analysis_result += line

                return analysis_result.strip()

            except Exception as e:
                logger.error(f"[AICU] AI分析请求异常: {e}")
                return None

    async def _get_bili_video_info(self, aid: str = None, bvid: str = None):
        """获取B站视频信息"""
        if not aid and not bvid:
            return None

        params = {}
        if bvid:
            params['bvid'] = bvid
        elif aid:
            params['aid'] = aid

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://www.bilibili.com'
        }

        async with AsyncSession() as session:
            try:
                response = await session.get(self.BILI_VIDEO_INFO_URL, params=params, headers=headers, timeout=10)
                if response.status_code == 200:
                    data = response.json()
                    if data.get('code') == 0:
                        return data.get('data', {})
            except Exception as e:
                logger.warning(f"[AICU] 获取视频信息失败: {e}")
        return None

    async def _get_bili_user_profile(self, uid: str):
        """直接从 B 站获取用户空间信息（头像/昵称/粉丝等）"""
        params = {
            "mid": uid,
            "photo": "1",
        }
        headers = {
            "User-Agent": self.DEFAULT_HEADERS.get("User-Agent"),
            "Referer": f"https://space.bilibili.com/{uid}",
        }
        if self.config.get("cookie"):
            headers["cookie"] = self.config.get("cookie")

        async with AsyncSession() as session:
            try:
                resp = await session.get(
                    self.BILI_USER_CARD_URL,
                    params=params,
                    headers=headers,
                    timeout=10,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("code") == 0:
                        return data
                    else:
                        logger.warning(
                            f"[AICU] B站用户卡片接口返回异常 code={data.get('code')}, message={data.get('message')}"
                        )
            except Exception as e:
                logger.warning(f"[AICU] 获取 B 站用户空间信息失败: {e}")
        return None

    # ================= 2. 原有评论查询功能 =================
    async def _fetch_all_data(self, uid: str, page_size: int):
        """并发获取所有用户数据"""
        # 个人信息直接走 B 站官方接口，避免依赖已失效的 worker.aicu.cc
        task_bili = self._get_bili_user_profile(uid)
        task_mark = self._make_request(self.AICU_MARK_API_URL, {'uid': uid})

        reply_data = await self._make_request(
            self.AICU_REPLY_API_URL,
            {'uid': uid, 'pn': "1", 'ps': str(page_size), 'mode': "0", 'keyword': ""}
        )

        if not reply_data or not reply_data.get('data'):
            logger.info("[AICU] 评论获取失败，尝试不带 Cookie 重试...")
            reply_data = await self._make_request(
                self.AICU_REPLY_API_URL,
                {'uid': uid, 'pn': "1", 'ps': str(page_size), 'mode': "0", 'keyword': ""},
                cookie_override=""
            )

        bili_data, mark_data = await asyncio.gather(task_bili, task_mark)
        return bili_data, mark_data, reply_data

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
        history_names = []  # 确保默认是空列表

        try:
            if mark_raw and mark_raw.get('code') == 0:
                m_data = mark_raw.get('data', {})
                if not isinstance(m_data, dict):
                    m_data = {}

                devices = m_data.get('device', [])
                if devices and isinstance(devices, list) and len(devices) > 0:
                    device_name = devices[0].get('name') or devices[0].get('type') or "未知设备"

                history_names = m_data.get('hname', [])
                # 确保 history_names 是列表
                if not isinstance(history_names, list):
                    history_names = []

            elif not self.config.get("cookie"):
                device_name = "需配置Cookie"
        except Exception as e:
            logger.warning(f"[AICU] 解析设备信息时出错: {e}")
            history_names = []  # 出错时返回空列表

        return device_name, history_names

    def _parse_replies(self, reply_raw):
        """解析评论列表"""
        replies = []
        if reply_raw and reply_raw.get('code') == 0:
            data_block = reply_raw.get('data', {})
            if 'replies' not in data_block and isinstance(data_block.get('data'), dict):
                data_block = data_block['data']
            replies = data_block.get('replies', []) or []

        # 确保 replies 是列表
        if not isinstance(replies, list):
            replies = []

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
        most_common_hour = hour_counts.most_common(1)
        active_hour = most_common_hour[0][0] if most_common_hour else "N/A"
        avg_len = round(sum(lengths) / len(lengths), 1) if lengths else 0

        return {
            "list": formatted_replies,
            "count": len(formatted_replies),
            "stats": {
                "active_hour": active_hour,
                "avg_length": avg_len
            }
        }

    async def _generate_ai_analysis(self, replies):
        """生成AI分析"""
        # 检查是否启用AI分析
        if not self.config.get("enable_ai_analysis", False):
            return None

        try:
            # 限制分析的最大评论数量
            max_comments = self.config.get("max_ai_comments", 20)
            analysis_replies = replies[:max_comments]

            # 构建分析文本
            analysis_text = f"请分析以下用户的评论内容，总结评论特点和发言风格：\n\n"
            for i, reply in enumerate(analysis_replies):
                analysis_text += f"评论{i+1} ({reply['readable_time']}): {reply['message']}\n"

            # 添加分析要求
            analysis_text += "\n请分析：\n1. 评论内容主题和情感倾向\n2. 发言者的兴趣偏好\n3. 语言风格和表达特点\n4. 可能的年龄群体或身份特征\n5. 总体评价"

            # 调用AI分析API
            analysis_result = await self._make_ai_analysis_request(analysis_text)

            return analysis_result

        except Exception as e:
            logger.error(f"[AICU] AI分析生成失败: {e}")
            return None

    # ================= 3. 新增弹幕查询功能 =================
    async def _fetch_danmaku_data(self, uid: str, page_size: int):
        """获取用户弹幕数据"""
        return await self._make_request(
            self.AICU_DANMAKU_API_URL,
            {'uid': uid, 'pn': "1", 'ps': str(page_size), 'keyword': ""}
        )

    def _parse_danmaku(self, danmaku_raw, enable_video_info: bool = True):
        """解析弹幕数据"""
        danmaku_list = []
        if danmaku_raw and danmaku_raw.get('code') == 0:
            data = danmaku_raw.get('data', {})
            cursor = data.get('cursor', {})
            total_count = cursor.get('all_count', 0)
            items = data.get('videodmlist', [])

            # 统计信息
            hours = []
            lengths = []
            video_ids = []

            for i, item in enumerate(items):
                ts = item.get('ctime', 0)
                dt = datetime.fromtimestamp(ts)
                content = item.get('content', '')
                oid = item.get('oid', '')  # 视频aid
                progress = item.get('progress', 0)  # 弹幕时间点(毫秒)

                # 转换为分:秒格式
                seconds = progress // 1000
                minutes = seconds // 60
                remaining_seconds = seconds % 60
                time_point = f"{minutes}:{remaining_seconds:02d}"

                hours.append(dt.strftime("%H"))
                lengths.append(len(content))
                video_ids.append(oid)

                danmaku_list.append({
                    "index": i + 1,
                    "content": content,
                    "readable_time": dt.strftime('%Y-%m-%d %H:%M'),
                    "video_id": oid,
                    "time_point": time_point,
                    "timestamp": ts,
                    "progress": progress
                })

            # 统计活跃时段
            hour_counts = Counter(hours)
            most_common_hour = hour_counts.most_common(1)
            active_hour = most_common_hour[0][0] if most_common_hour else "N/A"

            # 统计最活跃的视频
            video_counts = Counter(video_ids)
            most_active_video = video_counts.most_common(1)[0][0] if video_counts else None

            avg_length = round(sum(lengths) / len(lengths), 1) if lengths else 0

            return {
                "list": danmaku_list,
                "total_count": total_count,
                "fetched_count": len(danmaku_list),
                "stats": {
                    "active_hour": active_hour,
                    "avg_length": avg_length,
                    "most_active_video": most_active_video,
                    "video_count": len(set(video_ids))
                }
            }

        return {
            "list": [],
            "total_count": 0,
            "fetched_count": 0,
            "stats": {
                "active_hour": "N/A",
                "avg_length": 0,
                "most_active_video": None,
                "video_count": 0
            }
        }

    # ================= 4. 新增直播弹幕查询功能 =================
    async def _fetch_live_danmaku_data(self, uid: str, page_size: int):
        """获取用户直播弹幕数据"""
        return await self._make_request(
            self.AICU_LIVE_DANMAKU_API_URL,
            {'uid': uid, 'pn': "1", 'ps': str(page_size), 'keyword': ""}
        )

    def _parse_live_danmaku(self, live_danmaku_raw):
        """解析直播弹幕数据"""
        live_list = []
        if live_danmaku_raw and live_danmaku_raw.get('code') == 0:
            data = live_danmaku_raw.get('data', {})
            cursor = data.get('cursor', {})
            total_count = cursor.get('all_count', 0)
            items = data.get('list', [])

            # 统计信息
            hours = []
            lengths = []
            room_ids = []
            anchors = []
            all_danmaku = []  # 收集所有弹幕用于展示

            for room_info in items:
                room_data = room_info.get('roominfo', {})
                danmaku_items = room_info.get('danmu', [])

                room_id = room_data.get('roomid', '')
                room_name = room_data.get('roomname', '')
                anchor_name = room_data.get('upname', '')
                anchor_uid = room_data.get('upuid', '')

                room_ids.append(room_id)
                anchors.append(anchor_name)

                for i, danmaku in enumerate(danmaku_items):
                    ts = danmaku.get('ts', 0)
                    dt = datetime.fromtimestamp(ts)
                    content = danmaku.get('text', '')
                    username = danmaku.get('uname', '')

                    hours.append(dt.strftime("%H"))
                    lengths.append(len(content))

                    all_danmaku.append({
                        "index": len(all_danmaku) + 1,
                        "content": content,
                        "readable_time": dt.strftime('%Y-%m-%d %H:%M'),
                        "room_id": room_id,
                        "room_name": room_name[:30] + "..." if len(room_name) > 30 else room_name,
                        "anchor_name": anchor_name[:15] + "..." if len(anchor_name) > 15 else anchor_name,
                        "username": username,
                        "timestamp": ts
                    })

            # 限制展示数量
            display_danmaku = all_danmaku[:50]  # 最多显示50条

            # 统计信息
            hour_counts = Counter(hours)
            most_common_hour = hour_counts.most_common(1)
            active_hour = most_common_hour[0][0] if most_common_hour else "N/A"

            room_counts = Counter(room_ids)
            most_active_room = room_counts.most_common(1)[0][0] if room_counts else None

            anchor_counts = Counter(anchors)
            most_active_anchor = anchor_counts.most_common(1)[0][0] if anchor_counts else None

            avg_length = round(sum(lengths) / len(lengths), 1) if lengths else 0

            return {
                "list": display_danmaku,
                "total_count": total_count,
                "fetched_count": len(all_danmaku),
                "stats": {
                    "active_hour": active_hour,
                    "avg_length": avg_length,
                    "most_active_room": most_active_room,
                    "most_active_anchor": most_active_anchor,
                    "room_count": len(set(room_ids)),
                    "anchor_count": len(set(anchors))
                }
            }

        return {
            "list": [],
            "total_count": 0,
            "fetched_count": 0,
            "stats": {
                "active_hour": "N/A",
                "avg_length": 0,
                "most_active_room": None,
                "most_active_anchor": None,
                "room_count": 0,
                "anchor_count": 0
            }
        }

    # ================= 5. 新增入场信息查询功能 =================
    async def _fetch_entry_data(self, uid: str, page_num: int = 0, page_size: int = None):
        """获取用户入场信息数据"""
        if page_size is None:
            page_size = self.DEFAULT_ENTRY_PAGE_SIZE

        return await self._make_request(
            self.AICU_ENTRY_API_URL,
            {
                'uid': uid,
                'pageSize': str(page_size),
                'pageNum': str(page_num),
                'target': ''
            },
            use_entry_headers=True
        )

    async def _fetch_medal_data(self, uid: str):
        """获取用户粉丝牌数据"""
        url = self.AICU_MEDAL_API_URL.format(uid=uid)
        return await self._make_request(url, {}, use_entry_headers=True)

    async def _fetch_guard_data(self, uid: str):
        """获取用户大航海数据"""
        url = self.AICU_GUARD_API_URL.format(uid=uid)
        return await self._make_request(url, {}, use_entry_headers=True)

    def _parse_medal_data(self, medal_raw):
        """解析粉丝牌数据"""
        medals = []
        if medal_raw and medal_raw.get('code') == 0:
            data = medal_raw.get('data', {})
            medal_list = data.get('list', [])

            for medal in medal_list:
                medal_info = medal.get('medal_info', {})
                target_name = medal.get('target_name', '')

                # 解析颜色值
                color_start = medal_info.get('medal_color_start', 0)
                color_end = medal_info.get('medal_color_end', 0)
                color_border = medal_info.get('medal_color_border', 0)

                # 转换颜色值为十六进制
                def int_to_hex(color_int):
                    if color_int <= 0:
                        return "#cccccc"
                    return f"#{color_int:06x}"

                medals.append({
                    "name": medal_info.get('medal_name', ''),
                    "level": medal_info.get('level', 0),
                    "target_name": target_name,
                    "color_start": int_to_hex(color_start),
                    "color_end": int_to_hex(color_end),
                    "color_border": int_to_hex(color_border),
                    "is_wearing": medal_info.get('wearing_status', 0) == 1,
                    "guard_level": medal_info.get('guard_level', 0),
                    "intimacy": medal_info.get('intimacy', 0),
                    "next_intimacy": medal_info.get('next_intimacy', 0),
                    "today_feed": medal_info.get('today_feed', 0),
                    "day_limit": medal_info.get('day_limit', 0)
                })

        return medals

    def _parse_guard_data(self, guard_raw):
        """解析大航海数据"""
        guards = []
        if guard_raw and guard_raw.get('code') == 0:
            data = guard_raw.get('data', {})

            # 处理 top3 列表
            top3_list = data.get('top3', [])
            guard_list = data.get('list', [])

            # 合并 top3 和 list
            all_guards = top3_list + guard_list

            for guard in all_guards:
                medal_info = guard.get('medal_info', {})
                guard_level = guard.get('guard_level', 0)

                # 跳过未开通大航海的项
                if guard_level == 0:
                    continue

                # 获取舰长等级名称
                guard_name_map = {
                    1: "总督",
                    2: "提督",
                    3: "舰长"
                }
                guard_name = guard_name_map.get(guard_level, "舰长")

                # 解析粉丝牌颜色
                color_start = medal_info.get('medal_color_start', 0)
                color_end = medal_info.get('medal_color_end', 0)
                color_border = medal_info.get('medal_color_border', 0)

                def int_to_hex(color_int):
                    if color_int <= 0:
                        return "#cccccc"
                    return f"#{color_int:06x}"

                guards.append({
                    "anchor_name": guard.get('username', ''),
                    "guard_name": guard_name,
                    "guard_level": guard_level,
                    "medal_name": medal_info.get('medal_name', ''),
                    "medal_level": medal_info.get('medal_level', 0),
                    "color_start": int_to_hex(color_start),
                    "color_end": int_to_hex(color_end),
                    "color_border": int_to_hex(color_border),
                    "accompany_days": guard.get('accompany', 0),
                    "rank": guard.get('rank', 0)
                })

        # 按舰长等级排序（总督>提督>舰长）
        guards.sort(key=lambda x: x['guard_level'])

        return guards

    def _parse_entry(self, entry_raw):
        """解析入场信息数据"""
        if not entry_raw or entry_raw.get('code') != 200:
            return {
                "list": [],
                "total": 0,
                "has_more": False,
                "page_num": 0,
                "page_size": 0,
                "stats": {
                    "room_count": 0,
                    "anchor_count": 0,
                    "total_duration": 0,
                    "avg_duration": 0
                }
            }

        data = entry_raw.get('data', {})
        total = data.get('total', 0)
        page_num = data.get('pageNum', 0)
        page_size = data.get('pageSize', 0)
        has_more = data.get('hasMore', False)

        records = data.get('data', {}).get('records', [])

        formatted_entries = []
        room_ids = []
        anchor_names = []
        durations = []
        live_titles = []

        for i, record in enumerate(records):
            channel = record.get('channel', {})
            live = record.get('live', {})
            danmakus = record.get('danmakus', [])

            # 提取主播信息
            anchor_name = channel.get('uName', '未知主播')
            anchor_avatar = channel.get('faceUrl', self.DEFAULT_AVATAR_URL)
            room_id = channel.get('roomId', '')
            room_title = channel.get('title', '')

            # 主播标签
            tags = channel.get('tags', [])
            if not isinstance(tags, list):
                tags = []
            tags = tags[:3]  # 只取前3个标签

            # 提取直播信息
            live_title = live.get('title', '')
            parent_area = live.get('parentArea', '')
            area = live.get('area', '')

            # 直播间统计数据
            watch_count = live.get('watchCount', 0)
            like_count = live.get('likeCount', 0)
            total_income = live.get('totalIncome', 0)
            danmakus_count = live.get('danmakusCount', 0)

            # 主播总数据
            channel_total_danmaku = channel.get('totalDanmakuCount', 0)
            channel_total_income = channel.get('totalIncome', 0)
            channel_total_live = channel.get('totalLiveCount', 0)

            start_date = live.get('startDate', 0)
            stop_date = live.get('stopDate', 0)

            # 计算直播时长（分钟）
            duration_minutes = 0
            if start_date > 0 and stop_date > 0:
                duration_seconds = (stop_date - start_date) // 1000
                duration_minutes = duration_seconds // 60

            # 提取入场时间（取第一个弹幕时间）
            entry_time = 0
            if danmakus and len(danmakus) > 0:
                entry_time = danmakus[0].get('sendDate', 0)

            # 转换为可读时间
            if entry_time > 0:
                entry_dt = datetime.fromtimestamp(entry_time / 1000)
                readable_entry_time = entry_dt.strftime('%Y/%m/%d %H:%M:%S')
                readable_date = entry_dt.strftime('%Y/%m/%d')
                readable_weekday = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'][entry_dt.weekday()]
            else:
                readable_entry_time = "未知时间"
                readable_date = "未知日期"
                readable_weekday = "未知"

            # 计算观看时长
            watch_duration = "N/A"
            if entry_time > 0 and stop_date > 0:
                watch_seconds = (stop_date - entry_time) // 1000
                if watch_seconds > 0:
                    watch_hours = watch_seconds // 3600
                    watch_minutes = (watch_seconds % 3600) // 60
                    watch_duration = f"{watch_hours}h {watch_minutes}m"

            # 收入信息格式化
            income_str = f"¥{total_income:.1f}" if total_income > 0 else "¥0"
            channel_income_str = f"¥{channel_total_income:.0f}" if channel_total_income > 0 else "¥0"

            # 观看人数格式化
            if watch_count >= 10000:
                watch_count_str = f"{watch_count/10000:.1f}w"
            else:
                watch_count_str = str(watch_count)

            # 点赞数格式化
            if like_count >= 10000:
                like_count_str = f"{like_count/10000:.1f}w"
            elif like_count >= 1000:
                like_count_str = f"{like_count/1000:.1f}k"
            else:
                like_count_str = str(like_count)

            # 弹幕数格式化
            if danmakus_count >= 10000:
                danmakus_count_str = f"{danmakus_count/10000:.1f}w"
            elif danmakus_count >= 1000:
                danmakus_count_str = f"{danmakus_count/1000:.1f}k"
            else:
                danmakus_count_str = str(danmakus_count)

            # 主播总弹幕数格式化
            if channel_total_danmaku >= 10000:
                channel_total_danmaku_str = f"{channel_total_danmaku/10000:.1f}w"
            elif channel_total_danmaku >= 1000:
                channel_total_danmaku_str = f"{channel_total_danmaku/1000:.1f}k"
            else:
                channel_total_danmaku_str = str(channel_total_danmaku)

            room_ids.append(room_id)
            anchor_names.append(anchor_name)
            durations.append(duration_minutes)
            live_titles.append(live_title)

            formatted_entries.append({
                "index": i + 1,
                "anchor_name": anchor_name,
                "anchor_avatar": anchor_avatar,
                "room_id": room_id,
                "room_title": room_title[:50] + "..." if len(room_title) > 50 else room_title,
                "live_title": live_title[:60] + "..." if len(live_title) > 60 else live_title,
                "readable_date": readable_date,
                "readable_weekday": readable_weekday,
                "readable_entry_time": readable_entry_time,
                "entry_timestamp": entry_time,
                "watch_duration": watch_duration,
                "duration_minutes": duration_minutes,
                "parent_area": parent_area,
                "area": area,
                "tags": tags,
                "total_income": income_str,
                "channel_total_income": channel_income_str,
                "danmakus_count": danmakus_count_str,
                "channel_total_danmaku": channel_total_danmaku_str,
                "watch_count": watch_count_str,
                "like_count": like_count_str,
                "channel_total_live": channel_total_live,
                "is_living": channel.get('isLiving', False)
            })

        # 统计信息
        room_count = len(set(room_ids))
        anchor_count = len(set(anchor_names))
        total_duration = sum(durations)
        avg_duration = round(total_duration / len(durations), 1) if durations else 0

        # 统计最常观看的主播
        anchor_counts = Counter(anchor_names)
        most_active_anchor = anchor_counts.most_common(1)[0][0] if anchor_counts else "N/A"

        return {
            "list": formatted_entries,
            "total": total,
            "has_more": has_more,
            "page_num": page_num,
            "page_size": page_size,
            "stats": {
                "room_count": room_count,
                "anchor_count": anchor_count,
                "total_duration": total_duration,
                "avg_duration": avg_duration,
                "most_active_anchor": most_active_anchor
            }
        }

    # ================= 6. 图片渲染 =================
    async def _render_image(self, render_data, template_name: str = "template.html"):
        """渲染图片"""
        template_path = self.plugin_dir / template_name
        if not template_path.exists():
            raise FileNotFoundError(f"找不到 {template_name} 文件")

        with open(template_path, "r", encoding="utf-8") as f:
            template_str = f.read()

        template = jinja2.Template(template_str)
        html_content = template.render(**render_data)

        file_name = f"aicu_{render_data['uid']}_{int(time.time())}.png"
        file_path = self.output_dir / file_name

        try:
            browser = await self._get_browser()
            # 入场信息需要更大的高度
            if template_name == "template_entry.html":
                viewport = {'width': 750, 'height': 2000}
            else:
                viewport = {'width': 600, 'height': 1000}  # 增加高度以适应AI分析

            # 获取超时配置
            timeout = self.config.get("browser_timeout", 30) * 1000  # 转换为毫秒

            page = await browser.new_page(viewport=viewport, device_scale_factor=2)

            try:
                await page.set_content(html_content, wait_until='networkidle', timeout=timeout)
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

    # ================= 7. 指令入口 =================
    @filter.command("评论")
    async def analyze_uid(self, event: AstrMessageEvent, uid: str):
        """查询 AICU 用户画像 - 支持多种UID格式"""
        # 验证并提取UID
        valid, result = self._validate_uid(uid)
        if not valid:
            yield event.plain_result(result)
            return

        # result 现在是提取后的纯数字UID
        extracted_uid = result

        yield event.plain_result(f"🔍 正在获取 UID: {extracted_uid} 的评论数据...")

        try:
            # 使用 max_reply_count 配置，如果没有则使用默认值
            page_size = self.config.get("max_reply_count", self.DEFAULT_REPLY_PAGE_SIZE)
            bili_raw, mark_raw, reply_raw = await self._fetch_all_data(extracted_uid, page_size)

            if not bili_raw and not reply_raw:
                yield event.plain_result(f"❌ 数据获取失败。请检查配置中的 Cookie 是否正确。")
                return

            profile = self._parse_profile(bili_raw, extracted_uid)
            device_name, history_names = self._parse_device(mark_raw)

            # 确保 history_names 是列表且可切片
            if not history_names:
                history_names = []
            elif not isinstance(history_names, list):
                history_names = []

            reply_data = self._parse_replies(reply_raw)

            # 生成AI分析
            ai_analysis = None
            if self.config.get("enable_ai_analysis", False) and reply_data["list"]:
                ai_analysis = await self._generate_ai_analysis(reply_data["list"])

            render_data = {
                "uid": extracted_uid,
                "profile": profile,
                "device_name": device_name,
                "history_names": history_names[:10],
                "total_count": reply_data["count"],
                "avg_length": reply_data["stats"]["avg_length"],
                "active_hour": reply_data["stats"]["active_hour"],
                "replies": reply_data["list"],
                "ai_analysis": ai_analysis,
                "enable_ai_analysis": self.config.get("enable_ai_analysis", False),
                "generate_time": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }

            img_path = await self._render_image(render_data)
            yield event.image_result(img_path)

        except Exception as e:
            logger.error(f"插件处理失败: {e}", exc_info=True)
            yield event.plain_result(f"❌ 插件运行错误，请查看后台日志。")

    @filter.command("弹幕")
    async def analyze_danmaku(self, event: AstrMessageEvent, uid: str):
        """查询用户弹幕记录 - 支持多种UID格式"""
        # 验证并提取UID
        valid, result = self._validate_uid(uid)
        if not valid:
            yield event.plain_result(result)
            return

        # result 现在是提取后的纯数字UID
        extracted_uid = result

        # 使用 max_danmaku_count 配置
        page_size = self.config.get("max_danmaku_count", self.DEFAULT_DANMAKU_PAGE_SIZE)
        # 使用 enable_video_info 配置
        enable_video_info = self.config.get("enable_video_info", True)

        yield event.plain_result(f"🔍 正在查询 UID: {extracted_uid} 的弹幕记录...")

        try:
            danmaku_raw = await self._fetch_danmaku_data(extracted_uid, page_size)

            if not danmaku_raw:
                yield event.plain_result(f"❌ 弹幕数据获取失败。请检查配置中的 Cookie 是否正确。")
                return

            danmaku_data = self._parse_danmaku(danmaku_raw, enable_video_info)

            if danmaku_data["total_count"] == 0:
                yield event.plain_result(f"🔍 未找到 UID: {extracted_uid} 的弹幕记录")
                return

            # 获取用户基本信息
            bili_raw, mark_raw, _ = await asyncio.gather(
                self._get_bili_user_profile(extracted_uid),
                self._make_request(self.AICU_MARK_API_URL, {'uid': extracted_uid}),
                asyncio.sleep(0)
            )

            profile = self._parse_profile(bili_raw, extracted_uid)
            device_name, history_names = self._parse_device(mark_raw)

            # 确保 history_names 是列表且可切片
            if not history_names:
                history_names = []
            elif not isinstance(history_names, list):
                history_names = []

            render_data = {
                "uid": extracted_uid,
                "profile": profile,
                "device_name": device_name,
                "history_names": history_names[:5],
                "danmaku_list": danmaku_data["list"],
                "total_count": danmaku_data["total_count"],
                "fetched_count": danmaku_data["fetched_count"],
                "avg_length": danmaku_data["stats"]["avg_length"],
                "active_hour": danmaku_data["stats"]["active_hour"],
                "video_count": danmaku_data["stats"]["video_count"],
                "most_active_video": danmaku_data["stats"]["most_active_video"],
                "generate_time": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                "search_type": "弹幕"
            }

            # 使用弹幕专用模板
            img_path = await self._render_image(render_data, "template_danmaku.html")
            yield event.image_result(img_path)

        except Exception as e:
            logger.error(f"弹幕查询失败: {e}", exc_info=True)
            yield event.plain_result(f"❌ 弹幕查询错误，请查看后台日志。")

    @filter.command("直播弹幕")
    async def analyze_live_danmaku(self, event: AstrMessageEvent, uid: str):
        """查询用户直播弹幕记录 - 支持多种UID格式"""
        # 验证并提取UID
        valid, result = self._validate_uid(uid)
        if not valid:
            yield event.plain_result(result)
            return

        # result 现在是提取后的纯数字UID
        extracted_uid = result

        # 使用 max_danmaku_count 配置
        page_size = self.config.get("max_danmaku_count", self.DEFAULT_DANMAKU_PAGE_SIZE)

        yield event.plain_result(f"🔍 正在查询 UID: {extracted_uid} 的直播弹幕记录...")

        try:
            live_danmaku_raw = await self._fetch_live_danmaku_data(extracted_uid, page_size)

            if not live_danmaku_raw:
                yield event.plain_result(f"❌ 直播弹幕数据获取失败。请检查配置中的 Cookie 是否正确。")
                return

            live_data = self._parse_live_danmaku(live_danmaku_raw)

            if live_data["total_count"] == 0:
                yield event.plain_result(f"🔍 未找到 UID: {extracted_uid} 的直播弹幕记录")
                return

            # 获取用户基本信息
            bili_raw, mark_raw, _ = await asyncio.gather(
                self._get_bili_user_profile(extracted_uid),
                self._make_request(self.AICU_MARK_API_URL, {'uid': extracted_uid}),
                asyncio.sleep(0)
            )

            profile = self._parse_profile(bili_raw, extracted_uid)
            device_name, history_names = self._parse_device(mark_raw)

            # 确保 history_names 是列表且可切片
            if not history_names:
                history_names = []
            elif not isinstance(history_names, list):
                history_names = []

            render_data = {
                "uid": extracted_uid,
                "profile": profile,
                "device_name": device_name,
                "history_names": history_names[:5],
                "live_list": live_data["list"],
                "total_count": live_data["total_count"],
                "fetched_count": live_data["fetched_count"],
                "avg_length": live_data["stats"]["avg_length"],
                "active_hour": live_data["stats"]["active_hour"],
                "room_count": live_data["stats"]["room_count"],
                "anchor_count": live_data["stats"]["anchor_count"],
                "most_active_room": live_data["stats"]["most_active_room"],
                "most_active_anchor": live_data["stats"]["most_active_anchor"],
                "generate_time": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                "search_type": "直播弹幕"
            }

            # 使用直播弹幕专用模板
            img_path = await self._render_image(render_data, "template_live.html")
            yield event.image_result(img_path)

        except Exception as e:
            logger.error(f"直播弹幕查询失败: {e}", exc_info=True)
            yield event.plain_result(f"❌ 直播弹幕查询错误，请查看后台日志。")

    @filter.command("入场")
    async def analyze_entry(self, event: AstrMessageEvent, uid: str):
        """查询用户入场记录 - 支持多种UID格式"""
        # 验证并提取UID
        valid, result = self._validate_uid(uid)
        if not valid:
            yield event.plain_result(result)
            return

        # result 现在是提取后的纯数字UID
        extracted_uid = result

        # 使用 dd_page_size 配置
        page_size = self.config.get("dd_page_size", self.DEFAULT_ENTRY_PAGE_SIZE)

        yield event.plain_result(f"🔍 正在查询 UID: {extracted_uid} 的入场记录...")

        try:
            # 并发获取所有数据
            tasks = [
                self._fetch_entry_data(extracted_uid, page_size=page_size),
                self._get_bili_user_profile(extracted_uid),
                self._make_request(self.AICU_MARK_API_URL, {'uid': extracted_uid}),
                self._fetch_medal_data(extracted_uid),
                self._fetch_guard_data(extracted_uid)
            ]

            entry_raw, bili_raw, mark_raw, medal_raw, guard_raw = await asyncio.gather(*tasks)

            if not entry_raw:
                yield event.plain_result(f"❌ 入场信息获取失败。请检查网络连接或API是否可用。")
                return

            entry_data = self._parse_entry(entry_raw)

            if entry_data["total"] == 0:
                yield event.plain_result(f"🔍 未找到 UID: {extracted_uid} 的入场记录")
                return

            profile = self._parse_profile(bili_raw, extracted_uid)
            device_name, history_names = self._parse_device(mark_raw)
            medals = self._parse_medal_data(medal_raw)
            guards = self._parse_guard_data(guard_raw)

            # 确保 history_names 是列表且可切片
            if not history_names:
                history_names = []
            elif not isinstance(history_names, list):
                history_names = []

            render_data = {
                "uid": extracted_uid,
                "profile": profile,
                "device_name": device_name,
                "history_names": history_names[:5],
                "medals": medals[:10],  # 最多显示10个粉丝牌
                "guards": guards[:5],   # 最多显示5个大航海
                "entry_list": entry_data["list"],
                "total_count": entry_data["total"],
                "fetched_count": len(entry_data["list"]),
                "has_more": entry_data["has_more"],
                "page_num": entry_data["page_num"] + 1,  # 转换为1-based
                "page_size": entry_data["page_size"],
                "room_count": entry_data["stats"]["room_count"],
                "anchor_count": entry_data["stats"]["anchor_count"],
                "avg_duration": entry_data["stats"]["avg_duration"],
                "most_active_anchor": entry_data["stats"]["most_active_anchor"],
                "generate_time": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                "search_type": "入场记录"
            }

            # 使用入场信息专用模板
            img_path = await self._render_image(render_data, "template_entry.html")
            yield event.image_result(img_path)

        except Exception as e:
            logger.error(f"入场记录查询失败: {e}", exc_info=True)
            yield event.plain_result(f"❌ 入场记录查询错误，请查看后台日志。")

    @filter.command("b站帮助")
    async def show_help(self, event: AstrMessageEvent):
        """显示B站查询插件帮助信息"""
        help_text = """
🎮 AICU B站查询插件帮助信息

🔍 可用命令：

1️⃣ 用户评论查询
📝 命令：/评论 <UID>
📋 说明：查询B站用户的评论记录、设备信息和活跃时段
💡 示例：/评论 123456789

2️⃣ 弹幕记录查询
📝 命令：/弹幕 <UID>
📋 说明：查询用户在B站视频中的弹幕记录
💡 示例：/弹幕 123456789

3️⃣ 直播弹幕查询
📝 命令：/直播弹幕 <UID>
📋 说明：查询用户在B站直播间的弹幕记录
💡 示例：/直播弹幕 123456789

4️⃣ 入场记录查询
📝 命令：/入场 <UID>
📋 说明：查询用户的直播间入场记录、粉丝牌和大航海信息
💡 示例：/入场 123456789

如有问题或建议，欢迎反馈！
"""
        yield event.plain_result(help_text.strip())
