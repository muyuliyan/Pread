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
