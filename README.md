# luci-app-esp32monitor

面向 ESP32 的只读 OpenWrt 状态接口及 LuCI 配置页面。纯 Shell/JavaScript，架构为 all，无额外 kmod 依赖；依赖 OpenWrt 的 procfs、sysfs、UCI 和 uHTTPd CGI 服务。

## 使用

编译：先在 menuconfig 的 LuCI → Applications 中选中 luci-app-esp32monitor，再在 OpenWrt 根目录执行 `make package/luci-app-esp32monitor/compile V=s`。简体中文界面需同时安装 luci-i18n-esp32monitor-zh-cn。
安装后在 **服务 → ESP32 监控（ESP32 Monitor）** 中配置并保存应用。
接口默认关闭，安装初始化会生成 32 位 UUID 随机令牌（移除连字符）。由 procd 管理后台采样进程，HTTP 请求只鉴权并读取最新快照。

```sh
curl -H 'X-API-Token: YOUR_TOKEN' http://ROUTER/cgi-bin/esp32-status
# 启用 URL Token 时也可以使用：
curl 'http://ROUTER/cgi-bin/esp32-status?token=YOUR_TOKEN'
```

## 配置

配置文件 `/etc/config/esp32monitor`，命名节 `main`，类型 `esp32monitor`：

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| enabled | 0 | 启用接口；关闭返回 HTTP 503 |
| api_token | 安装时随机生成 | LuCI 要求 8–128 位字母、数字、下划线或连字符 |
| wan_device | 空 | 自动通过 WAN ubus 状态或默认路由检测，或手动指定设备 |
| interfaces | 空列表 | 监控全部非 lo 设备，或用 UCI list 指定设备 |
| only_link_up | 1 | 仅返回 carrier=1 或 operstate=up 的接口 |
| temperature_source | auto | 自动探测 thermal/hwmon，或指定完整传感器路径 |
| s3_gif_enabled | 0 | 启用 S3 GIF 屏保支持；勾选后显示屏保类型选项 |
| screensaver_type | clock | 屏保类型二选一：clock（时间屏保）、gif（GIF 屏保） |
| screensaver_timeout | 60 | 屏保等待时间，单位秒，整数 0–86400；0 禁用 |
| allow_query_token | 1 | 允许 URL 中的 token 参数；非空请求头优先 |

后台约每秒采样一次，不提供 sample_interval。HTTP 请求不再等待采样。指定 WAN 不改变接口筛选。
温度路径仅允许枚举到的 thermal/hwmon 属性，读不到或格式无效返回 null。
URL Token 只使用上述 URL 安全字符，不做百分号解码。禁用 URL Token 后客户端必须发送 X-API-Token；部分 uHTTPd 构建不能向 CGI 转交此请求头。
原脚本的 `/etc/esp32-status.token`、固定默认令牌和环境变量不再作为配置来源；已有客户端需要改为新令牌。

## 响应与兼容

保留原 JSON 字段：ok、hostname、cpu_percent、memory_percent、temperature_c、uptime_seconds、wan_device、interface_count、interfaces。
新增系统日期时间字段（字符串）：

| 字段 | 格式 | 示例 |
| --- | --- | --- |
| system_date | YYYY-MM-DD | 2026-09-29 |
| system_time | HH:MM:SS（24 小时制） | 21:30:05 |

响应新增字符串字段 `screensaver_type`：`"clock"` 表示时间屏保，`"gif"` 表示 GIF 屏保。
只有启用 `s3_gif_enabled=1` 后才能选择 GIF 屏保，默认关闭。关闭时隐藏类型选项并保留原选择，但接口始终返回 `clock`；重新开启后恢复选择。已有配置缺少此开关也视为关闭。
类型未设置或非法值回退为 `clock`。GIF 可在 LuCI 中上传，播放由 ESP32 固件处理。
两种屏保共用 `screensaver_timeout`，等待时间为 0 时均禁用。

响应还包含数字字段 `screensaver_timeout`，例如 `"screensaver_timeout":60`。
ESP32 固件应在无用户操作达到此秒数时进入屏保，0 表示禁用；正常状态轮询不应重置无操作计时。
路由器仅下发配置，不直接控制屏幕；旧 ESP32 固件需增加此字段的处理才会生效。
旧 UCI 配置缺少此选项或值非法时返回默认值 60；有前导零的合法整数会规范化为 JSON 数字。

日期和时间在采样结束后通过同一次 `date` 调用获取，使用路由器系统本地时区，避免跨午夜时日期与时间不一致。
返回的是路由器当前系统时钟，不代表已经完成 NTP 校时；ESP32 可以直接显示这两个字段。

接口条目包含 name、download_bps、upload_bps、download_bytes、upload_bytes。
bps 字段为了兼容保留命名，实际单位为 **B/s**，根据两次采样的实际时间差计算；累计计数为字节。
没有温度驱动不影响其他指标。设备消失会在下一次快照中排除；新设备、ifindex 改变、计数回退或读取失败时，对应速率为 null，真实零流量才返回 0。
接口仅接受 GET（405）；令牌错误返回 401；禁用返回 503；快照不存在或无效返回 503。
配置文件作为 conffile 保留，初始化不会覆盖已有非空令牌。

## 开发验证

`python3 -m unittest discover -s tests -v` 使用 BusyBox ash/awk 和隔离的 proc/sys/UCI 替身验证采样与 CGI，不修改宿主配置。测试中的 jsonfilter 使用 Python 替身；实际 procd、uHTTPd 和 jsonfilter 仍需目标机验证。
LuCI 页面及实际 uHTTPd 请求头转交仍需在目标路由器上联调。

## GIF 文件传输

启用 S3 GIF 支持并选择 GIF 屏保后，出现上传按钮。支持 GIF87a/GIF89a，最大 2 MiB；服务端验证文件头和大小，不执行完整 GIF 解码校验。屏幕尺寸适配与解码能力取决于 ESP32 固件。
上传立即替换 `/etc/esp32monitor/screensaver.gif`，使用临时文件验证后原子替换，失败保留旧图；设置需另外保存应用。文件存于闪存，并加入 sysupgrade 保留列表。

启用 S3 GIF 支持、选择 GIF 屏保且已上传文件时，状态返回：
```json
{"gif_url":"/cgi-bin/esp32-gif","gif_version":"SHA-256内容摘要","gif_size":153600}
```
选择时间屏保时，即使已经上传 GIF，也返回 `screensaver_type:"clock"`、`gif_url:null`、`gif_version:null`、`gif_size:0`，并保留已上传文件供以后切换使用。
没有文件或未启用支持时，返回 `gif_url:null`、`gif_version:null`、`gif_size:0`。
ESP32 应在版本变化时通过同一主机下载，使用与状态接口相同的 Token（请求头或允许时的 URL 参数），验证下载内容 SHA-256 后替换本地缓存。上传替换期间若内容与状态摘要不同，应重新读取状态并重试。
下载接口只接受 GET；服务禁用返回 503，鉴权失败返回 401，GIF 支持关闭或未上传返回 404。正常响应为 `image/gif`。
路由器不主动推送、转码或播放 GIF；ESP32 固件仍需实现下载、缓存和解码。下载失败保留旧缓存，无可用图片时回退时间屏保。

## v2 后台采样与升级

版本 release 9 起，服务 `/etc/init.d/esp32monitor` 管理
`/usr/libexec/esp32monitor-collector`。采集逻辑在
`/usr/share/esp32monitor/collector.sh`，十进制计数差值算法在 `rates.awk`。
单个进程保留前一轮基线，完成一整轮后在 `/tmp/esp32monitor` 内原子替换
`snapshot.json`；不写入闪存。每个请求读取一次同一份完整快照，不触发采样。

| 字段 | 语义 |
| --- | --- |
| schema_version | 2；旧字段保留，但无效速率现在可能为 null |
| boot_id | 内核启动 UUID |
| collector_id | 每次采样进程启动生成的新 UUID |
| sample_seq | 本次进程每次发布递增，从 1 开始 |
| sample_end_ms | `/proc/uptime` 换算的采样结束毫秒数，不是 Unix 时间戳，源精度通常约 10ms |
| interval_ms | 相邻采样结束时刻间隔，第一轮为 null |
| age_ms | CGI 请求时计算的数据年龄，不写死在缓存中 |
| stale | 年龄超过 3000ms 或时钟异常时为 true |

每个接口新增 `ifindex` 和 `interval_ms`；接口速率使用该接口相邻两次读取的实际间隔。
CPU 第一次采样也返回 null。RX/TX 读取失败时相应累计值为 null，并使下一轮速率
继续为 null，直到取得两个连续有效计数。累计值始终是当前内核原始值，不补偿复位，
不将多个接口相加。WAN RX 为下载、TX 为上传；LAN 方向是路由器视角。
原始字节文本直接写入 JSON；先逐位求差再用 awk 计算速率，避免大于 2^53 的
累计计数先转浮点导致小增量丢失。速率本身取整为 B/s。

无快照返回 HTTP 503；陈旧快照仍返回 HTTP 200 和 stale=true。
客户端必须显示过期状态，不应将旧速率作为实时值。使用
`boot_id + collector_id + sample_seq` 去重；序号跳跃仅说明客户端未收到中间样本。
进程重启后基线重建，第一份新快照的速率不可用。
物理/虚拟接口在两个采样时点之间发生又恢复的短暂变化不能保证由轮询全部识别。

LuCI 保存应用会触发服务重载。SSH 修改后执行：

```sh
uci set esp32monitor.main.enabled='1'
uci commit esp32monitor
/etc/init.d/esp32monitor enable
/etc/init.d/esp32monitor restart
curl -sS "http://127.0.0.1/cgi-bin/esp32-status?token=$(uci -q get esp32monitor.main.api_token)"
```

启用、停用服务应通过 LuCI 保存应用或上述 init 脚本完成；不要只修改 enabled 后期待
已停止的采集进程自行启动。Token 鉴权每次请求读取最新配置。其他配置每轮重新读取；
GIF 元信息在下一轮更新，并按文件身份缓存摘要，避免每秒重复哈希。
仅运行一个由 procd 管理的采集进程，不应同时手动启动多个采集器。

ESP32 升级要求：支持速率/计数 null、stale 标志、采样身份与序号；累计计数使用
uint64_t 或能保留整数精度的解析方式。TB 按 1000 进制、TiB 按 1024 进制由显示端选择。
插件继续保持 LUCI_PKGARCH:=all，无新增架构相关编译代码；新增 coreutils-stat 依赖用于 GIF 文件身份检查，由 OpenWrt 为目标架构提供。仍需在实际 32/64 位
OpenWrt 上验证 procd 重启、LuCI 配置应用和 HTTP 响应。

### 连续曲线的当前限制

当前只保留最新快照，没有历史样本补取接口。客户端轮询可能收到重复序号或跳过中间
样本，不能据此保证连续曲线完整。重复序号不应在曲线中插入空点；真实无效值和长期
断线也不应填零。已报告的 ESP32 曲线断点尚未定位，需要结合固件绘图逻辑与实际返回
数据排查；历史缓存和增量补取尚未实现。
