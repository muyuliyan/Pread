# Pread

面向 AI 研究的本地论文推荐和双语精读工具。设计见 [DESIGN.md](DESIGN.md)，手动精读流程见 [每周论文精读](docs/reading-session.md)。

## 推荐 CLI

使用 Python 3.11 或更新版本。

~~~sh
python -m pip install -e .
research collect --days 30
research rank --top 20
research weekly --no-collect
python -m unittest discover -s tests -v
~~~

采集使用 [arXiv API](https://info.arxiv.org/help/api/user-manual.html)，并按论文 ID 和版本保存元数据。可复制 pread/defaults.toml，用 research --config PATH 指定兴趣、评分权重和时区。默认使用关键词，安装 pread[semantic] 后可添加 --semantic 进行本地模型排序。推荐输出包括主推荐、最多两篇备选、分项得分和证据覆盖率。

用 Windows Task Scheduler 每周五执行 research weekly。工作目录应指向项目目录，也可以用 --db PATH 指定绝对数据库路径。每个 ISO 周保存一份结果，空候选可重试。research pause 暂停生成，research resume 恢复，research status ID read 或 skipped 可从后续候选排除论文。

单核 VPS 可运行 research collect，再用 research export-markers 导出 JSON。将文件传到本地后，用 research import-markers PATH 导入版本记录。在 Linux 上可用每周定时器运行采集。源数据收集和本地推荐可分别触发。

## 双语阅读器

~~~sh
python -m pip install -e ".[reader]"
research prepare ARXIV_ID --version 1
research reader ARXIV_ID
~~~

也可以在阅读器里选论文并点击“准备 PDF”。原始 PDF 以版本和 SHA-256 保存。段落列表和目录可定位原文、译文及 PDF 页。公式、图表和扫描页以原版 PDF 查看。阅读进度和 Markdown 笔记保存在本地。

模型连接可配置 PREAD_LLM_URL（OpenAI 兼容服务的 API 基础地址，通常以 /v1 结尾）、PREAD_LLM_MODEL 和可选的 PREAD_LLM_KEY。点击“翻译当前段”或执行 research translate ARXIV_ID ANCHOR 时，当前段落发送给所配置的模型，已有译文按版本、段落内容与模型复用。

## 即时查询

~~~sh
research glossary-add "Persistent Kernel" "在多项任务完成前持续运行的内核" "本地笔记 / GPU Kernel"
research lookup "Persistent Kernel" --paper ARXIV_ID
research lookup "实验使用了哪些 GPU" --paper ARXIV_ID --semantic --explain
~~~

点击阅读器的“查询”打开悬浮窗口，默认查询当前论文，可切换为全库。精确术语优先返回本地术语表，其他查询返回 FTS5 段落与页码。安装 pread[semantic] 后可以融合向量结果。“带出处解释”只将问题与检索到的段落发送给已配置的模型。

Windows 全局快捷键默认为 Ctrl+Shift+P（显示或隐藏）、K（查询）、T（翻译）、E（解释）、N（笔记）。可在 TOML 的 [hotkeys] 下用 reader、lookup、translate、explain、note 分别设置字母、数字或 false。Esc 隐藏查询窗口，其他应用的文本可手动复制粘贴到查询框。

## 审稿、阅读反馈与论文关联

~~~sh
research review ARXIV_ID
research weekly --semantic --rerank --review --calibrate
research feedback 2026-W48 ARXIV_ID --minutes 210 --relevance 5 --understanding 4 --quality 4 --completed
research feedback-report
research related ARXIV_ID
research link SOURCE_ID TARGET_ID "比较基线" "Results, p. 6"
research research-search "FlashAttention"
~~~

review 使用已准备的 PDF。weekly --review 会为本周最多五篇候选取回 PDF，并向已配置的模型发送精选摘录与相关先前论文的摘要。审稿引用与摘录逐字核对，再将有效的分项分数纳入排序。失败的候选保留基础评分，可查看失败原因。已生成的周推荐可重复读取。使用 --calibrate 时，收集至少四周反馈后才会小幅调整主题权重。

安装 pread[semantic] 后，可用 --rerank 加入本地二次排序。默认模型为 [BGE Reranker v2 M3](https://huggingface.co/BAAI/bge-reranker-v2-m3)，可通过 TOML 的 reranker_model 指定模型名或本地目录。

related 提供基于共享词汇的候选关联，也显示手工添加并有出处的论文关系。lookup --external 会在本地证据不足时联网查询 arXiv，返回论文元数据和链接。
