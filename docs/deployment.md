# 定时采集与本地推荐

## Windows

在仓库根目录创建虚拟环境并安装项目。计划任务默认使用当前 Windows 账户，于本机时间每周五 19:00 生成推荐。可用 `-At` 修改时间，脚本会保留同名的已有任务并提示冲突。

~~~powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[reader]"
.\deploy\windows\register-weekly.ps1
~~~

如使用 VPS 采集，注册时传入 `-Markers D:\data\markers.json`。任务会先导入快照，再在本地排序。手动验收可先执行 `research weekly --markers D:\data\markers.json`。

## Debian VPS

在 VPS 的仓库根目录执行以下命令。服务每周五北京时间 08:00 单进程采集，只安装基础 CLI。它按页写 SQLite，流式导出最近 90 天的版本记录，成功后原子替换快照。

~~~sh
mkdir -p "$HOME/.local/share/pread" "$HOME/.config/systemd/user"
python3 -m venv "$HOME/.local/share/pread/.venv"
"$HOME/.local/share/pread/.venv/bin/pip" install .
install -m 644 deploy/systemd/pread-collect.service deploy/systemd/pread-collect.timer "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
systemctl --user start pread-collect.service
systemctl --user enable --now pread-collect.timer
~~~

检查 `journalctl --user -u pread-collect.service` 和 `systemctl --user list-timers pread-collect.timer`。退出登录后仍需运行时，可为部署账户启用 systemd 用户管理器的 linger。示例服务配置了 128 MiB 的内存回收阈值和 192 MiB 的硬上限，实际占用以目标 VPS 的运行数据为准。 [systemd 定时器](https://www.freedesktop.org/software/systemd/man/latest/systemd.timer.html) · [资源控制](https://www.freedesktop.org/software/systemd/man/latest/systemd.resource-control.html)

通过已有 SSH 连接把 `~/.local/share/pread/markers.json` 同步到本地。若同步与本地任务可能重叠，先写临时文件，再替换正式快照。正式文件就绪后，运行本地周推荐，选择论文时用 `research prepare ARXIV_ID --version N` 获取指定版本 PDF。

快照只含论文元数据。PDF、翻译、向量与阅读笔记由本地保存。VPS 上的 PDF 缓存与 FPQ 暂保留为 [设计备选](../DESIGN.md#33-可选采集代理与按需下载)。
