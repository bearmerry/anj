import csv
import os
import queue
import random
import re
import threading
import time
from dataclasses import dataclass, asdict
from typing import Callable, List, Optional
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup
from openpyxl import Workbook
import tkinter as tk
from tkinter import ttk, filedialog, messagebox


USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36",
]

# 常见城市中英文别名映射（可按需补充）
CITY_ALIASES = {
    "北京": "beijing",
    "北京市": "beijing",
    "beijing": "beijing",
    "上海": "shanghai",
    "上海市": "shanghai",
    "shanghai": "shanghai",
    "广州": "guangzhou",
    "广州市": "guangzhou",
    "guangzhou": "guangzhou",
    "深圳": "shenzhen",
    "深圳市": "shenzhen",
    "shenzhen": "shenzhen",
    "杭州": "hangzhou",
    "杭州市": "hangzhou",
    "hangzhou": "hangzhou",
    "南京": "nanjing",
    "南京市": "nanjing",
    "nanjing": "nanjing",
    "苏州": "suzhou",
    "苏州市": "suzhou",
    "suzhou": "suzhou",
    "天津": "tianjin",
    "天津市": "tianjin",
    "tianjin": "tianjin",
    "重庆": "chongqing",
    "重庆市": "chongqing",
    "chongqing": "chongqing",
    "武汉": "wuhan",
    "武汉市": "wuhan",
    "wuhan": "wuhan",
    "成都": "chengdu",
    "成都市": "chengdu",
    "chengdu": "chengdu",
    "西安": "xian",
    "西安市": "xian",
    "xian": "xian",
    "郑州": "zhengzhou",
    "郑州市": "zhengzhou",
    "zhengzhou": "zhengzhou",
    "长沙": "changsha",
    "长沙市": "changsha",
    "changsha": "changsha",
    "青岛": "qingdao",
    "青岛市": "qingdao",
    "qingdao": "qingdao",
}


def normalize_city(city: str) -> str:
    value = city.strip().lower()
    if not value:
        return ""
    return CITY_ALIASES.get(value, value)


@dataclass
class HouseItem:
    title: str
    community: str
    district: str
    house_info: str
    overview: str
    area_sqm: str
    total_price_wan: str
    unit_price_yuan: str
    detail_url: str


class AnjukeScraper:
    """安居客二手房页面爬虫（仅做学习用途）。"""

    def __init__(self, city: str, keyword: str = "", timeout: int = 10):
        self.city = normalize_city(city)
        self.keyword = keyword.strip()
        self.timeout = timeout
        self.session = requests.Session()

    def _build_url_candidates(self, page: int) -> List[str]:
        base = f"https://{self.city}.anjuke.com/sale/"
        params = []
        if self.keyword:
            params.append(f"kw={quote(self.keyword)}")

        candidates = []

        query_params = list(params)
        if page > 1:
            query_params.append(f"p={page}")
        if query_params:
            candidates.append(f"{base}?{'&'.join(query_params)}")
        else:
            candidates.append(base)

        # 安居客部分城市分页为路径式：/sale/p2/
        if page > 1:
            path_url = f"{base}p{page}/"
            if params:
                path_url = f"{path_url}?{'&'.join(params)}"
            candidates.append(path_url)

        return candidates

    @staticmethod
    def _looks_like_blocked(html: str) -> bool:
        soup = BeautifulSoup(html, "html.parser")
        page_text = soup.get_text(" ", strip=True)
        title_text = soup.title.get_text(" ", strip=True) if soup.title else ""
        combined = f"{title_text} {page_text}".lower()

        blocked_markers = [
            "访问验证",
            "人机验证",
            "安全验证",
            "行为验证",
            "异常访问",
            "请输入验证码",
            "滑动验证",
            "captcha",
        ]
        marker_hit = any(marker.lower() in combined for marker in blocked_markers)

        captcha_dom_hit = bool(
            soup.select_one(
                "#captcha, .captcha, #nc_1_n1z, .verify-code, .geetest_panel, iframe[src*='captcha']"
            )
        )
        has_listing_nodes = bool(soup.select("div.property, li.list-item"))
        return (marker_hit or captcha_dom_hit) and not has_listing_nodes

    @staticmethod
    def _interruptible_sleep(seconds: float, should_stop: Optional[Callable[[], bool]] = None):
        if seconds <= 0:
            return
        if not should_stop:
            time.sleep(seconds)
            return
        end_time = time.time() + seconds
        while time.time() < end_time:
            if should_stop():
                return
            time.sleep(0.1)

    def _fetch_html(self, page: int, log, should_stop: Optional[Callable[[], bool]] = None) -> Optional[str]:
        base_headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Referer": f"https://{self.city}.anjuke.com/",
        }

        last_error = None
        for url in self._build_url_candidates(page):
            for attempt in range(1, 4):
                if should_stop and should_stop():
                    log("检测到停止请求，结束当前抓取。")
                    return None
                headers = dict(base_headers)
                headers["User-Agent"] = random.choice(USER_AGENTS)
                try:
                    resp = self.session.get(url, headers=headers, timeout=self.timeout)
                    resp.raise_for_status()
                    html = resp.text
                    if self._looks_like_blocked(html):
                        log(f"第 {page} 页疑似命中风控（第 {attempt}/3 次）：{url}")
                        self.session = requests.Session()
                        self._interruptible_sleep(1.0 + random.uniform(0.6, 1.8), should_stop=should_stop)
                        continue
                    return html
                except Exception as e:
                    last_error = e
                    log(f"第 {page} 页请求失败（{url}，第 {attempt}/3 次）：{e}")
                    self._interruptible_sleep(0.8 + random.uniform(0.4, 1.2), should_stop=should_stop)
                    continue

        if last_error:
            raise last_error
        return None

    @staticmethod
    def _extract_text(element, selectors: List[str]) -> str:
        for selector in selectors:
            found = element.select_one(selector)
            if found and found.get_text(strip=True):
                return found.get_text(" ", strip=True)
        return ""

    @staticmethod
    def _extract_price_from_text(text: str):
        total = ""
        unit = ""
        normalized = text.replace(",", "").replace("，", "")
        total_match = re.search(r"(\d+(?:\.\d+)?)\s*万", normalized)
        unit_match = re.search(
            r"(\d+(?:\.\d+)?)\s*元\s*/\s*(?:平(?:方米)?|㎡|m²|m2)",
            normalized,
            flags=re.IGNORECASE,
        )
        if total_match:
            total = total_match.group(1)
        if unit_match:
            unit_value = unit_match.group(1)
            unit = unit_value[:-2] if unit_value.endswith(".0") else unit_value
        return total, unit

    @staticmethod
    def _extract_area_from_text(text: str) -> str:
        area_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:㎡|m²|平米|平方)", text, flags=re.IGNORECASE)
        if area_match:
            return area_match.group(1)
        return ""

    @staticmethod
    def _extract_overview(element) -> str:
        selectors = [
            ".property-content-info.property-content-info-attribute .property-content-info-text",
            ".property-content-info-text.property-content-info-attribute",
            ".details-item",
            ".property-content-info",
            ".property-content-detail",
            ".property-content",
        ]

        wanted_labels = ("发布人", "佣金", "房产公司")
        excluded_labels = ("户型",)

        for selector in selectors:
            nodes = element.select(selector)
            texts = []
            for n in nodes:
                text = n.get_text(" ", strip=True)
                if not text:
                    continue
                segments = [seg.strip() for seg in re.split(r"[|｜/]+", text) if seg.strip()]
                for seg in segments or [text]:
                    if any(label in seg for label in excluded_labels):
                        continue
                    if any(label in seg for label in wanted_labels):
                        texts.append(seg)
            if texts:
                return " | ".join(texts)

        # 回退：有些页面不按分段展示，而是整块文本，使用正则提取指定字段
        fallback_text = element.get_text(" ", strip=True)
        normalized = re.sub(r"\s+", " ", fallback_text)
        extracted = []
        for label in wanted_labels:
            # 例如：发布人: 张三 / 佣金：1.0% / 房产公司：某某地产
            pattern = rf"{label}\s*[：:]\s*([^|｜/，,。;；]+)"
            match = re.search(pattern, normalized)
            if match:
                extracted.append(f"{label}：{match.group(1).strip()}")
        if extracted:
            return " | ".join(extracted)
        return ""

    def _parse_items(self, html: str) -> List[HouseItem]:
        soup = BeautifulSoup(html, "html.parser")
        containers = soup.select("div.property")

        if not containers:
            containers = soup.select("li.list-item")

        result = []
        for c in containers:
            title = self._extract_text(c, ["h3", "a.property-content-title-name", "a.house-title"])
            community = self._extract_text(c, [".property-content-info-comm-name", ".comm-address", ".property-content-info-text"])
            district = self._extract_text(c, [".property-content-info-comm-address", ".property-content-info-text:nth-of-type(2)"])
            house_info = self._extract_text(
                c,
                [
                    ".property-content-info",
                    ".property-content-info-text.property-content-info-attribute",
                    ".details-item",
                    ".tags",
                ],
            )
            overview = self._extract_overview(c)
            price_text = self._extract_text(c, [".property-price", ".pro-price", ".price"])
            full_text = c.get_text(" ", strip=True)
            total, unit = self._extract_price_from_text(price_text)
            area = self._extract_area_from_text(house_info)
            if not unit:
                _, unit = self._extract_price_from_text(full_text)
            if not total:
                total, _ = self._extract_price_from_text(full_text)
            if not area:
                area = self._extract_area_from_text(full_text)

            link = c.select_one("a[href]")
            detail_url = link.get("href", "") if link else ""

            if title or total or unit or area:
                result.append(
                    HouseItem(
                        title=title,
                        community=community,
                        district=district,
                        house_info=house_info,
                        overview=overview,
                        area_sqm=area,
                        total_price_wan=total,
                        unit_price_yuan=unit,
                        detail_url=detail_url,
                    )
                )

        return result

    def crawl(
        self,
        max_pages: int,
        delay_seconds: float,
        log,
        should_stop: Optional[Callable[[], bool]] = None,
        on_page_items: Optional[Callable[[List[HouseItem]], None]] = None,
    ) -> List[HouseItem]:
        all_items: List[HouseItem] = []
        empty_pages = 0

        for page in range(1, max_pages + 1):
            if should_stop and should_stop():
                log("检测到停止请求，已中断后续页面抓取。")
                break
            log(f"正在抓取第 {page} 页...")
            try:
                html = self._fetch_html(page, log=log, should_stop=should_stop)
            except Exception as e:
                log(f"第 {page} 页请求失败：{e}")
                self.session = requests.Session()
                self._interruptible_sleep(delay_seconds + random.uniform(0.4, 1.2), should_stop=should_stop)
                continue

            if not html:
                log(f"第 {page} 页响应为空")
                continue

            items = self._parse_items(html)
            log(f"第 {page} 页解析到 {len(items)} 条记录")
            all_items.extend(items)
            if on_page_items:
                on_page_items(all_items)

            if items:
                empty_pages = 0
            else:
                empty_pages += 1
                if empty_pages >= 2:
                    log("连续 2 页无数据，可能触发风控或到达结果末页，提前停止。")
                    break

            self._interruptible_sleep(delay_seconds + random.uniform(0.3, 1.0), should_stop=should_stop)

        return all_items


class AppUI:
    def __init__(self, root):
        self.root = root
        self.root.title("安居客房价爬虫（学习版）")
        self.root.geometry("920x620")

        self.msg_queue = queue.Queue()
        self.running = False
        self.stop_event = threading.Event()

        self.city_var = tk.StringVar(value="shanghai")
        self.keyword_var = tk.StringVar(value="")
        self.pages_var = tk.IntVar(value=2)
        self.delay_var = tk.DoubleVar(value=1.2)
        self.output_var = tk.StringVar(value="anjuke_prices.xlsx")
        self.min_total_var = tk.StringVar(value="")
        self.max_total_var = tk.StringVar(value="")
        self.min_unit_var = tk.StringVar(value="")
        self.max_unit_var = tk.StringVar(value="")
        self.min_area_var = tk.StringVar(value="")
        self.max_area_var = tk.StringVar(value="")

        self._build_form()
        self._build_table()
        self._build_log()
        self.root.after(200, self._flush_queue)

    def _build_form(self):
        frm = ttk.LabelFrame(self.root, text="参数配置")
        frm.pack(fill=tk.X, padx=12, pady=10)

        ttk.Label(frm, text="城市拼音").grid(row=0, column=0, padx=6, pady=8, sticky=tk.W)
        ttk.Entry(frm, textvariable=self.city_var, width=16).grid(row=0, column=1, padx=6, pady=8)

        ttk.Label(frm, text="关键词").grid(row=0, column=2, padx=6, pady=8, sticky=tk.W)
        ttk.Entry(frm, textvariable=self.keyword_var, width=16).grid(row=0, column=3, padx=6, pady=8)

        ttk.Label(frm, text="页数").grid(row=0, column=4, padx=6, pady=8, sticky=tk.W)
        ttk.Spinbox(frm, from_=1, to=50, textvariable=self.pages_var, width=8).grid(row=0, column=5, padx=6, pady=8)

        ttk.Label(frm, text="间隔秒数").grid(row=0, column=6, padx=6, pady=8, sticky=tk.W)
        ttk.Spinbox(frm, from_=0.2, to=10, increment=0.2, textvariable=self.delay_var, width=8).grid(row=0, column=7, padx=6, pady=8)

        ttk.Label(frm, text="输出文件").grid(row=1, column=0, padx=6, pady=8, sticky=tk.W)
        ttk.Entry(frm, textvariable=self.output_var, width=52).grid(row=1, column=1, columnspan=4, padx=6, pady=8, sticky=tk.EW)
        ttk.Button(frm, text="选择", command=self.choose_output).grid(row=1, column=5, padx=6, pady=8)

        self.btn_start = ttk.Button(frm, text="开始抓取", command=self.start_crawl)
        self.btn_start.grid(row=1, column=7, padx=6, pady=8)
        self.btn_stop = ttk.Button(frm, text="停止抓取", command=self.stop_crawl, state=tk.DISABLED)
        self.btn_stop.grid(row=1, column=8, padx=6, pady=8)

        ttk.Label(frm, text="总价(万)").grid(row=2, column=0, padx=6, pady=8, sticky=tk.W)
        ttk.Entry(frm, textvariable=self.min_total_var, width=8).grid(row=2, column=1, padx=(6, 2), pady=8, sticky=tk.W)
        ttk.Label(frm, text="~").grid(row=2, column=1, padx=(66, 2), pady=8, sticky=tk.W)
        ttk.Entry(frm, textvariable=self.max_total_var, width=8).grid(row=2, column=2, padx=(4, 6), pady=8, sticky=tk.W)

        ttk.Label(frm, text="单价(元/平)").grid(row=2, column=3, padx=6, pady=8, sticky=tk.W)
        ttk.Entry(frm, textvariable=self.min_unit_var, width=8).grid(row=2, column=4, padx=(6, 2), pady=8, sticky=tk.W)
        ttk.Label(frm, text="~").grid(row=2, column=4, padx=(66, 2), pady=8, sticky=tk.W)
        ttk.Entry(frm, textvariable=self.max_unit_var, width=8).grid(row=2, column=5, padx=(4, 6), pady=8, sticky=tk.W)

        ttk.Label(frm, text="面积(㎡)").grid(row=2, column=6, padx=6, pady=8, sticky=tk.W)
        ttk.Entry(frm, textvariable=self.min_area_var, width=8).grid(row=2, column=7, padx=(6, 2), pady=8, sticky=tk.W)
        ttk.Label(frm, text="~").grid(row=2, column=7, padx=(66, 2), pady=8, sticky=tk.W)
        ttk.Entry(frm, textvariable=self.max_area_var, width=8).grid(row=2, column=8, padx=(4, 6), pady=8, sticky=tk.W)

    def _build_table(self):
        table_frame = ttk.LabelFrame(self.root, text="结果预览")
        table_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=8)

        columns = ("title", "community", "district", "house_info", "overview", "area", "total", "unit")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=12)
        self.tree.heading("title", text="标题")
        self.tree.heading("community", text="小区")
        self.tree.heading("district", text="区域")
        self.tree.heading("house_info", text="房源信息")
        self.tree.heading("overview", text="房源概况")
        self.tree.heading("area", text="面积(㎡)")
        self.tree.heading("total", text="总价(万)")
        self.tree.heading("unit", text="单价(元/平)")
        self.tree.column("title", width=220)
        self.tree.column("community", width=140)
        self.tree.column("district", width=130)
        self.tree.column("house_info", width=180)
        self.tree.column("overview", width=220)
        self.tree.column("area", width=90, anchor=tk.CENTER)
        self.tree.column("total", width=90, anchor=tk.CENTER)
        self.tree.column("unit", width=110, anchor=tk.CENTER)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scrollbar = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscroll=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    def _build_log(self):
        log_frame = ttk.LabelFrame(self.root, text="日志")
        log_frame.pack(fill=tk.BOTH, padx=12, pady=(0, 12))
        self.log_text = tk.Text(log_frame, height=8)
        self.log_text.pack(fill=tk.BOTH, expand=True)

    def choose_output(self):
        path = filedialog.asksaveasfilename(
            title="选择导出文件",
            defaultextension=".xlsx",
            filetypes=[
                ("Excel 文件", "*.xlsx"),
                ("CSV 文件", "*.csv"),
                ("所有文件", "*.*"),
            ],
        )
        if path:
            self.output_var.set(path)

    def log(self, msg: str):
        self.msg_queue.put(("log", msg))

    def _flush_queue(self):
        try:
            while True:
                kind, payload = self.msg_queue.get_nowait()
                if kind == "log":
                    self.log_text.insert(tk.END, payload + "\n")
                    self.log_text.see(tk.END)
                elif kind == "result":
                    self._show_result(payload)
                elif kind == "done":
                    self.running = False
                    self.stop_event.clear()
                    self.btn_start.config(state=tk.NORMAL)
                    self.btn_stop.config(state=tk.DISABLED)
                    if payload:
                        messagebox.showinfo("完成", payload)
        except queue.Empty:
            pass
        finally:
            self.root.after(200, self._flush_queue)

    def _show_result(self, items: List[HouseItem]):
        for i in self.tree.get_children():
            self.tree.delete(i)

        for item in items[:300]:
            self.tree.insert(
                "",
                tk.END,
                values=(
                    item.title,
                    item.community,
                    item.district,
                    item.house_info,
                    item.overview,
                    item.area_sqm,
                    item.total_price_wan,
                    item.unit_price_yuan,
                ),
            )

    def _save_csv(self, items: List[HouseItem], path: str):
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "title",
                    "community",
                    "district",
                    "house_info",
                    "overview",
                    "area_sqm",
                    "total_price_wan",
                    "unit_price_yuan",
                    "detail_url",
                ],
            )
            writer.writeheader()
            for item in items:
                writer.writerow(asdict(item))

    def _save_excel(self, items: List[HouseItem], path: str):
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "anjuke_prices"
        headers = [
            "title",
            "community",
            "district",
            "house_info",
            "overview",
            "area_sqm",
            "total_price_wan",
            "unit_price_yuan",
            "detail_url",
        ]
        sheet.append(headers)
        for item in items:
            sheet.append(
                [
                    item.title,
                    item.community,
                    item.district,
                    item.house_info,
                    item.overview,
                    item.area_sqm,
                    item.total_price_wan,
                    item.unit_price_yuan,
                    item.detail_url,
                ]
            )
        workbook.save(path)

    def _save_output(self, items: List[HouseItem], path: str):
        ext = os.path.splitext(path)[1].lower()
        if ext == ".xlsx":
            self._save_excel(items, path)
            return
        self._save_csv(items, path)

    @staticmethod
    def _to_file_link(path: str) -> str:
        full_path = os.path.abspath(path)
        return f"file://{quote(full_path)}"

    @staticmethod
    def _to_float(value: str):
        text = value.strip()
        if not text:
            return None
        return float(text)

    @staticmethod
    def _in_range(value: str, low, high) -> bool:
        if low is None and high is None:
            return True
        try:
            number = float(value)
        except (TypeError, ValueError):
            return False
        if low is not None and number < low:
            return False
        if high is not None and number > high:
            return False
        return True

    def _apply_filters(self, items: List[HouseItem]):
        total_min = self._to_float(self.min_total_var.get())
        total_max = self._to_float(self.max_total_var.get())
        unit_min = self._to_float(self.min_unit_var.get())
        unit_max = self._to_float(self.max_unit_var.get())
        area_min = self._to_float(self.min_area_var.get())
        area_max = self._to_float(self.max_area_var.get())

        filtered = []
        for item in items:
            if not self._in_range(item.total_price_wan, total_min, total_max):
                continue
            if not self._in_range(item.unit_price_yuan, unit_min, unit_max):
                continue
            if not self._in_range(item.area_sqm, area_min, area_max):
                continue
            filtered.append(item)
        return filtered

    def start_crawl(self):
        if self.running:
            return

        city = self.city_var.get().strip()
        if not city:
            messagebox.showerror("错误", "请输入城市（支持中文或拼音），例如 上海 / shanghai")
            return

        self.running = True
        self.stop_event.clear()
        self.btn_start.config(state=tk.DISABLED)
        self.btn_stop.config(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)

        pages = max(1, int(self.pages_var.get()))
        delay = max(0.2, float(self.delay_var.get()))
        keyword = self.keyword_var.get().strip()
        output_file = self.output_var.get().strip() or "anjuke_prices.xlsx"
        try:
            self._to_float(self.min_total_var.get() or "")
            self._to_float(self.max_total_var.get() or "")
            self._to_float(self.min_unit_var.get() or "")
            self._to_float(self.max_unit_var.get() or "")
            self._to_float(self.min_area_var.get() or "")
            self._to_float(self.max_area_var.get() or "")
        except ValueError:
            self.running = False
            self.btn_start.config(state=tk.NORMAL)
            messagebox.showerror("错误", "过滤条件请输入数字，留空表示不限。")
            return

        def worker():
            try:
                self.log("免责声明：请遵守网站 robots 与服务条款，控制抓取频率。")
                city_slug = normalize_city(city)
                self.log(f"城市输入：{city} -> 站点城市标识：{city_slug}")
                scraper = AnjukeScraper(city=city_slug, keyword=keyword)
                items = scraper.crawl(
                    max_pages=pages,
                    delay_seconds=delay,
                    log=self.log,
                    should_stop=self.stop_event.is_set,
                    on_page_items=lambda new_items: self.msg_queue.put(("result", self._apply_filters(new_items))),
                )
                filtered_items = self._apply_filters(items)
                self.log(f"过滤前 {len(items)} 条，过滤后 {len(filtered_items)} 条")
                self.msg_queue.put(("result", filtered_items))
                self._save_output(filtered_items, output_file)
                file_link = self._to_file_link(output_file)
                self.log(f"Excel 下载链接（本地）：{file_link}")
                done_msg = "抓取已手动停止" if self.stop_event.is_set() else "抓取完成"
                self.msg_queue.put(("done", f"{done_msg}，共 {len(filtered_items)} 条。已保存到：{output_file}\n下载链接：{file_link}"))
            except Exception as e:
                self.msg_queue.put(("done", f"执行失败：{e}"))

        threading.Thread(target=worker, daemon=True).start()

    def stop_crawl(self):
        if not self.running:
            return
        self.stop_event.set()
        self.log("已收到停止指令：将保留当前结果并尽快结束抓取。")
        self.btn_stop.config(state=tk.DISABLED)


def main():
    root = tk.Tk()
    AppUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
