# 安居客房价爬虫（带 UI）

> 仅用于学习示例。请遵守目标网站的 robots.txt、服务条款与相关法律法规。

## 功能
- 图形化界面（Tkinter）配置抓取参数
- 支持城市、关键词、页数、抓取间隔设置
- 实时日志输出
- 表格预览结果
- 导出 CSV

## 运行
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

## 参数说明
- **城市拼音**：例如 `shanghai`、`beijing`、`guangzhou`
- **关键词**：可留空，或输入地铁/区域/小区关键词
- **页数**：建议小规模抓取，避免高频访问
- **间隔秒数**：建议 >= 1 秒

## 说明
安居客页面结构可能变化，若抓不到数据，可根据浏览器开发者工具调整 `app.py` 内的 CSS 选择器。
