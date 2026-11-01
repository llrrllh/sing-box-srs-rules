# SKK 同源 SRS 规则

本项目把已确认的 **28 份 SKK JSON 规则**编译成 sing-box 的 SRS 二进制格式，保留同一来源、规则内容与分类。Spotify 继续使用现有的远程 SRS，不在这里重复生成。

这是一份 **GitHub 规则发布项目**，不是可直接导入 sing-box MT 的 ZIP 配置包。仓库中没有代理节点、密码、UUID、Reality 参数或个人配置。

## 包含内容

- `manifest.json`：28 份上游原始 URL、对应标签、输出位置与异常缩减阈值。
- `sources/`：本次编译对应的完整上游 JSON 快照，字节内容保持原样。
- `rules/`：对应的 28 个 `.srs` 二进制文件。
- `metadata.json`：每份源文件与 SRS 的 SHA-256、大小和条目数量。
- `scripts/update_rules.py`：下载、检查、编译与写回工具，仅使用 Python 标准库。
- `.github/workflows/update-rules.yml`：每天北京时间 08:20 自动检查更新，也可手动运行。

## 放到 GitHub 后如何更新

1. 将本项目内容放到你自己可公开读取的 GitHub 仓库中，保留 `.github/workflows/` 目录。
2. 首次提交编译脚本或工作流时自动运行；之后每天北京时间 08:20 更新，也可在 Actions 页面手动运行 `Update same-source SRS rules`。
3. 工作流下载固定版本的官方 sing-box 1.14.4 编译器，并核对固定 SHA-256；随后下载清单内的 SKK JSON，再编译 SRS。
4. 所有源通过检查和编译后，才更新 `sources/`、`rules/`、`metadata.json` 并提交。任何一次下载、JSON 检查、条目阈值检查或编译失败都会让任务报错，并保留已有发布快照。
5. sing-box MT 使用公开的 SRS 原始文件 URL，并以 `format: "binary"` 远程订阅；客户端可继续设置 `update_interval: "24h"`。

本仓库为 `llrrllh/sing-box-srs-rules`，默认分支为 `main`。广告规则地址为：

```text
https://raw.githubusercontent.com/llrrllh/sing-box-srs-rules/main/rules/domainset/reject.srs
```

其他规则保留同样的目录和文件名，把上游 `.json` 改为本项目中的 `.srs`。**仅把原 SKK JSON 链接的后缀改成 `.srs` 不会生成规则。**

项目与上游不含节点数据。实际的 sing-box 代理配置应单独保管，不要上传到公开仓库。

## 手动编译

安装 sing-box 1.14.4 后执行：

```bash
python3 scripts/update_rules.py --sing-box /path/to/sing-box
```

也可用本项目的 JSON 快照离线重建：

```bash
python3 scripts/update_rules.py --sing-box /path/to/sing-box --source-dir sources
```

当前清单为部分条目数量设置了初始快照约一半的下限，防止上游异常空白或意外大幅缩减覆盖已有规则。4 份区域媒体 IP 文件当前只有占位标记，允许没有实际 IP 条目。若上游将来确实大幅精简规则，应先核对来源，再更新 `minimum_entries`。

SRS 能减少文件体积与 JSON 解析开销，但本项目的编译和配置检查不等于已经在具体 MT 设备上实测稳定运行。媒体、AI、Spotify 等出口选择和分流顺序仍由你的独立配置决定。

## 来源、许可与编译器

- 规则来源：[Sukka Ruleset](https://github.com/SukkaW/Surge)，发布服务为 <https://ruleset.skk.moe/>。
- 上游声明：除 `List/ip/china_ip.conf` 对应的中国 IP 数据外，项目使用 AGPL-3.0；中国 IP 数据使用 CC BY-SA 2.0。此包包含源快照以便追溯与重建。请保留上游署名和许可声明；编译格式不会改变来源的许可条件。
- 上游许可原文：本项目 `LICENSE`；中国 IP 数据许可说明见 `SOURCE_LICENSES.md`。
- 编译器：[sing-box v1.14.4](https://github.com/SagerNet/sing-box/releases/tag/v1.14.4)，Linux amd64 官方归档固定 SHA-256：`699a1b510aa4396b2176d32d1181920399ebf1e267835da6f3b598d0f7c24968`。该哈希来自官方 release asset 的 `digest` 字段。
- Spotify 原始 SRS 来源：[Yuu518/sing-box-rules](https://github.com/Yuu518/sing-box-rules)，仅在清单中记录原链接，不在本项目重新分发其内容。

## 私下改写现有配置中的订阅地址

规则公开地址可访问后，可使用 `scripts/rewrite_profile.py` 只替换现有配置中的 28 份 SKK 订阅为 SRS；Spotify 和业务分流、节点分组保持原样：

```bash
python3 scripts/rewrite_profile.py \
  --input /private/path/sing-box-mt.json \
  --output /private/path/sing-box-mt-srs.json \
  --base-url https://raw.githubusercontent.com/llrrllh/sing-box-srs-rules/main
```

脚本拒绝把代理配置读入或写到这个公开项目内，也不会覆盖已经存在的输出文件。实际私有配置不包含在本包中。
